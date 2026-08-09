"""층 회계 계약(ALPHA-862) - 산술이 맞아야 하고, 적합이 설명을 참칭하면 안 된다.

β=1 고정 뒤의 계약은 셋이다: (1) 합 항등식이 부동소수 오차 안에서 정확하다,
(2) 층 기여는 회귀가 아니라 **회계**다 - 시장은 그대로, 섹터는 시장 차감,
(3) 섹터는 **KRX 업종지수뿐**이다(ALPHA-877) - 프록시 ETF 겹침 선택은 폐기했고,
업종지수 5분봉이 없는 구간 모드는 정직 부재다(1분봉은 ALPHA-887 이 수집하나 5분
롤업·소급이 없어 이 경로가 못 읽는다). 회귀·ρ·표본 게이트가
있던 자리의 검정은 칼만(ALPHA-803)이 신뢰구간과 함께 다시 가져온다.
"""
import datetime as dt
import re

import numpy as np
import pytest

from edge_analysis.statics.layers import MARKET_CODE, Rollup, decompose, overlap

DAYS = [dt.date(2026, 1, 1) + dt.timedelta(d) for d in range(90)]


def _prices(rets: np.ndarray) -> list[float]:
    return list(100.0 * np.exp(np.concatenate([[0.0], np.cumsum(rets)])))


class FakeLake:
    """`layers_daily`·`s3_etf_holdings`·KRX 표(`sector_*`)·`bars_5m` 질의 모양만 응답한다."""

    def __init__(self, series: dict, holds: dict, krx=None):
        # krx = (업종지수 코드, [전일 종가, 당일 종가]). None 이면 KRX 표 부재.
        self.series, self.holds, self.krx = series, holds, krx
        self.exists: dict = {}

    def sql(self, q: str):
        if "FROM bars_5m" in q:
            # 구간 모드 5분봉. **pick 필터를 일부러 무시한다** - 섹터 ETF 계열이
            # 패널에 실려 와도 층이 안 서야 하는 계약을 더 세게 시험하기 위해서다.
            return [[s, m["lr5"], 1e6]
                    for s, m in self.series.items() if "lr5" in m]
        if "FROM layers_daily" in q:
            kinds = re.search(r"kind IN \(([^)]*)\)", q).group(1)
            want = {k.strip().strip("'") for k in kinds.split(",")}
            if "list(close" not in q:     # 구간 모드의 이름 조회(_CLOCK_NAMES)
                return [[s, m["name"]] for s, m in self.series.items()
                        if m["kind"] in want]
            only = re.search(r"symbol IN \(([^)]*)\)", q)
            picked = ({s.strip().strip("'") for s in only.group(1).split(",")}
                      if only else None)
            return [[s, m["name"], _prices(m["ret"]), DAYS[: len(m["ret"]) + 1],
                     m.get("vol", [1e6] * (len(m["ret"]) + 1))]
                    for s, m in self.series.items()
                    if m["kind"] in want and (picked is None or s in picked)]
        if "FROM s3_etf_holdings" in q:
            etf = re.search(r"etf_id = '([^']+)'", q).group(1)
            return [[t, f"종목{t}", w * 100.0] for t, w in self.holds.get(etf, {}).items()]
        if "FROM sector_member" in q or "FROM sector_index" in q:
            if not self.krx:
                return []                 # KRX 표 부재 - 후보 없음으로 접힌다
            code, closes = self.krx
            day = re.search(r"trade_date <= DATE '([^']+)'", q).group(1)
            return [[day, closes[-1], code], ["전일", closes[-2], code]]
        if "FROM s3_etf_profile" in q:
            return []
        raise AssertionError(f"예상 못 한 질의: {q[:60]}")


def _lake():
    rng = np.random.default_rng(0)
    n = 80
    mkt = rng.normal(0, 0.01, n)
    sec = rng.normal(0, 0.01, n)
    ali = rng.normal(0, 0.01, n)
    tgt = 1.2 * mkt + 0.8 * sec + rng.normal(0, 0.001, n)
    S = {
        MARKET_CODE: {"name": "KODEX 200", "kind": "market", "ret": mkt},
        "T": {"name": "대상ETF", "kind": "sector", "ret": tgt},
        "TWIN": {"name": "쌍둥이", "kind": "sector", "ret": tgt + rng.normal(0, 1e-4, n)},
        "SEC": {"name": "인접섹터", "kind": "sector", "ret": sec},
        "ALIEN": {"name": "무관ETF", "kind": "sector", "ret": ali},
    }
    H = {                                  # T 와의 비중 겹침: TWIN 1.0 · SEC 0.15 · ALIEN 0
        "T":     {"a": 0.5, "b": 0.3, "c": 0.2},
        "TWIN":  {"a": 0.5, "b": 0.3, "c": 0.2},
        "SEC":   {"a": 0.15, "x": 0.85},
        "ALIEN": {"y": 0.6, "z": 0.4},
        MARKET_CODE: {"a": 0.1, "q": 0.9},
    }
    for tk in "abcxyzq":
        S[tk] = {"name": f"종목{tk}", "kind": "stock",
                 "ret": 1.0 * mkt + (0.8 * sec if tk in "abc" else 0.0)
                        + rng.normal(0, 0.01, n)}
    return FakeLake(S, H)


