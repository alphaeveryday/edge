"""근거 행 유도·게이트·렌더 — 스펙 케이스 A~D(§7)를 엔진 산출 모양으로 재현.

이 테스트가 지키는 의도(Rule 9):
  · 통과한 검정만 행이 된다(§0) — 비유의·부적용·date 귀무·미발화 계열 방아쇠가
    행을 만들면 깨진다. 특히 §5 가 지목한 구멍(trigger_fired=None 통과)을 검증한다.
  · 근거 0인 고객 노출 문장은 빌드 실패다(§0·§6-d) — 게이트가 실제로 죽는지 본다.
  · ref 는 정수이고 유형 순서 → ref 오름차순이다(§1) — 문자열 정렬(e_9 > e_10)이면
    깨진다.
  · 렌더는 결정론이다(§0) — 같은 입력 2회 = 동일 바이트.
  · 카드 금지 어휘(귀무·ATT·노출·고유층)가 렌더에 나오면 깨진다(스펙 독자 기준).
"""
from __future__ import annotations

import pytest

from edge_analysis.statics.evidence_card import (
    EvidenceFormatError,
    NotRenderable,
    StatTestRecord,
)
from edge_analysis.statics.evidence_render import render_row, render_rows, render_stat_test
from edge_analysis.statics.evidence_rows import build_evidence_rows

DAY = "2026-08-07"
TICKER = "069500"
NAME = "KODEX 반도체"

_LINEAGE = (
    {"view": "bars_5m", "entity": TICKER, "as_of": "13:20"},
    {"view": "layers", "entity": TICKER, "as_of": "13:20"},
)


def _blocks(*, tail: dict | None = None) -> list[dict]:
    """엔진 final_explanation_payload 의 고정 블록 H·1·2·3 + 조건부 4/N."""
    blocks = [
        {"block_code": "H", "block_title": "헤더",
         "evidence_refs": [f"bars_5m:{TICKER}"]},
        {"block_code": "1", "block_title": "기여 분해",
         "evidence_refs": ["bars_5m:000660", "bars_5m:005930"]},
        {"block_code": "2", "block_title": "시간 구간",
         "evidence_refs": [f"bars_5m:{TICKER}"]},
        {"block_code": "3", "block_title": "움직임 분해", "evidence_refs": []},
    ]
    if tail is not None:
        blocks.append(tail)
    return blocks


_EVENT_BLOCK = {"block_code": "4", "block_title": "이벤트 병치",
                "evidence_refs": ["source_event:ev_001"]}
_ABSENT_BLOCK = {"block_code": "N", "block_title": "부재 고지", "evidence_refs": []}

_EVENTS = [{"source_event_id": "ev_001", "title": "SK하이닉스 공급계약 해지",
            "available_at": "2026-08-07T10:31:00+09:00"}]

# 케이스 B 의 환율 검정(§7) — 게이트 전 조건 통과.
_FX_PASS = {
    "stage": "test", "trigger": "거시", "trigger_kind": "계열", "trigger_fired": True,
    "null_kind": "pair", "channel": "FX환", "exposure": "거시/민감도", "layer": "섹터",
    "verdict": "성립", "applies_today": True, "n": 412, "p": 0.0121,
    "effect_low": 0.0048, "effect_high": -0.0045, "reason": "",
}


def _build(blocks, *, stat_tests=(), events=(), sector_name="반도체"):
    return build_evidence_rows(
        blocks=blocks, lineage=_LINEAGE, stat_tests=list(stat_tests),
        events=list(events), ticker=TICKER, etf_name=NAME, day=DAY,
        window_end="13:20", sector_name=sector_name)


