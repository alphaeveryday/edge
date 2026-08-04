"""분석 기록 — statics 산출을 **기존 계보 체인**에 싣는다. 새 표를 만들지 않는다.

## 정본 체인 (ERD 실측)

    price_movement_trigger ─┐
    minute_price_trigger ───┴→ etf_contribution_observation (+member)
                               → explanation_route
                               → explanation_run
                               → explanation_result
                               + analysis_evidence_bundle

계보 id 는 **소비한 트리거에서 파생**한다(`stable_id`). 그래서 DB 에 있는 그 트리거
행에 계보가 매달리고, 재실행이 같은 id 를 낸다(전부 ON CONFLICT DO NOTHING).

## 두 설명이 한 행에 들어간다

새 `explanation_type` 어휘를 만들지 않았다 - CHECK 가 네 값에 묶여 있고, 그 어휘는
**근거 성격**을 뜻한다. 우리 판정이 정확히 그것을 낸다:

    EVENT_SUPPORTED  성립 엣지·통과 함의가 있다
    PRICE_ONLY       시장 환원만 - 사건은 무유의
    MIXED            혼합 라우팅 (어느 층도 지배 못 함)
    UNCERTAIN        전부 판정불가

    summary   정직한 설명 전문 (통계량·게이트·구간)
    headline  쉬운 설명 (토스식, 수치 없음) - 원장 주석이 'MTS 카드/목록용 한 줄'
    stage_results  층 회계 · 라우팅 · 게이트 판정 · 근거 묶음 id · 창 몫

## 왜 psycopg 별 연결인가

레이크의 `rdb` 는 `READ_ONLY` 로 붙어 있다. 쓰기는 별 연결이어야 한다.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

PIPELINE_ID = "alphamale-etf-daily-v1"      # observability.stable_id 와 같은 재료
DSN_ENV = "EDGE_RDB_DSN"
BUNDLE_VERSION_ENV = "ALPHAMALE_RELEASE_BUNDLE_VERSION"

TYPES = ("PRICE_ONLY", "EVENT_SUPPORTED", "MIXED", "UNCERTAIN")
LEVELS = ("HIGH", "MEDIUM", "LOW")

# 우리 라우팅 → 원장 `route_code` 어휘. **어휘를 늘리지 않는다** - CHECK 가 5종이고
# 그 뜻이 우리 5분기와 일대일로 맞는다. 늘리면 마이그레이션이 필요하고, 서빙이
# 모르는 코드를 받는다.
#   시장·섹터  공통요인이 끌었다        -> COMMON_FACTOR
#   고유        상위 종목에 집중됐다     -> CONCENTRATED
#   혼합        어느 층도 지배 못 함     -> FALLBACK_2LEG (2레그 폴백이 정확히 그 뜻)
#   괴리단독    ETF 수급·유동성          -> FLOW_DOMINATED
#   미상        가격 회계만 남았다       -> PRICE_ONLY
ROUTE_CODES = {"시장": "COMMON_FACTOR", "섹터": "COMMON_FACTOR",
               "고유": "CONCENTRATED", "혼합": "FALLBACK_2LEG",
               "괴리단독": "FLOW_DOMINATED"}
# CHECK 가 강제한다: COMMON_FACTOR·CONCENTRATED 는 event_search 필수, 나머지는 금지.
# 그래서 `event_search` 를 호출자가 정하게 두면 안 된다 - 코드에서 파생한다.
EVENT_SEARCH = {"COMMON_FACTOR", "CONCENTRATED"}


def route_code_of(route_kind: str) -> tuple[str, bool]:
    """라우팅 → (원장 코드, event_search). 미상은 PRICE_ONLY 로 떨어진다."""
    code = ROUTE_CODES.get(route_kind, "PRICE_ONLY")
    return code, code in EVENT_SEARCH


def stable_id(prefix: str, *parts: str) -> str:
    """결정적 발번 (ADR-0027 의 결정적 계열). 구분자는 `\\x01` - 원장과 같아야 한다."""
    material = "\x01".join((PIPELINE_ID, *(str(p) for p in parts)))
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:26]}"


@dataclass(frozen=True, slots=True)
class Verdicts:
    """이 셀의 통계 판정 요약. **설명 성격과 확신도를 이것이 정한다** - 모델이 아니다."""

    applied_edges: int = 0          # 오늘 적용된 성립 엣지
    credible: int = 0               # 진단 셋을 통과한 함의
    significant_market: int = 0     # 유의한 시장 사건 시행
    undecided: int = 0              # 판정불가
    route_kind: str = ""            # 시장 | 섹터 | 고유 | 혼합 | 괴리단독
    idio_qualified: bool = True     # 고유 자격 (잔차 공통상관)
    bundles: tuple[str, ...] = ()   # 근거 묶음 id

    @property
    def explanation_type(self) -> str:
        """근거 성격. **어휘를 늘리지 않는다** - CHECK 가 네 값이고 그것으로 충분하다."""
        if self.applied_edges or self.credible or self.significant_market:
            return "EVENT_SUPPORTED"
        if self.route_kind == "혼합":
            return "MIXED"
        if self.route_kind in ("시장", "섹터", "고유", "괴리단독"):
            # 사건이 아무것도 안 섰다 - 가격 회계만 남았다. 그것도 답이다.
            return "PRICE_ONLY"
        return "UNCERTAIN"

    @property
    def confidence_level(self) -> str:
        """확신도. 고유 자격 미달·판정불가 다수는 LOW 로 내린다 - 숨기지 않는다."""
        if not self.idio_qualified:
            return "LOW"
        if self.applied_edges or self.credible:
            return "HIGH" if self.undecided == 0 else "MEDIUM"
        return "LOW" if self.undecided else "MEDIUM"


@dataclass(frozen=True, slots=True)
class Cell:
    """기록 대상 한 셀. 트리거는 둘 중 하나만 - 어느 축에서 왔는지가 계보다."""

    etf_instrument_id: str
    trade_date: str
    honest: str
    headline: str
    verdicts: Verdicts
    price_trigger_id: str = ""
    minute_trigger_id: str = ""
    etf_return: float | None = None
    nav_return: float | None = None
    constituent_return: float | None = None
    premium_return: float | None = None
    reconciliation_error: float | None = None
    advancing: int | None = None
    constituents: int | None = None
    top3_ratio: float | None = None
    route_code: str = ""
    route_reason: str = ""
    event_search: bool = False
    stage: dict = field(default_factory=dict)

    @property
    def trigger_id(self) -> str:
        """계보의 뿌리. **둘 다 없으면 기록하지 않는다** - 트리거 없는 분석은 계보가 없다."""
        return self.price_trigger_id or self.minute_trigger_id


def record(cell: Cell, dsn: str = "") -> tuple[dict[str, str], str]:
    """계보를 적재한다. 반환 (id 들, 사유). 사유가 비면 성공.

    **트리거가 없으면 거부한다.** 트리거 없이 적재하면 남의 계보에 붙거나 고아 행이
    생긴다 - 파이프라인이 이미 같은 규율을 쓴다(트리거 행이 없으면 '평온한 날').
    """
    if not cell.trigger_id:
        return {}, "트리거 id 가 없다 - 계보 없는 분석은 적재하지 않는다"
    dsn = dsn or os.environ.get(DSN_ENV, "")
    if not dsn:
        return {}, f"{DSN_ENV} 없음 - 적재 생략"
    obs_id = stable_id("cob", cell.trigger_id)
    route_id = stable_id("rte", obs_id)
    code, search = route_code_of(cell.route_code)
    v = cell.verdicts
    stage = dict(cell.stage) | {
        # 우리 어휘를 jsonb 에 그대로 남긴다 - 원장 코드로 접힌 정보(시장 vs 섹터)를
        # 되찾을 자리가 필요하다. 코드는 서빙용, jsonb 는 감사용.
        "route": cell.route_code, "route_code": code, "verdicts": {
            "applied_edges": v.applied_edges, "credible": v.credible,
            "significant_market": v.significant_market, "undecided": v.undecided,
            "idio_qualified": v.idio_qualified},
        "evidence_bundles": list(v.bundles)}
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=25) as con, con.cursor() as cur:
            # `bundle_version` 은 `release_bundle` 로 가는 FK 다. 지어낸 기본값을 쓰면
            # 적재가 통째로 실패한다(실측 'statics-1' -> ForeignKeyViolation).
            # 배포는 env 로 준다(Terraform `analysis_release_bundle_version`);
            # 없으면 **원장이 답을 안다** - PUBLISHED 최신을 고른다.
            bundle_version = os.environ.get(BUNDLE_VERSION_ENV) or ""
            if not bundle_version:
                got = cur.execute(
                    "SELECT bundle_version FROM release_bundle"
                    " WHERE status = 'PUBLISHED' ORDER BY bundle_version DESC"
                    " LIMIT 1").fetchone()
                if not got:
                    return {}, "release_bundle 에 PUBLISHED 번들이 없다 - 적재 불가"
                bundle_version = got[0]
            cur.execute(
                "INSERT INTO etf_contribution_observation ("
                " contribution_observation_id, price_movement_trigger_id,"
                " minute_price_trigger_id, etf_return, nav_return,"
                " constituent_contribution_return, premium_discount_contribution_return,"
                " reconciliation_error, advancing_constituent_count,"
                " total_constituent_count, top3_contribution_ratio,"
                " available_at, data_version)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)"
                " ON CONFLICT (contribution_observation_id) DO NOTHING",
                (obs_id, cell.price_trigger_id or None, cell.minute_trigger_id or None,
                 cell.etf_return, cell.nav_return, cell.constituent_return,
                 cell.premium_return, cell.reconciliation_error, cell.advancing,
                 cell.constituents, cell.top3_ratio, bundle_version))
            cur.execute(
                "INSERT INTO explanation_route (explanation_route_id,"
                " contribution_observation_id, route_code, event_search_required,"
                " decision_reason, evaluated_at)"
                " VALUES (%s,%s,%s,%s,%s,now())"
                " ON CONFLICT (explanation_route_id) DO NOTHING",
                (route_id, obs_id, code, search, cell.route_reason[:2000]))
            run_id = stable_id("run", cell.etf_instrument_id, cell.trade_date, route_id)
            cur.execute(
                "INSERT INTO explanation_run (explanation_run_id, explanation_route_id,"
                " bundle_version, explanation_as_of, run_reason, run_status,"
                " started_at, finished_at)"
                " VALUES (%s,%s,%s,now(),%s,'SUCCEEDED',now(),now())"
                " ON CONFLICT (explanation_run_id) DO NOTHING",
                (run_id, route_id, bundle_version,
                 "MINUTE" if cell.minute_trigger_id else "DAILY"))
            res_id = stable_id("res", run_id)
            cur.execute(
                "INSERT INTO explanation_result (explanation_result_id,"
                " explanation_run_id, etf_instrument_id, trade_date,"
                " explanation_as_of, explanation_type, summary, confidence_level,"
                " stage_results, publication_status, generated_at, headline)"
                " VALUES (%s,%s,%s,%s,now(),%s,%s,%s,%s::jsonb,'DRAFT',now(),%s)"
                " ON CONFLICT (explanation_result_id) DO NOTHING",
                (res_id, run_id, cell.etf_instrument_id, cell.trade_date,
                 v.explanation_type, cell.honest, v.confidence_level,
                 json.dumps(stage, ensure_ascii=False, default=str),
                 cell.headline or None))
            con.commit()
        return {"observation": obs_id, "route": route_id, "run": run_id,
                "result": res_id}, ""
    except Exception as e:                  # noqa: BLE001 - 실패를 삼키지 않는다
        return {}, f"{type(e).__name__}: {str(e)[:120]}"


def open_minute_triggers(lake_dsn: str = "", *, limit: int = 20) -> list[dict]:
    """**아직 분석되지 않은** 분봉 트리거. 실시간 축의 작업 목록이다.

    '분석됐다' 의 정의는 계보다: `etf_contribution_observation.minute_price_trigger_id`
    에 그 트리거가 붙어 있으면 끝난 것이다. 별 상태 컬럼을 두지 않는다 - 상태를
    두면 그것과 계보가 갈라진다(원장이 답을 알아야 한다).
    """
    dsn = lake_dsn or os.environ.get(DSN_ENV, "")
    if not dsn:
        return []
    import psycopg
    with psycopg.connect(dsn, connect_timeout=25) as con:
        rows = con.execute(
            "SELECT t.trigger_id, t.entity_id, t.window_start, t.change_rate,"
            "       t.threshold, t.detection_policy_version"
            " FROM minute_price_trigger t"
            " LEFT JOIN etf_contribution_observation o"
            "        ON o.minute_price_trigger_id = t.trigger_id"
            " WHERE o.contribution_observation_id IS NULL"
            " ORDER BY t.window_start DESC LIMIT %s", (limit,)).fetchall()
    return [{"trigger_id": r[0], "entity_id": r[1], "window_start": r[2],
             "change_rate": float(r[3]), "threshold": float(r[4]),
             "policy": r[5]} for r in rows]


def _selfcheck() -> None:
    assert stable_id("cob", "pmt_X").startswith("cob_")
    assert len(stable_id("cob", "pmt_X")) == 30
    assert stable_id("cob", "pmt_X") == stable_id("cob", "pmt_X")   # 결정적
    assert stable_id("cob", "pmt_X") != stable_id("cob", "pmt_Y")

    # 판정 → 어휘. **어휘를 늘리지 않는다**
    assert Verdicts(applied_edges=1, route_kind="고유").explanation_type == "EVENT_SUPPORTED"
    assert Verdicts(credible=2, route_kind="시장").explanation_type == "EVENT_SUPPORTED"
    assert Verdicts(significant_market=1, route_kind="시장").explanation_type == "EVENT_SUPPORTED"
    assert Verdicts(route_kind="시장").explanation_type == "PRICE_ONLY"
    assert Verdicts(route_kind="혼합").explanation_type == "MIXED"
    assert Verdicts().explanation_type == "UNCERTAIN"
    assert all(Verdicts(route_kind=k).explanation_type in TYPES
               for k in ("시장", "섹터", "고유", "혼합", "괴리단독", ""))

    # 고유 자격 미달은 무조건 LOW - 숨기지 않는다
    assert Verdicts(applied_edges=3, idio_qualified=False).confidence_level == "LOW"
    assert Verdicts(applied_edges=1).confidence_level == "HIGH"
    assert Verdicts(applied_edges=1, undecided=2).confidence_level == "MEDIUM"
    assert Verdicts(undecided=4).confidence_level == "LOW"
    assert all(Verdicts(applied_edges=n, undecided=m).confidence_level in LEVELS
               for n in (0, 1) for m in (0, 3))

    # 라우팅 매핑은 CHECK 두 개를 동시에 만족해야 한다
    for k, want in (("시장", "COMMON_FACTOR"), ("고유", "CONCENTRATED"),
                    ("혼합", "FALLBACK_2LEG"), ("괴리단독", "FLOW_DOMINATED"),
                    ("", "PRICE_ONLY")):
        c, sch = route_code_of(k)
        assert c == want, (k, c)
        assert sch == (c in EVENT_SEARCH)

    # 트리거 없는 분석은 적재 거부
    ids, why = record(Cell(etf_instrument_id="i", trade_date="2026-07-31",
                           honest="h", headline="p", verdicts=Verdicts()))
    assert not ids and "트리거" in why
    print("ok")


if __name__ == "__main__":
    _selfcheck()
