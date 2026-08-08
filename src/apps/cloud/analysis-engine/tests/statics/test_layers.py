"""층 분해 계약 - 산술이 맞아야 하고, 통계적 적합이 설명을 참칭하면 안 된다."""
import datetime as dt
import re

import numpy as np
import pytest

from edge_analysis.statics.layers import (MARKET_CODE, MIN_BETA_N, Rollup, decompose,
                                          overlap, residual_rho)

DAYS = [dt.date(2026, 1, 1) + dt.timedelta(d) for d in range(90)]


def _prices(rets: np.ndarray) -> list[float]:
    return list(100.0 * np.exp(np.concatenate([[0.0], np.cumsum(rets)])))


class FakeLake:
    """`layers_daily` 와 `s3_etf_holdings` 두 질의 모양만 응답한다."""

    def __init__(self, series: dict, holds: dict):
        self.series, self.holds = series, holds

    def sql(self, q: str):
        if "FROM layers_daily" in q:
            kinds = re.search(r"kind IN \(([^)]*)\)", q).group(1)
            want = {k.strip().strip("'") for k in kinds.split(",")}
            return [[s, m["name"], _prices(m["ret"]), DAYS[: len(m["ret"]) + 1],
                     m.get("vol", [1e6] * (len(m["ret"]) + 1))]
                    for s, m in self.series.items() if m["kind"] in want]
        if "FROM s3_etf_holdings" in q:
            etf = re.search(r"etf_id = '([^']+)'", q).group(1)
            return [[t, f"종목{t}", w * 100.0] for t, w in self.holds.get(etf, {}).items()]
        raise AssertionError(f"예상 못 한 질의: {q[:60]}")


def _lake(*, sector_beta: float = 0.0, alien_beta: float = 0.0):
    rng = np.random.default_rng(0)
    n = 80
    mkt = rng.normal(0, 0.01, n)
    sec = rng.normal(0, 0.01, n)          # 시장과 독립인 섹터 요인
    ali = rng.normal(0, 0.01, n)
    tgt = 1.2 * mkt + sector_beta * sec + alien_beta * ali + rng.normal(0, 0.001, n)
    S = {
        MARKET_CODE: {"name": "KODEX 200", "kind": "market", "ret": mkt},
        "T": {"name": "대상ETF", "kind": "sector", "ret": tgt},
        "TWIN": {"name": "쌍둥이", "kind": "sector", "ret": tgt + rng.normal(0, 1e-4, n)},
        "SEC": {"name": "인접섹터", "kind": "sector", "ret": sec},
        "ALIEN": {"name": "무관ETF", "kind": "sector", "ret": ali},
    }
    H = {                                  # T 와의 비중 겹침: TWIN 0.9 · SEC 0.15 · ALIEN 0
        "T":     {"a": 0.5, "b": 0.3, "c": 0.2},
        "TWIN":  {"a": 0.5, "b": 0.3, "c": 0.2},
        "SEC":   {"a": 0.15, "x": 0.85},
        "ALIEN": {"y": 0.6, "z": 0.4},
        MARKET_CODE: {"a": 0.1, "q": 0.9},
    }
    # 구성종목도 섹터 요인에 노출된다 - **ETF 가 섹터 β 를 갖는데 구성종목이 안 갖는
    # 것은 현실에 없다.** 이전 픽스처는 그렇게 만들어져 있어서 ρ 게이트(층은 잔차
    # 공통상관을 줄여야 한다)가 배선되자마자 섹터를 전부 막았다 - 게이트가 아니라
    # 픽스처가 틀렸다. T 의 구성종목(a·b·c)만 섹터에 실린다.
    for tk in "abcxyzq":
        S[tk] = {"name": f"종목{tk}", "kind": "stock",
                 "ret": 1.0 * mkt + (sector_beta * sec if tk in "abc" else 0.0)
                        + rng.normal(0, 0.01, n)}
    return FakeLake(S, H)


def _day(n: int = 80) -> str:
    return DAYS[n].isoformat()