class TestCaseA_공시가끌었다:
    """§7 케이스 A — 사건 문서 + 통과 검정. 플랫과 검정이 둘 다 남는다(§6-b)."""

    def _out(self):
        return _build(_blocks(tail=_EVENT_BLOCK),
                      stat_tests=[_FX_PASS], events=_EVENTS)

    def test_row_types_and_integer_ref_order(self):
        out = self._out()
        assert [(r.ref, r.type) for r in out.rows] == [
            (1, "PRICE"), (2, "PRICE"), (3, "PRICE"),
            (4, "HOLDING"), (5, "NEWS"), (6, "STAT_TEST")]

    def test_block_ref_table(self):
        """§7 의 블록↔근거 표 — 검정은 몫의 설명이라 [3]에 붙는다."""
        out = self._out()
        assert out.block_refs == {
            "H": (1,), "1": (4, 2), "2": (1,),
            "3": (1, 3, 6), "4": (5,)}

    def test_flat_and_stat_rows_both_survive(self):
        """§6-b: 검정이 가격 데이터를 썼다는 이유로 플랫 가격 행을 지우지 않는다."""
        out = self._out()
        assert any(r.type == "PRICE" for r in out.rows)
        assert any(r.type == "STAT_TEST" for r in out.rows)

    def test_render_exact_bytes(self):
        out = self._out()
        pad = " " * 15
        assert render_rows(out.rows) == "\n".join([
            "[1] 가격   KODEX 반도체 5분봉",
            pad + "S3.bars_5m / 08-07 13:20",
            "[2] 가격   구성종목 5분봉(2종목)",
            pad + "S3.bars_5m / 08-07 13:20",
            "[3] 가격   층 분해 계열(시장·업종)",
            pad + "S3.layers_daily / 08-07 13:20",
            "[4] 구성종목   PDF 구성비중 · 08-07",
            pad + "RDB.etf_holding_snapshot / 08-07 13:20",
            "[5] 뉴스   「SK하이닉스 공급계약 해지」",
            pad + "RDB.source_event · ev_001 / 08-07 10:31",
            "[6] 통계검정   원/달러가 크게 움직인 날, 환율에 민감한 종목이 더 움직였나",
            pad + "전 종목 일봉 수익률 · 원/달러 일봉 변화",
            pad + "├ 기준     업종 전체 움직임",
            pad + "├ 방법     민감한종목비교",
            pad + "├ 표본     과거 412건",
            pad + "├ 차이     평균 -0.93%p",
            pad + "└ 유의확률   p=0.0121",
        ])

    def test_render_is_deterministic_same_bytes_twice(self):
        a = render_rows(self._out().rows)
        b = render_rows(self._out().rows)
        assert a == b and isinstance(a, str)

    def test_stat_record_persisted_fields_not_rendered_sentence(self):
        """§0 완성 문장 저장 금지 — 영속 레코드는 template+slots 다."""
        out = self._out()
        rec = out.stat_records[6]
        assert rec.template == "TUPLE_PANEL"
        assert rec.slots == {"trigger": "원/달러", "channel": "환율"}
        assert rec.basis == "SECTOR" and rec.method == "SENSITIVE_STOCKS"
        assert rec.n == 412 and rec.unit == "COUNT"
        assert rec.p == pytest.approx(0.0121)
        assert rec.estimate == pytest.approx(-0.0093)

    def test_forbidden_card_vocabulary_never_renders(self):
        text = render_rows(self._out().rows)
        for word in ("귀무", "ATT", "노출", "고유층"):
            assert word not in text, f"카드 금지 어휘 {word!r} 가 렌더에 나왔다"


class TestCaseB_이벤트없는날:
    """§7 케이스 B — 부재 고지 블록이 서고, 환율 검정은 [3]에 붙는다."""

    def _out(self):
        return _build(_blocks(tail=_ABSENT_BLOCK), stat_tests=[_FX_PASS])

    def test_absent_block_has_no_rows_and_passes_gate(self):
        out = self._out()
        assert out.block_refs["N"] == ()        # §7: 부재 문구는 게이트의 예외
        assert not any(r.type == "NEWS" for r in out.rows)

    def test_stat_row_attaches_to_relative_block(self):
        out = self._out()
        stat_ref = next(r.ref for r in out.rows if r.type == "STAT_TEST")
        assert stat_ref in out.block_refs["3"]

    def test_single_hypothesis_omits_correction_fragment(self):
        """§3.4: k=1 이면 `· 가설 N건 보정` 조각을 생략한다."""
        text = render_rows(self._out().rows)
        assert "가설" not in text
        assert "p=0.0121" in text

    def test_no_band_omits_position_line(self):
        """§3.6: 놓을 분포가 없으면 위치 줄 자체가 생략된다."""
        assert "위치" not in render_rows(self._out().rows)


class TestCaseC_시장이끌고간날:
    """§7 케이스 C 의 렌더 축 — 문서(뉴스) 행 + 검정 없음. 검정(SIMILAR_DAYS)은
    엔진 요청창 경로가 아직 못 만든다(§9 보고) — 템플릿 렌더는 아래 커버리지가 본다."""

    def test_news_only_event_block(self):
        news = [{"source_event_id": "ev_rate", "title": "한국은행, 기준금리 0.25%p 인상",
                 "available_at": "2026-08-07T09:58:00+09:00"}]
        out = _build(_blocks(tail={
            "block_code": "4", "block_title": "이벤트 병치",
            "evidence_refs": ["source_event:ev_rate"]}), events=news)
        assert out.block_refs["4"] == (5,)
        assert not any(r.type == "STAT_TEST" for r in out.rows)

    def test_market_event_template_renders_with_day_unit(self):
        rec = StatTestRecord(
            ref=8, template="MARKET_EVENT", basis="MARKET",
            slots={"etype": "기준금리 인상"}, method="SIMILAR_DAYS",
            n=14, unit="DAY", estimate=-0.0121, p=0.0308,
            series=("KOSPI200 일봉 수익률",))
        text = render_stat_test(rec)
        assert "기준금리 인상이 있던 날 시장이 평소와 다르게 움직였나" in text
        assert "과거 14일" in text
        assert "평균 -1.21%p" in text
        assert "시장 전체 움직임" in text


