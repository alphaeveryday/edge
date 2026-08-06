"""`stability` 도구의 계약 검증 — 뒤집힌 관계는 반드시 뒤집혔다고 말해야 한다.

**못 잡으면 무엇이 거짓으로 납품되는가**: 전·후 부호가 갈린 관계도 전 기간
패널에서는 평균이 섞여 작은 효과 하나 + p 로 보고된다. 산문은 그 부호를 오늘
셀에 적용하므로, 오늘이 반대 부호 국면이면 방향까지 틀린 예측이 나가고 인용된
p 가 그 오류를 통계로 보증한다. 실제로 재현되지 않는 관계를 "검정된 관계" 로
파는 것이라, 표본 크기 다음으로 감사가 찍는 지점이다.

두 번째 검사는 그 반대 실패를 막는다: 한쪽 기간이 얇을 때 조용히 한쪽 결과만
내면 그건 "재현됐다" 로 읽힌다. 부재는 사유와 함께 판정불가여야 한다.
"""
import numpy as np

from edge_analysis.statics.core.paneltest import MIN_N
from edge_analysis.statics.core.surface import TOOLS
from edge_analysis.statics.core import tool_stability  # noqa: F401 - 등록 부수효과

STABILITY = TOOLS["stability"].fn


class PanelLake:
    """점 패널 한 질의만 응답한다. 행 모양은 `_POINT_PANEL` 계약
    (instrument_id, trade_date, ar, 노출열)."""

    def __init__(self, rows):
        self.rows = rows

    def sql(self, q: str):
        assert "'가격잔차'" not in q                  # 어휘가 SQL 에 새면 안 된다
        assert "trade_date < DATE" in q              # PIT - 오늘 이후 사건 없음
        return list(self.rows)


def _panel(sign_late: float, *, n_per_date: int = 12, seed: int = 7):
    """전기 4일 · 후기 4일, 노출 x 에 단조인 ar. 후기 부호만 인자로 뒤집는다."""
    rng = np.random.default_rng(seed)
    rows = []
    for month, sign in (("01", 1.0), ("06", sign_late)):
        for di in range(1, 5):
            x = rng.normal(size=n_per_date)
            ar = sign * 0.02 * x + rng.normal(scale=0.003, size=n_per_date)
            rows += [(f"i{k}", f"2026-{month}-0{di}", float(ar[k]), float(x[k]))
                     for k in range(n_per_date)]
    return rows


def test_same_sign_reproduces_and_flipped_sign_is_reported_as_flipped():
    kw = dict(day="2026-07-27", etype="COMPANY.EARNINGS.RESULT_RELEASE",
              exposure="가격잔차/변동성")
    same = STABILITY(PanelLake(_panel(+1.0)), **kw)
    assert same["verdict"] == "계산됨" and same["stable"] == "재현"
    assert same["split_date"] == "2026-06-01"        # 날짜 중앙값 - 같은 날은 안 걸친다
    assert same["n_early"] >= MIN_N and same["n_late"] >= MIN_N
    assert same["eff_early"] > 0 and same["eff_late"] > 0
    assert 0.0 < same["ratio"] <= 1.0                # 작은쪽/큰쪽
    assert same["p_early"] < 0.05 and same["p_late"] < 0.05

    flip = STABILITY(PanelLake(_panel(-1.0)), **kw)
    assert flip["verdict"] == "계산됨" and flip["stable"] == "뒤집힘"
    assert flip["eff_early"] > 0 > flip["eff_late"]
    assert "방향까지 틀린다" in flip["note"]          # 뒤집힘은 경고와 함께만 나간다

    # 결정론: 같은 입력 → 같은 출력 (SEED 고정 순열)
    assert STABILITY(PanelLake(_panel(-1.0)), **kw) == flip


def test_thin_early_slice_is_unmeasurable_with_reason():
    rows = [(f"i{k}", "2026-01-05", 0.01 * k, float(k)) for k in range(8)]
    rng = np.random.default_rng(3)
    for di in range(1, 7):
        x = rng.normal(size=10)
        ar = 0.02 * x + rng.normal(scale=0.003, size=10)
        rows += [(f"j{k}", f"2026-06-0{di}", float(ar[k]), float(x[k]))
                 for k in range(10)]
    r = STABILITY(PanelLake(rows), day="2026-07-27",
                  etype="COMPANY.EARNINGS.RESULT_RELEASE",
                  exposure="가격잔차/변동성")
    assert r["verdict"] == "판정불가" and r["stable"] == "판정불가"
    assert "전기" in r["reason"] and f"MIN_N={MIN_N}" in r["reason"]
    assert r["n_early"] < MIN_N and r["n_late"] >= MIN_N
    assert r["eff_early"] is None and r["eff_late"] is None   # 한쪽 결과만 내지 않는다