# ── 산술 ──────────────────────────────────────────────────────────────────
def test_layer_sum_plus_idio_equals_total_exactly():
    # 20R 실측: 서사가 "원수익 -9.61 = 시장 -6.11 + 고유 +0.55" 를 인쇄했는데
    # 합이 -5.56 이었다 - 산업층 -4.35 를 통째로 빠뜨렸다. 읽는 사람이 검산하면
    # 무너지는 산출은 직관 이전에 신뢰의 문제다. 고유는 **잔여로 정의**되므로
    # 항등식은 부동소수 오차 안에서 정확해야 한다.
    r = decompose(_lake(sector_beta=0.8), "T", _day())
    assert r is not None
    assert sum(x.contribution for x in r.layers) + r.idio == pytest.approx(r.total, abs=1e-12)


def test_decompose_records_why_it_returned_none():
    """`None` 은 정상 반환값이지만 **침묵이면 안 된다.**

    재료 부재("대상 계열이 없다")와 표본 부족("β 창이 안 찬다")은 처방이 다른데 지금은
    같은 `None` 이다. 2026-08-06 장중에 전 런이 `layer_route=미상` 이었는데 왜인지
    로그로 못 봤다. 이 모듈은 로깅을 모르므로 `exists` 에 적고 소비는 호출자가 한다.
    """
    lake = _lake(sector_beta=0.8)
    lake.exists = {}

    assert decompose(lake, "없는종목", _day()) is None
    assert "없는종목" in lake.exists["layers"]


def test_decompose_distinguishes_short_history_from_missing_series():
    """두 `None` 은 처방이 다르다 — 재료 부재는 적재 일감, 표본 부족은 창·유니버스 문제다.

    한쪽만 사유를 남기면 운영에서 둘을 못 가른다. `MIN_BETA_N` 미달 경로도 말해야 한다.
    """
    lake = _lake()
    lake.exists = {}
    for m in lake.series.values():
        m["ret"] = m["ret"][:10]

    assert decompose(lake, "T", DAYS[10].isoformat()) is None
    assert "표본 부족" in lake.exists["layers"]
    assert str(MIN_BETA_N) in lake.exists["layers"]


def test_min_beta_sample_is_one_number_across_the_two_definitions():
    """`layers` 와 `attribute` 의 최소 표본이 같은 값이다 (ALPHA-849).

    WHY: 두 모듈이 각자 상수를 들고 있다(순환 import 를 피하려고). 갈리면 같은 런에서
    **층은 서는데 갭 귀속만 부재**가 되고, 산문은 "층은 봤는데 갭은 모른다"로 인쇄된다 —
    재료가 같은데 판정이 갈리는 것이라 운영자가 원인을 찾을 데가 없다. 한쪽만 고치는
    변경이 조용히 통과하지 않도록 여기서 묶는다.
    """
    from edge_analysis.statics.attribute import MIN_BETA_N as ATTRIBUTE_MIN

    assert MIN_BETA_N == ATTRIBUTE_MIN, (
        f"layers({MIN_BETA_N}) != attribute({ATTRIBUTE_MIN}) — 한쪽만 옮겼다"
    )


def test_a_series_shorter_than_the_old_requirement_still_stands():
    """옛 요건(40)에는 못 미치지만 새 요건(20)은 넘는 계열이 **층으로 선다**.

    WHY: 요건을 낮춘 목적이 이것이다 — 상장이 늦어 40일을 물리적으로 못 채우는 계열
    (실측 0210A0, 33거래일)이 후보에서 빠지면 그 ETF 는 섹터 층이 통째로 없다. 값만
    바꾸고 이 경로를 안 태우면 "낮췄다"는 주장이 코드로 확인되지 않는다.
    """
    assert MIN_BETA_N <= 33 < 40, "이 테스트의 전제(33일 계열)가 요건과 안 맞는다"

    lake = _lake()
    lake.exists = {}
    for m in lake.series.values():
        m["ret"] = m["ret"][:33]

    roll = decompose(lake, "T", DAYS[33].isoformat())

    assert roll is not None, "33거래일 계열이 표본 부족으로 빠졌다 — 요건 완화가 안 먹었다"
    assert "표본 부족" not in lake.exists.get("layers", "")