def _krx_lake():
    """KRX 표까지 있는 레이크 - 업종지수 '1013'(전기전자·코스피), 당일 +2%."""
    lake = _lake()
    lake.krx = ("1013", [100.0, 102.0])
    return lake


KRX_RET = float(np.log(102.0 / 100.0))


def _day(n: int = 80) -> str:
    return DAYS[n].isoformat()


def _now(lake, sym: str) -> float:
    return float(lake.series[sym]["ret"][-1])


# ── 산술: 회계는 항등식이다 ────────────────────────────────────────────────
def test_layer_sum_plus_idio_equals_total_exactly():
    # 20R 실측: 서사가 "원수익 -9.61 = 시장 -6.11 + 고유 +0.55" 를 인쇄했는데
    # 합이 -5.56 이었다 - 산업층 -4.35 를 통째로 빠뜨렸다. 읽는 사람이 검산하면
    # 무너지는 산출은 직관 이전에 신뢰의 문제다. 고유는 **잔여로 정의**되므로
    # 항등식은 부동소수 오차 안에서 정확해야 한다.
    r = decompose(_lake(), "T", _day())
    assert r is not None
    assert sum(x.contribution for x in r.layers) + r.idio == pytest.approx(r.total, abs=1e-12)


def test_market_contribution_is_the_market_return_itself():
    """β=1 계약: 시장 기여 = 시장 당일 수익률 **그대로**(ALPHA-862).

    회귀 β 를 곱하면 이 단언이 깨진다 - 계수 고정을 누가 조용히 되돌리면 여기서
    드러난다. 시변 β 는 칼만(ALPHA-803)이 신뢰구간과 함께 가져오는 것이지, 60일
    상수 회귀가 몰래 돌아올 자리가 아니다.
    """
    lake = _lake()
    r = decompose(lake, "T", _day())
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.contribution == pytest.approx(_now(lake, MARKET_CODE), abs=1e-12)
    assert m.ret == m.contribution


def test_sector_contribution_is_market_subtracted():
    """섹터 기여 = 섹터 수익 − 시장 수익 (β=1 직교화).

    차감이 없으면 시장 몫이 두 번 세어진다 - "시장이 민 건지 섹터가 민 건지"
    배분 순서 논쟁이 그대로 돌아온다. `ret` 은 원 수익률로 남긴다: 산문이
    "섹터 +1.2% (시장 차감 +0.4%p)" 를 말할 재료다.
    """
    lake = _krx_lake()
    r = decompose(lake, "T", _day())
    s = next(x for x in r.layers if x.kind == "섹터")
    assert s.code == "KRX:1013"
    assert s.ret == pytest.approx(KRX_RET, abs=1e-12)
    assert s.contribution == pytest.approx(
        KRX_RET - _now(lake, MARKET_CODE), abs=1e-12)


# ── 부재는 침묵이 아니다 ──────────────────────────────────────────────────
def test_decompose_records_why_it_returned_none():
    """`None` 은 정상 반환값이지만 **침묵이면 안 된다** - 2026-08-06 장중 전 런이
    `layer_route=미상` 이었는데 왜인지 로그로 못 봤다. 이 모듈은 로깅을 모르므로
    `exists` 에 적고 소비는 호출자가 한다."""
    lake = _lake()
    assert decompose(lake, "없는종목", _day()) is None
    assert "없는종목" in lake.exists["layers"]


def test_target_without_todays_row_is_absent_not_stale():
    """당일 행이 없으면 어제 값으로 지어내지 않는다 - 부재로 선다.

    낡은 값으로 회계하면 산출은 나오는데 그 수치가 어제 것이다 - 조용히 틀린
    설명이 부재보다 나쁘다.
    """
    lake = _lake()
    lake.series["T"]["ret"] = lake.series["T"]["ret"][:-1]   # T 만 하루 늦다
    assert decompose(lake, "T", _day()) is None
    assert "layers" in lake.exists


