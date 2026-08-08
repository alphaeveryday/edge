"""엔진 산출 → `evidence_card.StatTestRecord` 어댑터.

`trial.py`·`mkttrial.py`·`paneltest.py`·`tool_baserate.py`·`lasso.py` 는 근거
포맷(v3)을 모르는 채로 이미 존재하는 검정기다 - 여기서 그 산출(dict·`EdgeReport`)을
읽어 §3.1 레코드로 옮긴다. **이 파일이 스펙과 코드 사이의 유일한 접합부다** - 필드
이름이 여기서만 갈리면 나머지는 전부 `evidence_card` 의 닫힌 어휘가 잡아준다.

방법마다 "계산됨/성립" 이 아니면 레코드를 만들지 않는다(§0 "통과 못 한 검정은
근거도 없다") - 그 판단은 `EvidenceFormatError` 로 여기서 먼저 막는다.
"""
from __future__ import annotations

from .evidence_card import EvidenceFormatError, NotRenderable, StatTestRecord, band_of

# vocab.py 의 LAYERS(§3.2 basis 의 실제 발생원 - edge_test 등에 넘기는 layer 문자열)와
# 근거 포맷의 basis 코드는 1:1 이다. 여기서만 잇는다 - 두 어휘가 따로 진화하면
# 어느 한쪽이 새 값을 추가할 때 이 매핑이 조용히 깨진다(그래서 KeyError 로 죽는다).
LAYER_TO_BASIS = {"시장": "MARKET", "섹터": "SECTOR", "고유": "IDIO"}


def _basis_of(layer: str) -> str:
    try:
        return LAYER_TO_BASIS[layer]
    except KeyError:
        raise EvidenceFormatError(f"layer={layer!r} 는 근거 basis 로 못 옮긴다 - "
                                  f"{sorted(LAYER_TO_BASIS)} 중 하나여야 한다") from None


def from_similar_stocks(result: dict, *, ref: int, event_title: str,
                        target_series: str, layer: str = "고유",
                        control_series: str = "전 종목 일봉 수익률",
                        k: int = 1) -> StatTestRecord:
    """`trial.run_trial`/`run_spillover` 산출 → `MATCHED_ATT`(§3.5).

    실제 반환 키는 `att`(estimate)·`p`·`pairs`(n, COUNT) 다 - §3.1 예시의 `n=41` 이
    가리키는 것이 정확히 `pairs` 다(§7 케이스 A 주석: "41종목이... n 필드가 말한다").
    `verdict != "계산됨"` 이면 근거가 아니라 판정불가이므로 여기서 막는다.
    """
    if result.get("verdict") != "계산됨":
        raise EvidenceFormatError(
            f"계산되지 않은 시행에서는 근거를 만들지 않는다: {result.get('reason', '사유 없음')}")
    if not result.get("balanced", True):
        raise EvidenceFormatError(
            "매칭 불균형(SMD 초과) 시행은 근거로 승격하지 않는다(§5 SIMILAR_STOCKS 게이트 1/3)")
    pretrend_ps = [v[1] for v in (result.get("lead") or {}).values() if v]
    if pretrend_ps and min(pretrend_ps) <= 0.05:
        raise EvidenceFormatError(
            "사전추세 위약이 유의하다(예고·유출 의심) - §5 SIMILAR_STOCKS 게이트 2/3")
    # §5 세 번째 조건 `novelty_placebo_p`(재보도 위약)는 trial.py 어디에도 소스가
    # 없다(2026-08-08 실측, novelty 위약은 mkttrial 쪽에만 있다) - 없는 값을 통과시킨
    # 척 지어내지 않는다. 이 검사는 **미실행**이고, 그 사실 자체가 §9 M-3 류의
    # 미결 사항이다 - 엔진에 그 위약이 배선되면 여기 세 번째 raise 를 추가한다.
    return StatTestRecord(
        ref=ref, template="MATCHED_ATT", basis=_basis_of(layer),
        slots={"event_title": event_title}, method="SIMILAR_STOCKS",
        n=int(result["pairs"]), unit="COUNT", estimate=float(result["att"]),
        p=float(result["p"]), series=(target_series, control_series), k=k)


def from_similar_days(result: dict, *, ref: int, etype_label: str,
                      index_series: str = "KOSPI200 일봉 수익률",
                      layer: str = "시장", k: int = 1) -> StatTestRecord:
    """`mkttrial.run_market_trial` 산출 → `MARKET_EVENT`(§3.5).

    `n_days`(DAY 단위) 를 쓴다 - `pairs` 는 소모된 대조군 슬롯 총합이라 §3.1 의
    "표본 과거 N일" 의미와 다르다(케이스 C: "unit 이 DAY 다... 대조가 날짜로 바뀐다").
    """
    if result.get("verdict") != "계산됨":
        raise EvidenceFormatError(
            f"계산되지 않은 시행에서는 근거를 만들지 않는다: {result.get('reason', '사유 없음')}")
    return StatTestRecord(
        ref=ref, template="MARKET_EVENT", basis=_basis_of(layer),
        slots={"etype": etype_label}, method="SIMILAR_DAYS",
        n=int(result["n_days"]), unit="DAY", estimate=float(result["att"]),
        p=float(result["p"]), series=(index_series,), k=k)