def test_decompose_does_not_pollute_unbound_which_counts_tables():
    """사유를 `unbound` 에 넣으면 안 된다 — 거긴 `표 → 못 묶은 사유` 이고 소비자가 있다.

    `duck.bind_day()` 는 `len(bound) - len(unbound)` 로 바인딩 수를 세고 `coverage()` 는
    "생성 실패" 로 인쇄한다. 합성 키가 끼면 개수가 어긋나고 표본 부족이 표 생성 실패로 읽힌다.
    """
    lake = _lake(sector_beta=0.8)
    lake.exists, lake.unbound = {}, {}

    assert decompose(lake, "없는종목", _day()) is None
    assert lake.unbound == {}


def test_a_later_success_clears_an_earlier_absence():
    """한 런이 `decompose` 를 두 번 부른다(라우팅·설명).

    앞 호출의 실패가 남으면 뒤 호출이 성공해도 커버리지가 실패를 말한다 — 부재를 안 지우는
    것은 부재를 지어내는 것과 같다.
    """
    lake = _lake(sector_beta=0.8)
    lake.exists = {}

    assert decompose(lake, "없는종목", _day()) is None
    assert "layers" in lake.exists

    assert decompose(lake, "T", _day()) is not None
    assert "layers" not in lake.exists


def test_decompose_tolerates_a_lake_without_coverage_dicts():
    """대역 레이크(`exists` 없음)에서도 죽지 않는다 — 관측이 본업을 무너뜨리면 안 된다."""
    assert decompose(_lake(sector_beta=0.8), "없는종목", _day()) is None


def test_missing_market_layer_leaves_a_reason():
    """**시장 층이 빠지면 사유가 남아야 한다.** 여기엔 `else` 가 없었다.

    정본이 부분 착지한 날(실측 8/3·8/4 13종) 시장 계열이 후보에서 빠지는데, 산문에는
    그 사실이 없고 남은 섹터·고유가 시장 몫까지 떠안은 채 인쇄된다. 조용한 부재다.
    사유는 `exists` 에 적는다 — `unbound` 는 `표 → 사유` 라 개수를 세는 소비자가 있다.
    """
    lake = _lake(sector_beta=0.8)
    lake.exists = {}
    del lake.series[MARKET_CODE]

    r = decompose(lake, "T", _day())
    assert r is not None and all(x.kind != "시장" for x in r.layers)
    # **사유까지 고정한다.** `MARKET_CODE in …` 만 단언하면 세 사유 중 무엇이 찍혀도
    # 통과해 "처방이 다르다"는 이 테스트의 계약을 하나도 안 지킨다(Rule 9).
    assert lake.exists["market_layer"] == f"시장 층 없음 - {MARKET_CODE} 계열 부재"

    # 시장 층이 선 런은 앞 런의 사유를 지운다 - 부재를 안 지우면 지어내는 것과 같다.
    lake2 = _lake(sector_beta=0.8)
    lake2.exists = {"market_layer": "앞 호출의 잔재"}
    assert decompose(lake2, "T", _day()) is not None
    assert "market_layer" not in lake2.exists


def test_market_absence_names_which_absence():
    """**두 사유는 처방이 다르다** - 계열 부재는 적재 일감, 당일 없음은 신선도다.
    하나만 검사하면 4분기를 상수 하나로 접어도 스위트가 초록이라 계약이 안 선다.

    나머지 둘(`β 창 결손`·`후보 탈락`)은 이 픽스처로 못 만든다 - `FakeLake` 는 날짜가
    연속인 계열만 낸다(구멍을 못 뚫고, 짧으면 당일부터 없다). 픽스처를 그 두 가지만을
    위해 늘리지 않았다 - 실환경 도달성은 `_on()`·`_pick` 쪽 계약이다.
    """
    lake = _lake(sector_beta=0.8)
    lake.exists = {}
    lake.series[MARKET_CODE]["ret"] = lake.series[MARKET_CODE]["ret"][:60]  # 당일까지 못 온다

    assert decompose(lake, "T", _day()) is not None
    assert lake.exists["market_layer"] == f"시장 층 없음 - {MARKET_CODE} 당일 없음"


