"""근거 데이터 구조 v3(`.tmp/근거 포맷에 대한 정리.md`) 전면 검증.

목표는 하나다 - **덕디비 가설검정 엔진(`trial`·`mkttrial`·`paneltest`·`tool_baserate`·
`lasso`)이 실제로 낼 수 있는 모든 데이터 케이스에서 이 포맷이 통과하는지**. 그래서
세 층으로 나눈다:

  1. 스펙 자체의 닫힌 어휘·조합(§2·§3.2·§3.3·§3.5·§3.6·§3.7) - 전수.
  2. 데이터 카탈로그(`docs/analysis-engine/data-catalog.md`)의 실제 데이터셋 이름을
     그대로 흘려 플랫 5종이 받아들이는지.
  3. 엔진이 실제로 반환하는 dict/`EdgeReport` 모양(§9 실측 필드)을 어댑터에 흘려
     STAT_TEST 레코드가 나오는지 - 실패해야 하는 케이스(판정불가·불균형·미배정 등)도
     포함한다. §3.3 이 "현재 렌더 불가" 라고 명시한 RELATED_STOCKS 는 통과가 아니라
     **일관되게 막히는지**가 검증 대상이다(§9-M1).

이 스키마를 실제로 구현한 코드가 이전까지 없었으므로, 이 파일 자체가 그 스키마의
첫 계약 문서다 - 통과가 곧 "이 포맷이 옳다"의 유일한 증거다.
"""
from __future__ import annotations

import string
import unicodedata

import pytest

from edge_analysis.statics.evidence_adapt import (
    from_by_condition, from_related_stocks, from_sensitive_stocks,
    from_similar_days, from_similar_stocks, from_vs_usual)
from edge_analysis.statics.evidence_card import (
    BANDS, BASES, DISCLOSURE_SOURCES, EvidenceFormatError, EvidenceRow,
    FIN_KINDS, FIN_METRICS, METHODS, METHOD_TEMPLATE, NotRenderable,
    StatTestRecord, TEMPLATE_METHOD, TEMPLATE_SLOTS, TEMPLATES, _josa,
    band_of, dedup_rows, disclosure_row, financial_row, holding_row,
    news_row, price_row, render_template, series_for, series_name)
from edge_analysis.statics.duck import BACKFILL_SETS, RDB_TABLES, S3_SETS
from edge_analysis.statics.krxsector import SECTOR_NAMES
from edge_analysis.statics.paneltest import EdgeReport

# ── 실측 필드 그대로 쓰는 고정 fixture (§9 조사 결과) ─────────────────────
TRIAL_OK = {"verdict": "계산됨", "null_kind": "pair", "att": -0.031, "p": 0.0088,
           "pairs": 41, "treated": 41, "dates": 5, "y_t": -0.02, "y_c": 0.011,
           "smd": {"ln시총": 0.04, "β_m": 0.02}, "balanced": True,
           "att_adj": -0.030, "p_adj": 0.009, "lead": {1: (0.001, 0.4, 30)},
           "pretrend_ok": True, "etype": "CONTRACT.SIGNING", "day": "2026-08-07"}

MKTTRIAL_OK = {"verdict": "계산됨", "null_kind": "pair", "att": -0.0121, "p": 0.0308,
              "n_days": 14, "pairs": 140, "treated_all": 14, "pool": 60,
              "pretrend": {"t-1": None, "t-2": None}, "overlap": 0,
              "etype": "RATE.HIKE", "day": "2026-08-07", "clean": True}

BASERATE_OK = {"verdict": "계산됨", "reason": "", "n": 250, "supports": None,
               "pct_rank": 0.96, "exceed_p": 0.012, "cond_n": 0,
               "cond_pct_rank": None, "today": -0.036, "note": "과거와 비슷한 수준"}

MODERATION_OK = {"verdict": "계산됨", "null_kind": "label", "rank": [], "att_base": -0.0093,
                 "p_max": 0.0121, "p_step": {"FX환/변화": 0.008}, "pi": {"FX환/변화": 0.71},
                 "selected": {"FX환/변화": 0.44}, "free_coef": {"treat": -0.01},
                 "free_coef_caveat": "…", "lam": 0.02,
                 "lam_sensitivity": {"0.02": ["FX환/변화"]}, "n": 412, "j": 3,
                 "dropped_collinear": {}}


def _edge_report(**over) -> EdgeReport:
    base = dict(verdict="성립", n=412, p=0.0121, effect_high=-0.0093, effect_low=0.0,
               today_exposure_pct=0.98, cond_today="", cond_satisfied=None,
               cond_measurable=True, reduction="일치", assignable=True,
               trigger_fired=None, reason="", null_kind="pair")
    base.update(over)
    return EdgeReport(**base)


# ══════════════════════════════════════════════════════════════════════
# 1. 스펙 닫힌 어휘 · 조합 전수
# ══════════════════════════════════════════════════════════════════════
class TestVocabClosure:
    def test_template_method_is_a_bijection(self):
        assert frozenset(TEMPLATE_METHOD) == TEMPLATES
        assert frozenset(TEMPLATE_METHOD.values()) == METHODS
        assert METHOD_TEMPLATE == {v: k for k, v in TEMPLATE_METHOD.items()}

    def test_every_template_has_slot_spec(self):
        assert frozenset(TEMPLATE_SLOTS) == TEMPLATES

    @pytest.mark.parametrize("bad", ["", "unknown", "성립", "MARKET ", None])
    def test_bad_basis_rejected(self, bad):
        with pytest.raises((EvidenceFormatError, TypeError)):
            StatTestRecord(ref=1, template="MARKET_EVENT", basis=bad,
                           slots={"etype": "x"}, method="SIMILAR_DAYS", n=10,
                           unit="DAY", estimate=0.01, p=0.01, series=("s",))


