"""Server-owned preview of the existing event-day panel-test design."""
from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import dataclass
from typing import Any

from .paneltest import FEATURES, LAYER_EXPOSURES, _base, edge_test
from .vocab import (CHANNELS, MIN_N, Condition, ExposureSource, HypothesisTuple,
                    Trigger)


_OUTCOME_ID = "outcome:daily_return"
_EVENT_DISTRIBUTION_OUTCOME_ID = "outcome:market_adjusted_return_day_0"


# 서로 다른 사건 preview 상한(ALPHA-938). 도구 실행(런타임)과 최종 제출(hypothesize
# 의 MAX_PREVIEW_SUBMISSIONS)이 같은 값을 강제한다 - 프롬프트 "최대 3개"의 코드 게이트.
MAX_DISTRIBUTION_PREVIEWS = 3


@dataclass(frozen=True, slots=True)
class EventDistributionPreview:
    """One current event and the PIT-safe event-day return distribution behind it."""

    source_event_id: str
    instrument_id: str
    event_type_code: str
    n: int
    mean: float
    today: float
    percentile: float


@dataclass(frozen=True, slots=True)
class EventDistributionPreviewResult:
    status: str
    reason: str | None
    distribution: EventDistributionPreview | None
    anchor_count: int | None
    historical_n: int | None
    min_n: int


@dataclass(frozen=True, slots=True)
class EventCandidate:
    """One deduplicated current news thread that can anchor an event distribution."""

    source_event_id: str
    thread_id: str
    instrument_id: str
    title: str
    available_at: str
    evidence_id: str = ""


# 정규장 마감(KST). 마감 **정각 포함** 이후 관측된 사건은 그날 종가에 선행할 수
# 없어(종가는 15:30 에 확정) 실효 거래일이 다음 거래일로 밀린다 — 경계는 보수적
# PIT 쪽으로 접는다. duck 세션이 TimeZone=Asia/Seoul 이라(statics/duck.py)
# available_at 캐스팅은 KST 벽시계로 떨어진다.
_SESSION_CLOSE = "15:30:00"

# 사건의 **실효 거래일**: 시장이 그 사건에 반응할 수 있었던 첫 거래 세션.
# event_date(사건 명목일)가 아니라 available_at(관측 가능 시점)에서 유도한다 —
# 주말·장마감 후 사건은 명목일에 거래가 없거나 세션이 이미 닫혀 "사건일 수익률"이
# 성립하지 않는다(ALPHA-932: 전 거래일 event_date 가 설명일 동일성 필터에서
# 앵커를 전멸시킨 실측). 달력은 v_daily 의 거래일 집합이다 — 별도 달력 표면을
# 만들지 않는다. TIMESTAMPTZ→TIME 직접 캐스팅은 DuckDB 에 없다 — 세션
# TZ(Asia/Seoul)가 적용되는 TIMESTAMP 경유로 벽시계를 얻는다.
_EFFECTIVE_BASE = f"""
        CASE WHEN CAST(CAST(e.available_at AS TIMESTAMP) AS TIME) >= TIME '{_SESSION_CLOSE}'
             THEN CAST(CAST(e.available_at AS TIMESTAMP) AS DATE) + 1
             ELSE CAST(CAST(e.available_at AS TIMESTAMP) AS DATE) END
"""