def test_market_proxy_explaining_itself_is_not_an_absence():
    """**069500 을 설명할 때 시장 층이 없는 것은 정상이다** - 사유를 적으면 오진이다.

    후보 집합 `xs` 는 대상 자신을 제외하므로(`sym != etf`) 시장 프록시 자신의 분해에는
    시장 층이 없다. `route.py` 가 그 경우를 `Route("시장", 1.0, …)` 로 이미 정식 처리한다
    (실측 069500 07-29). 여기서 부재를 신고하면 정상 런마다 존재하지 않는 β 결손을
    가리켜, 커버리지를 보는 사람이 백필(ALPHA-828)을 쫓게 된다 — **조용한 부재를
    시끄러운 오진으로 바꾸는 것**이라 고치려던 병보다 나쁘다.
    """
    lake = _lake(sector_beta=0.8)
    lake.exists = {}

    assert decompose(lake, MARKET_CODE, _day()) is not None
    assert "market_layer" not in lake.exists


def test_market_layer_always_enters_first():
    # 공통충격이 섹터로 새면 섹터 서사가 거짓이 된다 - 시장은 경쟁 없이 먼저 들어간다.
    r = decompose(_lake(sector_beta=0.8), "T", _day())
    assert r.layers[0].kind == "시장" and r.layers[0].code == MARKET_CODE


# ── 후보 자격 ─────────────────────────────────────────────────────────────
def test_twin_etf_is_barred_as_tautology():
    # "반도체가 왜 빠졌냐"에 "반도체가 빠져서" 는 산술은 맞아도 설명이 아니다.
    # 라이브 실측: KODEX 반도체를 TIGER 반도체(겹침 0.93)로 설명했다.
    r = decompose(_lake(sector_beta=0.8), "T", _day())
    assert "쌍둥이" in r.twins
    assert all(x.code != "TWIN" for x in r.layers)


def test_unrelated_etf_is_barred_even_when_it_fits():
    # 라이브 실측: KODEX 2차전지산업을 KODEX 게임산업(겹침 0)이 β0.71[0.40,1.02] 로
    # "설명"했다. 60일 표본의 우연이고 투자자·금융학자 둘 다 즉시 거부한다.
    # **적합도가 아니라 구성 겹침이 후보 자격을 정한다.**
    r = decompose(_lake(sector_beta=0.0, alien_beta=0.9), "T", _day())
    assert "무관ETF" in r.alien
    assert all(x.code != "ALIEN" for x in r.layers)


def test_related_sector_enters_when_it_carries_the_day():
    r = decompose(_lake(sector_beta=1.5), "T", _day())
    assert any(x.code == "SEC" and x.kind == "섹터" for x in r.layers)


def test_layer_count_and_coverage_bound_the_budget():
    # 설명 예산이 유한해야 서사가 닫힌다 - 층 ≤3, 커버 도달 시 정지.
    r = decompose(_lake(sector_beta=1.5), "T", _day(), max_layers=3, cover=0.5)
    assert len(r.layers) <= 3
    if len(r.layers) < 3:                       # 조기 정지했다면 커버를 채웠거나 후보가 없다
        assert r.coverage >= 0.5 or all(x.kind == "시장" for x in r.layers)


# ── 정직성 ────────────────────────────────────────────────────────────────
def test_halted_names_are_excluded_and_counted():
    # 거래량 0 인 날의 수익률 0 은 거짓이다(정지 종가는 직전 값). 다만 **진짜 보합은
    # 정보다** - 시장이 -7% 미는데 안 빠졌으면 그게 그 종목의 힘이다. 거래량으로만 갈린다.
    lake = _lake(sector_beta=0.8)
    lake.series["a"]["vol"] = [0.0] * 81
    r = decompose(lake, "T", _day())
    assert r.halted >= 1
    assert all(n.ticker != "a" for n in r.names)


