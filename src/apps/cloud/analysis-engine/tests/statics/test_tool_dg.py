"""`dg_catalog`·`dg_probe` — 947 항목 사전을 도구 표면에 여는 두 도구.

이 세 검사가 잡는 버그는 전부 **라이브에서는 그럴듯한 수로 위장되는** 것들이다.

(a) **오늘이 자기 과거 분포에 섞이는 버그**. 섞이면 표본평균이 오늘 쪽으로 끌리고
    표준편차가 오늘의 편차만큼 부풀어 z 가 수축한다 - 극단일수록 수축이 커서
    정확히 알고 싶은 날에 가장 크게 틀린다. 같은 입력으로 '오염된 z' 를 직접
    계산해 실제 z 가 그보다 크다는 것으로 회귀를 막는다.

(b) **범주형을 숫자로 읽는 버그**. `value` 는 VARCHAR 이고 4종은 `'정상'` 같은
    라벨이다. 캐스팅 실패를 0/NaN 으로 접으면 거짓 z 가 나오는데, 그 z 는 수치형
    항목의 z 와 형태가 같아 사후에 구분되지 않는다. 판정불가와 '범주' 사유를 강제한다.

(c) **절단을 침묵하는 버그**. 947 항목 중 40개만 보내고 생략을 말하지 않으면
    에이전트는 받은 목록이 전부라고 믿고 "그런 항목은 없다" 를 결론으로 쓴다.
"""
from __future__ import annotations

import pytest

from edge_analysis.statics.core import tool_dg
from edge_analysis.statics.core.paneltest import MIN_N
from edge_analysis.statics.core.tool_dg import _dg_catalog, _dg_probe

DAY = "2026-06-01"
CODE = "S41000060F"

# 사전 대역. 실제 `items_dict()` 는 S3 를 내려받으므로 검사에서는 갈아 끼운다.
_DICT = tuple((f"S4100{i:03d}", f"항목{i}({'배수' if i % 3 == 0 else '수량'})",
               "price" if i % 2 else "flow", "가격,수익률" if i % 2 else "신용거래")
              for i in range(50)) + ((CODE, "종가(원)", "price", "가격,수익률"),
                                     ("S410002600", "거래정지구분", "price",
                                      "주식수,시가총액"))

@pytest.fixture(autouse=True)
def _fake_items(monkeypatch):
    monkeypatch.setattr(tool_dg, "_items", lambda: _DICT)


class _Lake:
    """가짜 레이크. 질의 세 종류(실재 코드 · 계열 · 횡단면)만 구분해 답한다."""

    def __init__(self, series: list[tuple[str, str]], cs: list[str],
                 live: tuple[str, ...] = (CODE,)):
        self.series, self.cs, self.live = series, cs, live

    def sql(self, q: str):
        if "GROUP BY 2" in q:                       # 그날 실재하는 item_code
            return [(DAY, c) for c in self.live]
        if q.startswith("SELECT trade_date, value"):
            return list(self.series)
        if q.startswith("SELECT value"):
            return [(v,) for v in self.cs]
        raise AssertionError(f"예상 밖 질의: {q[:80]}")


def _hist(n: int = 40) -> list[tuple[str, str]]:
    """0 과 1 을 번갈아 내는 `n` 일 (결정론). 마지막은 1 이라 prev=1 이다."""
    return [(f"2026-04-{1 + k % 28:02d}", str(k % 2)) for k in range(n)]


