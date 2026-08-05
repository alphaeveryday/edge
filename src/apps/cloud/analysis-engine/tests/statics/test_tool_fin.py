"""`fin_item` — 재무 692 항목 전량에서 하나를 골라 재는 도구.

가짜 레이크가 **DuckDB 를 실제로 돌린다**. 이 도구의 위험한 부분은 파이썬 산수가
아니라 SQL 안에 있는 두 가지 — PIT 게이트(`make_date(FY+1, 4, 1) <= day`)와 넓은 표
UNPIVOT — 이고, 미리 만든 행 목록을 돌려주는 가짜 레이크는 그 둘을 통째로 건너뛴다.
그러면 "테스트는 통과하고 선견은 남는다". 그래서 로컬 CSV 를 파티션 경로 모양대로
깔고 도구가 만든 질의문을 그대로 실행한다 - 바꾸는 것은 S3 접두사 하나뿐이다.

세 검사가 각각 잡는 버그:

(a) **산수와 척도**. YoY 는 연속 연도 차, z 는 **최신값을 뺀 과거**의 표준편차로
    나눈 것, 횡단면 분위수는 그해 값이 있는 종목 중 비율이다. 최신값을 이력 분포에
    같이 넣으면 극단값이 자기 평균을 끌어올려 z 가 눌리고(드묾을 과소평가) 도구가
    "평범하다" 고 말한다. 같은 검사 안에서 **유니버스 밖 티커**도 확인한다: 값이
    없을 때 0·None 을 조용히 돌려주면 호출자는 그것을 '재무적으로 무사하다' 로 읽는다.

(b) **선견 금지**. FY 마감 직후(1월 5일)에 부르면 그 FY 는 아직 공시되지 않았으므로
    보이면 안 된다. 이 검사가 없으면 도구는 결산 직후 3개월 동안 미래를 읽고, 그
    구간의 모든 엣지가 통계적으로 유의하게 나온다 - 가장 비싼 침묵하는 버그다.

(c) **고르지 않는다**. 이름으로 찾는 경로는 후보만 내고 판정불가로 말해야 한다.
    도구가 47개 중 하나를 골라 주면 그 선택이 근거 없이 결론에 들어가고, 사후에
    "왜 부채비율이 아니라 부채총계였나" 에 답이 없다.
"""
from __future__ import annotations

import gzip
import math
from pathlib import Path

import duckdb
import pytest

from edge_analysis.statics.tool_fin import (BUCKET, FIN, MARKET, MIN_YEARS_Z,
                                            _fin_item)
from edge_analysis.statics.vocab import MIN_N

AS_OF = "2026-08-02"
ITEM = "M000102001"                 # 부채비율 - 수치 항목
TK = "005930"                       # DataGuide 표기는 A005930
FILLERS = 40                        # 횡단면 표본을 MIN_N 위로 올린다(41 >= 30)
YEARS = list(range(2016, 2024))     # FY2016~FY2023
VALS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 20.0]

# 항목 사전. 이름에 콤마가 있는 항목(실측 27개)과 값이 0개인 항목(실측 존재)을 같이
# 넣는다 - 전자는 정규식이 콤마를 먹고, 후자는 후보 정렬이 뒤로 미뤄야 한다.
REPORT = """item_code,item_name,non_null,bytes
M000102001,부채비율(%),42884,101
M000902001,부채총계(천원),44754,202
M000104004,이자보상배율(배),41048,303
M000612002,"DPS(보통주, 결산현금)(원)",58270,404
M000902099,부채비율(전기)(%),0,505

years,8,2016,2023
tickers,41
"""


