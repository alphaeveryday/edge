"""설명 조립 산출 → 근거 행(v3) 결정론 유도 + 빌드 게이트(§5) (ALPHA-888).

입력은 이미 만들어진 설명 조립 산출물이다 — `final_explanation.blocks`(고객 노출
문장), `window.lineage`(무엇을 읽었나), `stat_tests` 버퍼(ALPHA-876, 전 판정 원값),
요청창 사건 목록. 여기서 6유형 근거 행을 **결정론으로** 유도한다: 같은 입력이면
같은 행, 같은 ref. 손으로 적는 칸은 없다.

두 규율이 이 모듈의 전부다.

1. **통과한 검정만 행이 된다**(§0). `verdict == 성립` + `applies_today` +
   `null_kind ∈ {label, pair}` + (계열 방아쇠면) `trigger_fired is True` 를 전부
   통과한 검정만 STAT_TEST 행으로 승격한다. §5 가 지목한 구멍 — `trigger_fired`
   가 `None`(미계측)인 계열 방아쇠가 `is not False` 를 타는 경로 — 을 여기서
   명시적 `is True` 로 닫는다. 떨어진 검정은 `skipped` 에 사유째 남는다(Rule 12).
2. **근거 0인 문장은 빌드 실패다**(§0·§6-d). 부재 고지(N 블록)를 뺀 모든 고객
   노출 블록에 근거 행이 1개 이상 서야 한다 — 못 세우면 `EvidenceFormatError`
   로 죽는다. 조용한 통과는 없다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .evidence_card import (
    EvidenceFormatError,
    EvidenceRow,
    StatTestRecord,
    TYPE_ORDER,
    holding_row,
    news_row,
    price_row,
    series_name,
)

# vocab.LAYERS(검정기의 층 문자열) ↔ basis 코드(§3.2). evidence_adapt.LAYER_TO_BASIS
# 와 같은 값이어야 한다 — 어댑터를 import 하면 순환이 없으므로 그대로 쓴다.
from .evidence_adapt import LAYER_TO_BASIS

# (노출 계열족, 변환) → §3.7 series 한 칸. paneltest.FEATURES 중 §3.7 대상 어휘
# (원/달러·국고10년·KOSPI200·KRX {업종})로 옮길 수 있는 조합만 연다 — 여기 없는
# 노출의 검정은 series 를 조립할 수 없어 행이 되지 못하고 `skipped` 로 남는다.
# 어휘 확장은 §3.7 대상 어휘와 함께 사람이 한다(§9 미결).
_EXPOSURE_SERIES = {
    ("거시", "민감도"): ("원/달러", "일봉", "변화"),
    ("금리", "민감도"): ("국고10년", "일봉", "변화"),
    ("지수잔차", "민감도"): ("KOSPI200", "일봉", "수익률"),
    # ("섹터", "민감도") 는 업종 이름이 있어야 한다 — _stat_records 가 sector_name
    # 으로 조립한다.
}

# 계열 방아쇠의 카드 표기(TUPLE_PANEL 의 {trigger} 슬롯). 채널이 무엇에 민감한가를
# 이미 말하므로, 거시 방아쇠는 채널로 갈라 §3.7 대상 어휘의 이름을 쓴다(케이스 B:
# "원/달러가 크게 움직인 날"). 여기 없는 (방아쇠, 채널) 조합은 렌더할 이름이 없어
# 행이 되지 못한다.
_TRIGGER_LABEL = {
    ("거시", "FX환"): "원/달러",
    ("거시", "R금리신용"): "국고10년",
}

_ALL_STOCKS_SERIES = "전 종목 일봉 수익률"

# channel 코드 → 카드 이름. 렌더 계층(evidence_render.CHANNEL_LABEL)과 같은 표를
# 빌드에서 쓰는 이유: ALPHA-880 어댑터 관례가 슬롯에 카드 이름을 담는다
# (from_sensitive_stocks 의 channel_label). §3.5 의 "코드 저장" 과 갈리는 지점이라
# 보고서 미결 목록에 올린다.
from .evidence_render import CHANNEL_LABEL as _CHANNEL_LABEL


@dataclass(frozen=True)
class EvidenceBuild:
    """유도 결과 — 정렬·채번 완료된 행 + 블록↔ref 표 + 탈락 검정 사유(감사)."""

    rows: tuple[EvidenceRow, ...]
    stat_records: dict[int, StatTestRecord]     # ref → 원 레코드 (영속용, §3.1)
    block_refs: dict[str, tuple[int, ...]]      # block_code → refs (§7 표)
    skipped: tuple[dict, ...]                   # 원 검정 + 행이 못 된 사유 (감사 손실 방지)


def _mmdd(day: str) -> str:
    """YYYY-MM-DD → MM-DD (§4 시각 형식의 날짜부)."""
    return day[5:] if len(day) == 10 else day


def _clock(day: str, hhmm: str) -> str:
    """플랫 행의 시각 줄 — `MM-DD HH:mm`(§4)."""
    return f"{_mmdd(day)} {hhmm}" if hhmm else _mmdd(day)


def _event_time(available_at: str, day: str) -> str:
    """사건 available_at(ISO)을 §4 시각으로. 파싱 불가면 날짜만 남긴다."""
    text = str(available_at or "")
    if len(text) >= 16 and text[4] == "-" and text[7] == "-":
        return f"{text[5:10]} {text[11:16]}"
    return _mmdd(day)


def _gate_reason(rec: dict) -> str | None:
    """§5 게이트 — 통과하지 못한 사유, 통과면 None.

    `applies_today` 는 검정기가 이미 접은 값이지만(§5 주석의 6개 선결조건), 계열
    방아쇠의 `trigger_fired is None`(미계측) 구멍이 그 안에 있다 — 여기서 `is True`
    로 명시 확인한다. `null_kind == "date"` 도 같은 방식으로 재확인한다(순환 귀무).
    """
    if rec.get("stage") != "test":
        return f"검정이 아닌 버퍼 레코드({rec.get('stage')!r})"
    if rec.get("verdict") != "성립":
        return f"불성립·판정불가는 근거가 아니다(verdict={rec.get('verdict')!r})"
    if not rec.get("applies_today"):
        return f"오늘 적용 요건 미충족: {rec.get('reason') or '사유 미기록'}"
    null_kind = rec.get("null_kind", "label")
    if null_kind not in ("label", "pair"):
        return f"date 귀무는 순환이라 귀속 자격이 없다(null_kind={null_kind!r})"
    if rec.get("trigger_kind") == "계열" and rec.get("trigger_fired") is not True:
        # §5 의 구멍: 미계측(None)이 `is not False` 를 타던 경로 — 발화를 명시 확인.
        return "계열 방아쇠의 오늘 발화가 명시 확인되지 않았다(§5 구멍 차단)"
    return None


def _stat_records(stat_tests: list[dict] | tuple, sector_name: str | None,
                  ) -> tuple[list[StatTestRecord], list[dict]]:
    """stat_tests 버퍼(etfcell) → 통과분의 `StatTestRecord` + 탈락 사유 목록.

    현재 버퍼는 `paneltest.edge_tests` 산출(SENSITIVE_STOCKS/TUPLE_PANEL)만 담는다.
    k(같이 검정한 가설 수, §3.4 보정)는 이 카드에서 **검정까지 간** 가설 수다 —
    제안·기각은 검정이 아니므로 세지 않는다.
    """
    tested = [r for r in stat_tests if r.get("stage") == "test"]
    k = max(1, len(tested))
    records: list[StatTestRecord] = []
    skipped: list[dict] = []
    for rec in stat_tests:
        ident = f"{rec.get('trigger')}×{rec.get('channel')}·{rec.get('exposure')}"
        why = _gate_reason(rec)
        if why is not None:
            skipped.append({"record": dict(rec), "reason": f"{ident}: {why}"})
            continue
        trigger_label = _TRIGGER_LABEL.get((str(rec["trigger"]), str(rec["channel"])))
        if trigger_label is None:
            skipped.append({"record": dict(rec),
                            "reason": f"{ident}: 방아쇠 카드 표기 미배선 — §3.7 대상 어휘 밖"})
            continue
        channel_label = _CHANNEL_LABEL.get(str(rec["channel"]))
        if channel_label is None:
            skipped.append({"record": dict(rec),
                            "reason": f"{ident}: 채널 카드 이름 미배선(vocab.CHANNELS 밖)"})
            continue
        exposure = str(rec.get("exposure") or "")
        family, _, transform = exposure.partition("/")
        if (family, transform) in _EXPOSURE_SERIES:
            target, freq, measure = _EXPOSURE_SERIES[(family, transform)]
        elif (family, transform) == ("섹터", "민감도") and sector_name:
            target, freq, measure = f"KRX {sector_name}", "일봉", "수익률"
        else:
            skipped.append({"record": dict(rec),
                            "reason": f"{ident}: 노출 계열의 series 이름 미배선 — §3.7 대상 어휘 밖"})
            continue
        effect_high = rec.get("effect_high")
        effect_low = rec.get("effect_low")
        if rec.get("p") is None or rec.get("n") is None or effect_high is None \
                or effect_low is None:
            skipped.append({"record": dict(rec),
                            "reason": f"{ident}: n·p·effect 없이 검정 행을 만들 수 없다"})
            continue
        records.append(StatTestRecord(
            ref=0,      # 채번은 build_evidence_rows 정렬 후에 한다
            template="TUPLE_PANEL",
            basis=LAYER_TO_BASIS.get(str(rec.get("layer")), "IDIO"),
            # channel 은 어휘 코드(FX환 등)로 오지만 슬롯에는 카드 이름을 담는다 —
            # ALPHA-880 어댑터(from_sensitive_stocks 의 channel_label)와 같은 관례다.
            slots={"trigger": trigger_label, "channel": channel_label},
            method="SENSITIVE_STOCKS",
            n=int(rec["n"]), unit="COUNT",
            estimate=float(effect_high) - float(effect_low),
            p=float(rec["p"]),
            series=(_ALL_STOCKS_SERIES,
                    series_name(target, freq, measure)),
            k=k,
            null_kind=str(rec.get("null_kind", "label")),
        ))
    return records, skipped


def build_evidence_rows(*, blocks: list[dict], lineage: list[dict] | tuple,
                        stat_tests: list[dict] | tuple, events: list[dict],
                        ticker: str, etf_name: str, day: str, window_end: str,
                        sector_name: str | None = None) -> EvidenceBuild:
    """설명 조립 산출에서 근거 행을 유도하고 §5 게이트를 세운다.

    블록별 유도 규칙(§7 의 블록↔근거 표를 엔진 산출로 재현):
      H(헤더)·2(시간 구간)  ETF 5분봉 가격 행 — lineage 의 bars_5m
      1(기여 분해)          구성종목 가격 행 + 구성비중(HOLDING) 행
      3(요인 분해)          ETF 가격 행 + 층 분해 계열 행 (+ 몫을 설명하는 검정 행,
                            §7 "상대적 비교는 몫의 설명" — SENSITIVE_STOCKS 가 붙는다)
      4(이벤트 병치)        사건 문서(NEWS) 행 + 나머지 통과 검정 행
      N(부재 고지)          행 없음 — §7 "부재 문구는 게이트의 예외"

    반환 행은 §1 정렬(유형 순서 고정 → ref 오름차순) 완료 상태고, ref 는 1부터의
    정수다 — 문자열 ref 는 사전순이 e_9 > e_10 을 내므로 두지 않는다(§1).
    """
    if not blocks:
        raise EvidenceFormatError("고객 노출 블록이 없다 — 근거를 유도할 문장이 없다")
    codes = [str(b.get("block_code")) for b in blocks]
    lineage_views = {str(entry.get("view")) for entry in lineage}

    when = _clock(day, window_end)
    # key → (row 재료). 채번 전 단계 — 정렬 뒤 ref 를 붙인다. lineage 가 없으면
    # 가격 행도 없다 — 그러면 아래 게이트가 헤더·구간 문장에서 스스로 죽는다(§5).
    specs: dict[str, EvidenceRow] = {}
    if "bars_5m" in lineage_views:
        specs["price_etf"] = price_row(
            0, dataset=f"{etf_name} 5분봉", vendor="S3.bars_5m", as_of=when)

    constituents = sorted({
        ref.removeprefix("bars_5m:")
        for b in blocks if str(b.get("block_code")) == "1"
        for ref in (b.get("evidence_refs") or ())
        if ref.startswith("bars_5m:") and ref.removeprefix("bars_5m:") != ticker
    })
    if constituents:
        specs["price_members"] = price_row(
            0, dataset=f"구성종목 5분봉({len(constituents)}종목)",
            vendor="S3.bars_5m", as_of=when)
    if "1" in codes:
        # 구성비중이 있어야 기여 분해가 선다 — 기준일은 T-1 확정 비중이지만 엔진
        # 산출에는 스냅샷 기준일이 없어 설명일을 적는다(§9 미결로 보고).
        specs["holding"] = holding_row(
            0, as_of_basis=_mmdd(day), vendor="RDB.etf_holding_snapshot", as_of=when)
    if "layers" in lineage_views:
        # [3] 요인 분해의 근거 — 층 몫(시장·업종·개별)을 낸 계열. 상품·지수명은
        # 산문 금지(ALPHA-871)라 계열의 역할명으로 적는다. 코드가 만든 고정
        # 문자열이라 자유 텍스트가 아니다.
        specs["price_layers"] = price_row(
            0, dataset="층 분해 계열(시장·업종)", vendor="S3.layers_daily", as_of=when)

    events_by_id = {str(e.get("source_event_id")): e for e in events}
    for eid in sorted(events_by_id):
        e = events_by_id[eid]
        specs[f"news:{eid}"] = news_row(
            0, title=str(e.get("title") or eid),
            # 언론사가 엔진 산출에 없다 — 문서는 id 로 저장하고 렌더가 해소한다(§2).
            publisher=f"RDB.source_event · {eid}",
            published_at=_event_time(str(e.get("available_at") or ""), day))

    stat_recs, skipped = _stat_records(list(stat_tests), sector_name)

    # ── 채번: 유형 순서 고정 → 유형 안에서는 유도 순서(§1) ─────────────────
    order = {t: i for i, t in enumerate(TYPE_ORDER)}
    keys = sorted(specs, key=lambda key: (order[specs[key].type],
                                          list(specs).index(key)))
    refs: dict[str, int] = {}
    rows: list[EvidenceRow] = []
    for i, key in enumerate(keys, start=1):
        row = specs[key]
        refs[key] = i
        rows.append(EvidenceRow(i, row.type, content=row.content,
                                source=row.source, time=row.time))
    stat_records: dict[int, StatTestRecord] = {}
    for rec in stat_recs:
        ref = len(rows) + 1
        numbered = StatTestRecord(
            ref=ref, template=rec.template, basis=rec.basis, slots=rec.slots,
            method=rec.method, n=rec.n, unit=rec.unit, estimate=rec.estimate,
            p=rec.p, series=rec.series, k=rec.k, band=rec.band,
            null_kind=rec.null_kind)
        stat_records[ref] = numbered
        rows.append(numbered.to_row())

    # ── 블록 ↔ ref (§7 표) ────────────────────────────────────────────────
    def _refs(*keys_: str) -> tuple[int, ...]:
        return tuple(refs[key] for key in keys_ if key in refs)

    sector_stat = tuple(ref for ref, rec in stat_records.items()
                        if rec.method == "SENSITIVE_STOCKS")
    other_stat = tuple(ref for ref in stat_records if ref not in sector_stat)
    all_stat = tuple(stat_records)
    news_keys = tuple(key for key in refs if key.startswith("news:"))
    block_refs: dict[str, tuple[int, ...]] = {}
    for b in blocks:
        code = str(b.get("block_code"))
        if code == "H" or code == "2":
            block_refs[code] = _refs("price_etf")
        elif code == "1":
            block_refs[code] = _refs("holding", "price_members") or _refs("price_etf")
        elif code == "3":
            # 검정은 몫의 설명이라 [3]에 붙는다(§7 케이스 B — 사건 병치가 아니다).
            block_refs[code] = _refs("price_etf", "price_layers") + sector_stat
        elif code == "4":
            required = str(b.get("evidence_requirement") or "")
            block_refs[code] = _refs(*news_keys) + (
                all_stat if required == "CAUSAL_STAT_TEST" else other_stat)
        elif code == "N":
            block_refs[code] = ()       # 부재 고지 — 게이트 예외(§7)
        else:
            block_refs[code] = ()

    # ── 빌드 게이트(§0·§6-d): 근거 0인 고객 노출 문장은 빌드 실패 ──────────
    for b in blocks:
        code = str(b.get("block_code"))
        if code == "N":
            continue
        if b.get("evidence_requirement") == "CAUSAL_STAT_TEST" and not any(
                ref in stat_records for ref in block_refs.get(code, ())):
            raise EvidenceFormatError(
                f"블록 [{code}] CAUSAL_STAT_TEST 요구를 적격 STAT_TEST 행이 만족하지 못했다")
        if not block_refs.get(code):
            raise EvidenceFormatError(
                f"근거 0인 문장 — 블록 [{code}] {b.get('block_title')!r} 에 근거 행을 "
                f"유도하지 못했다. 문장을 만들지 않는다(§5)")

    return EvidenceBuild(rows=tuple(rows), stat_records=stat_records,
                         block_refs=block_refs, skipped=tuple(skipped))


__all__ = ["EvidenceBuild", "build_evidence_rows"]