def test_missing_market_layer_leaves_a_reason():
    """시장 층이 조용히 빠지면 남은 섹터·고유가 시장 몫까지 떠안는다 - 사유가 남는다."""
    lake = _lake()
    del lake.series[MARKET_CODE]
    r = decompose(lake, "T", _day())
    assert r is not None
    assert all(x.kind != "시장" for x in r.layers)
    assert MARKET_CODE in lake.exists["market_layer"]
    # 시장 층 부재면 섹터도 차감 기준이 없다 - 섹터를 세우지 않는다.
    assert all(x.kind != "섹터" for x in r.layers)


def test_market_proxy_explaining_itself_is_not_an_absence():
    """대상이 시장 프록시 자신이면 시장 층 없음은 정상이다 - 사유를 적으면
    정상 런마다 존재하지 않는 결손을 신고한다(조용한 부재를 시끄러운 오진으로)."""
    lake = _lake()
    r = decompose(lake, MARKET_CODE, _day())
    assert r is not None
    assert "market_layer" not in lake.exists


def test_a_later_success_clears_an_earlier_absence():
    """한 런이 `decompose` 를 두 번 부른다(라우팅·설명) - 앞 호출의 실패가 남으면
    뒤 호출이 성공해도 커버리지가 실패를 말한다. 부재를 안 지우는 것은 부재를
    지어내는 것과 같다."""
    lake = _lake()
    assert decompose(lake, "없는종목", _day()) is None
    assert "layers" in lake.exists
    assert decompose(lake, "T", _day()) is not None
    assert "layers" not in lake.exists


def test_decompose_tolerates_a_lake_without_coverage_dicts():
    lake = _lake()
    del lake.exists
    assert decompose(lake, "T", _day()) is not None


# ── 섹터 후보: KRX 업종지수뿐이다 (ALPHA-877) ─────────────────────────────
def test_sector_is_the_krx_industry_index_never_a_proxy_etf():
    """섹터의 최종형은 KRX 업종지수다 - 겹침이 좋은 섹터 ETF(SEC 0.15)가 패널에
    있어도 층을 참칭하지 못한다. 지수는 KRX 가 산출하고 업종은 구성종목 최빈이
    정하므로 어느 쪽도 우리 선택이 아니다."""
    r = decompose(_krx_lake(), "T", _day())
    s = next(x for x in r.layers if x.kind == "섹터")
    assert s.code == "KRX:1013"
    assert "전기전자" in s.name
    assert all(x.code not in ("SEC", "TWIN", "ALIEN") for x in r.layers)


def test_daily_without_krx_tables_leaves_sector_absent_with_reason():
    """KRX 표가 없으면 섹터 층은 부재다 - 프록시 ETF 폴백으로 조용히 메우지 않는다
    (폐기한 겹침 선택이 몰래 돌아오면 여기서 잡힌다). 사유가 커버리지에 남는다."""
    lake = _lake()                        # SEC(겹침 0.15)가 패널에 멀쩡히 있다
    r = decompose(lake, "T", _day())
    assert r is not None
    assert any(x.kind == "시장" for x in r.layers)
    assert all(x.kind != "섹터" for x in r.layers)
    assert r.twins == () and r.alien == ()
    assert "업종지수 후보 없음" in lake.exists["sector_layer"]


def test_clock_mode_sector_layer_is_honestly_absent():
    """구간 모드의 섹터 층은 정직 부재다(ALPHA-877) - 업종지수 5분봉이 없고(1분봉은
    ALPHA-887 이 수집하나 롤업·소급이 없어 이 경로가 못 읽는다), 섹터 ETF 5분봉이
    패널에 실려 와도 층이 서면 안 된다(프록시 ETF 겹침 선택이 몰래 부활하면 여기서
    깨진다). 사유가 남고 시장+고유 2층 회계는 그대로 선다."""
    lake = _krx_lake()                    # KRX 일봉 표까지 있어도 5분봉은 아니다
    lake.series["T"]["lr5"] = 0.02
    lake.series[MARKET_CODE]["lr5"] = 0.01
    lake.series["SEC"]["lr5"] = 0.015     # 프록시 후보였던 섹터 ETF 의 구간수익
    lake.series["TWIN"]["lr5"] = 0.02
    r = decompose(lake, "T", _day(), clock=("09:00:00", "10:00:00"))
    assert r is not None
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.contribution == pytest.approx(0.01, abs=1e-12)
    assert all(x.kind != "섹터" for x in r.layers)
    assert r.twins == () and r.alien == ()
    # 사유는 근거를 **둘 다** 담아야 한다: "5분 롤업만 붙이면 된다"로 읽히면 다음
    # 사람이 과거 구간까지 되는 줄 알고 소급 없는 dataset 위에 소비를 얹는다.
    why = lake.exists["sector_layer"]
    assert "5분봉 없음" in why and "소급 불가" in why, why
    # 옛 문구 "분봉 미수집" 은 ALPHA-887 이후 거짓이다 — 되살아나면 여기서 깨진다.
    assert "미수집" not in why, why
    assert sum(x.contribution for x in r.layers) + r.idio == pytest.approx(
        r.total, abs=1e-12), "2층 회계 항등식은 그대로 선다"