def event_distribution_preview(lake, *, source_event_id: str, instrument_id: str, day: str,
                               as_of: str, today: float | None,
                               min_n: int) -> EventDistributionPreviewResult:
    """Return a closed readiness result for one current event distribution."""
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    # NaN·inf 도 부재다 — percentile 비교가 전부 False 로 접혀 0.0 이 READY 분포에
    # 실리는 것을 경계 자체가 막는다(호출자의 사전 필터에 기대지 않는다).
    if today is None or not math.isfinite(today):
        return EventDistributionPreviewResult(
            "UNAVAILABLE", "TODAY_RETURN_UNAVAILABLE", None, None, None, min_n)
    clock = as_of.replace("T", " ")[11:19]
    base = _base(day, clock)
    try:
        # 앵커는 사건·종목으로만 잡는다 — PIT 은 v_event 의 available_at 클램프가
        # 이미 보장하고, 날짜 동일성 요구는 후보 발견 창("직전 거래일~요청창 끝")과
        # 어긋나 주말·전일 사건을 전멸시킨다(ALPHA-932). 대신 **현재성**을 따로
        # 판정한다: 실효 거래일 이후 오늘 전에 완결된 거래일이 있으면 시장은 이미
        # 그날 반응했다 — 오늘 수익률과 비교할 사건이 아니다.
        anchor = lake.sql(base + f"""
        SELECT DISTINCT e.instrument_id, e.event_type_code,
               ({_EFFECTIVE_BASE}) <= DATE {literal(day)}
               AND NOT EXISTS (
                   -- 달력은 **그 종목의** 거래일이다: 거래정지면 시장이 열려도 이
                   -- 종목은 반응할 수 없다 - 전 시장 달력은 정지 종목의 앵커를
                   -- 잘못 기각하고 재개일 표본을 놓친다.
                   SELECT 1 FROM v_daily cal
                   WHERE cal.instrument_id = e.instrument_id
                     AND cal.trade_date >= ({_EFFECTIVE_BASE})
                     AND cal.trade_date < DATE {literal(day)}
               ) AS is_current
        FROM v_event e
        WHERE e.source_event_id = {literal(source_event_id)}
          AND e.instrument_id = {literal(instrument_id)}
        """)
    except Exception as e:  # noqa: BLE001 - this is an unavailable server data surface
        raise PreviewExecutionError("EVENT_DISTRIBUTION_UNAVAILABLE") from e
    if not anchor:
        return EventDistributionPreviewResult(
            "UNAVAILABLE", "ANCHOR_NOT_FOUND", None, 0, None, min_n)
    if len(anchor) != 1:
        raise PreviewExecutionError("EVENT_DISTRIBUTION_ANCHOR_AMBIGUOUS")
    instrument_id, event_type_code, is_current = anchor[0]
    instrument_id, event_type_code = str(instrument_id), str(event_type_code)
    if not is_current:
        return EventDistributionPreviewResult(
            "UNAVAILABLE", "ANCHOR_NOT_CURRENT", None, 1, None, min_n)
    try:
        # 과거 표본도 같은 실효 거래일 축이다: 사건별 available_at → 첫 거래일 →
        # 그날 AR. 명목일 조인은 주말 사건을 표본에서 조용히 떨어뜨려 분포를
        # 얇게 만든다(같은 결함의 과거 분포판).
        historical = lake.sql(base + f""",
        _eff AS (
            SELECT e.instrument_id,
                   (SELECT MIN(cal.trade_date) FROM v_daily cal
                     WHERE cal.instrument_id = e.instrument_id
                       AND cal.trade_date >= ({_EFFECTIVE_BASE})) AS trade_date
            FROM v_event e
            WHERE e.event_type_code = {literal(event_type_code)}
        )
        SELECT DISTINCT f.instrument_id, f.trade_date, d.ar
        FROM _eff f
        JOIN v_daily d
          ON d.instrument_id = f.instrument_id AND d.trade_date = f.trade_date
        WHERE f.trade_date < DATE {literal(day)}
          AND d.ar IS NOT NULL
        """)
    except Exception as e:  # noqa: BLE001 - no partial distribution is publishable
        raise PreviewExecutionError("EVENT_DISTRIBUTION_UNAVAILABLE") from e
    # NaN 은 모든 비교가 False 라 mean·percentile 을 조용히 오염시킨다 - 유한값만
    # 표본이다. 걸러서 min_n 에 못 미치면 분포가 없는 것과 같다.
    values = [value for value in (float(row[2]) for row in historical)
              if math.isfinite(value)]
    if len(values) < min_n:
        return EventDistributionPreviewResult(
            "UNAVAILABLE", "HISTORY_BELOW_MIN", None, 1, len(values), min_n)
    observed = float(today)
    return EventDistributionPreviewResult(
        "READY", "READY",
        EventDistributionPreview(
            source_event_id=source_event_id,
            instrument_id=instrument_id,
            event_type_code=event_type_code,
            n=len(values),
            mean=sum(values) / len(values),
            today=observed,
            percentile=sum(value <= observed for value in values) / len(values),
        ),
        1, len(values), min_n,
    )