class TestCaseD_근거가안생기는경우:
    """§7 케이스 D — 게이트에서 죽으면 검정 행이 없다. 카드에 안 보여도 skipped 에
    사유가 남는다(원장 tuple_registry 대응 축)."""

    @pytest.mark.parametrize("mutation, why", [
        ({"verdict": "불성립"}, "불성립"),
        ({"verdict": "판정불가"}, "판정불가"),
        ({"applies_today": False, "reason": "오늘 조건 미충족"}, "적용"),
        ({"null_kind": "date"}, "date"),
        # §5 의 구멍: 계열 방아쇠 미계측(None)이 `is not False` 를 타던 경로.
        ({"trigger_fired": None}, "발화"),
        # 어휘 미배선 — series 를 §3.7 문법으로 못 만들면 행이 없다.
        ({"trigger": "거래량", "channel": "Q수량"}, "어휘"),
    ])
    def test_failed_tests_produce_no_rows(self, mutation, why):
        rec = {**_FX_PASS, **mutation}
        out = _build(_blocks(tail=_ABSENT_BLOCK), stat_tests=[rec])
        assert not any(r.type == "STAT_TEST" for r in out.rows), \
            f"통과 못 한 검정({why})이 행이 됐다 — §0 위반"
        assert len(out.skipped) == 1
        assert out.block_refs["3"] == (1, 3)    # 층 행만 남는다 — 문장은 산다

    def test_k_counts_all_tested_hypotheses(self):
        """§3.4: k 는 같이 검정한 가설 수다 — 떨어진 가설도 센다."""
        failed = {**_FX_PASS, "verdict": "불성립"}
        out = _build(_blocks(tail=_ABSENT_BLOCK), stat_tests=[_FX_PASS, failed])
        assert "가설 2건 보정" in render_rows(out.rows)


class TestBuildGate:
    """§0·§6-d: 근거 0인 고객 노출 문장은 빌드 실패 — assert 가 실제로 선다."""

    def test_event_block_without_derivable_evidence_dies(self):
        with pytest.raises(EvidenceFormatError, match=r"근거 0인 문장"):
            _build(_blocks(tail=_EVENT_BLOCK))     # 사건도 검정도 없다

    # 옛 CAUSAL_STAT_TEST 요구 분기(뉴스 행 불충분·검정 행 충족)는 생산자 없는
    # 사문이라 분기째 제거됐다(ALPHA-949) — 검정 행의 블록 부착은 케이스 A·
    # test_stat_row_attaches_to_relative_block 이 커버한다.

    def test_missing_bars_lineage_kills_header_sentence(self):
        """lineage 가 비면 가격 행이 없고, 헤더 문장이 근거 0으로 죽는다(§5)."""
        with pytest.raises(EvidenceFormatError, match=r"근거 0인 문장"):
            build_evidence_rows(
                blocks=_blocks(tail=_ABSENT_BLOCK), lineage=(),
                stat_tests=[], events=[], ticker=TICKER, etf_name=NAME,
                day=DAY, window_end="13:20")

    def test_absence_only_card_builds_empty_row_set(self):
        """부재 고지만 있는 카드는 게이트의 예외다(§7) — 행 0개로 성립한다."""
        out = build_evidence_rows(
            blocks=[dict(_ABSENT_BLOCK)], lineage=(), stat_tests=[], events=[],
            ticker=TICKER, etf_name=NAME, day=DAY, window_end="13:20")
        assert out.rows == () and out.block_refs == {"N": ()}

    def test_empty_blocks_die(self):
        with pytest.raises(EvidenceFormatError):
            build_evidence_rows(
                blocks=[], lineage=_LINEAGE, stat_tests=[], events=[],
                ticker=TICKER, etf_name=NAME, day=DAY, window_end="13:20")


class TestRefIntegerOrdering:
    """§1: ref 가 문자열이면 사전순이 e_9 > e_10 을 낸다 — 정수 오름차순을 증명."""

    def test_refs_beyond_ten_sort_numerically(self):
        events = [{"source_event_id": f"ev_{i:03d}", "title": f"사건 {i}",
                   "available_at": "2026-08-07T10:00:00+09:00"} for i in range(9)]
        refs = ["source_event:" + e["source_event_id"] for e in events]
        out = _build(_blocks(tail={
            "block_code": "4", "block_title": "이벤트 병치",
            "evidence_refs": refs}), events=events)
        got = [r.ref for r in out.rows]
        assert got == sorted(got) and got[-1] > 10
        assert all(isinstance(r.ref, int) for r in out.rows)