# ── 종목 귀속 ─────────────────────────────────────────────────────────────
def test_names_idio_is_return_minus_layer_contributions():
    """종목 고유 = 종목수익 − Σ층기여 (β=1). 순위는 비중 × 고유다 - 큰 종목의
    작은 움직임과 작은 종목의 큰 움직임을 같은 자로 잰다."""
    lake = _lake()
    r = decompose(lake, "T", _day())
    base = sum(x.contribution for x in r.layers)
    for n in r.names:
        assert n.idio == pytest.approx(_now(lake, n.ticker) - base, abs=1e-12)
        assert n.contribution == pytest.approx(n.weight * n.idio, abs=1e-12)
    ranks = [abs(n.contribution) for n in r.names]
    assert ranks == sorted(ranks, reverse=True)


def test_halted_names_are_excluded_and_counted():
    """거래정지 종목의 수익률 0 은 참이 아니다 - 빼되, 뺐다는 사실을 센다."""
    lake = _lake()
    lake.series["a"]["vol"] = [1e6] * 80 + [0]      # 당일만 거래량 0
    r = decompose(lake, "T", _day())
    assert all(n.ticker != "a" for n in r.names)
    assert r.halted == 1


def test_short_history_yields_no_decomposition_not_a_zero():
    """이력이 한 점뿐이면 당일 수익률 자체가 없다 - 0 이 아니라 부재다."""
    lake = _lake()
    for m in lake.series.values():
        m["ret"] = m["ret"][:0]
    assert decompose(lake, "T", DAYS[0].isoformat()) is None


# ── 구조 ──────────────────────────────────────────────────────────────────
def test_overlap_is_weight_intersection():
    lake = _lake()
    assert overlap(lake, "T", "TWIN", _day()) == pytest.approx(1.0)
    assert overlap(lake, "T", "SEC", _day()) == pytest.approx(0.15)
    assert overlap(lake, "T", "ALIEN", _day()) == pytest.approx(0.0)


def test_rollup_is_frozen_dataclass():
    r = decompose(_lake(), "T", _day())
    assert isinstance(r, Rollup)
    with pytest.raises(AttributeError):
        r.total = 0.0


# ── 1분봉 실측 주입 (ALPHA-866) ───────────────────────────────────────────
def test_intraday_overrides_target_and_market_returns():
    """대상·시장의 구간수익은 호출자가 커밋된 1분봉에서 계산한 값이 선다 - 레이크
    `bars_5m` 은 정본이 낡으면 폴백으로 내려가는 별도 경로라, 발화를 만든 봉과 층
    판정이 다른 가격을 보면 안 된다. 이름은 레이크 것을 지킨다."""
    lake = _lake()
    r = decompose(lake, "T", _day(),
                  intraday={"T": (0.02, False), MARKET_CODE: (0.01, False)})
    assert r.total == pytest.approx(0.02, abs=1e-12)
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.contribution == pytest.approx(0.01, abs=1e-12)
    assert m.name == "KODEX 200", "이름은 레이크 것 - 수익률만 갈아 끼운다"
    assert r.total == pytest.approx(
        sum(x.contribution for x in r.layers) + r.idio, abs=1e-12)


def test_intraday_erects_the_market_layer_when_the_lake_is_stale():
    """레이크에 시장 계열이 아예 없어도(스테일 정본) 1분봉 실측이 시장 층을 세운다 -
    '레이크가 낡아서' 시장 층이 빠지면 남은 층이 시장 몫까지 떠안는다."""
    lake = _lake()
    del lake.series[MARKET_CODE]
    r = decompose(lake, "T", _day(),
                  intraday={"T": (0.02, False), MARKET_CODE: (0.01, False)})
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.ret == pytest.approx(0.01, abs=1e-12)
    assert "market_layer" not in lake.exists, "층이 섰는데 부재 사유가 남으면 오진이다"