_LAYER_LABELS = {
    "고유": "시장·업종 조정 수익률",
    "섹터": "시장 조정 수익률",
    "시장": "시장 수익률",
}
_FEATURE_LABELS = {
    ("가격잔차", "누적"): "최근 20거래일 누적 가격잔차",
    ("가격잔차", "변동성"): "최근 20거래일 가격잔차 변동성",
    ("거래량", "수준"): "최근 20거래일 평균 거래량",
    ("거래량", "변화"): "거래량 변화율",
    ("주주", "수준"): "외국인 지분율",
    ("주주", "변화"): "외국인 지분율의 20거래일 변화",
    ("신용", "수준"): "신용거래 비중",
    ("공매도", "수준"): "차입공매도 잔고 비중",
    ("배수", "수준"): "PBR 수준",
    ("주식수", "변화"): "상장주식수의 20거래일 변화율",
    ("주식수", "수준"): "자기주식 보유 비중",
    ("수급", "누적"): "최근 20거래일 외국인 순매수 누적",
    ("지수잔차", "민감도"): "시장 수익률 베타",
    ("국면", "수준"): "시장 변동성 국면",
    ("거시", "민감도"): "원/달러 변화에 대한 최근 60거래일 수익률 베타",
    ("금리", "민감도"): "국고채 10년물 금리 변화에 대한 수익률 베타",
    ("섹터", "민감도"): "업종 초과수익률 베타",
    ("레버리지", "수준"): "차입금 의존도",
    ("레버리지", "변화"): "차입금 의존도의 전년 대비 변화",
    ("수익성", "수준"): "ROE 수준",
    ("수익성", "변화"): "ROE의 전년 대비 변화",
    ("성장", "수준"): "매출액 증가율",
    ("재무파생", "수준"): "이자보상배율",
}


