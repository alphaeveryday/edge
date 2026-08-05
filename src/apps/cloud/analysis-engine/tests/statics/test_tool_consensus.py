"""`consensus_revision` — 전방 이익 기대의 개정을 잰다.

세 검사가 각각 하나씩 잡는다.

(a) **개정률·부호 버그.** 로그 개정률의 분자·분모를 뒤집으면 상향이 하향으로 나오고,
    그 부호는 `signed` 를 타고 신뢰성 검사로 들어가 "실적 우려로 빠졌다" 를 지지하는
    근거가 된다. 상향·하향 두 계열에 같은 코드를 태워 부호와 `supports` 가 뒤집히는지
    본다.

(b) **선견.** 이게 핵심이다. `as_of_date > day` 인 스냅샷을 하나라도 읽으면 이 도구는
    "아직 나오지 않은 컨센서스로 오늘을 설명" 하게 되고, 그 결과는 백테스트에서만
    좋고 납품에서는 거짓이다. 컨센서스는 주간 격자라 미래 스냅샷 하나가 `latest` 를
    통째로 갈아치운다 - 티가 안 나면서 결과는 전부 바뀐다. 필터를 SQL 에만 두면
    가짜 레이크로 이 검사를 할 수 없으므로, 파이썬 필터가 살아 있는지를 여기서 못
    박는다: 미래 행을 섞은 레이크와 안 섞은 레이크의 결과가 **완전히 같아야** 한다.

(c) **부재를 침묵으로 말하는 버그.** 149 컨센서스 코드 중 947 항목 사전에 있는 것은
    0 개다(실측). 이름 모르는 코드를 조용히 빼면 호출자는 "그 종목엔 그 항목이 없다"
    로 읽는다 - 실제로는 값이 있고 우리가 이름을 모를 뿐이다. 범주형('IC')·일자
    ('20260727')·미해소 수치 세 종류가 각각 **다른 사유**로 제외 목록에 남아야 한다.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from edge_analysis.statics.paneltest import MIN_N
from edge_analysis.statics.tool_consensus import _consensus_revision

DAY = "2026-07-31"
K0 = date(2025, 6, 2)           # 주간 격자의 시작 (월요일)
N_SNAP = 61                     # 0..60 → 2025-06-02 ~ 2026-07-27
K_LAST, K_PRIOR = 60, 47        # day-90=2026-05-02 이하의 최신 스냅샷이 k=47 이다

# 다섯 항목 중 EPS·순이익만 쓰고 나머지는 제외 경로를 보려고 일부러 뺀다.
EPS, NET = "FM30011505", "FM30011450"
# 제외되어야 할 것들: 범주형 · 결산월 · 추정기준일 · 이름 미해소 수치
OTHERS = {"FM30010000": "IC", "FM30012000": "202612",
          "FM30011680": "20260727", "FM30011370": "4036536.82"}

# **연월을 닮은 진짜 수치.** 라이브에서 실제로 샜다: A005930 의 FM30041256=207611
# 이 일자 코드로 오분류됐다. 최신 스냅샷 값만 보면 `2026|12` 로 읽히지만 직전
# 스냅샷은 450000 이라 '언제나 그 꼴' 이 아니다 - 그 차이가 판정의 근거다.
LOOKALIKE = "FM30041256"
LOOKALIKE_VALS = {"prev": "450000", "last": "202612"}


def _snap(k: int) -> str:
    return str(K0 + timedelta(days=7 * k))


def _val(k: int, drift: float) -> float:
    """결정론적 톱니 + 기하 추세. 톱니가 없으면 개정 이력의 분산이 0 이라 z 가
    성립하지 않고(그건 별개의 정당한 침묵이다) 이 검사가 z 경로를 못 밟는다.

    `%.6f` 로 레이크에 넣으므로 기대값도 같은 자리에서 자른다 - 안 그러면 이
    검사는 도구가 아니라 문자열 왕복 오차를 재게 된다."""
    return round(1000.0 * (drift ** k) * (1.0 + 0.002 * ((k % 5) - 2)), 6)


class _Lake:
    """가짜 레이크. 자기이력 질의와 횡단면 질의를 SQL 모양으로 갈라 답한다.

    `future=True` 면 `day` 이후 스냅샷을 **두 질의 모두에** 섞어 넣는다 - 실제 S3
    파티션은 그런 행을 주지 않지만, 그래서 SQL `WHERE` 하나에만 기대면 이 규율이
    검사되지 않은 채 남는다. 검사되지 않는 규율은 유지되지 않는다.
    """

    def __init__(self, drift: float = 1.01, future: bool = False,
                 peers: int = 40):
        self.drift, self.future, self.peers = drift, future, peers

    @property
    def _slow(self) -> float:
        """순이익은 EPS 의 **절반 속도**로 고친다. 두 항목의 개정률이 같으면
        `headline`(최대 |rev_pct|)이 동점이 되어 무엇을 고르는지 검사가 못 본다."""
        return 1.0 + (self.drift - 1.0) / 2

    def _own(self) -> list[tuple]:
        rows: list[tuple] = []
        for k in range(N_SNAP):
            d = _snap(k)
            rows.append((d, EPS, f"{_val(k, self.drift):.6f}"))
            rows.append((d, NET, f"{_val(k, self._slow) * 7.2:.6f}"))
            if k == K_LAST - 1:
                rows.append((d, LOOKALIKE, LOOKALIKE_VALS["prev"]))
            if k == K_LAST:
                rows += [(d, c, v) for c, v in OTHERS.items()]
                rows.append((d, LOOKALIKE, LOOKALIKE_VALS["last"]))
        if self.future:
            # 미래 스냅샷 두 개. 값을 극단으로 틀어 놓아 새어 들어오면 반드시 티가 난다.
            for extra, mult in ((61, 5.0), (62, 0.1)):
                d = _snap(extra)
                rows.append((d, EPS, f"{_val(K_LAST, self.drift) * mult:.6f}"))
                rows.append((d, NET, f"{_val(K_LAST, self._slow) * 7.2 * mult:.6f}"))
        return rows

    def _xs(self) -> list[tuple]:
        rows: list[tuple] = []
        for i in range(self.peers):
            # 동료 종목은 -1% 부터 촘촘히 상향. 우리 종목(+12.9%)이 상위에 오도록.
            g = 1.0 + (i - 20) * 0.0005
            for code, base in ((EPS, 1000.0), (NET, 7200.0)):
                rows.append((_snap(K_PRIOR), f"A{i:06d}", code, f"{base:.6f}"))
                rows.append((_snap(K_LAST), f"A{i:06d}", code, f"{base * g:.6f}"))
        if self.future:
            for i in range(self.peers):
                rows.append((_snap(61), f"A{i:06d}", EPS, "999999"))
        return rows

    def sql(self, q: str):
        assert "as_of_date <= DATE" in q or "as_of_date IN" in q
        return self._xs() if "item_code IN" in q else self._own()


def _rev_expected(drift: float) -> float:
    return math.log(_val(K_LAST, drift) / _val(K_PRIOR, drift))


def test_revision_rate_and_sign_are_right():
    up = _consensus_revision(_Lake(1.01), day=DAY, ticker="000660", claim_sign=1)
    assert up["verdict"] == "계산됨" and up["fiscal_year"] == 2026
    assert up["as_of_used"] == {"latest": _snap(K_LAST), "prior": _snap(K_PRIOR),
                                "n_grid": N_SNAP}

    eps = up["items"][EPS]
    assert eps["rev_pct"] == round(_rev_expected(1.01) * 100, 4)
    assert eps["latest"] == _val(K_LAST, 1.01) and eps["prior"] == _val(K_PRIOR, 1.01)
    assert eps["name"] == "미해소" and "EPS" in eps["label"]   # 사전엔 없다, 근거는 있다
    assert eps["n_obs"] >= MIN_N and eps["z"] is not None
    assert eps["x_n"] == 40 and eps["x_pct"] == 1.0            # 동료 전부보다 큰 상향

    # 방향은 EPS 가 준다(§18 이 포기한 분자 채널). 부호가 곧 뜻이다.
    assert up["signed"] > 0 and up["supports"] is True
    # headline 은 최대 |rev_pct| 항목이고(여기선 EPS, 순이익은 절반 속도),
    # **이름과 근거가 한 문자열**이다 - 떼어 놓으면 산문이 근거를 흘린다.
    assert up["headline"].startswith("EPS(컨센서스 평균) [문서실측")
    assert abs(eps["rev_pct"]) > abs(up["items"][NET]["rev_pct"])

    down = _consensus_revision(_Lake(0.99), day=DAY, ticker="000660", claim_sign=1)
    assert down["signed"] < 0 and down["supports"] is False
    assert down["items"][EPS]["rev_pct"] == round(_rev_expected(0.99) * 100, 4)

    # 주장 부호를 모르면 지지 여부를 말하지 않는다 - 상향 자체는 어떤 주장도 아니다.
    mute = _consensus_revision(_Lake(1.01), day=DAY, ticker="000660")
    assert mute["supports"] is None and mute["signed"] > 0


def test_future_snapshots_change_nothing():
    """선견 금지 회귀. 미래 스냅샷이 새면 `latest` 가 갈리고 결과가 전부 바뀐다."""
    clean = _consensus_revision(_Lake(1.01), day=DAY, ticker="000660", claim_sign=1)
    dirty = _consensus_revision(_Lake(1.01, future=True), day=DAY,
                                ticker="000660", claim_sign=1)
    assert dirty == clean
    assert dirty["as_of_used"]["latest"] == _snap(K_LAST) <= DAY


def test_categorical_and_unnamed_codes_are_excluded_with_a_reason():
    r = _consensus_revision(_Lake(1.01), day=DAY, ticker="000660")
    assert set(r["items"]) == {EPS, NET}
    assert set(r["excluded"]) == set(OTHERS) | {LOOKALIKE}

    # 네 부재는 서로 다른 사건이다 - 사유가 같으면 구분이 사라진다.
    assert "범주형" in r["excluded"]["FM30010000"]
    assert "일자·결산월" in r["excluded"]["FM30012000"]
    assert "일자·결산월" in r["excluded"]["FM30011680"]
    assert "사전 미해소" in r["excluded"]["FM30011370"]        # 수치인데 이름을 모른다
    # 최신 값이 '202612' 라도 이력이 그 꼴이 아니면 일자 코드가 아니다(라이브 회귀).
    assert "사전 미해소" in r["excluded"][LOOKALIKE]
    assert f"이름 미확정 {len(OTHERS) + 1}개 제외" in r["note"]

    class Dead:
        def sql(self, q):
            raise RuntimeError("Catalog Error: rdb does not exist")

    d = _consensus_revision(Dead(), day=DAY, ticker="000660")
    assert d["verdict"] == "판정불가" and "RuntimeError" in d["reason"]
    assert d["signed"] is None and d["items"] == {}           # 수를 지어내지 않는다