def test_intraday_constituents_do_not_become_a_layer():
    """`intraday` 에 실려 온 구성종목은 층이 못 된다 - 종목이 층을 참칭하면
    '삼성전자가 섹터로 뽑히는' 그 실수로 돌아간다. 섹터는 KRX 업종지수뿐이고,
    구성종목은 종목 귀속(`_names`)에만 선다."""
    lake = _krx_lake()
    r = decompose(lake, "T", _day(), intraday={
        "T": (0.02, False), MARKET_CODE: (0.01, False),
        "a": (0.5, False), "b": (0.0, False), "c": (0.0, False)})
    sec = next(x for x in r.layers if x.kind == "섹터")
    assert sec.code == "KRX:1013", "구성종목이 섹터 자리를 차지하면 안 된다"
    assert all(x.code not in ("a", "b", "c") for x in r.layers)
    a = next(n for n in r.names if n.ticker == "a")
    base = sum(x.contribution for x in r.layers)
    assert a.ret == pytest.approx(0.5, abs=1e-12)
    assert a.idio == pytest.approx(0.5 - base, abs=1e-12)


def test_intraday_halt_excludes_the_name_and_counts_it():
    """1분봉 실측의 정지 판정(구간 거래량 0)도 레이크 판정과 같은 계약을 탄다 -
    빼되, 뺐다는 사실을 센다."""
    lake = _lake()
    r = decompose(lake, "T", _day(), intraday={
        "T": (0.02, False), MARKET_CODE: (0.01, False),
        "a": (0.1, True), "b": (0.0, False), "c": (0.0, False)})
    assert all(n.ticker != "a" for n in r.names)
    assert r.halted == 1


def test_intraday_mode_market_gap_does_not_fall_back_to_the_lake():
    """커밋 봉 모드에서 시장 결손이면 시장 층을 **세우지 않는다** - 레이크에 시장
    계열이 멀쩡히 있어도. 내려가면 발화를 만든 봉과 판정이 다른 가격을 보는 갈림이
    결손일마다 조용히 부활하고, 원장 어디에도 그 표식이 없다(Rule 12). 차감 기준이
    없으니 섹터도 안 선다."""
    lake = _lake()                    # 레이크에는 시장 계열이 멀쩡히 있다
    r = decompose(lake, "T", _day(), intraday={"T": (0.02, False)})
    assert r is not None
    assert all(x.kind != "시장" for x in r.layers)
    assert "커밋 1분봉 결손" in lake.exists["market_layer"]
    assert all(x.kind != "섹터" for x in r.layers)


def test_intraday_mode_target_gap_is_absence_not_a_lake_read():
    """커밋 봉 모드에서 대상 자신이 결손이면 분해는 부재다 - 레이크의 대상 계열로
    지어내지 않는다. 부재 사유가 남는다."""
    lake = _lake()
    r = decompose(lake, "T", _day(), intraday={MARKET_CODE: (0.01, False)})
    assert r is None
    assert "layers" in lake.exists


def test_intraday_mode_names_exclude_units_the_ledger_dropped():
    """원장이 결손으로 떨어뜨린 구성종목은 귀속에서도 빠진다 - 레이크에 그 종목이
    있어도 되살리지 않는다. dropped_units 와 귀속이 같은 세계를 말해야 한다."""
    lake = _lake()                    # 레이크에는 a·b·c 전부 있다
    r = decompose(lake, "T", _day(), intraday={
        "T": (0.02, False), MARKET_CODE: (0.01, False), "a": (0.3, False)})
    assert {n.ticker for n in r.names} == {"a"}
    assert r.weight_covered == pytest.approx(0.5, abs=1e-12), "커버리지가 얇음을 수치로 말해야 한다"


# ── 광역 ETF 2층 (ALPHA-871) ──────────────────────────────────────────────
def test_broad_market_target_folds_the_sector_layer():
    """대상이 시장과 ≥80% 겹치면 섹터를 세우지 않는다 - 시장의 부분집합에 섹터를
    세우면 시장 몫을 두 번 나눠 갖는 동어반복이다. KRX 업종지수 후보(ALPHA-877)에도
    같은 규칙이 선다. 접은 사실은 커버리지에 남는다."""
    lake = _krx_lake()                    # 안 접으면 KRX 섹터가 설 재료가 있다
    lake.holds["T"] = dict(lake.holds[MARKET_CODE])   # 시장과 완전 겹침
    r = decompose(lake, "T", _day())
    assert r is not None
    assert any(x.kind == "시장" for x in r.layers)
    assert all(x.kind != "섹터" for x in r.layers)
    assert "시장+고유 2층" in lake.exists["sector_layer"]
