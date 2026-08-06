"""근거 묶음 적재 배선 — 꼬리표 id 는 나가는데 본문은 어디에도 없던 결함을 막는다.

쉬운 설명의 각 주장에는 `{basis, ev_..., +1}` 꼬리표가 붙고, 파이프라인이 그것을
되긁어 `explanation_result` 에 넣는다. 묶음 본문이 `analysis_evidence_bundle` 에
들어가지 않으면 그 id 는 **아무것도 가리키지 않는 참조**가 된다 - 되짚을 수 없는
근거는 근거가 아니다.

여기서 지키는 것은 넷이다:
  (a) 묶음이 있으면 산문을 만든 그 자리에서 적재한다
  (b) DSN 이 없으면 예외가 아니라 **사유 한 줄**로 끝난다
  (c) 적재가 죽어도 셀 산문은 나가고, 죽은 사실이 그 산문에 남는다
  (d) 같은 묶음을 다시 적재하면 '중복으로 건너뜀' 으로 보고한다 (조용한 0행 금지)
"""
import inspect

import pytest

from edge_analysis.statics.core import duck, evidence
from edge_analysis.statics.core.evidence import _fake_con, say_save, stat_bundle



def test_evidence_lineage_migration_matches_bundle_contract():
    """배포 스키마도 통계 사건의 시계열·원문·사건 흐름 계보를 보존해야 한다."""
    from pathlib import Path

    migration = (Path(__file__).resolve().parents[5] / "libs/schema/migrations-cloud"
                 / "V202608051100__add_analysis_evidence_lineage.sql")
    sql = migration.read_text(encoding="utf-8")
    assert "thread_ids TEXT[] NOT NULL DEFAULT '{}'" in sql
    assert "series_lineage JSONB NOT NULL DEFAULT '{}'::jsonb" in sql
    assert "ADD COLUMN IF NOT EXISTS sign SMALLINT NOT NULL DEFAULT 0" in sql



def test_news_objectset_exposes_event_facts_for_plain_language():
    """설명 재료는 제목·역할별 참여자·사건 흐름 위치를 함께 제공한다."""
    class Lake:
        exists = {"tau_sidecar": False}

        def sql(self, query):
            if "max(CAST(ts AS DATE))" in query:
                return [("2026-07-30",)]
            assert "event_argument" in query and "current_stage" in query
            return [("evt_1", "news_1", "삼성전자 HBM 공급 계약",
                     "COMPANY.CONTRACT.SIGNING", "supply_1", "FIRST_IN_THREAD",
                     "DEFINITIVE_SIGNED", ["ISSUER=삼성전자", "CUSTOMER=엔비디아"],
                     "2026-07-31 13:10:00")]

    objs = evidence.news_objectset(Lake(), "inst_1", "2026-07-31")
    assert objs[0]["title"] == "삼성전자 HBM 공급 계약"
    assert objs[0]["arguments"] == ["ISSUER=삼성전자", "CUSTOMER=엔비디아"]
    assert objs[0]["novelty"] == "FIRST_IN_THREAD"
    assert objs[0]["stage"] == "DEFINITIVE_SIGNED"


def test_statistical_stock_explanation_always_receives_event_facts():
    """통계가 성립한 날에도 사건 제목·참여자·흐름이 쉬운 설명 재료로 들어간다."""
    from edge_analysis.statics.core import attribute

    source = inspect.getsource(attribute.run_cell)
    assert "objs = news_objectset(lake, instrument_id, day)" in source
    assert "news_objectset(lake, instrument_id, day) if allow else []" not in source


def test_stock_plain_payload_includes_verified_att_effects():
    """쉬운 설명은 엣지 p값뿐 아니라 검증된 ATT 크기와 진단을 받는다."""
    from edge_analysis.statics.core import attribute

    source = inspect.getsource(attribute.run_cell)
    assert 'verified_imps' in source
    assert '"kind": "일단위 ATT"' in source
    assert '"att": i.att' in source and '"pretrend_ok": i.pretrend_ok' in source


def _bundles() -> list:
    return [stat_bundle("069500", "2026-08-04", "바스켓이 끌었어요", layer="괴리단독",
                        sign=1, kind="5분 괴리 분해", 바스켓몫=0.011),
            stat_bundle("069500", "2026-08-04", "수급은 반대로 밀었어요", layer="괴리단독",
                        sign=-1, kind="시장사건 시행", att=-0.004, p=0.03)]


def test_prose_sites_load_the_bundles_they_just_made(monkeypatch):
    """산문을 만든 **그 함수 안에서** 적재해야 한다.

    호출 여부만 단위로 검사하면 '`say_save` 는 잘 동작한다, 다만 아무도 안 부른다'
    라는 상태를 초록으로 통과시킨다 - 실제로 그 상태였다. 그래서 사용 자리를 소스로
    직접 막는다(test_tuple_system 의 `.pct` 금지와 같은 규율).
    """
    from edge_analysis.statics.core import attribute
    from edge_analysis.statics.window import etfcell
    for fn in (attribute.run_cell, etfcell._dual):
        src = inspect.getsource(fn)
        assert "narrate_plain(" in src, f"{fn.__qualname__} 이 쉬운 설명을 안 만든다"
        assert "say_save(bundles)" in src, (
            f"{fn.__qualname__} 이 묶음을 적재하지 않는다 - 꼬리표 id 가 허공을 가리킨다")

    # 그리고 그 배선이 실제로 `save` 까지 간다.
    seen: list = []
    monkeypatch.setattr(evidence, "save",
                        lambda bs, dsn="", **kw: seen.append(list(bs)) or (len(bs), 0, ""))
    bundles = _bundles()
    assert say_save(bundles) == "(근거 묶음 2건 적재)"
    assert seen == [bundles]
    # 묶음이 없으면 부르지 않는다 - 빈 INSERT 는 사유 없는 소음이다.
    assert say_save([]) == "" and len(seen) == 1