class TestTemplateRenderCoverage:
    """§3.5 문형 6종 — 렌더 가능한 5종은 결정론 렌더, RELATION_PANEL 은 구조적 차단."""

    @pytest.mark.parametrize("record, expected_head", [
        (dict(template="MATCHED_ATT", basis="IDIO",
              slots={"event_title": "단일판매ㆍ공급계약해지"}, method="SIMILAR_STOCKS",
              n=41, unit="COUNT", estimate=-0.031, p=0.0088, k=3,
              series=("000660 일봉 수익률", "전 종목 일봉 수익률"), band="MIDDLE"),
         "「단일판매ㆍ공급계약해지」가 난 종목이 비슷한 다른 종목보다 더 움직였나"),
        (dict(template="MARKET_EVENT", basis="MARKET", slots={"etype": "기준금리 인상"},
              method="SIMILAR_DAYS", n=14, unit="DAY", estimate=-0.0121, p=0.0308,
              series=("KOSPI200 일봉 수익률",)),
         "기준금리 인상이 있던 날 시장이 평소와 다르게 움직였나"),
        (dict(template="TUPLE_PANEL", basis="SECTOR",
              slots={"trigger": "원/달러", "channel": "환율"}, method="SENSITIVE_STOCKS",
              n=412, unit="COUNT", estimate=-0.0093, p=0.0121,
              series=("전 종목 일봉 수익률", "원/달러 일봉 변화")),
         "원/달러가 크게 움직인 날, 환율에 민감한 종목이 더 움직였나"),
        (dict(template="MODERATION", basis="IDIO",
              slots={"event": "기준금리 인상", "conditions": "R금리신용/수준"},
              method="BY_CONDITION", n=210, unit="COUNT", estimate=-0.011, p=0.02,
              series=("000660 일봉 수익률", "국고10년 일봉 변화")),
         "「기준금리 인상」의 영향이 R금리신용/수준에 따라 달라지나"),
        (dict(template="EVENT_TAIL", basis="IDIO",
              slots={"event_title": "현금ㆍ현물배당결정"}, method="VS_USUAL",
              n=248, unit="DAY", estimate=-0.036, p=0.04, band="TOP_TAIL",
              series=("000660 일봉 수익률",)),
         "「현금ㆍ현물배당결정」 이후 움직임이 이 종목의 평소와 견줘 큰 편인가"),
    ])
    def test_renderable_templates(self, record, expected_head):
        text = render_stat_test(StatTestRecord(ref=9, **record))
        first = text.splitlines()[0]
        assert first == f"[9] 통계검정   {expected_head}"
        # 두 번 렌더 = 같은 바이트 (§0 렌더 결정론).
        assert text == render_stat_test(StatTestRecord(ref=9, **record))

    def test_correction_fragment_and_band_line(self):
        text = render_stat_test(StatTestRecord(
            ref=9, template="MATCHED_ATT", basis="IDIO",
            slots={"event_title": "단일판매ㆍ공급계약해지"}, method="SIMILAR_STOCKS",
            n=41, unit="COUNT", estimate=-0.031, p=0.0088, k=3,
            series=("000660 일봉 수익률", "전 종목 일봉 수익률"), band="MIDDLE"))
        assert "p=0.0088 · 가설 3건 보정" in text
        assert text.splitlines()[-1].endswith("위치     과거와 비슷한 수준")
        assert "이 종목만의 움직임" in text     # IDIO 를 고유층이라 부르지 않는다

    def test_relation_panel_is_structurally_blocked(self):
        """§9-M1: RELATED_STOCKS 는 레코드 생성 자체가 막힌다."""
        with pytest.raises(NotRenderable):
            StatTestRecord(
                ref=9, template="RELATION_PANEL", basis="IDIO",
                slots={"src": "A", "rel": "SUPPLY_CHAIN", "dst": "B"},
                method="RELATED_STOCKS", n=10, unit="COUNT", estimate=0.01,
                p=0.01, series=("000660 일봉 수익률",))

    def test_stat_row_never_renders_a_time_line(self):
        """§3.4: 통계검정에는 시각 줄이 없다 — 줄 자체를 생략한다."""
        out = _build(_blocks(tail=_ABSENT_BLOCK), stat_tests=[_FX_PASS])
        stat = next(r for r in out.rows if r.type == "STAT_TEST")
        assert stat.time == ""
        assert " / " not in render_row(stat)
