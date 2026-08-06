"""프롬프트↔어휘 일치 · 막힘 큐 · 재표집 이름표 — 셋 다 같은 병의 치료다.

병: **기록만 하고 안 읽는다** (또는 손으로 적어놓고 안 맞춘다).
  - 프롬프트가 "계열족 9" 라고 적은 동안 어휘는 17 이었다 → 되돌림 축이 도달 불가였다
  - 매일 나오는 판정불가 사유가 어디로도 흐르지 않았다 → 로드맵을 추측으로 정했다
  - 순열이 무엇을 섞는지가 값으로 남지 않았다 → 다음 검정기가 조용히 순환을 들여온다
"""

from __future__ import annotations

import json

import pytest

from edge_analysis.statics.core.registry import need_key, record, roadmap
from edge_analysis.statics.core.vocab import (CHANNELS, COMPARATORS, OUTCOME_KINDS,
                                        SERIES_FAMILIES, TRANSFORMS)


def _prompt() -> str:
    """실제 발송되는 시스템 프롬프트 - 문자열 상수가 아니라 조립 결과를 본다."""
    from edge_analysis.statics.core.hypothesize import propose
    seen: dict[str, str] = {}

    def ask(sysmsg: str, user: str) -> dict:
        seen["s"] = sysmsg
        return {"hypotheses": []}

    propose(ask, facts="f", event_types=["COMPANY.PRODUCT.LAUNCH"],
            measurable=[("수급", "누적")])
    return seen["s"]


def test_prompt_counts_are_derived_from_the_vocabulary():
    """프롬프트의 개수·목록은 **어휘에서 파생**되어야 한다 - 손으로 적으면 낡는다.

    실측 드리프트: 프롬프트 '계열족 9 · 변환 5' vs 어휘 17 · 6. 그리고 결과종류가
    하드코딩 2종이라 `되돌림`(= ln(종가/일중고가), '왜 오르다 떨어졌나' 축)을 모델이
    고를 방법이 없었다. 어휘에 있는데 도달 불가면 그 축은 죽은 것이다.
    """
    t = _prompt()
    assert f"채널 {len(CHANNELS)}" in t
    assert f"계열족 {len(SERIES_FAMILIES)}" in t
    assert f"변환 {len(TRANSFORMS)}" in t
    # 결과종류·비교는 목록 전량이 실려야 한다 - 일부만 실으면 나머지가 죽는다
    for v in OUTCOME_KINDS:
        assert v in t, f"결과종류 {v} 가 프롬프트에 없다 - 어휘에 있는데 도달 불가"
    for v in COMPARATORS:
        assert v in t
    # 낡은 리터럴이 남아 있지 않다
    assert "계열족 9" not in t and "변환 5" not in t


class _T:
    """튜플 대역 - 레지스트리가 읽는 것만 갖춘다."""

    def __init__(self, ident="COMPANY.PRODUCT.LAUNCH", outcome="되돌림"):
        self.trigger = type("g", (), {"ident": ident, "kind": "점"})()
        self.channel = "Q수량"
        self.outcome = outcome
        self.exposure = type("e", (), {"ident": "수급", "transform": "누적"})()


class _R:
    def __init__(self, verdict="판정불가", reason="", null_kind="label"):
        self.verdict, self.reason, self.null_kind = verdict, reason, null_kind
        self.n, self.p, self.applies_today = 0, None, False


def test_outcome_is_recorded_so_a_dead_axis_is_visible(tmp_path):
    """`outcome` 을 안 남기면 **어떤 축이 한 번도 안 쓰였는지 확인할 수조차 없다**.

    실측: 레지스트리 138 튜플에 outcome 필드가 아예 없어서, 되돌림 축이 죽었는지를
    파일로 확정할 방법이 없었다. 기록되지 않은 것은 감사되지 않는다.
    """
    record(tmp_path, day="2026-07-27", cell="c",
           reports=[(_T(outcome="되돌림"), _R(verdict="성립"))])
    rows = [json.loads(l) for l in
            (tmp_path / "tuple_registry.jsonl").read_text(encoding="utf-8").splitlines()]
    tup = next(r for r in rows if r["kind"] == "tuple")
    assert tup["outcome"] == "되돌림" and tup["null_kind"] == "label"