class TestBandOf:
    @pytest.mark.parametrize("pct,want", [
        (1.00, "TOP_TAIL"), (0.95, "TOP_TAIL"), (0.9499, "UPPER"),
        (0.75, "UPPER"), (0.7499, "MIDDLE"), (0.26, "MIDDLE"),
        (0.25, "LOWER"), (0.0501, "LOWER"), (0.05, "BOTTOM_TAIL"), (0.0, "BOTTOM_TAIL")])
    def test_boundaries_cover_full_range_without_gaps(self, pct, want):
        assert band_of(pct) == want

    def test_none_passes_through_silently(self):
        assert band_of(None) is None

    @pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
    def test_out_of_range_rejected(self, bad):
        with pytest.raises(EvidenceFormatError):
            band_of(bad)


class TestSeriesGrammar:
    def test_series_name_is_the_only_assembly_point(self):
        assert series_name("000660", "일봉", "수익률") == "000660 일봉 수익률"

    @pytest.mark.parametrize("freq,measure", [("주봉", "수익률"), ("일봉", "변동")])
    def test_series_name_rejects_open_vocab(self, freq, measure):
        with pytest.raises(EvidenceFormatError):
            series_name("000660", freq, measure)

    @pytest.mark.parametrize("method,items", [
        ("SIMILAR_STOCKS", ["000660 일봉 수익률", "전 종목 일봉 수익률"]),
        ("SIMILAR_DAYS", ["KOSPI200 일봉 수익률"]),
        ("SENSITIVE_STOCKS", ["전 종목 일봉 수익률", "원/달러 일봉 변화"]),
        ("VS_USUAL", ["000660 일봉 수익률"]),
        ("BY_CONDITION", ["000660 일봉 수익률", "FX환 민감도 계열"]),
        ("BY_CONDITION", ["000660 일봉 수익률", "a", "b", "c"]),   # 상한 없음
    ])
    def test_valid_arity_per_method(self, method, items):
        assert series_for(method, items) == tuple(items)

    def test_related_stocks_is_never_renderable(self):
        with pytest.raises(NotRenderable):
            series_for("RELATED_STOCKS", ["아무거나"])

    @pytest.mark.parametrize("method,items", [
        ("SIMILAR_STOCKS", ["한 칸뿐"]),
        ("SIMILAR_DAYS", ["칸", "둘"]),
        ("SENSITIVE_STOCKS", ["칸"]),
        ("VS_USUAL", ["칸", "둘"]),
        ("BY_CONDITION", ["칸 하나뿐"]),
    ])
    def test_wrong_arity_rejected(self, method, items):
        with pytest.raises(EvidenceFormatError):
            series_for(method, items)

    def test_similar_stocks_targets_must_differ(self):
        with pytest.raises(EvidenceFormatError):
            series_for("SIMILAR_STOCKS", ["전 종목 일봉 수익률", "전 종목 일봉 수익률"])

    def test_empty_slot_rejected(self):
        with pytest.raises(EvidenceFormatError):
            series_for("VS_USUAL", [" "])


# ══════════════════════════════════════════════════════════════════════
# 2. 데이터 카탈로그(S3/RDB) 실제 데이터셋 이름으로 플랫 5종 검증
# ══════════════════════════════════════════════════════════════════════
# `docs/analysis-engine/data-catalog.md` 는 사람이 손으로 정리한 2차 문서라 실제
# 등록과 갈릴 수 있다(실측: `s3_dg_price` 가 `duck.py` 에는 있는데 그 문서엔 없다).
# 그래서 문서가 아니라 **`duck.py` 의 실제 등록 상수를 직접 임포트**한다 - 여기서
# 만든 목록은 duck.py 가 새 데이터셋을 등록하는 순간 같이 넓어지고, 문서 드리프트와
# 무관하게 항상 "덕디비에서 실제로 조회 가능한 것 전체"와 일치한다.
ALL_DUCKDB_DATASETS = sorted({
    *RDB_TABLES, *(name for name, _, _ in S3_SETS), *BACKFILL_SETS, "bars_5m"})
# 이름에 "hold" 를 포함하는 것 = 구성종목(보유) 계열 원천 전수.
HOLDING_SOURCES = sorted(n for n in ALL_DUCKDB_DATASETS if "hold" in n)
CATALOG_FIN_ITEMS = [(m, k) for m in FIN_METRICS for k in FIN_KINDS]   # 5×2=10 전수


