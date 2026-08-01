"""canonical(S3) PIT 표면 — **미래를 못 보게 하는 장치가 실제로 걸려 있는지.**

이 표면의 존재 이유는 시점 클램프 하나다. Cube 의 `*_latest` 를 쓰면 2026-07-16 을
설명하면서 그 뒤에 정정된 재무·수정된 컨센서스를 보게 되고, 그때 **에러가 나지 않는다.**
조용히 미래를 본 설명은 틀렸다는 표시조차 남기지 않으므로, 검사할 것은 "질의가 도는가"가
아니라 **모든 뷰에 클램프가 붙었는가**와 **클램프를 우회하는 경로가 막혔는가**다.

실 매니페스트(`infra/canonical/pit-manifest.yml`)와 손으로 쓴 최소 매니페스트 둘 다로
돌린다 - 실물은 실제 모양을 잡고, 최소본은 생성물이 없는 체크아웃에서도 가드를 잠근다.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from edge_analysis.adapters import canonical_surface as cs
from edge_analysis.adapters.sql_surface import SqlSurface
from edge_analysis.config import PipelineError

# 첫 CTE 는 `WITH ` 뒤에 붙는다 - 그 하나를 놓치면 "다 잘려 있다"를 20/21 로 잘못 센다.
_CTE = re.compile(r"(?m)^(?:WITH )?(v_[a-z0-9_]+) AS \($")

_REL = "infra/canonical/pit-manifest.yml"
_REAL = next((p / _REL for p in Path(__file__).resolve().parents if (p / _REL).exists()),
             None)


def _tiny_table(name: str, cols: list[str], note: str = "") -> dict:
    return {
        "name": f"v_{name}", "table": name, "group": "tiny", "note": note,
        "identity": [cols[0]],
        "columns": [{"name": c, "type": "string", "note": ""} for c in cols],
        # 생성기가 내는 것과 같은 모양: 클램프가 **SQL 문자열 안에** 있고 시점만 토큰이다.
        "pit_sql": (f"SELECT {', '.join(cols)}\n  FROM tiny_lake.{name}\n"
                    f"  WHERE available_at <= DATE '__AS_OF__'"),
    }


# 최소 매니페스트. 실 생성물이 없어도 가드·클램프·관계 절이 검사되도록 실물과 같은 키만
# 남기고 줄였다. 링크는 실물의 주장(끝 컬럼은 corp_code, 정체 키가 아니다)을 그대로 담는다.
_TINY: dict = {
    "database": "tiny_lake",
    "as_of_token": "__AS_OF__",
    "objects": [
        {"kind": "COMPANY_ENTITY", "view": "v_company_master", "key": "biz_reg_no",
         "alt_keys": ["corp_code"], "label": "corp_name"},
    ],
    "links": [
        {"role": "PARENT", "edge": "v_affiliation_edge",
         "from": {"kind": "COMPANY_ENTITY", "column": "parent_code",
                  "view": "v_company_master", "join_on": "corp_code"},
         "to": {"kind": "COMPANY_ENTITY", "column": "child_code",
                "view": "v_company_master", "join_on": "corp_code"},
         "resolved": True, "note": "corp_code 로 붙는다"},
        {"role": "SHAREHOLDER", "edge": "v_shareholder_stake",
         "from": {"kind": "COMPANY_ENTITY", "column": "entity",
                  "view": "v_company_master", "join_on": "corp_code"},
         "to": {"kind": "COMPANY_ENTITY", "column": "holder_id",
                "view": "v_company_master", "join_on": "vendor_code"},
         "resolved": False, "note": "이름만 있는 행이 많다"},
    ],
    "unbound": [{"kind": "COHORT", "why": "코호트 테이블이 canonical 에 없다"}],
    "tables": [
        _tiny_table("financial_metric", ["entity", "metric_code", "value"], "재무 지표"),
        _tiny_table("consensus_point", ["entity", "metric_code", "estimate"]),
        _tiny_table("affiliation_edge", ["parent_code", "child_code", "equity_pct"]),
        _tiny_table("company_master", ["biz_reg_no", "corp_code", "corp_name"]),
    ],
}


@pytest.fixture(params=["real", "tiny"])
def manifest(request) -> dict:
    """두 매니페스트로 같은 검사를 돌린다 - 가드가 실물의 우연에 기대면 안 된다."""
    if request.param == "tiny":
        return _TINY
    if _REAL is None:
        pytest.skip(f"{_REL} 이 없다 - data-pipeline 의 `pit --out` 으로 생성한다")
    return cs.load_manifest(_REAL)


class _Runner:
    """실행기 스텁. **검사 대상은 결과가 아니라 실제로 보내진 문장이다.**"""

    def __init__(self, rows: list[dict] | None = None, boom: Exception | None = None):
        self.rows = rows if rows is not None else []
        self.boom = boom
        self.sql = ""

    def __call__(self, sql: str) -> list[dict]:
        self.sql = sql
        if self.boom is not None:
            raise self.boom
        return self.rows


def _surface(manifest: dict, runner: _Runner | None = None, as_of="2026-07-16"):
    return cs.CanonicalSurface(runner or _Runner(), manifest, as_of=as_of)


# ── 시점 클램프 ──────────────────────────────────────────────────────────────
def test_every_view_is_clamped_to_the_as_of_day(manifest):
    """**이 모듈의 존재 이유다.** 한 표만 클램프가 빠져도 그 표는 미래를 보고,

    에러가 안 나므로 결과를 읽어서는 알 수 없다. 그래서 CTE 수와 클램프 수를 맞춘다.
    """
    r = _Runner()
    _surface(manifest, r).query("SELECT 1")
    ctes = _CTE.findall(r.sql)
    assert set(ctes) == {t["name"] for t in manifest["tables"]}
    assert len(ctes) == r.sql.count("available_at <= DATE '2026-07-16'"), (
        "CTE 하나에 클램프 하나여야 한다")
    assert "__AS_OF__" not in r.sql, "치환 안 된 토큰은 Athena 에서 그대로 날짜가 된다"


def test_a_date_object_is_accepted_as_the_as_of(manifest):
    """호출부가 `date` 를 들고 있는 자리가 많다 - 문자열 변환을 부르는 쪽에 미루지 않는다."""
    r = _Runner()
    _surface(manifest, r, as_of=date(2026, 7, 16)).query("SELECT 1")
    assert "available_at <= DATE '2026-07-16'" in r.sql


@pytest.mark.parametrize("bad", ["2026-7-16", "'; DROP--", "20260716", "", "어제",
                                 "2026/07/16", "16-07-2026"])
def test_an_as_of_that_is_not_an_iso_day_is_refused(manifest, bad):
    """토큰이 **문자열 치환**이라 여기가 유일한 방어선이다. 날짜가 아니면 그대로 SQL 이
    되고, `2026-7-16` 처럼 0 패딩만 빠져도 Athena 는 다른 날을 읽거나 조용히 실패한다."""
    with pytest.raises(PipelineError, match="as_of"):
        _surface(manifest, as_of=bad)


def test_trailing_garbage_after_the_day_is_cut_not_interpolated(manifest):
    """열 글자로 자르므로 뒤에 붙인 것은 SQL 에 닿지 못한다 - 잘림이 곧 방어다."""
    r = _Runner()
    _surface(manifest, r, as_of="2026-07-16'; DROP TABLE t --").query("SELECT 1")
    assert "available_at <= DATE '2026-07-16'" in r.sql
    assert "DROP" not in r.sql


# ── 가드 ─────────────────────────────────────────────────────────────────────
def test_a_base_table_cannot_be_queried_directly(manifest):
    """**클램프를 우회하는 유일한 경로다.** `financial_metric` 을 그냥 부르면 정정본까지
    다 보이고, 그것은 미래를 본 것이다."""
    with pytest.raises(PipelineError, match="직접 접근 금지"):
        _surface(manifest).query("SELECT * FROM financial_metric")


def test_the_clamped_view_of_the_same_table_is_allowed(manifest):
    """가드가 넓으면 정상 질의까지 막혀 표면이 무용지물이 된다 - 막는 것과 여는 것이 짝이다."""
    r = _Runner()
    assert _surface(manifest, r).query("SELECT * FROM v_financial_metric") == []
    assert r.sql, "가드를 통과했으면 실행기까지 가야 한다"


@pytest.mark.parametrize("sql", [
    "SELECT 1 -- 주석으로 뒤를 가린다",
    "SELECT /* 가린다 */ 1",
    "SELECT 1; SELECT 2",
    "SELECT * FROM (UNLOAD ('SELECT 1') TO 's3://bucket/x')",
    "SELECT * FROM (CREATE TABLE t (x int))",
    "SELECT * FROM (DROP TABLE v_financial_metric)",
])
def test_tokens_that_break_the_read_only_contract_are_refused(manifest, sql):
    """SELECT 로 시작한다고 읽기 전용이 아니다 - 주석은 뒤를 가리고, 세미콜론은 문장을
    하나 더 붙이고, UNLOAD 는 레이크에 쓴다."""
    with pytest.raises(PipelineError):
        _surface(manifest).query(sql)


def test_an_empty_query_is_refused(manifest):
    with pytest.raises(PipelineError, match="빈 질의"):
        _surface(manifest).query("   ")


# ── 원장 ─────────────────────────────────────────────────────────────────────
def test_a_failed_query_stays_in_the_ledger_with_its_error(manifest):
    """**거부된 질의와 안 던진 질의는 다르다.** 원장에 안 남기면 둘 다 "부재"로 보이고,

    모델이 무엇을 물으려다 막혔는지가 곧 표면의 결함 목록인데 그 목록이 사라진다.
    """
    s = _surface(manifest, _Runner(boom=RuntimeError("Athena FAILED")))
    with pytest.raises(PipelineError):
        s.query("SELECT * FROM v_financial_metric")
    assert len(s.ledger.calls) == 1
    call = s.ledger.calls[-1]
    assert call["rows"] == 0
    assert "RuntimeError" in call["error"] and "Athena FAILED" in call["error"]
    assert s.ledger.queries == ["SELECT * FROM v_financial_metric"]


def test_a_successful_query_records_how_many_rows_came_back(manifest):
    """행수가 남아야 "물어봤는데 0행"과 "안 물어봤다"를 P8 이 구분한다."""
    s = _surface(manifest, _Runner(rows=[{"a": 1}, {"a": 2}]))
    s.query("SELECT * FROM v_financial_metric")
    assert s.ledger.calls[-1] == {"query": "SELECT * FROM v_financial_metric",
                                  "rows": 2, "error": ""}


# ── ask ──────────────────────────────────────────────────────────────────────
def test_ask_turns_a_refusal_into_a_sentence(manifest):
    """한 번 틀렸다고 세션이 죽으면 남은 가설을 못 물어본다 - 고쳐 쓸 기회를 준다."""
    got = _surface(manifest).ask("SELECT * FROM financial_metric")
    assert got.startswith("오류:") and "직접 접근 금지" in got


def test_ask_says_that_absence_is_also_an_observation(manifest):
    """0행을 침묵으로 돌려주면 모델이 "자료가 없다"와 "그런 일이 없었다"를 섞는다."""
    assert "없다는 것도 관측이다" in _surface(manifest).ask("SELECT 1")


def test_ask_lays_the_rows_out_with_a_header(manifest):
    r = _Runner(rows=[{"corp": "A", "pct": 30.0}])
    assert _surface(manifest, r).ask("SELECT 1").splitlines() == ["corp | pct", "A | 30"]


# ── 표면 설명 ────────────────────────────────────────────────────────────────
def test_the_schema_carries_the_join_columns(manifest):
    """`parent_code` 는 `corp_code` 에 붙는다. 정체 키(`biz_reg_no`)로 조인하면 **에러 없이
    0행**이고, 그 0행은 "계열 관계가 없다"로 잘못 읽힌다 - 이 절이 그걸 막는다."""
    text = _surface(manifest).schema()
    assert "parent_code -> v_company_master.corp_code" in text
    assert "정체 키로 조인하지 마라" in text
    assert not re.search(r"-> v_[a-z_]+\.biz_reg_no", text), (
        "정체 키를 조인 대상으로 실으면 이 절이 오히려 0행을 부른다")


def test_an_unresolved_edge_says_so(manifest):
    """미해소 간선을 표시하지 않으면 빈 조인 결과를 "관계가 없다"로 오해한다."""
    assert "[미해소" in _surface(manifest).schema()


def test_the_schema_names_every_view_and_its_columns(manifest):
    """손으로 쓴 설명은 컬럼이 바뀌어도 안 바뀐다 - 그러면 모델이 없는 컬럼을 부르고,
    그 실패가 "자료가 없다"로 읽힌다. 그래서 매니페스트에서 생성한다."""
    text = _surface(manifest).schema()
    for t in manifest["tables"]:
        assert t["name"] in text
        assert t["columns"][0]["name"] in text


def test_the_schema_declares_the_vocabulary_that_has_no_table(manifest):
    """없는 것을 없다고 적지 않으면 모델이 계속 그걸 묻고 매번 빈손으로 돌아온다."""
    assert "테이블이 아직 없는 것" in _surface(manifest).schema()


# ── 매니페스트 적재 ──────────────────────────────────────────────────────────
def test_a_missing_manifest_is_loud(tmp_path):
    """조용히 비우면 표면이 그냥 없는 것이 되고, 그 부재가 "자료가 없다"로 기록된다."""
    with pytest.raises(PipelineError, match="매니페스트가 없다"):
        cs.load_manifest(tmp_path / "pit-manifest.yml")


def test_a_manifest_without_tables_is_refused(tmp_path):
    p = tmp_path / "pit-manifest.yml"
    p.write_text("database: x\ntables: []\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="비었거나"):
        cs.load_manifest(p)
    with pytest.raises(PipelineError, match="테이블이 없다"):
        cs.CanonicalSurface(_Runner(), {"tables": []}, as_of="2026-07-16")


# ── 두 표면 라우팅 ───────────────────────────────────────────────────────────
class _FakePrimary:
    """Postgres 표면 대역. 라우팅은 **뷰 이름**만 보므로 이름만 있으면 된다."""

    def __init__(self, schema: str = "v_daily · v_flow · v_event · v_instrument"):
        self._schema = schema
        self.ledger = "pg-ledger"

    def schema(self) -> str:
        return self._schema

    def query(self, sql: str, *, limit: int = cs.MAX_ROWS) -> list[dict]:
        return [{"surface": "pg"}]

    def ask(self, sql: str, *, show: int = 20) -> str:
        return "pg"


@pytest.mark.parametrize("sql,where", [
    ("SELECT * FROM v_daily WHERE r > 0.05", "pg"),
    ("SELECT * FROM v_flow", "pg"),
    ("SELECT * FROM v_event JOIN v_daily USING (trade_date)", "pg"),
    ("SELECT * FROM v_financial_metric", "canonical"),
    ("SELECT * FROM v_affiliation_edge", "canonical"),
    ("SELECT count(*) FROM v_consensus_point", "canonical"),
])
def test_routing_is_decided_by_the_view_name(manifest, sql, where):
    """에이전트는 표면 하나를 본다. 어느 저장소인지 알게 하면 "어디에 물어야 하나"라는
    없는 문제를 풀게 되고, 라우팅이 확률적이면 같은 질의가 날마다 다른 곳에 간다."""
    r = _Runner(rows=[{"surface": "canonical"}])
    s = cs.Surfaces(_FakePrimary(), _surface(manifest, r))
    assert s.query(sql)[0]["surface"] == where


def test_a_name_collision_is_refused_at_construction(manifest):
    """조용히 한쪽을 고르면 같은 이름이 날마다 다른 표를 가리킬 수 있다 - 그 질의는
    틀린 곳을 읽고도 성공으로 보인다."""
    clashing = _FakePrimary("v_daily · v_financial_metric 도 여기 있다")
    with pytest.raises(PipelineError, match="겹친다"):
        cs.Surfaces(clashing, _surface(manifest))


def test_the_two_real_surfaces_do_not_share_a_view_name():
    """실물끼리의 불변식이다 - 표를 하나 늘릴 때 이름이 겹치면 여기서 걸려야 한다."""
    if _REAL is None:
        pytest.skip(f"{_REL} 이 없다")
    pg = SqlSurface(None, as_of="2026-07-16", trade_date=date(2026, 7, 16))
    canonical = _surface(cs.load_manifest(_REAL))
    assert not (frozenset(canonical._views) & cs._view_names(pg.schema()))
    cs.Surfaces(pg, canonical)


def test_without_canonical_the_vocabulary_is_absent_not_empty(manifest):
    """미배선을 "자료 없음"으로 적으면 P8 커버리지 원장이 거짓말을 한다 - 안 물어본 것과
    물어봤는데 없는 것은 다르다."""
    alone = cs.Surfaces(_FakePrimary())
    assert alone.missing and "미배선" in alone.missing[0]
    assert "v_financial_metric" not in alone.schema()
    assert alone.query("SELECT * FROM v_financial_metric")[0]["surface"] == "pg"

    wired = cs.Surfaces(_FakePrimary(), _surface(manifest))
    assert wired.missing == []
    assert "v_financial_metric" in wired.schema()