def _schema(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


def _feature_id(key: tuple[str, str]) -> str:
    return f"feature:{key[0]}/{key[1]}"


def _condition_id(key: tuple[str, str], direction: str) -> str:
    return f"condition:{key[0]}/{key[1]}:{direction}"


class HypothesisPreviewRuntime:
    """One-run option catalog and readiness preview for ``paneltest.edge_test``."""

    def __init__(self, lake, event_sets, *, day: str,
                 default_event_set_handle: str = "",
                 candidates: tuple[EventCandidate, ...] | None = None,
                 current_event_returns: dict[str, float] | None = None) -> None:
        self._lake = lake
        self._event_sets = event_sets
        self._day = day
        self._default_event_set_handle = default_event_set_handle
        self._current_event_returns = dict(current_event_returns or {})
        self.as_of = getattr(event_sets, "as_of", "")
        self._run_id = secrets.token_hex(12)
        self._previews: dict[str, PreviewResolution] = {}
        self._distribution_attempts: dict[str, dict[str, Any]] = {}
        grouped: dict[str, EventCandidate] = {}
        for candidate in candidates or ():
            prior = grouped.get(candidate.thread_id)
            if prior is None or (candidate.available_at, candidate.title,
                                 candidate.source_event_id) > (
                                     prior.available_at, prior.title,
                                     prior.source_event_id):
                grouped[candidate.thread_id] = candidate
        self._candidates = tuple(grouped[key] for key in sorted(grouped)) if candidates is not None else None
        self._candidate_by_id = {
            self._candidate_id(candidate): candidate for candidate in self._candidates or ()
        }

    def _candidate_id(self, candidate: EventCandidate) -> str:
        raw = (f"{self._run_id}:{candidate.thread_id}:{candidate.instrument_id}:"
               f"{candidate.source_event_id}")
        return "candidate_" + hashlib.sha256(raw.encode()).hexdigest()[:20]

    def tool_specs(self) -> list[dict[str, Any]]:
        handle = {"type": "string", "minLength": 16, "maxLength": 64}
        option = {"type": "string", "minLength": 1, "maxLength": 200}
        if self._candidates is not None:
            return [
                {"name": "hypothesis.list_options",
                 "description": "List current deduplicated event candidates and measurable outcomes.",
                 "input_schema": _schema({}, ())},
                {"name": "hypothesis.preview",
                 "description": "Compute the selected event type's historical event-day return distribution.",
                 "input_schema": _schema({"candidate_id": option, "outcome_id": option},
                                         ("candidate_id", "outcome_id"))},
            ]
        return [
            {"name": "hypothesis.list_options",
             "description": "List the current run's vocabulary. Omit event_set_handle for the server-owned set; pass one exposure_id to list only its compatible modifiers.",
             "input_schema": _schema({"event_set_handle": handle, "exposure_id": option}, ())},
            {"name": "hypothesis.preview",
             "description": "Check one server-defined event-day panel-test design and return a run-scoped handle.",
             "input_schema": _schema({
                 "event_set_handle": handle,
                 "trigger_id": option,
                 "outcome_id": option,
                 "layer_id": option,
                 "exposure_id": option,
                 "modifier_id": option,
             }, ("trigger_id", "outcome_id", "layer_id", "exposure_id"))},
        ]

    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if self._candidates is not None:
            return self._call_distribution(name, arguments)
        operations = {
            "hypothesis.list_options": (self._list_options, {"event_set_handle", "exposure_id"}, set()),
            "hypothesis.preview": (
                self._preview,
                {"event_set_handle", "trigger_id", "outcome_id", "layer_id", "exposure_id", "modifier_id"},
                {"trigger_id", "outcome_id", "layer_id", "exposure_id"}),
        }
        if name not in operations:
            return self._event_sets.call(name, arguments)
        if not isinstance(arguments, dict):
            return self._error("INVALID_ARGUMENTS", "arguments must be an object")
        fn, allowed, required = operations[name]
        if set(arguments) - allowed or required - set(arguments):
            return self._error("INVALID_ARGUMENTS", "arguments do not match this tool")
        try:
            return fn(**arguments)
        except ValueError as exc:
            code = getattr(exc, "code", "HANDLE_NOT_FOUND")
            out = self._error(code, "event set handle is not available")
            if name == "hypothesis.list_options" and self._default_event_set_handle:
                out["retry"] = {"tool": "hypothesis.list_options", "arguments": {}}
            elif name == "hypothesis.preview" and self._default_event_set_handle:
                retry = self._validated_preview_retry(arguments)
                out["retry"] = ( {"tool": "hypothesis.preview", "arguments": retry}
                                 if retry is not None else
                                 {"tool": "hypothesis.list_options", "arguments": {}} )
            return out
        except Exception:  # noqa: BLE001 - keep engine details server-side
            return self._error("EXECUTION_FAILED", "hypothesis preview could not be prepared")

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "error": {"code": code, "message": message}}

    def _call_distribution(self, name: str,
                           arguments: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return self._error("INVALID_ARGUMENTS", "arguments must be an object")
        if name == "hypothesis.list_options":
            if arguments:
                return self._error("INVALID_ARGUMENTS", "arguments do not match this tool")
            return self._list_distribution_options()
        if name == "hypothesis.preview":
            if set(arguments) != {"candidate_id", "outcome_id"}:
                return self._error("INVALID_ARGUMENTS", "arguments do not match this tool")
            return self._preview_distribution(**arguments)
        return self._event_sets.call(name, arguments)

    def _list_distribution_options(self) -> dict[str, Any]:
        return {
            "ok": True,
            "event_candidates": [
                {"id": self._candidate_id(candidate),
                 "label": f"{candidate.available_at[11:16]}, {candidate.title}"
                 if len(candidate.available_at) >= 16 else candidate.title}
                for candidate in self._candidates or ()
            ],
            "outcomes": [{
                "id": _EVENT_DISTRIBUTION_OUTCOME_ID,
                "label": "사건 당일 시장 초과수익률",
            }],
        }

    def _preview_distribution(self, candidate_id: str, outcome_id: str) -> dict[str, Any]:
        candidate = self._candidate_by_id.get(candidate_id)
        if candidate is None or outcome_id != _EVENT_DISTRIBUTION_OUTCOME_ID:
            return self._error("OPTION_NOT_ALLOWED", "candidate or outcome is not available")
        # 상한(최대 3개 사건)은 제출뿐 아니라 **도구 실행 시점**에도 서버가 강제한다
        # (ALPHA-938 봇 리뷰) - 제출만 자르면 왕복 예산 안에서 4·5번째 preview SQL 이
        # 그대로 실행돼 프롬프트가 광고한 계약과 어긋난다. 이미 시도한 사건의 재조회는
        # 상한에 안 걸린다(같은 사건 재시도는 새 비용 축이 아니다).
        if (candidate.source_event_id not in self._distribution_attempts
                and len(self._distribution_attempts) >= MAX_DISTRIBUTION_PREVIEWS):
            return self._error(
                "PREVIEW_LIMIT_EXCEEDED",
                f"preview 는 서로 다른 사건 최대 {MAX_DISTRIBUTION_PREVIEWS}개까지다 - "
                "이미 받은 READY handle 로 최종 제출하라")
        recipe = {"run_id": self._run_id, "candidate_id": candidate_id,
                  "outcome_id": outcome_id}
        handle = "hpr_" + hashlib.sha256(json.dumps(
            recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        try:
            result = event_distribution_preview(
                self._lake, source_event_id=candidate.source_event_id,
                instrument_id=candidate.instrument_id, day=self._day,
                as_of=self.as_of.replace("T", " "),
                today=self._current_event_returns.get(candidate.instrument_id), min_n=MIN_N,
            )
        except PreviewExecutionError as exc:
            self._distribution_attempts[candidate.source_event_id] = {
                "preview_status": "FAILED", "preview_reason": str(exc),
                "historical_n": None, "min_n": MIN_N, "handle": handle,
            }
            raise
        distribution = result.distribution
        ready = result.status == "READY"
        self._distribution_attempts[candidate.source_event_id] = {
            "preview_status": result.status,
            "preview_reason": result.reason,
            "historical_n": result.historical_n,
            "min_n": result.min_n,
            "handle": handle,
        }
        summary = (f"{candidate.title} 사건의 같은 유형 과거 사건일 시장 초과수익률 분포를 "
                   "확인합니다.")
        carrier = None
        if distribution is not None:
            feature = sorted(FEATURES)[0]
            carrier = HypothesisTuple(
                conditions=(), trigger=Trigger("점", distribution.event_type_code),
                channel=sorted(CHANNELS)[0],
                exposure=ExposureSource("속성", feature[0], feature[1]),
                outcome="수익률", layer="고유",
            )
        self._previews[handle] = PreviewResolution(
            handle, carrier, summary, ready, distribution=distribution,
            candidate=candidate,
        )
        return {
            "ok": True,
            "handle": handle,
            "available": ready,
            "status": "READY" if ready else "UNAVAILABLE",
            "reason": result.reason,
            "summary": summary,
            "method": "동일 사건 유형의 과거 사건일 시장 초과수익률 분포",
            **({"sample": {"historical_event_observations": result.historical_n}}
               if result.historical_n is not None else {}),
        }

    def distribution_attempts(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._distribution_attempts.items()}

    def distribution(self, handle: str) -> EventDistributionPreview | None:
        preview = self._previews.get(handle)
        return None if preview is None else preview.distribution

    def distribution_resolution(self, handle: str) -> "PreviewResolution | None":
        preview = self._previews.get(handle)
        return preview if preview is not None and preview.distribution is not None else None

    def _validated_preview_retry(self, arguments: dict[str, Any]) -> dict[str, str] | None:
        """Keep only IDs the server catalog validates for the default event scope."""
        try:
            trigger_id = arguments["trigger_id"]
            outcome_id = arguments["outcome_id"]
            layer_id = arguments["layer_id"]
            exposure_id = arguments["exposure_id"]
            modifier_id = arguments.get("modifier_id")
            _parse_trigger(trigger_id, self._event_types(self._event_set_handle(None)))
            exposure = _parse_feature(exposure_id)
            layer = _parse_layer(layer_id)
            if outcome_id != _OUTCOME_ID or layer not in _allowed_layers(exposure):
                return None
            condition = _parse_modifier(modifier_id) if modifier_id is not None else None
            if condition and (condition.ident, condition.transform) == exposure:
                return None
        except (KeyError, ValueError):
            return None
        retry = {"trigger_id": trigger_id, "outcome_id": outcome_id,
                 "layer_id": layer_id, "exposure_id": exposure_id}
        if modifier_id is not None:
            retry["modifier_id"] = modifier_id
        return retry

    def _event_set_handle(self, event_set_handle: str | None) -> str:
        if event_set_handle is None:
            if self._default_event_set_handle:
                return self._default_event_set_handle
            raise _OptionError("event set handle is required")
        if not isinstance(event_set_handle, str):
            raise ValueError("event set handle must be a string")
        return event_set_handle

    def _event_types(self, event_set_handle: str) -> tuple[str, ...]:
        return tuple(sorted(set(self._event_sets.event_type_codes(event_set_handle))))

    def _list_options(self, event_set_handle: str | None = None,
                      exposure_id: str | None = None) -> dict[str, Any]:
        event_set_handle = self._event_set_handle(event_set_handle)
        event_types = self._event_types(event_set_handle)
        features = [
            {"id": _feature_id(key), "label": _FEATURE_LABELS[key],
             "layers": [f"layer:{layer}" for layer in sorted(_allowed_layers(key))]}
            for key in sorted(FEATURES)
        ]
        result = {
            "ok": True,
            "event_set_handle": event_set_handle,
            "triggers": [{"id": f"event:{event_type}", "label": f"{event_type} 사건"}
                         for event_type in event_types],
            "outcomes": [{"id": _OUTCOME_ID, "label": "수익률"}],
            "layers": [{"id": f"layer:{layer}", "label": _LAYER_LABELS[layer]}
                       for layer in sorted(_LAYER_LABELS)],
            "exposures": features,
        }
        if exposure_id is not None:
            exposure = _parse_feature(exposure_id)
            result["modifiers"] = [
                {"id": _condition_id(key, direction),
                 "label": f"{_FEATURE_LABELS[key]} {'상위 10%' if direction == 'high_90' else '하위 10%'}"}
                for key in sorted(FEATURES) if key != exposure
                for direction in ("high_90", "low_10")
            ]
        return result

    def _preview(self, trigger_id: str, outcome_id: str, layer_id: str,
                 exposure_id: str, modifier_id: str | None = None,
                 event_set_handle: str | None = None) -> dict[str, Any]:
        event_set_handle = self._event_set_handle(event_set_handle)
        event_types = self._event_types(event_set_handle)
        trigger = _parse_trigger(trigger_id, event_types)
        exposure = _parse_feature(exposure_id)
        layer = _parse_layer(layer_id)
        if layer not in _allowed_layers(exposure):
            return self._error("OPTION_NOT_ALLOWED", "exposure is not available for this layer")
        if outcome_id != _OUTCOME_ID:
            return self._error("OPTION_NOT_ALLOWED", "outcome is not available")
        condition = _parse_modifier(modifier_id) if modifier_id is not None else None
        if condition and (condition.ident, condition.transform) == exposure:
            return self._error("OPTION_NOT_ALLOWED", "modifier must differ from exposure")
        hypothesis = HypothesisTuple(
            conditions=() if condition is None else (condition,),
            trigger=Trigger("점", trigger),
            channel=sorted(CHANNELS)[0],
            exposure=ExposureSource("속성", exposure[0], exposure[1]),
            outcome="수익률",
            layer=layer,
        )
        report = edge_test(self._lake, hypothesis, self._day, m_tests=1)
        ready = report.verdict != "판정불가"
        summary = (
            f"{trigger} 사건이 있었던 과거 거래일에서 {_FEATURE_LABELS[exposure]} 상위 20% 종목과 "
            f"나머지 종목의 {_LAYER_LABELS[layer]} 차이를 검정합니다."
        )
        if condition is not None:
            summary += f" 조건은 {_modifier_label(condition)}입니다."
        recipe = {
            "run_id": self._run_id, "event_set_handle": event_set_handle, "trigger_id": trigger_id,
            "outcome_id": outcome_id, "layer_id": layer_id,
            "exposure_id": exposure_id, "modifier_id": modifier_id,
        }
        handle = "hpr_" + hashlib.sha256(json.dumps(
            recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        result = {
            "ok": True,
            "handle": handle,
            "available": ready,
            "status": "READY" if ready else "UNAVAILABLE",
            "summary": summary,
            "method": "사건 거래일 내 노출 상위 20% 대 나머지 비교, 거래일별 층화 순열검정",
            "sample": {"historical_event_observations": int(report.n)},
        }
        self._previews[handle] = PreviewResolution(handle, hypothesis, result["summary"], ready)
        return result

    def resolve(self, handle: str) -> "PreviewResolution":
        """Resolve only this runtime's READY preview without reinterpreting model fields."""
        if not isinstance(handle, str) or handle not in self._previews:
            raise PreviewResolutionError("UNKNOWN_PREVIEW_HANDLE")
        preview = self._previews[handle]
        if not preview.ready or preview.hypothesis is None:
            raise PreviewResolutionError("PREVIEW_NOT_READY")
        return preview


def _allowed_layers(feature: tuple[str, str]) -> tuple[str, ...]:
    return tuple(sorted(layer for layer, allowed in LAYER_EXPOSURES.items()
                        if allowed is None or feature in allowed))


def _parse_trigger(value: str, event_types: tuple[str, ...]) -> str:
    if not isinstance(value, str) or not value.startswith("event:"):
        raise _OptionError("trigger is not available")
    trigger = value.removeprefix("event:")
    if trigger not in event_types:
        raise _OptionError("trigger is not available")
    return trigger


def _parse_feature(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value.startswith("feature:"):
        raise _OptionError("feature is not available")
    key = tuple(value.removeprefix("feature:").split("/", 1))
    if len(key) != 2 or key not in FEATURES:
        raise _OptionError("feature is not available")
    return key  # type: ignore[return-value]


def _parse_layer(value: str) -> str:
    layer = value.removeprefix("layer:") if isinstance(value, str) else ""
    if layer not in _LAYER_LABELS:
        raise _OptionError("layer is not available")
    return layer


def _parse_modifier(value: str) -> Condition:
    if not isinstance(value, str) or not value.startswith("condition:"):
        raise _OptionError("modifier is not available")
    try:
        family_transform, direction = value.removeprefix("condition:").rsplit(":", 1)
        family, transform = family_transform.split("/", 1)
    except ValueError as exc:
        raise _OptionError("modifier is not available") from exc
    key = family, transform
    if key not in FEATURES or direction not in {"high_90", "low_10"}:
        raise _OptionError("modifier is not available")
    return Condition(family, transform, ">=" if direction == "high_90" else "<=",
                     0.9 if direction == "high_90" else 0.1)


def _modifier_label(condition: Condition) -> str:
    return f"{_FEATURE_LABELS[(condition.ident, condition.transform)]} " + (
        "상위 10%" if condition.comparator == ">=" else "하위 10%")


class _OptionError(ValueError):
    code = "OPTION_NOT_ALLOWED"


@dataclass(frozen=True, slots=True)
class PreviewResolution:
    handle: str
    hypothesis: HypothesisTuple | None
    summary: str
    ready: bool
    distribution: EventDistributionPreview | None = None
    candidate: EventCandidate | None = None


class PreviewResolutionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PreviewExecutionError(RuntimeError):
    """A server-owned preview could not read its required PIT surface."""


__all__ = ["EventCandidate", "EventDistributionPreview", "EventDistributionPreviewResult",
           "HypothesisPreviewRuntime",
           "PreviewExecutionError", "PreviewResolution", "PreviewResolutionError",
           "event_distribution_preview"]