def test_missing_dsn_ends_with_a_reason_not_an_exception(monkeypatch):
    """자격증명 부재는 사고가 아니다 - 그러나 **조용하면** 사고다."""
    monkeypatch.setattr(duck, "rdb_dsn_from_env", lambda: "")
    line = say_save(_bundles())
    assert line == ("(근거 묶음 미적재 - EDGE_RDB_DSN 도 PG* 도 없다 - 저장 생략 "
                    "(꼬리표는 산출물에 남는다))")


def test_dsn_comes_from_the_same_resolver_the_lake_uses(monkeypatch):
    """근거 적재와 레이크가 **같은 DB** 를 봐야 한다. env 를 따로 읽으면 컨테이너에서
    갈린다(Fargate 는 EDGE_RDB_DSN 없이 PG* 여섯 개만 준다)."""
    monkeypatch.setattr(duck, "rdb_dsn_from_env", lambda: "dsn=조립됨")
    got: list = []
    seen: set = set()
    inner = _fake_con(seen)
    say_save(_bundles(), connect=lambda dsn, **kw: got.append(dsn) or inner(dsn, **kw))
    assert got == ["dsn=조립됨"]


def test_load_failure_leaves_a_reason_and_never_kills_the_prose():
    """적재는 부가 산물이다. 죽더라도 셀 설명은 나가고, 죽은 사실은 산문에 남는다."""
    line = say_save(_bundles(), "dsn=fake",
                    connect=_fake_con(set(), fail="커넥션이 죽었다"))
    assert line == "(근거 묶음 미적재 - RuntimeError: 커넥션이 죽었다)"

    # 산문 조립까지 확인: 사유가 사람이 읽는 자리에 실제로 실린다.
    from edge_analysis.statics.core.plain import dual
    out = dual("정직한 설명", "바스켓이 끌었어요 {statistical, ev_x, +1}\n" + line)
    assert "정직한 설명" in out and line in out


@pytest.mark.parametrize("boom", ["cursor", "executemany", "commit"])
def test_no_failure_point_inside_the_load_escapes(boom):
    """접속만 막아 보면 커서·커밋에서 죽는 경우를 못 잡는다. 세 자리 모두 사유가 된다."""
    class Con:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self):
            if boom == "cursor":
                raise RuntimeError("커서 없음")
            return Cur()
        def commit(self):
            if boom == "commit":
                raise RuntimeError("커밋 실패")

    class Cur:
        rowcount = 2
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def executemany(self, *a):
            if boom == "executemany":
                raise RuntimeError("제약 위반")

    line = say_save(_bundles(), "dsn=fake", connect=lambda *a, **kw: Con())
    assert line.startswith("(근거 묶음 미적재 - RuntimeError:")


def test_second_load_of_the_same_bundles_is_reported_as_skipped():
    """`ON CONFLICT DO NOTHING` 은 조용하다. 요청 건수를 그대로 보고하면 '오늘 근거를
    남겼다' 와 '이미 다 들어 있어 아무것도 안 썼다' 가 산출물에서 똑같이 보인다."""
    table: set = set()
    assert say_save(_bundles(), "dsn=fake", connect=_fake_con(table)) == "(근거 묶음 2건 적재)"
    assert (say_save(_bundles(), "dsn=fake", connect=_fake_con(table))
            == "(근거 묶음 0건 적재 · 2건 중복으로 건너뜀)")
    # 하나만 새 것이면 그 하나만 적재로 센다.
    mixed = [*_bundles(), stat_bundle("069500", "2026-08-04", "환율이 밀었어요",
                                      sign=-1, kind="밤사이 환원", factor_ret=-0.002)]
    assert (say_save(mixed, "dsn=fake", connect=_fake_con(table))
            == "(근거 묶음 1건 적재 · 2건 중복으로 건너뜀)")


def test_unknown_rowcount_does_not_claim_zero_duplicates():
    """드라이버가 rowcount 를 모르겠다(-1)고 하면 중복을 셀 근거가 없다 - 그때 '0건
    중복' 이라고 단정하면 없는 사실을 만든다."""
    class Cur:
        rowcount = -1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def executemany(self, *a): pass

    class Con:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return Cur()
        def commit(self): pass

    assert evidence.save(_bundles(), "dsn=fake", connect=lambda *a, **kw: Con()) == (2, 0, "")


def test_bundles_exist_even_with_the_narrative_path_off(monkeypatch):
    """서사 경로를 **꺼도** 통계 묶음은 만들어진다 - 스위치는 뉴스 조회만 막는다.

    전역 상수를 단언하면 스위치를 켜는 날 이 테스트가 깨진다 - 그건 계약이 아니라
    현재 설정을 굳히는 것이다. 스위치를 끈 **상태를 만들어** 검사한다.
    """
    from edge_analysis.statics.core.plain import _assemble, context
    monkeypatch.setattr(evidence, "NARRATIVE_ENABLED", False)
    ctx = context(ticker_name="K", day_log=0.05, idio_log=0.04, route_kind="고유",
                  market_name="코스피", recent={}, established=["시장사건"],
                  overnight=[], unexplained_top=False)
    st = {"ref": "s1", "kind": "시장사건 시행", "att": 0.011, "p": 0.01}
    _prose, bundles = _assemble(
        [{"text": "오늘 올랐어요", "basis": "statistical", "refs": ["s1"], "sign": 1}],
        ctx, {"s1": st}, [], "069500", "2026-08-04", "고유")
    assert [b.basis for b in bundles] == ["statistical"]