def from_sensitive_stocks(report, *, ref: int, trigger_label: str, channel_label: str,
                          base_series: str, sensitivity_series: str,
                          layer: str = "섹터", k: int = 1) -> StatTestRecord:
    """`paneltest.edge_test` 의 `EdgeReport` → `TUPLE_PANEL`(§3.5).

    `EdgeReport` 는 검정 자체의 대칭차(`effect_high - effect_low`)만 주고 표본
    분할 크기는 안 주므로 `n` 은 `report.n`(패널 전체 표본)을 쓴다 - `SIMILAR_STOCKS`
    의 `n=pairs` 와 뜻이 다르다는 점을 어댑터 경계에서 명시한다.

    `applies_today` 가 §5 게이트의 6개 선결조건(assignable·null_kind·조건·환원·
    방아쇠 발화)을 이미 접으므로, 이게 거짓이면 근거로 승격하지 않는다.
    """
    if report.verdict != "성립" or not report.applies_today:
        raise EvidenceFormatError(
            f"applies_today 를 통과 못 한 패널검정은 근거가 아니다: "
            f"verdict={report.verdict} reason={report.reason or report.cond_today}")
    if report.p is None or report.n is None:
        raise EvidenceFormatError("p/n 이 없는 EdgeReport 는 근거로 옮길 수 없다")
    estimate = (report.effect_high or 0.0) - (report.effect_low or 0.0)
    return StatTestRecord(
        ref=ref, template="TUPLE_PANEL", basis=_basis_of(layer),
        slots={"trigger": trigger_label, "channel": channel_label},
        method="SENSITIVE_STOCKS", n=int(report.n), unit="COUNT",
        estimate=float(estimate), p=float(report.p),
        series=(base_series, sensitivity_series), k=k)


def from_vs_usual(result: dict, *, ref: int, event_title: str, target_series: str,
                  layer: str = "고유", k: int = 1) -> StatTestRecord:
    """`tool_baserate._base_rate` 산출 → `EVENT_TAIL`(§3.5).

    band 는 여기서 처음 생긴다(§9-M3) - `pct_rank` 를 `band_of` 로 접는다. `estimate`
    는 이 검정엔 "평균 차이" 개념이 없어 오늘 값 자체(`today`)를 싣는다 - `p` 대신
    `exceed_p` 를 쓴다(같은 뜻: 양측 초과확률).
    """
    if result.get("verdict") != "계산됨":
        raise EvidenceFormatError(
            f"계산되지 않은 base_rate 에서는 근거를 만들지 않는다: "
            f"{result.get('reason', '사유 없음')}")
    return StatTestRecord(
        ref=ref, template="EVENT_TAIL", basis=_basis_of(layer),
        slots={"event_title": event_title}, method="VS_USUAL",
        n=int(result["n"]), unit="DAY", estimate=float(result["today"]),
        p=float(result["exceed_p"]), series=(target_series,),
        band=band_of(result.get("pct_rank")), k=k)


def from_by_condition(moderation: dict, *, ref: int, event_label: str,
                      target_series: str, condition_series: list[str],
                      layer: str = "고유", k: int = 1) -> StatTestRecord:
    """`lasso.moderate` 산출(`trial.run_trial(..., moderators=...)["moderation"]`)
    → `MODERATION`(§3.5).

    `p_max`(Westfall-Young maxT, 다중조절자 보정 포함)를 쓴다 - `p_step` 개별값이
    아니다. `conditions` 슬롯은 §3.5 "배열이다 - 실제로 영향을 가른 것만" 이므로
    `selected`(post-LASSO 로 살아남은 조절자) 의 이름만 담는다.
    """
    if moderation is None or moderation.get("verdict") != "계산됨":
        why = (moderation or {}).get("reason", "moderation 미계산")
        raise EvidenceFormatError(f"계산되지 않은 조절검정에서는 근거를 만들지 않는다: {why}")
    selected = list(moderation.get("selected") or {})
    if not selected:
        raise EvidenceFormatError("BY_CONDITION 은 실제로 영향을 가른 조건이 있어야 한다"
                                  "(selected 가 비었다) - §3.5 conditions 는 후보 전체가 아니다")
    return StatTestRecord(
        ref=ref, template="MODERATION", basis=_basis_of(layer),
        slots={"event": event_label, "conditions": "·".join(sorted(selected))},
        method="BY_CONDITION", n=int(moderation["n"]), unit="COUNT",
        estimate=float(moderation["att_base"]), p=float(moderation["p_max"]),
        series=(target_series, *condition_series), k=k)


def from_related_stocks(*_a, **_kw) -> StatTestRecord:
    """§9-M1: `paneltest._relation_test` 는 `assignable=False` 를 무조건 반환하므로,
    지금 이 어댑터는 입력이 무엇이든 항상 렌더 불가다. 인자를 받는 이유는 호출부가
    다른 5개 어댑터와 같은 모양으로 시도했다가 여기서 막히는 경로를 그대로 재현하기
    위해서다."""
    raise NotRenderable(
        "RELATED_STOCKS 는 _relation_test 가 assignable=False 를 강제해 "
        "현재 렌더 불가다(§3.3·§9-M1) - 소스-타깃 창 정렬(5분봉)이 붙어야 열린다")


__all__ = ["LAYER_TO_BASIS", "from_by_condition", "from_related_stocks",
           "from_sensitive_stocks", "from_similar_days", "from_similar_stocks",
           "from_vs_usual"]