class TestFlatEvidenceAgainstCatalog:
    def test_dataset_registry_is_nonempty_and_has_no_surprises(self):
        """이 상수 자체가 잘못 임포트돼 텅 비면 아래 파라미터화 테스트가 전부
        '0개 케이스라 자동 통과' 로 조용히 무의미해진다 - 그 실패 양식을 막는다."""
        assert len(ALL_DUCKDB_DATASETS) >= 55          # 실측 RDB 19 + S3 30+ + 백필 10 + bars_5m
        assert "s3_dg_price" in ALL_DUCKDB_DATASETS    # 카탈로그 문서에 없던 그 데이터셋
        assert "sector_index" in ALL_DUCKDB_DATASETS and "document" in ALL_DUCKDB_DATASETS

    @pytest.mark.parametrize("dataset", ALL_DUCKDB_DATASETS)
    def test_price_row_accepts_every_real_duckdb_dataset_name(self, dataset):
        """가격 evidence 의 `dataset` 필드는 §2 상 자유 텍스트다(닫힌 어휘 없음) -
        "전수"의 뜻은 열거가 아니라 **덕디비가 실제로 낼 수 있는 이름 전체가 이 자유
        텍스트 필드를 거부당하지 않는다**는 것이다."""
        row = price_row(1, dataset=dataset, vendor="한국거래소", as_of="08-07 08:10")
        assert row.type == "PRICE" and row.content == dataset

    @pytest.mark.parametrize("source", HOLDING_SOURCES)
    def test_holding_row_accepts_every_real_holding_source(self, source):
        row = holding_row(2, as_of_basis="08-06", vendor=source, as_of="08-07 06:30")
        assert row.content == "PDF 구성비중 · 08-06"

    def test_price_row_rejects_time_window_suffix(self):
        with pytest.raises(EvidenceFormatError):
            price_row(1, dataset="KODEX 반도체 1분봉 · 08-07 09:00–13:20",
                     vendor="코스콤", as_of="08-07 13:22")

    @pytest.mark.parametrize("source_code", sorted(DISCLOSURE_SOURCES))   # §2 닫힌 어휘 3/3 전수
    def test_disclosure_row_accepts_all_source_codes(self, source_code):
        row = disclosure_row(3, title="단일판매·공급계약 해지", source_code=source_code,
                            rcept_no="20260807000412", published_at="08-07 10:31")
        assert source_code in row.source

    def test_disclosure_row_rejects_vendor_outside_catalog(self):
        with pytest.raises(EvidenceFormatError):
            disclosure_row(3, title="x", source_code="NAVER", rcept_no="1",
                          published_at="08-07")

    # 언론사명은 §2 상 자유 텍스트다(구성종목 vendor·가격 vendor 와 같은 성격) -
    # duck.py 에 대응하는 닫힌 레지스트리가 없어 대표 예시로 남긴다.
    @pytest.mark.parametrize("publisher", ["연합뉴스", "코스콤", "BigKinds"])
    def test_news_row_accepts_representative_publishers(self, publisher):
        row = news_row(4, title="기준금리 0.25%p 인상", publisher=publisher,
                       published_at="08-07 09:58")
        assert row.source == publisher

    @pytest.mark.parametrize("metric,kind", CATALOG_FIN_ITEMS)
    def test_financial_row_covers_all_metric_kind_combinations(self, metric, kind):
        row = financial_row(5, metric=metric, kind=kind, period="2025 4개분기",
                           vendor="DART", as_of="07-15 18:00")
        assert metric in row.content and kind in row.content

    def test_financial_row_rejects_metric_outside_catalog(self):
        with pytest.raises(EvidenceFormatError):
            financial_row(5, metric="ROE", kind="실적", period="FY2025",
                         vendor="DataGuide", as_of="08-01")


# ══════════════════════════════════════════════════════════════════════
# 3. 행 골격 · dedup · 정렬 (§0·§1)
# ══════════════════════════════════════════════════════════════════════
class TestEvidenceRowSkeleton:
    def test_stat_test_row_cannot_carry_a_time(self):
        with pytest.raises(EvidenceFormatError):
            EvidenceRow(1, "STAT_TEST", content="c", source="s", time="08-07")

    def test_flat_row_cannot_carry_detail(self):
        with pytest.raises(EvidenceFormatError):
            EvidenceRow(1, "PRICE", content="c", source="s", detail={"p": 0.01})

    @pytest.mark.parametrize("content,source", [("", "s"), ("c", ""), ("  ", "s")])
    def test_empty_content_or_source_is_zero_evidence(self, content, source):
        """§0 "근거 0인 문장은 빌드 실패다" 를 문자 그대로 강제한다."""
        with pytest.raises(EvidenceFormatError):
            EvidenceRow(1, "PRICE", content=content, source=source)

    def test_unknown_type_rejected(self):
        with pytest.raises(EvidenceFormatError):
            EvidenceRow(1, "UNKNOWN", content="c", source="s")

    def test_dedup_keeps_first_occurrence_and_sorts_by_type_then_ref(self):
        rows = [
            EvidenceRow(9, "STAT_TEST", content="c9a", source="s"),
            EvidenceRow(2, "NEWS", content="c2", source="s"),
            EvidenceRow(1, "PRICE", content="c1", source="s"),
            EvidenceRow(9, "STAT_TEST", content="c9b-중복", source="s"),   # 같은 ref
        ]
        out = dedup_rows(rows)
        assert [r.ref for r in out] == [1, 2, 9]
        assert [r.type for r in out] == ["PRICE", "NEWS", "STAT_TEST"]
        assert out[-1].content == "c9a"   # 첫 등장이 대표


