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

import pytest

from edge_analysis.statics.evidence_adapt import (
    from_by_condition, from_related_stocks, from_sensitive_stocks,
    from_similar_days, from_similar_stocks, from_vs_usual)
from edge_analysis.statics.evidence_card import (
    BANDS, BASES, EvidenceFormatError, EvidenceRow, FIN_KINDS, FIN_METRICS,
    METHODS, METHOD_TEMPLATE, NotRenderable, StatTestRecord, TEMPLATE_METHOD,
    TEMPLATE_SLOTS, TEMPLATES, band_of, dedup_rows, disclosure_row,
    financial_row, holding_row, news_row, price_row, series_for, series_name)
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
# docs/analysis-engine/data-catalog.md §1·§3·§5·§7 에서 그대로 가져온 대표 이름.
CATALOG_PRICE_DATASETS = [
    "KODEX 반도체 일봉 수정주가", "layers_daily 시장 프록시", "s3_price_daily 종목 일봉",
    "bars_5m 5분봉", "us_market SPY 일봉", "fx_usdkrw 환율 일봉",
    "s3_index_daily 해외지수", "s3_rates_daily 국고채 금리",
]
CATALOG_HOLDING_BASES = ["s3_etf_holdings", "etf_holding_snapshot", "etf_holdings_fmp"]
CATALOG_DISCLOSURE_SOURCES = ["DART", "KRX", "KIND"]
CATALOG_NEWS_PUBLISHERS = ["연합뉴스", "코스콤", "BigKinds"]
CATALOG_FIN_ITEMS = [(m, k) for m in FIN_METRICS for k in FIN_KINDS]   # 5×2=10 전수


class TestFlatEvidenceAgainstCatalog:
    @pytest.mark.parametrize("dataset", CATALOG_PRICE_DATASETS)
    def test_price_row_accepts_every_catalog_price_dataset(self, dataset):
        row = price_row(1, dataset=dataset, vendor="한국거래소", as_of="08-07 08:10")
        assert row.type == "PRICE" and row.content == dataset

    def test_price_row_rejects_time_window_suffix(self):
        with pytest.raises(EvidenceFormatError):
            price_row(1, dataset="KODEX 반도체 1분봉 · 08-07 09:00–13:20",
                     vendor="코스콤", as_of="08-07 13:22")

    @pytest.mark.parametrize("basis", CATALOG_HOLDING_BASES)
    def test_holding_row_accepts_every_catalog_source(self, basis):
        row = holding_row(2, as_of_basis="08-06", vendor=basis, as_of="08-07 06:30")
        assert row.content == "PDF 구성비중 · 08-06"

    @pytest.mark.parametrize("source_code", CATALOG_DISCLOSURE_SOURCES)
    def test_disclosure_row_accepts_all_source_codes(self, source_code):
        row = disclosure_row(3, title="단일판매·공급계약 해지", source_code=source_code,
                            rcept_no="20260807000412", published_at="08-07 10:31")
        assert source_code in row.source

    def test_disclosure_row_rejects_vendor_outside_catalog(self):
        with pytest.raises(EvidenceFormatError):
            disclosure_row(3, title="x", source_code="NAVER", rcept_no="1",
                          published_at="08-07")

    @pytest.mark.parametrize("publisher", CATALOG_NEWS_PUBLISHERS)
    def test_news_row_accepts_catalog_publishers(self, publisher):
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