def test_names_are_ranked_by_weight_times_idio():
    # 큰 종목의 작은 움직임과 작은 종목의 큰 움직임을 같은 자로 잰다.
    r = decompose(_lake(sector_beta=0.8), "T", _day())
    got = [abs(n.contribution) for n in r.names]
    assert got == sorted(got, reverse=True)
    for n in r.names:
        assert n.contribution == pytest.approx(n.weight * n.idio)


def test_rho_reports_leftover_common_factor():
    # ρ≈0 이어야 '고유'라 부를 자격이 생긴다. 남아 있으면 이름 없는 공통요인의 직접 증거다.
    assert residual_rho([np.array([1.0, 2, 3]), np.array([1.0, 2, 3.1])]) is None
    same = [np.array([1.0, 2, 3, 4])] * 3
    assert residual_rho(same) == pytest.approx(1.0)


def test_overlap_is_weight_intersection():
    lake = _lake()
    assert overlap(lake, "T", "TWIN", _day()) == pytest.approx(1.0)
    assert overlap(lake, "T", "ALIEN", _day()) == pytest.approx(0.0)
    assert overlap(lake, "T", "SEC", _day()) == pytest.approx(0.15)


def test_short_history_yields_no_decomposition_not_a_zero():
    # 창이 안 차면 판정불가지 0 이 아니다 - 부재를 기각으로 위장하지 않는다.
    lake = _lake()
    for m in lake.series.values():
        m["ret"] = m["ret"][:10]
    assert decompose(lake, "T", DAYS[10].isoformat()) is None


def test_rollup_is_frozen_dataclass():
    assert Rollup.__dataclass_params__.frozen


def test_window_mode_attributes_names_on_the_window_axis(monkeypatch):
    """**구간 모드면 종목 계열도 구간 축이어야 한다.** 이게 빠지면 두 가지가 한꺼번에
    깨진다 - 설명 대상은 구간 수익률인데 종목만 일봉이라 **축이 섞이고**(값이 나와도
    틀렸다), `hist` 가 구간 계열에서 만들어지므로 일봉이 그 날짜를 못 담으면 `_on` 의
    all-or-nothing 에 **전 종목이 조용히 탈락**한다.

    실측 2026-08-07(305720): 일봉 `layers_daily` 가 08-05·08-06 을 안 담아 25종 전부가
    빠졌다 - `n_names=0 · weight_covered=0.00`, 산문은 "구성종목 기여를 계산하지
    못했습니다". `clock` 을 넘기자 같은 런이 `n_names=25 · weight_covered=1.00` 이 됐다.

    `decompose` 안의 다른 `_series` 호출 셋은 전부 `clock` 을 넘긴다. 여기만 빠져 있었다.
    """
    from edge_analysis.statics import layers as L

    seen = []
    monkeypatch.setattr(L, "_series",
                        lambda lake, day, kinds, **kw: seen.append((kinds, kw.get("clock"))) or {})
    monkeypatch.setattr(L, "holdings", lambda _l, _e, _d: [("000660", "SK하이닉스", 0.5)])
    clock = ("09:00:00", "12:00:00")
    L._names(object(), "T", DAYS[50].isoformat(), [DAYS[49]],
             [np.array([0.01])], [0.0], 5, clock=clock)

    # 종목 계열을 **구간 축으로** 요청했다. `None` 이면 일봉이라 위 사고가 재발한다.
    assert seen == [(("stock",), clock)]


def test_daily_mode_keeps_asking_the_daily_axis(monkeypatch):
    """위 가드의 반대편 - 일봉 모드(`clock=None`)는 그대로 일봉을 물어야 한다.
    이게 없으면 '항상 구간으로 묻기' 퇴화를 아무도 못 잡는다."""
    from edge_analysis.statics import layers as L

    seen = []
    monkeypatch.setattr(L, "_series",
                        lambda lake, day, kinds, **kw: seen.append((kinds, kw.get("clock"))) or {})
    monkeypatch.setattr(L, "holdings", lambda _l, _e, _d: [("000660", "SK하이닉스", 0.5)])
    L._names(object(), "T", DAYS[50].isoformat(), [DAYS[49]],
             [np.array([0.01])], [0.0], 5, None)

    assert seen == [(("stock",), None)]