# ══════════════════════════════════════════════════════════════════════
# 4. STAT_TEST 레코드 — 6 템플릿 × basis × band × k 조합
# ══════════════════════════════════════════════════════════════════════
_VALID_RECORDS = {
    "MATCHED_ATT": dict(basis="IDIO", slots={"event_title": "공급계약 해지"},
                        method="SIMILAR_STOCKS", n=41, unit="COUNT", estimate=-0.031,
                        p=0.0088, series=("000660 일봉 수익률", "전 종목 일봉 수익률")),
    "MARKET_EVENT": dict(basis="MARKET", slots={"etype": "기준금리 인상"},
                         method="SIMILAR_DAYS", n=14, unit="DAY", estimate=-0.0121,
                         p=0.0308, series=("KOSPI200 일봉 수익률",)),
    "TUPLE_PANEL": dict(basis="SECTOR", slots={"trigger": "원/달러", "channel": "환율"},
                        method="SENSITIVE_STOCKS", n=412, unit="COUNT",
                        estimate=-0.0093, p=0.0121,
                        series=("전 종목 일봉 수익률", "원/달러 일봉 변화")),
    "MODERATION": dict(basis="IDIO", slots={"event": "공급계약 해지",
                                           "conditions": "환율 민감도"},
                       method="BY_CONDITION", n=412, unit="COUNT", estimate=-0.0093,
                       p=0.0121, series=("000660 일봉 수익률", "FX환 민감도 계열")),
    "EVENT_TAIL": dict(basis="IDIO", slots={"event_title": "공급계약 해지"},
                       method="VS_USUAL", n=250, unit="DAY", estimate=-0.036,
                       p=0.012, series=("000660 일봉 수익률",)),
}


class TestStatTestRecordCombinations:
    @pytest.mark.parametrize("template", sorted(_VALID_RECORDS))
    @pytest.mark.parametrize("basis", sorted(BASES))
    @pytest.mark.parametrize("band", [None, *sorted(BANDS)])
    @pytest.mark.parametrize("k", [1, 3])
    def test_every_renderable_template_x_basis_x_band_x_k_combination(
            self, template, basis, band, k):
        """§3.2 basis 3값 × §3.6 band(무·5값) × k(=1 생략 · >1 표기) 전수.

        RELATION_PANEL(RELATED_STOCKS) 은 이 표에서 뺐다 - §3.3 이 "현재 렌더
        불가"라 명시했으므로 여기 섞으면 "통과해야 할 조합"과 "막혀야 할 조합"이
        같은 표에 섞여 테스트 의도가 흐려진다(아래 별도 클래스에서 검증).
        """
        base = dict(_VALID_RECORDS[template])
        base["basis"] = basis
        rec = StatTestRecord(ref=7, template=template, band=band, k=k, **base)
        row = rec.to_row()
        assert row.type == "STAT_TEST" and row.time == ""
        assert ("k" in row.detail) == (k > 1)
        assert ("band" in row.detail) == (band is not None)
        if band is not None:
            assert row.detail["band"] == band

    def test_template_method_mismatch_rejected(self):
        base = dict(_VALID_RECORDS["MATCHED_ATT"])
        with pytest.raises(EvidenceFormatError):
            StatTestRecord(ref=1, template="MARKET_EVENT", **base)   # method 는 SIMILAR_STOCKS인데

    @pytest.mark.parametrize("slots", [
        {}, {"event_title": "x", "extra": "y"}, {"wrong_key": "x"}, {"event_title": " "}])
    def test_slot_shape_must_match_template_exactly(self, slots):
        base = dict(_VALID_RECORDS["MATCHED_ATT"])
        base["slots"] = slots
        with pytest.raises(EvidenceFormatError):
            StatTestRecord(ref=1, template="MATCHED_ATT", **base)

    @pytest.mark.parametrize("field,bad", [
        ("n", 0), ("p", -0.01), ("p", 1.5), ("k", 0)])
    def test_numeric_bounds(self, field, bad):
        base = dict(_VALID_RECORDS["EVENT_TAIL"])
        base[field] = bad
        with pytest.raises(EvidenceFormatError):
            StatTestRecord(ref=1, template="EVENT_TAIL", **base)

    def test_bad_band_rejected(self):
        base = dict(_VALID_RECORDS["EVENT_TAIL"])
        with pytest.raises(EvidenceFormatError):
            StatTestRecord(ref=1, template="EVENT_TAIL", band="EXTREME", **base)


class TestRelationPanelIsAlwaysBlocked:
    """§9-M1 — RELATION_PANEL 은 구조적으로 못 만든다. '가끔 실패' 가 아니라
    '항상' 이어야 한다 - series_for 가 그 항상성을 강제한다."""

    def test_relation_panel_cannot_be_constructed(self):
        with pytest.raises(NotRenderable):
            StatTestRecord(
                ref=1, template="RELATION_PANEL", basis="IDIO",
                slots={"src": "A", "rel": "SUPPLY_CHAIN", "dst": "B"},
                method="RELATED_STOCKS", n=10, unit="COUNT", estimate=0.01, p=0.01,
                series=("아무 series"           ,))