def test_z_excludes_today_and_cross_section_rank_is_right():
    series = _hist() + [(DAY, "10")]
    cs = [str(i) for i in range(1, 21)]             # 1..20, 오늘 값 10 은 딱 중간
    r = _dg_probe(_Lake(series, cs), day=DAY, ticker="000660", item_code=CODE)

    assert r["verdict"] == "계산됨" and r["kind"] == "수치"
    assert r["n"] == 40 and r["today"] == 10.0 and r["prev"] == 1.0
    assert r["chg"] == 9.0 and r["signed"] == 9.0   # 부호가 뜻을 갖는 양
    assert r["supports"] is None                    # 지지/부정을 판정하지 않는다
    assert r["cs_n"] == 20 and r["cs_pct_rank"] == 0.5

    # 과거만으로 낸 z: mean=0.5, sd=sqrt(10/39).
    import numpy as np
    past = np.array([float(v) for _, v in _hist()])
    want = (10.0 - past.mean()) / past.std(ddof=1)
    assert abs(r["z"] - round(float(want), 6)) < 1e-9

    # 오늘을 분포에 넣었다면(=버그) z 가 이만큼 수축한다. 실제 z 가 더 커야 한다.
    dirty = np.append(past, 10.0)
    z_dirty = (10.0 - dirty.mean()) / dirty.std(ddof=1)
    assert r["z"] > z_dirty * 3                     # 실측 18.8 vs 3.2

    # 표본이 얇으면 z 는 침묵하되 오늘 값·횡단면은 그대로 말한다(부재≠무변화).
    thin = _dg_probe(_Lake(_hist(MIN_N - 1) + [(DAY, "10")], cs),
                     day=DAY, ticker="000660", item_code=CODE)
    assert thin["z"] is None and thin["n"] == MIN_N - 1
    assert str(MIN_N) in thin["note"] and thin["today"] == 10.0


def test_categorical_item_is_undecidable_with_a_categorical_reason():
    cs = ["정상"] * 8 + ["거래정지"] * 2 + ["관리"]
    r = _dg_probe(_Lake(_hist() + [(DAY, "정상")], cs),
                  day=DAY, ticker="000660", item_code="S410002600")

    assert r["verdict"] == "판정불가" and "범주" in r["reason"]
    assert r["kind"] == "범주" and r["z"] is None and r["signed"] is None
    assert r["today"] == "정상"                     # 원값을 그대로 말한다
    assert [c["value"] for c in r["top_categories"]] == ["정상", "거래정지", "관리"]
    assert r["top_categories"][0]["n"] == 8

    # 항목은 수치형인데 이 종목만 결측인 경우는 **다른 사유**여야 한다.
    miss = _dg_probe(_Lake(_hist() + [(DAY, "-")], [str(i) for i in range(1, 21)]),
                     day=DAY, ticker="000660", item_code=CODE)
    assert miss["verdict"] == "판정불가" and miss["kind"] == "수치"
    assert "범주" not in miss["reason"] and "결측" in miss["reason"]


def test_catalog_respects_limit_and_reports_what_it_omitted(monkeypatch):
    monkeypatch.setattr(tool_dg, "_items", lambda: _DICT)
    lake = _Lake([], [], live=(CODE, "S4100003"))

    r = _dg_catalog(lake, day=DAY, limit=5)
    assert r["verdict"] == "계산됨"
    assert r["n_dict"] == len(_DICT) and len(r["items"]) == 5
    assert r["omitted"] == len(_DICT) - 5           # 생략을 침묵하지 않는다
    assert sum(r["groups"].values()) == len(_DICT)  # 그룹 수는 **전량** 기준
    assert r["n_live"] == 2
    # live 를 앞에 둔다 - 잘릴 때 잴 수 있는 것부터 남는다.
    assert [i["code"] for i in r["items"][:2]] == ["S4100003", CODE]
    assert r["signed"] is None and r["supports"] is None

    # 상한을 넘겨도 MAX_ITEMS 에서 잘린다.
    big = _dg_catalog(lake, day=DAY, limit=10_000)
    assert len(big["items"]) == min(len(_DICT), tool_dg.MAX_ITEMS)

    # 일치가 0개면 빈 목록이 아니라 판정불가 + 사유다.
    none = _dg_catalog(lake, day=DAY, query="연금")
    assert none["verdict"] == "판정불가" and "연금" in none["reason"]
    assert none["items"] == [] and none["n_dict"] == len(_DICT)

    # 부분일치는 진짜로 거른다 — 그리고 **분류에도** 걸린다(라이브 실측: '배수' 는
    # 이름에 없고 category='주가배수' 에만 있어 이름만 보면 20종이 0건이 됐다).
    hit = _dg_catalog(lake, day=DAY, query="배수", limit=100)
    assert 0 < len(hit["items"]) < len(_DICT)
    assert all("배수" in i["name"] for i in hit["items"])

    cat = _dg_catalog(lake, day=DAY, query="신용", limit=100)
    assert cat["verdict"] == "계산됨"
    assert all(i["category"] == "신용거래" for i in cat["items"])
    assert all("신용" not in i["name"] for i in cat["items"])   # 이름엔 없다