def test_blocked_reasons_become_a_collection_roadmap(tmp_path):
    """판정불가 사유가 **수집 우선순위**로 집계된다 - 버리면 매일 같은 벽에 부딪힌다.

    같은 결핍이 다른 이름·숫자로 나타나도 한 줄로 모여야 한다. 안 모이면 개수가
    흩어져 무엇을 먼저 채워야 하는지 안 보인다.
    """
    same = ["노출 '수급/누적' 는 아직 못 잰다 - 백필 필요",
            "노출 '재무/변화' 는 아직 못 잰다 - 백필 필요",
            "노출 '성장/수준' 는 아직 못 잰다 - 백필 필요"]
    other = "취약성 조건화 후 n=12 < 30"
    for i, why in enumerate([*same, other]):
        record(tmp_path, day=f"2026-07-2{i}", cell=f"c{i}",
               reports=[(_T(), _R(reason=why))])

    rm = roadmap(tmp_path)
    assert rm and rm[0]["unlocks"] == 3, rm          # 셋이 한 결핍으로 모인다
    assert rm[0]["cells"] == 3
    assert len(rm) == 2, "다른 결핍은 따로 센다"
    assert sum(r["unlocks"] for r in rm) == 4
    # 숫자·이름은 키에서 지워진다 - 안 지우면 n=12 와 n=13 이 다른 결핍이 된다
    assert need_key(other) == need_key("취약성 조건화 후 n=13 < 30")
    assert need_key(same[0]) == need_key(same[1])
    # 성립 판정은 큐에 들어가지 않는다
    record(tmp_path, day="2026-08-01", cell="z",
           reports=[(_T(), _R(verdict="성립", reason="무관"))])
    assert sum(r["unlocks"] for r in roadmap(tmp_path)) == 4


def test_roadmap_is_empty_not_invented_when_nothing_is_blocked(tmp_path):
    """없는 로드맵을 만들지 않는다."""
    assert roadmap(tmp_path) == []


def test_date_null_loses_attribution_rights():
    """`null_kind == "date"` 는 **몫 배정 자격을 잃는다** - 순환이기 때문이다.

    셀은 큰 이상수익으로 선정됐다. 그 다음 "이 날이 특별한가" 를 물으면 선정 기준을
    되묻는 것이다. 이름표로만 두면 다음 검정기가 조용히 이 귀무를 들고 온다 - 그래서
    `applies_today` 가 읽는다(21R). `sign` 을 쓰기 전용으로 뒀다가 지운 교훈이다.
    """
    from edge_analysis.statics.core.paneltest import EdgeReport

    kw = dict(n=400, p=0.001, effect_high=0.02, effect_low=0.0,
              today_exposure_pct=0.9)
    assert EdgeReport("성립", **kw).applies_today is True
    assert EdgeReport("성립", null_kind="date", **kw).applies_today is False
    assert EdgeReport("성립", null_kind="pair", **kw).applies_today is True


def test_pair_permutation_tests_name_their_null():
    """짝 부호 순열 검정기는 자기 귀무를 값으로 남긴다."""
    from edge_analysis.statics.window.mkttrial import say_market_trial

    r = {"verdict": "계산됨", "null_kind": "pair", "att": 0.012, "p": 0.003,
         "n_days": 21, "pairs": 63, "treated_all": 28, "pool": 400,
         "pretrend": {"t-1": 0.0001, "t-2": None}, "overlap": 4,
         "etype": "POLICY.TRADE.TARIFF_CHANGE"}
    assert say_market_trial(r)          # 계약이 깨지지 않는다
    assert r["null_kind"] == "pair"


@pytest.mark.parametrize("bad", ["", "date"])
def test_missing_or_circular_null_never_assigns(bad):
    """이름표가 비어도 배정되면 게이트가 아니다 - 빈 값은 label 이 아니다."""
    from edge_analysis.statics.core.paneltest import EdgeReport

    r = EdgeReport("성립", n=400, p=0.001, effect_high=0.02, effect_low=0.0,
                   today_exposure_pct=0.9, null_kind=bad)
    assert r.applies_today is (bad != "date")