# ══════════════════════════════════════════════════════════════════════
# 4.5 §3.5 문형 렌더 — 받침 있는 슬롯값에서도 자연스러운가
# ══════════════════════════════════════════════════════════════════════
# §3.5 표는 "{event_title}가 난"·"{etype}가 있던"처럼 "가"를 문자로 박아 뒀지만,
# 공시·사건 제목은 받침으로 끝나는 쪽이 더 흔하다("변경"·"결정"·"인상"·"취득").
# 그대로 이으면 "…변경가 났다" 같은 비문이 나간다 - §7 케이스 C 의 손으로 쓴
# 예문("인상**이** 있던 날")이 이미 표 자체가 문자 그대로의 접합이 아님을 보여준다.
_BATCHIM_ENDS = ["대표이사 변경", "유상증자 결정", "기준금리 인상", "자기주식 취득",
                "감사의견 거절", "제3자배정 유상증자 결정"]        # 받침 있음 → "이"
_NO_BATCHIM_ENDS = ["단일판매·공급계약 해지", "실적 발표", "무상증자",
                    "영업정지", "원/달러"]                        # 받침 없음 → "가"


class TestTemplateRenderingIsNaturalKorean:
    @pytest.mark.parametrize("title", _BATCHIM_ENDS)
    def test_matched_att_uses_i_after_batchim(self, title):
        s = render_template("MATCHED_ATT", {"event_title": title})
        assert s == f"「{title}」이 난 종목이 비슷한 다른 종목보다 더 움직였나"

    @pytest.mark.parametrize("title", _NO_BATCHIM_ENDS)
    def test_matched_att_uses_ga_without_batchim(self, title):
        s = render_template("MATCHED_ATT", {"event_title": title})
        assert s == f"「{title}」가 난 종목이 비슷한 다른 종목보다 더 움직였나"

    def test_matched_att_matches_spec_worked_example_verbatim(self):
        """§7 케이스 A [9]의 손으로 쓴 문장과 토씨 하나까지 같아야 한다."""
        s = render_template("MATCHED_ATT", {"event_title": "단일판매·공급계약 해지"})
        assert s == "「단일판매·공급계약 해지」가 난 종목이 비슷한 다른 종목보다 더 움직였나"

    @pytest.mark.parametrize("etype", _BATCHIM_ENDS)
    def test_market_event_uses_i_after_batchim(self, etype):
        s = render_template("MARKET_EVENT", {"etype": etype})
        assert s == f"{etype}이 있던 날 시장이 평소와 다르게 움직였나"

    def test_market_event_matches_spec_worked_example_verbatim(self):
        """§7 케이스 C [8] - "인상**이**" 를 그대로 재현해야 한다."""
        s = render_template("MARKET_EVENT", {"etype": "기준금리 인상"})
        assert s == "기준금리 인상이 있던 날 시장이 평소와 다르게 움직였나"

    @pytest.mark.parametrize("trigger", _BATCHIM_ENDS)
    def test_tuple_panel_uses_i_after_batchim(self, trigger):
        s = render_template("TUPLE_PANEL", {"trigger": trigger, "channel": "환율"})
        assert s == f"{trigger}이 크게 움직인 날, 환율에 민감한 종목이 더 움직였나"

    def test_tuple_panel_matches_spec_worked_example_verbatim(self):
        """§3.4 예시 - "원/달러**가**" 를 그대로 재현해야 한다."""
        s = render_template("TUPLE_PANEL", {"trigger": "원/달러", "channel": "환율"})
        assert s == "원/달러가 크게 움직인 날, 환율에 민감한 종목이 더 움직였나"

    def test_moderation_and_event_tail_have_no_batchim_dependent_particle(self):
        """"의"·"이후"는 받침과 무관하다 - 슬롯값이 무엇이든 같은 조사가 붙는다."""
        for title in (*_BATCHIM_ENDS, *_NO_BATCHIM_ENDS):
            assert render_template("MODERATION", {"event": title, "conditions": "환율 민감도"}) \
                == f"「{title}」의 영향이 환율 민감도에 따라 달라지나"
            assert render_template("EVENT_TAIL", {"event_title": title}) \
                == f"「{title}」 이후 움직임이 이 종목의 평소와 견줘 큰 편인가"

    def test_unknown_template_rejected(self):
        with pytest.raises(EvidenceFormatError):
            render_template("NO_SUCH_TEMPLATE", {})

    # ── 손으로 고른 예시가 아니라 실제 저장소 데이터로 넓힌다 ──────────────
    # `krxsector.SECTOR_NAMES` 는 §3.7 "KRX {업종}" 대상 어휘의 실제 값 26종
    # 전부다(중복 제외) - trigger 슬롯에 실제로 올 수 있는 것 전수. 기대값은
    # `_josa` 를 다시 불러 계산하지 않고 **손으로 판정**해 박아 둔다 - 같은 함수로
    # 기대값을 만들면 그 함수 자체의 버그(예: `% 28` 을 `% 27` 로 잘못 고치는 것)를
    # 이 테스트가 못 잡는다(Rule 9 - 검정 대상과 같은 셈으로 답을 만들면 안 된다).
    _SECTOR_JOSA = {   # 받침 없음(→가) 16 · 받침 있음(→이) 10, 전부 손으로 판정
        "음식료·담배": "가", "섬유·의류": "가", "종이·목재": "가", "화학": "이",
        "제약": "이", "비금속": "이", "금속": "이", "기계·장비": "가",
        "전기전자": "가", "의료·정밀기기": "가", "운송장비·부품": "이", "유통": "이",
        "전기·가스": "가", "건설": "이", "운송·창고": "가", "통신": "이",
        "금융": "이", "증권": "이", "보험": "이", "일반서비스": "가",
        "제조": "가", "부동산": "이", "IT 서비스": "가", "오락·문화": "가",
        "출판·매체복제": "가", "기타제조": "가"}

    def test_sector_josa_table_covers_every_real_sector_name(self):
        """이 표 자체가 SECTOR_NAMES 전수를 놓치지 않았는지 - 표가 낡으면
        아래 파라미터화 테스트가 조용히 일부만 도는 실패 양식을 막는다."""
        assert set(self._SECTOR_JOSA) == set(SECTOR_NAMES.values())

    @pytest.mark.parametrize("sector,want", sorted(_SECTOR_JOSA.items()))
    def test_tuple_panel_trigger_covers_every_real_krx_sector_name(self, sector, want):
        trigger = f"KRX {sector}"
        s = render_template("TUPLE_PANEL", {"trigger": trigger, "channel": "환율"})
        assert s == f"{trigger}{want} 크게 움직인 날, 환율에 민감한 종목이 더 움직였나"

    # `data-pipeline/tests/test_dart_disclosure.py` 에 실제로 쓰인 report_nm -
    # 지어낸 제목이 아니라 이 저장소가 실제로 수집·테스트하는 공시 제목이다.
    # "체결"·"결정"은 받침이 있고 "해지"·"보고서"·"결의"는 없다 - 스펙 §7 케이스 A 가
    # 고른 예시("해지")는 우연히 받침 없는 쪽이라 이 버그를 안 드러냈다.
    REAL_DART_REPORT_NAMES = [
        ("단일판매ㆍ공급계약체결", "이"), ("단일판매ㆍ공급계약해지", "가"),
        ("현금ㆍ현물배당결정", "이"), ("주주총회소집결의", "가"), ("사업보고서", "가")]

    @pytest.mark.parametrize("title,want", REAL_DART_REPORT_NAMES)
    def test_matched_att_covers_real_dart_report_titles(self, title, want):
        s = render_template("MATCHED_ATT", {"event_title": title})
        assert s == f"「{title}」{want} 난 종목이 비슷한 다른 종목보다 더 움직였나"

    def test_stat_test_record_to_row_content_is_the_rendered_sentence(self):
        """레코드의 `content` 가 이제 `template:slots` 디버그 표기가 아니라
        실제 렌더된 문장이어야 한다."""
        base = dict(_VALID_RECORDS["MARKET_EVENT"])
        rec = StatTestRecord(ref=1, template="MARKET_EVENT", **base)
        content = rec.to_row().content
        assert content == render_template("MARKET_EVENT", rec.slots)
        assert not content.startswith("MARKET_EVENT:")   # 옛 디버그 표기 잔재 없음


