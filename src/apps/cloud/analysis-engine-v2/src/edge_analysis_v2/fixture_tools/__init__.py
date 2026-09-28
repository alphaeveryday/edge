"""Fixture-backed tool surface shared by agent execution and tests."""
from copy import deepcopy
from uuid import uuid4

from .common import available, holdings, instant, table
from . import chart, flow, news


class FixtureTools:
    """Expose deterministic calculations independently from audit persistence.

    Args:
        fixture: Raw observations and a fixed context; copied at construction.
    """

    final_tool_names = {"get_issue_evidence", "get_etf_holdings", "calculate_investor_flow", "calculate_weighted_flow", "calculate_chart_indicators", "evaluate_indicator_transition"}

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
        return {"context": deepcopy(context), "holdings": holdings(self.fixture), "news": [{k: r[k] for k in ("news_id", "title", "published_at")} for r in news.visible(self.fixture)[:100]], "flow": table(rows, ["instrument_id", "date", "investor", "net_amount_krw"]), "previous_analysis": deepcopy(self.fixture.get("previous_analysis"))}