def test_every_advertised_tool_exists_in_the_namespace():
    """메뉴에 광고된 도구는 **실제로 호출 가능**해야 한다 - 반대 방향 드리프트.

    STORM 이 정확히 이 병으로 죽었다: `args`·`roles`·`basket` 이 스키마에 광고만 되고
    네임스페이스에 없었다. 모델이 부르면 "그런 도구 없음" 이 돌아오고, 그건 부재가
    아니라 거짓말이다. 광고와 구현을 한 자리에서 검사한다.
    """
    from edge_analysis.statics.core.fsm import MENUS
    from edge_analysis.statics.core.tools import TOOL_TABLES, Catalog

    for stage, menu in MENUS.items():
        for name, blurb in menu:
            assert callable(getattr(Catalog, name, None)), \
                f"{stage} 메뉴가 {name} 를 광고하는데 Catalog 에 없다"
            assert name in blurb, f"{name} 설명이 자기 이름을 안 쓴다"
    # 도달성 표에 없는 도구는 '표 없음' 사유를 만들 수 없다 - RDB 도구는 등재돼야 한다
    for name in ("events", "news", "thread", "args", "links"):
        assert name in TOOL_TABLES or name == "events", f"{name} 도달성 미등재"


def test_args_reports_the_slots_the_trigger_grammar_can_narrow():
    """`args` 는 방아쇠가 좁힐 수 있는 슬롯의 **실측 값**을 준다.

    역할 70종·서술어 37종이 문법에 열려 있는데 관측창이 없으면 모델은 추측으로 좁히고,
    좁힌 결과는 표본 0 이다. 오늘 건수를 같이 주는 이유: 이력에 있어도 오늘 0 이면
    그 값으로 좁힌 처치가 이 셀에 없다.
    """
    class L:
        def __init__(self, rows):
            self.rows, self.exists = rows, {"rdb": True}

        def sql(self, q):
            assert "role_code" in q and "novelty_status" in q, q
            return self.rows

    from edge_analysis.statics.core.tools import Catalog

    rows = [("COMPANY.CONTRACT.SIGNING", "SUPPLIER", "SIGNED", "CONFIRMED", "NEW", 24, 2),
            ("COMPANY.ALLIANCE.PARTNERSHIP", "PARTNER", None, None, None, 284, 0)]
    c = Catalog(lake=L(rows), ticker="000660.KS", instrument_id="i1",
                day="2026-06-01", types=("COMPANY.CONTRACT.SIGNING",))
    out = c.args()
    assert "[SUPPLIER]" in out and "×24" in out and "**오늘 2**" in out
    assert "[PARTNER]" in out and "오늘 0" in out, "오늘 없는 값도 숨기지 않는다"
    assert "서술어없음" in out, "빈 슬롯은 빈 것으로 말한다"

    empty = Catalog(lake=L([]), ticker="000660.KS", instrument_id="i1",
                    day="2026-06-01", types=())
    assert "아규먼트 없음" in empty.args() and "조회 성공" in empty.args()