# ══════════════════════════════════════════════════════════════════════
# 4.6 조사 판정의 일반 증명 — 예시가 아니라 알고리즘 자체
# ══════════════════════════════════════════════════════════════════════
# `event_title`·`etype`·`trigger` 는 자유 텍스트(또는 티커 6자리·KOSPI200 처럼
# 사실상 무한한 인스턴스를 갖는 부류)라 "모든 값을 나열해서 통과시킨다"는 애초에
# 불가능하다. 대신 `_josa` 가 실제로 보는 것은 **슬롯값 전체가 아니라 마지막 한
# 글자뿐**이라는 사실을 이용한다 - 그 마지막 글자가 속할 수 있는 부류는 유한하다:
#   · 완성형 한글 음절(가~힣) 11,172개 — 어떤 한국어 단어든 마지막 글자는 반드시 이 중 하나
#   · 숫자 0~9 (티커 6자리·KOSPI200·국고10년의 "10" 등이 여기로 떨어진다)
#   · 라틴 알파벳 26개(대소문자 무관)
#   · 그 무엇도 아닌 경우(공백·기호뿐인 문자열)
# 이 네 부류를 전부 덮으면 "어떤 한국어 명사구가 오든" 을 개별 단어 나열 없이
# 증명한 것이 된다 - 11,172+10+26+1 = 11,209번의 검사로 사실상 무한한 어휘를 덮는다.
class TestJosaIsProvenCorrectForEveryPossibleSlotValue:
    def test_every_precomposed_hangul_syllable(self):
        """가(U+AC00)~힣(U+D7A3) 전부. **다른 방법으로 기대값을 만든다** -
        `_josa` 와 같은 나눗셈 공식을 다시 쓰면 그 공식 자체의 버그(예: `%28`
        을 `%27`로 잘못 고치는 것)를 이 테스트가 못 잡는다. 대신 `unicodedata`
        의 NFD 정준분해를 오라클로 쓴다: 받침 있는 음절은 초성·중성·종성
        자모 3개로 풀리고, 없으면 초성·중성 2개로 풀린다 - 코드포인트 산술과
        무관한 독립적 판정 근거다."""
        checked = 0
        for cp in range(0xAC00, 0xD7A4):          # 11,172 개, 결측 없이 연속
            ch = chr(cp)
            has_batchim = len(unicodedata.normalize("NFD", ch)) == 3
            want = "받침있음" if has_batchim else "받침없음"
            got = "받침있음" if _josa(ch, "받침있음", "받침없음") == "받침있음" else "받침없음"
            assert got == want, f"U+{cp:04X} {ch!r}: NFD 분해={want} 인데 _josa={got}"
            checked += 1
        assert checked == 11172          # 표에 구멍이 없었는지 - 범위를 잘못 좁히면 통과가 거짓말이 된다

    def test_every_ascii_digit_always_has_batchim(self):
        """티커 6자리·KOSPI200·국고10년의 "10" 이 전부 여기로 떨어진다 - 숫자
        10개 전부가 항상 받침 취급됨을 확인하면, 숫자로 끝나는 **모든** 문자열
        (몇 자리든) 이 이 규칙 하나로 덮인다는 뜻이다."""
        for d in string.digits:
            assert _josa(d, "이", "가") == "이", d

    def test_every_latin_letter_matches_the_lmnr_heuristic(self):
        """l·m·n·r(대소문자 무관)만 받침 취급, 나머지 22개는 아님 - 이 26개가
        영문으로 끝나는 모든 슬롯값(코드·티커 접미사 등)의 전체 정의역이다."""
        with_jong_letters = set("lmnrLMNR")
        for c in string.ascii_letters:
            want = "이" if c in with_jong_letters else "가"
            assert _josa(c, "이", "가") == want, c

    @pytest.mark.parametrize("junk", ["", "   ", "···", "!!!", "()"])
    def test_no_alnum_or_hangul_falls_back_without_jong(self, junk):
        assert _josa(junk, "이", "가") == "가"

    def test_ticker_like_six_digit_codes_are_always_i_not_ga(self):
        """실제 티커 6자리 표본(양 극단 포함)으로 일반 증명을 재확인한다 -
        전수(1,000,000개)를 돌 필요가 없다는 것이 위 숫자 전수 증명의 요점이다."""
        for ticker in ("000660", "005930", "000000", "999999", "042700"):
            s = render_template("TUPLE_PANEL", {"trigger": ticker, "channel": "환율"})
            assert s.startswith(f"{ticker}이 ")