def _lake(tmp: Path):
    """파티션 경로 모양 그대로 깐 로컬 레이크. 접두사만 바꿔 도구의 SQL 을 그대로 돈다."""
    part = tmp / f"as_of_date={AS_OF}"
    part.mkdir(parents=True, exist_ok=True)
    (part / "_report.csv").write_text(REPORT, encoding="utf-8")
    # `A100099` 는 문자 값만 있는 열이다. 실측 692 항목의 이름은 전부 단위 접미
    # (천원·%·배)를 달고 있어 지금 데이터엔 범주형이 없지만, 항목 전량을 이름으로
    # 여는 도구는 언제든 그런 열을 만난다 - 그때 0·None 을 돌려주면 '값이 0' 으로 읽힌다.
    cols = ["year"] + [f"A1000{i:02d}" for i in range(FILLERS)] + [f"A{TK}", "A100099"]
    lines = [",".join(cols)]
    for y, v in zip(YEARS, VALS):
        # 채움 종목은 해마다 같은 값 i (0..39). 그래서 FY2023 횡단면은 0..39 + 20 이고
        # 20 이하가 21개 + 자기 자신 1개 = 22/41 - 손으로 검산되는 수다.
        lines.append(",".join([str(y)] + [str(float(i)) for i in range(FILLERS)]
                              + [str(v), "해당없음"]))
    d = part / f"item={ITEM}"
    d.mkdir(exist_ok=True)
    with gzip.open(d / f"{ITEM}_부채비율___.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    prefix = f"s3://{BUCKET}/{FIN}{MARKET}"

    class _Lake:
        root = tmp.as_posix() + "/"

        def sql(self, q: str):
            assert prefix in q, "도구가 재무 파티션 경로를 짚지 않았다"
            return duckdb.connect().execute(q.replace(prefix, self.root)).fetchall()

    return _Lake()


def test_yoy_z_and_cross_section_arithmetic(tmp_path):
    r = _fin_item(_lake(tmp_path), day="2025-06-01", ticker=TK, item_code=ITEM)
    assert r["verdict"] == "계산됨", r["reason"]
    assert r["name"] == "부채비율(%)" and r["item_code"] == ITEM
    assert (r["latest_year"], r["latest"], r["prev"]) == (2023, 20.0, 7.0)
    assert r["yoy"] == 13.0 and r["signed"] == 13.0 and r["supports"] is None
    assert r["n_years"] == len(YEARS)
    assert r["available_from"] == "2024-04-01"

    # z 는 **최신값을 뺀** 과거 7년으로 잰다: mean 4, sd(ddof=1) = sqrt(28/6).
    sd = math.sqrt(28 / 6)
    assert r["z"] == pytest.approx((20.0 - 4.0) / sd)
    assert r["z"] > 7                       # 최신값을 분포에 넣으면 4 근처로 눌린다

    assert r["cs_n"] == FILLERS + 1 >= MIN_N
    assert r["cs_pct_rank"] == pytest.approx(22 / 41)
    assert r["note"] == ""

    # 유니버스 밖 티커: 값을 지어내지 않고 **열 부재**를 사유로 말한다.
    miss = _fin_item(_lake(tmp_path), day="2025-06-01", ticker="000660", item_code=ITEM)
    assert miss["verdict"] == "판정불가" and miss["latest"] is None
    assert "A000660" in miss["reason"] and "열 자체가 없다" in miss["reason"]

    # 문자 값만 있는 열: 판정불가 + 사유. 0 이나 None 을 조용히 돌려주면 그 항목이
    # '0 이다' 로 읽히고, 횡단면 표본(41)은 그대로라 아무도 이상을 못 본다.
    cat = _fin_item(_lake(tmp_path), day="2025-06-01", ticker="100099", item_code=ITEM)
    assert cat["verdict"] == "판정불가" and "비수치" in cat["reason"]
    assert cat["n_years"] == 0 and cat["latest"] is None and cat["signed"] is None


def test_fiscal_year_is_invisible_until_available_from(tmp_path):
    # FY2023 마감 직후. 사업보고서 법정 기한(결산 후 90일)이 안 지났으므로 FY2023 은
    # 아직 관측 불가다 - 보이면 선견이고, 그 선견은 조용히 유의한 엣지를 만든다.
    r = _fin_item(_lake(tmp_path), day="2024-01-05", ticker=TK, item_code=ITEM)
    assert r["verdict"] == "계산됨", r["reason"]
    assert r["latest_year"] == 2023 - 1
    assert r["latest"] == 7.0 and r["latest"] != VALS[-1]
    assert r["prev"] == 6.0 and r["yoy"] == pytest.approx(1.0)
    assert r["n_years"] == len(YEARS) - 1
    assert r["available_from"] == "2023-04-01" <= "2024-01-05"

    # 4월 1일에는 열린다 - 게이트가 '언제나 한 해 늦게' 가 아니라 정확히 그 날짜다.
    on = _fin_item(_lake(tmp_path), day="2024-04-01", ticker=TK, item_code=ITEM)
    assert on["latest_year"] == 2023 and on["latest"] == 20.0

    # 이력이 얇으면 z 만 침묵하고 값·YoY 는 그대로 낸다(부재를 0 으로 말하지 않는다).
    thin = _fin_item(_lake(tmp_path), day="2021-06-01", ticker=TK, item_code=ITEM)
    assert thin["latest_year"] == 2020 and thin["n_years"] == 5
    assert thin["z"] is None and str(MIN_YEARS_Z) in thin["note"]
    assert thin["yoy"] == pytest.approx(1.0)


def test_query_lists_candidates_and_refuses_to_pick(tmp_path):
    r = _fin_item(_lake(tmp_path), day="2025-06-01", ticker=TK, query="부채")
    assert r["verdict"] == "판정불가"
    codes = [c["item_code"] for c in r["candidates"]]
    assert codes == ["M000902001", "M000102001", "M000902099"]  # 값 많은 순, 0은 뒤로
    assert r["item_code"] is None and r["latest"] is None and r["z"] is None
    assert "후보 3개" in r["reason"] and "item_code" in r["reason"]

    # 콤마가 든 이름도 사전에 온전히 들어와 있다(정규식이 이름을 자르지 않는다).
    dps = _fin_item(_lake(tmp_path), day="2025-06-01", ticker=TK, query="결산현금")
    assert [c["name"] for c in dps["candidates"]] == ["DPS(보통주, 결산현금)(원)"]

    # 없는 이름은 0 후보 + 사유. 빈 목록을 '해당 없음' 으로 눙치지 않는다.
    zero = _fin_item(_lake(tmp_path), day="2025-06-01", ticker=TK, query="없는항목이름")
    assert zero["verdict"] == "판정불가" and zero["candidates"] == []
    assert "0개" in zero["reason"]

    # 항목도 이름도 없으면 무엇을 재야 하는지가 정해지지 않았다 - 임의로 고르지 않는다.
    none = _fin_item(_lake(tmp_path), day="2025-06-01", ticker=TK)
    assert none["verdict"] == "판정불가" and "item_code" in none["reason"]