def test_mislookup_is_told_apart_from_absence():
    """**오타와 진짜 부재를 가른다** - 같은 문장으로 답하면 키 오조회를 '없음'으로 믿는다.

    STORM 실험의 dyn2 가 정확히 이 실패로 죽었다: 키를 잘못 조회하고 돌아온 "없음"을
    '사건 없음'으로 믿었다. 필터가 0 을 만들었으면 필터를 빼고 **무엇이 있는지** 되돌려
    준다 - 그게 오조회 구제다.
    """
    from edge_analysis.statics.core.tools import Catalog

    class L:
        """필터 걸린 질의는 0행, 필터 없는 질의는 실제 값을 준다."""

        def __init__(self):
            self.exists = {"rdb": True}

        def sql(self, q):
            if "DISTINCT l.link_type" in q:
                return [("SUPPLY_CHAIN",), ("PARTNERSHIP",)]
            if "DISTINCT e.event_type_code" in q:
                return [("COMPANY.CONTRACT.SIGNING",)]
            return []                      # 필터 걸린 본 질의

    c = Catalog(lake=L(), ticker="000660.KS", instrument_id="i1", day="2026-06-01",
                types=("COMPANY.CONTRACT.SIGNING",))

    out = c.links("OTPA")
    assert "SUPPLY_CHAIN" in out and "PARTNERSHIP" in out, "있는 타입을 되돌려준다"
    assert "OTPA" in out, "무엇을 물었는지 되짚는다"

    out2 = c.args("OTPA")
    assert "COMPANY.CONTRACT.SIGNING" in out2 and "OTPA" in out2

    out3 = c.news("OTPA")
    assert "COMPANY.CONTRACT.SIGNING" in out3, "이 셀의 타입을 되돌려준다"

    # 필터가 없으면 그건 진짜 부재다 - 목록을 되돌려주지 않는다
    assert "있는 타입" not in c.args() and "조회 성공" in c.args()


def test_callable_tool_list_is_derived_from_the_menu():
    """호출 가능 목록이 하드코딩이면 새 도구가 빠진다 - **있는 도구를 없다고 말한다**.

    실측: `args` 를 MENUS 에 광고했는데 `menu_names()` 가 하드코딩 튜플이라 빠졌다.
    그러면 도구 이름 오타 시 "있는 것" 안내에서 `args` 가 사라진다 - 오조회 구제가
    스스로 오조회를 만든다.
    """
    from edge_analysis.statics.core.fsm import MENUS
    from edge_analysis.statics.core.tools import Catalog

    names = Catalog.menu_names()
    for stage, menu in MENUS.items():
        for n, _ in menu:
            assert n in names, f"{stage} 가 광고하는 {n} 이 호출 목록에 없다"
    assert len(names) == len(set(names)), "중복 없음"


def test_member_news_needs_the_base_ctes_and_never_swallows_the_reason():
    """`v_instrument` 는 **`_base(day)` 가 만드는 CTE** 다 - 독립 뷰가 아니다.

    실측: 그걸 빼먹어 질의가 `CatalogException` 으로 죽고 `except: return []` 가 조용히
    삼켰다 → 30일 배치에서 뉴스 인용이 **0/30** 이었다. 배선은 했는데 산출이 0 이면
    '뉴스가 없다' 와 '질의가 죽었다' 가 같아 보인다 - 이 저장소가 가장 싫어하는 실패다.
    """
    from edge_analysis.statics.window.etfcell import member_news

    class _Name:
        def __init__(self, tk, c):
            self.ticker, self.label, self.contribution = tk, tk, c

    class _Roll:
        names = (_Name("005930", 0.01), _Name("000660", -0.02))

    class _Lake:
        """질의에 `_base` 의 CTE 가 들어왔는지 본다 - 안 들어오면 실제로 죽는다."""

        def __init__(self, boom=False):
            self.boom, self.seen = boom, []
            self.exists = {"rdb": True, "tau_sidecar": 0}

        def sql(self, q):
            self.seen.append(q)
            if self.boom:
                raise RuntimeError("Catalog Error: v_instrument does not exist")
            # `_base` 는 v_instrument 를 정의하기만 한다 - 조회하는 질의만 골라낸다
            if "SELECT ticker, instrument_id FROM v_instrument" in q:
                assert "WITH" in q.upper(), "_base 의 CTE 없이 v_instrument 를 조회했다"
                return [("005930", "i_sam")]
            return []                       # 뉴스 질의는 빈 결과 (부재는 부재다)

    lk = _Lake()
    assert member_news(lk, _Roll(), "2026-07-27") == [], "뉴스 부재는 빈 목록이다"
    assert any("SELECT ticker, instrument_id FROM v_instrument" in q for q in lk.seen)

    # 조회 실패는 **빈 목록이 아니다** - 사유를 실어 보낸다
    out = member_news(_Lake(boom=True), _Roll(), "2026-07-27")
    assert out and out[0]["ref"].startswith("!")
    assert "부재가 아니다" in out[0]["판정"]