# ── 슬롯 대부분은 조사와 무관하다는 것도 증명 대상이다 ────────────────────
# 6 템플릿의 슬롯을 전부 세면 8개인데, 받침에 반응하는 자리는 3개뿐이다(위
# 클래스가 증명). 나머지 5개는 뒤따르는 말이 "의"·"에"·"까지"·"이후"·"에 따라"
# 처럼 애초에 받침과 무관한 조사/부사이기 때문이다 - 이걸 "안 깨진다"로 말로
# 적어두는 대신, 받침 있는/없는 슬롯값을 실제로 넣어보고 **그 슬롯 자리를 뺀
# 나머지 문장이 한 글자도 안 변하는지** 로 확인한다.
class TestNonAdjacentSlotsAreProvablyBatchimInvariant:
    _PAIR = ("전기전자", "화학")   # (받침 없음, 받침 있음) - 어느 쪽이 와도 무관해야 하는 슬롯에 흘려 넣는다

    @pytest.mark.parametrize("template,slots_of", [
        ("RELATION_PANEL", lambda v: {"src": v, "rel": "SUPPLY_CHAIN", "dst": "B"}),
        ("MODERATION", lambda v: {"event": v, "conditions": "환율 민감도"}),
        ("EVENT_TAIL", lambda v: {"event_title": v}),
    ])
    def test_swapping_batchim_only_changes_the_slot_substring_itself(self, template, slots_of):
        no_batchim, has_batchim = self._PAIR
        a = render_template(template, slots_of(no_batchim))
        b = render_template(template, slots_of(has_batchim))
        # 슬롯값 자체를 서로 맞바꾼 뒤에도 두 문장이 완전히 같아야 한다 -
        # 즉 슬롯 자리 말고는 받침이 결과에 아무 영향도 못 미친다.
        assert a.replace(no_batchim, "␝") == b.replace(has_batchim, "␝")

    def test_moderation_conditions_slot_is_also_batchim_invariant(self):
        no_batchim, has_batchim = self._PAIR
        a = render_template("MODERATION", {"event": "e", "conditions": no_batchim})
        b = render_template("MODERATION", {"event": "e", "conditions": has_batchim})
        assert a.replace(no_batchim, "␝") == b.replace(has_batchim, "␝")

    def test_tuple_panel_channel_slot_is_batchim_invariant(self):
        """`{channel}` 뒤는 항상 "에" - trigger 와 달리 channel 은 무관해야 한다."""
        no_batchim, has_batchim = self._PAIR
        a = render_template("TUPLE_PANEL", {"trigger": "원/달러", "channel": no_batchim})
        b = render_template("TUPLE_PANEL", {"trigger": "원/달러", "channel": has_batchim})
        assert a.replace(no_batchim, "␝") == b.replace(has_batchim, "␝")


