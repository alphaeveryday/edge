"""Fixture-backed tool surface shared by agent execution and tests."""
from copy import deepcopy
from uuid import uuid4

from edge_analysis_v2.tools.fixture_data.common import available, holdings, instant, table
from edge_analysis_v2.tools.fixture_data import instrument_factors, chart, flow, macro, news, valuation, financial_observations
from edge_analysis_v2.tools.fixture_data.demo import make_fixture
from edge_analysis_v2.tools.fixture_data.replay import make_replay_fixture


class FixtureTools:
    """Expose deterministic calculations independently from audit persistence.

    Args:
        fixture: Raw observations and a fixed context; copied at construction.
    """

    final_tool_names = {"get_issue_evidence", "get_etf_holdings", "calculate_investor_flow", "calculate_weighted_flow", "calculate_chart_indicators", "evaluate_indicator_transition", "compare_macro_observations", "calculate_valuation", "calculate_weighted_valuation", "get_instrument_factors", "compare_financial_observations", "calculate_valuation_range"}

    def __init__(self, fixture):
        self.fixture = deepcopy(fixture)
        instant(self.fixture["context"]["analysis_at"])
        self._tools = {}
        self._register('get_financial_observations', '기업의 공개 실적·예상 이력을 표로 읽습니다. 실제/예상, 대상 연도, 작성자와 기사 ID를 보존합니다. 비어 있으면 전망을 만들어 채우지 마세요. 비교는 compare_financial_observations, 조건부 가격 계산은 calculate_valuation_range를 사용합니다.', {'instrument_id':{'type':'string'}}, lambda **args:financial_observations.read(self.fixture, **args), '', ['공개 실적·예상 자료'])
        self._register('compare_financial_observations', '같은 기업·지표·단위·대상 기간의 두 원천 값을 비교합니다. get_financial_observations의 ID를 사용하세요. 예상 수정은 실제 실적 증가가 아닙니다. 예: previous_id=eps-old, current_id=eps-new.', {'previous_id':{'type':'string'},'current_id':{'type':'string'}}, lambda **args:financial_observations.compare(self.fixture, **args), r'\Delta=C-P;\ r=100(C-P)/P\quad(P>0)', ['공개 실적·예상 자료'])
        self._register('calculate_valuation_range', '공개 연간 EPS와 선택한 PER 배수로 개별 종목의 조건부 가격 범위와 현재 PER을 계산합니다. eps_id는 get_financial_observations에서 선택하고, per_low/high는 원문으로 정당화한 가정입니다. 배수 근거도 함께 인용하세요. return_low/high_pct는 각 가정 가격까지의 변화율이며 최대 손실·최대 수익이 아닙니다. 양수는 현재가 위입니다. 밴드 밖으로도 주가가 움직일 수 있습니다. 현재 PER이 과거보다 낮다고 시장 미반영이 입증되지는 않습니다.', {'eps_id':{'type':'string'},'per_low':{'type':'number','exclusiveMinimum':0},'per_high':{'type':'number','exclusiveMinimum':0}}, lambda **args:financial_observations.valuation_range(self.fixture, **args), r'P_{lo}=EPS\times PER_{lo};\ P_{hi}=EPS\times PER_{hi};\ r=100(P/P_{close}-1);\ PER_{current}=P_{close}/EPS', ['공개 연간 EPS 자료','종목 확정 종가','분석자가 선택한 PER 가정'], version='v2')
        self._register("search_news_threads", "공개된 뉴스의 단계별 사건과 중복 보도 수를 탐색합니다. 기사 근거는 get_issue_evidence로 확보하세요.", {}, lambda: news.search(self.fixture), "", ["뉴스 기사"])
        self._register("get_issue_evidence", "기사 ID로 원문을 읽습니다. include_body=true는 탐색, false는 최종 문장의 기사 근거입니다.", {"news_ids": {"type": "array", "items": {"type": "string"}}, "include_body": {"type": "boolean"}}, lambda **args: news.evidence(self.fixture, **args), "", ["뉴스 기사"])
        self._register("get_etf_holdings", "분석 시각까지 공개된 전체 구성종목과 비중을 확인합니다. 일부 종목을 전체로 환산하지 않습니다.", {}, lambda: holdings(self.fixture), r"\sum_i w_i=1", ["ETF 구성종목 비중"])
        parameters = {"investor": {"type": "string", "enum": ["foreign", "institution", "individual"]}, "lookback_days": {"type": "integer", "minimum": 1, "maximum": 30}, "operation": {"type": "string", "enum": ["sum", "frequency", "streak"]}, "direction": {"type": "string", "enum": ["net_buy", "net_sell", "none"]}}
        for name, extra in (('sum_investor_net_flow',{'instrument_id':{'type':'string'}}), ('sum_weighted_net_flow',{})):
            self._register(name, '확정된 최근 N거래일의 순매수 금액을 부호 그대로 합산합니다. 양수 순매수, 음수 순매도. 매수일만 골라 더하지 않습니다. weighted는 전체 구성종목 비중을 반영한 값입니다. 예: investor=foreign, lookback_days=5.', {k:parameters[k] for k in ('investor','lookback_days')} | extra, lambda **args:flow.calculate(self.fixture,operation='sum',direction='none',**args), r'S=\sum_d x_d;\ S_w=\sum_d\sum_iw_{i,d}x_{i,d}', ['투자자별 확정 순매수','일별 ETF 구성종목 비중'])
        self.final_tool_names = self.final_tool_names | {'sum_investor_net_flow','sum_weighted_net_flow'}
        for name, extra in (("calculate_investor_flow", {"instrument_id": {"type": "string"}}), ("calculate_weighted_flow", {})):
            self._register(name, "확정 거래일의 순매수 합계·빈도·최신일부터 연속을 계산합니다. sum은 direction=none. 가중 수급은 ETF 구성종목 기준이며 ETF 자체 거래가 아닙니다. exact=false인 연속은 최소 일수입니다.", parameters | extra, lambda **args: flow.calculate(self.fixture, **args), r"F_d=\sum_iw_{i,d}x_{i,d};\ S=\sum_dF_d;\ M=\sum_d[sF_d>0]", ["투자자별 확정 순매수", "일별 ETF 구성종목 비중"])
        self._register("calculate_chart_indicators", "ETF 가격으로 RSI14 모멘텀과 반전 Williams14 바닥지수를 계산합니다. 높은 바닥지수는 과매도 관찰이며 반등확률이 아닙니다.", {}, lambda: chart.indicators(self.fixture), r"RSI=100G/(G+L);\ B=100(H_{14}-P)/(H_{14}-L_{14})", ["ETF 일봉과 장중 가격"])
        self._register("evaluate_indicator_transition", "최근 지수 관측의 80/20 진입·이탈을 확인합니다. 구간 유지에는 최근5개 관측이 모두 필요합니다.", {"indicator": {"type": "string", "enum": ["momentum", "bottom"]}}, lambda **args: chart.transition(self.fixture, **args), r"U(x)=[x\ge80];\ L(x)=[x\le20]", ["ETF 일봉과 장중 가격"], version="v2")
        series = {"type": "string", "enum": list(macro.SERIES)}
        self._register("get_macro_observations", "거시지표의 공개된 최근21개 관측값을 탐색합니다. 수치 비교 근거는 compare_macro_observations로 확정합니다.", {"series": series}, lambda **args: macro.read(self.fixture, **args), "", ["목 거시경제 관측"], version="v2")
        self._register("compare_macro_observations", "같은 지표의 두 정확한 관측을 비교합니다. 금리·물가의 차이는 %p, 상대변화는 %입니다. 기업이나 ETF 영향은 계산하지 않습니다.", {"series": series, "previous_at": {"type": "string"}, "current_at": {"type": "string"}, "operation": {"type": "string", "enum": ["difference", "percent_change"]}}, lambda **args: macro.compare(self.fixture, **args), r"D=C-P;\ R=100(C/P-1)", ["목 거시경제 관측"])
        self._register("calculate_valuation", "개별 종목의 공개4분기 EPS와 최신 BPS로 PER·PBR을 계산합니다. 양수 분모만 지원하며 자료 누락·적자를 중립으로 바꾸지 않습니다.", {"instrument_id": {"type": "string"}}, lambda **args: valuation.calculate(self.fixture, **args), r"PER=P/\sum_{q=1}^{4}EPS_q;\ PBR=P/BPS", ["종목 종가", "공개 분기 EPS와 BPS"])
        self._register("calculate_weighted_valuation", "전체 구성종목의 PER·PBR을 편입비중으로 가중평균합니다. 비중 합1과 전 종목 유효값이 필요합니다.", {}, lambda: valuation.weighted(self.fixture), r"\bar x=\sum_iw_ix_i", ["종목 종가", "공개 분기 EPS와 BPS", "ETF 구성종목 비중"])

        self._register('get_instrument_factors',
            '종목의 차트·확정 수급·밸류·공통 매크로를 읽고 다음 조사 대상을 고릅니다. instrument_id는 초기 종목 목록에서 선택합니다. factors를 생략하면 전체, 지정하면 해당 요인만 반환합니다. 예: factors=["flow","valuation"]. 이력은 columns/rows, 시점은 이번 분석에 고정됩니다. ETF 수급·밸류는 구성종목 가중값입니다. null은 미확보입니다. 반환값은 근거로 사용 가능하고 이력의 새 계산은 해당 계산 툴로 확인합니다.',
            {'instrument_id': {'type': 'string', 'minLength': 1},
             'factors': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                         'items': {'type': 'string', 'enum': list(instrument_factors.FACTORS)}}},
            lambda **args: instrument_factors.read(self.fixture, **args),
            instrument_factors.FORMULA_LATEX, ['종목 가격', '확정 수급', '구성종목 비중', '공개 재무', '거시 관측', 'ETF 분배금·좌수'],
            required=['instrument_id'])

    def _register(self, name, description, parameters, callback, formula, sources, *, version="v1", required=None):
        self._tools[name] = {"description": description, "parameters": parameters, "callback": callback, "formula": formula, "sources": sources, "version": version, "required": list(parameters) if required is None else required}

    @property
    def schemas(self):
        """Return strict required-argument function schemas."""
        return [{"type": "function", "function": {"name": name, "description": ("[최종 근거 가능] " if name in self.final_tool_names else "[탐색 전용 · 이 호출 ID는 최종 근거 불가] ") + tool["description"], "parameters": {"type": "object", "properties": tool["parameters"], "required": tool["required"], "additionalProperties": False}}} for name, tool in self._tools.items()]

    @property
    def definitions(self):
        """Return immutable administrator-readable calculation definitions."""
        return [{"tool_id": f"{name}:{tool['version']}", "function_name": name, "version": tool["version"], "description": tool["description"], "formula_latex": tool["formula"], "source_names": tool["sources"]} for name, tool in self._tools.items()]

    def call(self, name, arguments):
        """Calculate once and return the exact object to be stored and shown."""
        if name not in self._tools or not isinstance(arguments, dict):
            raise ValueError("unknown tool or invalid arguments")
        tool = self._tools[name]
        if not set(tool["required"]) <= set(arguments) <= set(tool["parameters"]):
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
        price_tables = {target: table([r for r in prices if r["instrument_id"] == target],
                                     ["date", "high", "low", "close", "volume", "turnover"])
                        for target in sorted({r["instrument_id"] for r in prices})}
        snapshots = {"instrument_id": context["etf_code"],
                     **table([r | {"at": r["observed_at"]} for r in chart.snapshots(self.fixture)],
                             ["at", "price", "high", "low", "available_at"])}
        catalog = sorted({(r['instrument_id'],r['metric'],r['period']) for r in financial_observations.visible(self.fixture)})
        exploration = {'financial_observation_catalog':table([dict(zip(['instrument_id','metric','period'],r)) for r in catalog], ['instrument_id','metric','period'])} if 'financial_observations' in self.fixture else {}
        return exploration | {"context": deepcopy(context), "instruments": deepcopy(self.fixture.get("instruments", [])), "holdings": holdings(self.fixture), "news": [{k: r[k] for k in ("news_id", "title", "published_at")} for r in news.visible(self.fixture)[:100]], "flow": flow.input_tables(rows), "prices": price_tables, "price_snapshots": snapshots, "macro": {series: macro.read(self.fixture, series) for series in macro.SERIES}, "financials": table(financials, ["instrument_id", "period", "eps", "bps", "eps_derivation", "available_at"]), "etf_units": table(units, ["date", "units", "available_at"]), "distributions": table(distributions, ["paid_at", "amount_per_unit", "available_at"]), "previous_analysis": deepcopy(self.fixture.get("previous_analysis"))}
