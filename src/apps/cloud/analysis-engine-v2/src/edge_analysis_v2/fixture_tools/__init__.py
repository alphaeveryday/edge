"""Fixture-backed tool surface shared by agent execution and tests."""
from copy import deepcopy
from uuid import uuid4

from .common import holdings, instant
from . import news


class FixtureTools:
    """Expose deterministic calculations independently from audit persistence.

    Args:
        fixture: Raw observations and a fixed context; copied at construction.
    """

    final_tool_names = {"get_issue_evidence", "get_etf_holdings"}

    def __init__(self, fixture):
        self.fixture = deepcopy(fixture)
        instant(self.fixture["context"]["analysis_at"])
        self._tools = {}
        self._register("search_news_threads", "공개된 뉴스의 단계별 사건과 중복 보도 수를 탐색합니다. 기사 근거는 get_issue_evidence로 확보하세요.", {}, lambda: news.search(self.fixture), "", ["뉴스 기사"])
        self._register("get_issue_evidence", "기사 ID로 원문을 읽습니다. include_body=true는 탐색, false는 최종 문장의 기사 근거입니다.", {"news_ids": {"type": "array", "items": {"type": "string"}}, "include_body": {"type": "boolean"}}, lambda **args: news.evidence(self.fixture, **args), "", ["뉴스 기사"])
        self._register("get_etf_holdings", "분석 시각까지 공개된 전체 구성종목과 비중을 확인합니다. 일부 종목을 전체로 환산하지 않습니다.", {}, lambda: holdings(self.fixture), r"\sum_i w_i=1", ["ETF 구성종목 비중"])

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
        return {"context": deepcopy(self.fixture["context"]), "holdings": holdings(self.fixture), "news": [{k: r[k] for k in ("news_id", "title", "published_at")} for r in news.visible(self.fixture)[:100]], "previous_analysis": deepcopy(self.fixture.get("previous_analysis"))}