# ══════════════════════════════════════════════════════════════════════
# 5. 어댑터 — 엔진 실측 필드 → STAT_TEST (성공/실패 둘 다)
# ══════════════════════════════════════════════════════════════════════
class TestAdaptersOnRealEngineShapes:
    def test_similar_stocks_success(self):
        rec = from_similar_stocks(TRIAL_OK, ref=9, event_title="단일판매·공급계약 해지",
                                  target_series="000660 일봉 수익률")
        assert rec.method == "SIMILAR_STOCKS" and rec.n == 41 and rec.p == 0.0088
        assert rec.basis == "IDIO"          # layer="고유" 기본값

    def test_similar_stocks_rejects_undetermined_verdict(self):
        with pytest.raises(EvidenceFormatError):
            from_similar_stocks({"verdict": "판정불가", "reason": "매칭 짝 부족"},
                                ref=9, event_title="x", target_series="s")

    def test_similar_stocks_rejects_unbalanced_match(self):
        bad = dict(TRIAL_OK, balanced=False)
        with pytest.raises(EvidenceFormatError):
            from_similar_stocks(bad, ref=9, event_title="x", target_series="s")

    def test_similar_stocks_rejects_significant_pretrend_placebo(self):
        """§5 게이트 2/3 - 사전추세 위약이 유의하면 예고·유출이라 근거로 못 옮긴다."""
        leaked = dict(TRIAL_OK, lead={1: (0.02, 0.01, 30)})   # p=0.01 <= 0.05
        with pytest.raises(EvidenceFormatError):
            from_similar_stocks(leaked, ref=9, event_title="x", target_series="s")

    def test_similar_stocks_tolerates_thin_pretrend_sample(self):
        """`lead[j]` 가 `None`(표본부족) 인 자리는 위약 판정에서 빠진다 - 못 잰
        것을 유의로 세면 정상 시행이 조용히 막힌다."""
        thin = dict(TRIAL_OK, lead={1: None, 2: (0.001, 0.9, 5)})
        rec = from_similar_stocks(thin, ref=9, event_title="x", target_series="s")
        assert rec.method == "SIMILAR_STOCKS"

    def test_similar_days_success_uses_day_unit(self):
        rec = from_similar_days(MKTTRIAL_OK, ref=8, etype_label="기준금리 인상")
        assert rec.unit == "DAY" and rec.n == 14 and rec.basis == "MARKET"

    def test_similar_days_rejects_undetermined(self):
        with pytest.raises(EvidenceFormatError):
            from_similar_days({"verdict": "판정불가", "reason": "처치일 6 < 10"},
                              ref=8, etype_label="x")

    def test_sensitive_stocks_success_from_real_edgereport_shape(self):
        report = _edge_report()
        rec = from_sensitive_stocks(report, ref=7, trigger_label="원/달러",
                                    channel_label="환율", base_series="전 종목 일봉 수익률",
                                    sensitivity_series="원/달러 일봉 변화")
        assert rec.method == "SENSITIVE_STOCKS" and rec.n == 412
        assert rec.estimate == pytest.approx(-0.0093)

    def test_sensitive_stocks_rejects_when_applies_today_is_false(self):
        report = _edge_report(cond_satisfied=False)   # applies_today 를 거짓으로 만든다
        assert report.applies_today is False
        with pytest.raises(EvidenceFormatError):
            from_sensitive_stocks(report, ref=7, trigger_label="x", channel_label="y",
                                  base_series="a", sensitivity_series="b")

    def test_sensitive_stocks_rejects_non_성립_verdict(self):
        report = _edge_report(verdict="판정불가", p=None, n=0)
        with pytest.raises(EvidenceFormatError):
            from_sensitive_stocks(report, ref=7, trigger_label="x", channel_label="y",
                                  base_series="a", sensitivity_series="b")

    def test_vs_usual_success_derives_band_from_pct_rank(self):
        rec = from_vs_usual(BASERATE_OK, ref=6, event_title="공급계약 해지",
                            target_series="000660 일봉 수익률")
        assert rec.band == "TOP_TAIL"    # pct_rank=0.96
        assert rec.unit == "DAY" and rec.p == 0.012

    def test_vs_usual_rejects_undetermined(self):
        thin = {"verdict": "판정불가", "reason": "표본 29 < 30", "n": 29,
               "pct_rank": None, "exceed_p": None, "today": None}
        with pytest.raises(EvidenceFormatError):
            from_vs_usual(thin, ref=6, event_title="x", target_series="s")

    def test_by_condition_success_lists_only_selected_conditions(self):
        rec = from_by_condition(MODERATION_OK, ref=5, event_label="공급계약 해지",
                                target_series="000660 일봉 수익률",
                                condition_series=["FX환 민감도 계열"])
        assert rec.slots["conditions"] == "FX환/변화"
        assert rec.p == 0.0121   # p_max, p_step 아님

    def test_by_condition_rejects_when_nothing_survives_lasso(self):
        empty = dict(MODERATION_OK, selected={})
        with pytest.raises(EvidenceFormatError):
            from_by_condition(empty, ref=5, event_label="x", target_series="s",
                              condition_series=[])

    def test_by_condition_rejects_none_moderation(self):
        """`trial.run_trial(..., moderators=...)` 는 moderation=None 을 낼 수 있다
        (positivity 위반 등, `mod_reason` 만 남는 경우) - 그 경로가 근거로 새면 안 된다."""
        with pytest.raises(EvidenceFormatError):
            from_by_condition(None, ref=5, event_label="x", target_series="s",
                              condition_series=[])

    def test_related_stocks_adapter_always_blocked_regardless_of_input(self):
        """§9-M1 — `_relation_test` 가 무엇을 반환하든(성립이든 판정불가든)
        `assignable=False` 라 어댑터는 인자를 보지도 않고 막는다."""
        for report in (_edge_report(assignable=False),
                       _edge_report(verdict="판정불가", assignable=False, p=None, n=0)):
            with pytest.raises(NotRenderable):
                from_related_stocks(report, ref=1)