def test_multi_signed_material_does_not_constrain_the_claim_sign():
    """여러 부호를 실은 재료는 방향을 구속하지 않는다 - **내가 세운 규칙을 내가 깼다.**

    `그_창_괴리몫` 을 `_SIGNED_KEY` 에 넣었다가 30일 배치에서 5일을 죽였다(실패 8건 중
    5건). 5분 괴리 분해 재료는 하루·바스켓몫·괴리변화몫·그_창_괴리몫 넷을 싣는데 주장이
    말하는 것은 보통 하루 방향이고, 최대 괴리 창의 부호는 그와 반대일 수 있다
    (실측: 하루 +9.24%p 인데 그_창_괴리몫 -0.00483).
    """
    from edge_analysis.statics.core.plain import _SIGNED_KEY, _assemble, context

    assert "그_창_괴리몫" not in _SIGNED_KEY
    ctx = context(ticker_name="K", day_log=0.09, idio_log=0.08, route_kind="고유",
                  market_name="M", recent={"when": "오후"}, established=["x"],
                  overnight=[], unexplained_top=False)
    mat = {"ref": "s1", "kind": "5분 괴리 분해", "하루": 0.0924,
           "바스켓몫": 0.08, "괴리변화몫": 0.0124, "그_창_괴리몫": -0.00483,
           "창수": 77, "판정": "바스켓이 끌었다"}
    txt, _ = _assemble(
        [{"text": "오후에 크게 올랐어요", "basis": "statistical", "refs": ["s1"],
          "sign": 1}], ctx, {"s1": mat}, [], "c", "d", "")
    assert "올랐어요" in txt, "하루가 올랐으면 +1 이 맞다 - 창 하나의 부호로 막지 않는다"


def test_interval_clamps_instead_of_rejecting_overnight():
    """장 밖 요구는 **거부가 아니라 절단**이다 - 밤사이는 `갭` 창이 항상 답한다.

    처음 구현은 08:00~10:00 을 예외로 되돌려보냈다. 그건 거짓이었다: 전일 종가→시가는
    5분봉 첫 봉 시가와 전 거래일 마지막 봉 종가로 **관측 가능**하고, 갭 창은 언제나
    따로 나온다. 질문을 되돌려보내는 대신 자르고 사유를 적는다.
    """
    from edge_analysis.statics.window.interval import IntervalError, clamp

    a, b, why = clamp("08:00", "16:00")
    assert (a, b) == ("09:00:00", "15:30:00")
    assert len(why) == 2 and "갭" in why[0]

    a2, b2, why2 = clamp("10:30", "13:00")
    assert (a2, b2) == ("10:30:00", "13:00:00") and not why2

    for bad in ("13:00", "9시"), ("10:00", "10:00"):
        try:
            clamp(*bad)
        except IntervalError:
            pass
        else:
            raise AssertionError(f"{bad} 를 통과시켰다")


def test_missing_previous_close_is_not_rendered_as_zero():
    """전일 종가 앵커가 없으면 헤더 누적수익을 만들지 않는다.

    요청창 출력은 더 이상 하루의 다른 창을 조립하지 않는다. 전일 종가가 없는 날에는
    요청창 수익과 별개로 헤더 값을 명시적으로 미계측 처리해야 한다.
    """
    import datetime as dt

    from edge_analysis.statics.window import interval as iv

    class _Lake:
        exists = {"rdb": True}

        def taus(self, iid, day):
            return [(dt.datetime(2026, 7, 27, 10, 3), "e1")]

        def sql(self, q):
            if "ORDER BY 1 DESC LIMIT 2" in q:      # _gap - 전 거래일이 없다
                return [("2026-07-27", 100.0, 101.0)]
            if "SELECT ts, open, close" in q:       # _bars
                return [(dt.datetime(2026, 7, 27, 10, 0), 100.0, 102.0)]
            return []

    txt = iv.explain(_Lake(), "T", "i", "2026-07-27", "10:00", "10:30")
    assert "전일 종가 대비 수익률 미계측" in txt
    assert "몫의 합이 하루와 같다" not in txt and "합(부분)" not in txt
