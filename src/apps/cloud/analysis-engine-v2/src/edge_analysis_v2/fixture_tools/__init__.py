"""Fixture-backed tool surface shared by agent execution and tests."""
from copy import deepcopy
from uuid import uuid4

from .common import available, holdings, instant, table
from . import chart, factors, flow, macro, news, valuation
from .demo import make_fixture


class FixtureTools:
    """Expose deterministic calculations independently from audit persistence.

    Args:
        fixture: Raw observations and a fixed context; copied at construction.
    """

    final_tool_names = {"get_issue_evidence", "get_etf_holdings", "calculate_investor_flow", "calculate_weighted_flow", "calculate_chart_indicators", "evaluate_indicator_transition", "compare_macro_observations", "calculate_valuation", "calculate_weighted_valuation", "get_factor_metrics"}

    def __init__(self, fixture):
        self.fixture = deepcopy(fixture)
        instant(self.fixture["context"]["analysis_at"])
        self._tools = {}
        self._register("search_news_threads", "공개된 뉴스의 단계별 사건과 중복 보도 수를 탐색합니다. 기사 근거는 get_issue_evidence로 확보하세요.", {}, lambda: news.search(self.fixture), "", ["뉴스 기사"])
        self._register("get_issue_evidence", "기사 ID로 원문을 읽습니다. include_body=true는 탐색, false는 최종 문장의 기사 근거입니다.", {"news_ids": {"type": "array", "items": {"type": "string"}}, "include_body": {"type": "boolean"}}, lambda **args: news.evidence(self.fixture, **args), "", ["뉴스 기사"])
        self._register("get_etf_holdings", "분석 시각까지 공개된 전체 구성종목과 비중을 확인합니다. 일부 종목을 전체로 환산하지 않습니다.", {}, lambda: holdings(self.fixture), r"\sum_i w_i=1", ["ETF 구성종목 비중"])
        parameters = {"investor": {"type": "string", "enum": ["foreign", "institution", "individual"]}, "lookback_days": {"type": "integer", "minimum": 1, "maximum": 30}, "operation": {"type": "string", "enum": ["sum", "frequency", "streak"]}, "direction": {"type": "string", "enum": ["net_buy", "net_sell", "none"]}}
        for name, extra in (("calculate_investor_flow", {"instrument_id": {"type": "string"}}), ("calculate_weighted_flow", {})):
            self._register(name, "확정 거래일의 순매수 합계·빈도·최신일부터 연속을 계산합니다. sum은 direction=none. 가중 수급은 ETF 구성종목 기준이며 ETF 자체 거래가 아닙니다. exact=false인 연속은 최소 일수입니다.", parameters | extra, lambda **args: flow.calculate(self.fixture, **args), r"F_d=\sum_iw_{i,d}x_{i,d};\ S=\sum_dF_d;\ M=\sum_d[sF_d>0]", ["투자자별 확정 순매수", "일별 ETF 구성종목 비중"])
        self._register("calculate_chart_indicators", "ETF 가격으로 RSI14 모멘텀과 반전 Williams14 바닥지수를 계산합니다. 높은 바닥지수는 과매도 관찰이며 반등확률이 아닙니다.", {}, lambda: chart.indicators(self.fixture), r"RSI=100G/(G+L);\ B=100(H_{14}-P)/(H_{14}-L_{14})", ["ETF 일봉과 장중 가격"])
        self._register("evaluate_indicator_transition", "최근 지수 관측의 80/20 진입·이탈을 확인합니다. 구간 유지에는 최근5개 관측이 모두 필요합니다.", {"indicator": {"type": "string", "enum": ["momentum", "bottom"]}}, lambda **args: chart.transition(self.fixture, **args), r"U(x)=[x\ge80];\ L(x)=[x\le20]", ["ETF 일봉과 장중 가격"])
        series = {"type": "string", "enum": list(macro.SERIES)}
        self._register("get_macro_observations", "거시지표의 공개된 최근21개 관측값을 탐색합니다. 수치 비교 근거는 compare_macro_observations로 확정합니다.", {"series": series}, lambda **args: macro.read(self.fixture, **args), "", ["목 거시경제 관측"])
        self._register("compare_macro_observations", "같은 지표의 두 정확한 관측을 비교합니다. 금리·물가의 차이는 %p, 상대변화는 %입니다. 기업이나 ETF 영향은 계산하지 않습니다.", {"series": series, "previous_at": {"type": "string"}, "current_at": {"type": "string"}, "operation": {"type": "string", "enum": ["difference", "percent_change"]}}, lambda **args: macro.compare(self.fixture, **args), r"D=C-P;\ R=100(C/P-1)", ["목 거시경제 관측"])
        self._register("calculate_valuation", "개별 종목의 공개4분기 EPS와 최신 BPS로 PER·PBR을 계산합니다. 양수 분모만 지원하며 자료 누락·적자를 중립으로 바꾸지 않습니다.", {"instrument_id": {"type": "string"}}, lambda **args: valuation.calculate(self.fixture, **args), r"PER=P/\sum_{q=1}^{4}EPS_q;\ PBR=P/BPS", ["종목 종가", "공개 분기 EPS와 BPS"])
        self._register("calculate_weighted_valuation", "전체 구성종목의 PER·PBR을 편입비중으로 가중평균합니다. 비중 합1과 전 종목 유효값이 필요합니다.", {}, lambda: valuation.weighted(self.fixture), r"\bar x=\sum_iw_ix_i", ["종목 종가", "공개 분기 EPS와 BPS", "ETF 구성종목 비중"])
        self._register("get_factor_metrics", "요인 상세 화면의 계산 가능한 지표와 관측시각을 확정합니다. 이동평균은 현재가 반영, 신고가는 확정 종가 비교, 거래대금은 전일/직전20일 평균, ATR은 첫14일 평균 시드 후 Wilder입니다. 금리 일정은 한국 날짜 차이, 환율·금리·브렌트는 최신 관측값입니다. 분배율은12개월 실제 지급액/현재가, 좌수변화는20확정일 전과 비교합니다. 상태·스티커·전망 판단은 포함하지 않습니다.", {"type": {"type": "string", "enum": ["차트", "매크로", "밸류", "수급"]}}, lambda **args: factors.metrics(self.fixture, **args), factors.FORMULA_LATEX, ["ETF 가격", "거시 관측", "공개 재무", "확정 수급", "구성종목 비중", "ETF 분배금 지급", "ETF 발행좌수"])

    def _register(self, name, description, parameters, callback, formula, sources):
        self._tools[name] = {"description": description, "parameters": parameters, "callback": callback, "formula": formula, "sources": sources}

    @property
    def schemas(self):
        """Return strict required-argument function schemas."""
        return [{"type": "function", "function": {"name": name, "description": tool["description"], "parameters": {"type": "object", "properties": tool["parameters"], "required": list(tool["parameters"]), "additionalProperties": False}}} for name, tool in self._tools.items()]

    @property
    def definitions(self):
        """Return immutable administrator-readable calculation definitions."""
        return [{"tool_id": f"{name}:v1", "function_name": name, "version": "v1", "description": tool["description"], "formula_latex": tool["formula"], "source_names": tool["sources"]} for name, tool in self._tools.items()]

    def call(self, name, arguments):
        """Calculate once and return the exact object to be stored and shown."""
        if name not in self._tools or not isinstance(arguments, dict):
            raise ValueError("unknown tool or invalid arguments")
        tool = self._tools[name]
        if set(arguments) != set(tool["parameters"]):
            raise ValueError("arguments must exactly match the tool schema")
        result = tool["callback"](**deepcopy(arguments))
        return {"tool_run_id": uuid4().hex, "result": result}

    def initial_input(self):
        """Return bounded raw data, without model answers or reference labels."""
        context = self.fixture["context"]
        dates = sorted({r["date"] for r in self.fixture.get("flow", []) if r["date"] <= context["flow_as_of_date"]})[-30:]
        rows = [r for r in available(self.fixture.get("flow", []), instant(context["analysis_at"])) if r["date"] in dates and r.get("finalized") is True]
        cutoff = instant(context["analysis_at"])
        prices = []
        source_prices = [r for r in available(self.fixture.get("prices", []), cutoff) if r["date"] < cutoff.date().isoformat()]
        for target in sorted({r["instrument_id"] for r in source_prices}):
            prices.extend(sorted([r for r in source_prices if r["instrument_id"] == target], key=lambda r: r["date"])[-40:])
        financials = []
        source_financials = available(self.fixture.get("financials", []), cutoff)
        for target in sorted({r["instrument_id"] for r in source_financials}):
            latest = {}
            for row in sorted([r for r in source_financials if r["instrument_id"] == target], key=lambda r: instant(r["available_at"])):
                latest[row["period"]] = row
            financials.extend(latest[p] for p in sorted(latest)[-4:])
        units = sorted([r for r in available(self.fixture.get("etf_units", []), cutoff) if r["date"] < cutoff.date().isoformat()], key=lambda r: r["date"])[-21:]
        distributions = available(self.fixture.get("distributions", []), cutoff, "paid_at")
        return {"context": deepcopy(context), "instruments": deepcopy(self.fixture.get("instruments", [])), "holdings": holdings(self.fixture), "news": [{k: r[k] for k in ("news_id", "title", "published_at")} for r in news.visible(self.fixture)[:100]], "flow": table(rows, ["instrument_id", "date", "investor", "net_amount_krw"]), "prices": table(prices, ["instrument_id", "date", "high", "low", "close", "volume", "turnover"]), "price_snapshots": chart.snapshots(self.fixture), "macro": {series: macro.read(self.fixture, series) for series in macro.SERIES}, "financials": table(financials, ["instrument_id", "period", "eps", "bps", "available_at"]), "etf_units": table(units, ["date", "units", "available_at"]), "distributions": table(distributions, ["paid_at", "amount_per_unit", "available_at"]), "previous_analysis": deepcopy(self.fixture.get("previous_analysis"))}
