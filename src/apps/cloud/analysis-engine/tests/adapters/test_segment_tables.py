"""부문 표 파서 — **실제 공시 표 세 형태를 그대로 넣는다.**

합성 픽스처로는 이 파서를 검증할 수 없다. 이 파일이 지키는 것은 "표가 회사마다 다르다"는
사실이고, 그 사실은 실물에만 있다. 그래서 아래 표본은 실제 panel 데이터에서 잘라 왔다:

    삼성전자 2026Q1   부문 | 주요제품 | 매출액 | 비중
    SK하이닉스 2026Q1 사업부문 | ... | 제79기 1분기 | 제78기 | 제77기   (기간 3개)
    POSCO 2026Q1     사업부문(ROWSPAN) | 품목 | 용도 | 3기간 × (매출액, 비율)

마지막 것이 핵심이다. 부문 이름이 ROWSPAN 으로 병합돼 있어 격자를 펴지 않으면 열연·냉연
행이 부문 없이 남고 조용히 빠진다 - **결손이 파싱 실패로 위장되는 경로**다.
"""
from __future__ import annotations

import pytest

from edge_analysis.adapters.segment_tables import check, grid, parse_table

SAMSUNG = """<TABLE>
<TR><TH>부  문</TH><TH>주요 제품</TH><TH>매출액</TH><TH>비중</TH></TR>
<TR><TD>DX 부문</TD><TD>TV, 모니터, 냉장고</TD><TD>526,547</TD><TD>39.3%</TD></TR>
<TR><TD>DS 부문</TD><TD>DRAM, NAND Flash</TD><TD>817,156</TD><TD>61.0%</TD></TR>
<TR><TD>SDC</TD><TD>스마트폰용 OLED패널 등</TD><TD>66,935</TD><TD>5.0%</TD></TR>
<TR><TD>Harman</TD><TD>디지털 콕핏</TD><TD>38,263</TD><TD>2.9%</TD></TR>
<TR><TD>기타</TD><TD>부문간 내부거래 제거 등</TD><TD>△110,167</TD><TD>△8.2%</TD></TR>
<TR><TD>총 계</TD><TD></TD><TD>1,338,734</TD><TD>100.0%</TD></TR>
</TABLE>"""

HYNIX = """<TABLE>
<TR><TH>사업부문</TH><TH>매출유형</TH><TH>품목</TH>
    <TH>제79기 1분기 매출액</TH><TH>제78기 매출액</TH><TH>제77기 매출액</TH></TR>
<TR><TD>반도체 부문</TD><TD>제품 외</TD><TD>DRAM, NAND Flash 등</TD>
    <TD>52,576,287</TD><TD>97,146,675</TD><TD>66,192,960</TD></TR>
<TR><TD>합계</TD><TD></TD><TD></TD>
    <TD>52,576,287</TD><TD>97,146,675</TD><TD>66,192,960</TD></TR>
</TABLE>"""

POSCO = """<TABLE>
<TR><TH ROWSPAN="2">사업부문</TH><TH ROWSPAN="2">품목</TH>
    <TH COLSPAN="2">2026년 (제59기) 1분기</TH><TH COLSPAN="2">2025년 (제58기)</TH></TR>
<TR><TH>매출액</TH><TH>비율</TH><TH>매출액</TH><TH>비율</TH></TR>
<TR><TD ROWSPAN="3">철강부문</TD><TD>열연</TD>
    <TD>30,634</TD><TD>20.5%</TD><TD>122,271</TD><TD>20.6%</TD></TR>
<TR><TD>냉연</TD><TD>49,397</TD><TD>33.0%</TD><TD>190,715</TD><TD>32.1%</TD></TR>
<TR><TD>스테인레스</TD><TD>22,546</TD><TD>15.1%</TD><TD>97,478</TD><TD>16.4%</TD></TR>
<TR><TD>인프라부문</TD><TD>무역</TD>
    <TD>46,423</TD><TD>31.0%</TD><TD>183,000</TD><TD>30.9%</TD></TR>
<TR><TD>합 계</TD><TD></TD>
    <TD>149,000</TD><TD>100.0%</TD><TD>593,464</TD><TD>100.0%</TD></TR>
</TABLE>"""


def test_rowspan_is_expanded_so_merged_segment_rows_are_not_lost():
    """격자를 펴지 않으면 열연·냉연이 부문 없이 남아 조용히 빠진다."""
    g = grid(POSCO)

    assert [r[0] for r in g[2:6]] == ["철강부문", "철강부문", "철강부문", "인프라부문"]
    # COLSPAN 도 전개된다 - 머리행 두 줄이 열 개수가 같아야 열 짝짓기가 성립한다
    assert len(g[0]) == len(g[1]) == len(g[2])


def test_a_clean_four_column_table_yields_segments_with_shares():
    rows = [r for r in parse_table(SAMSUNG) if not r["is_total"]]

    assert [r["segment"] for r in rows] == ["DX 부문", "DS 부문", "SDC", "Harman", "기타"]
    assert rows[0]["value"] == 526547 and rows[0]["share"] == pytest.approx(0.393)
    # △ 는 음수다. 내부거래 제거를 양수로 읽으면 부문 합이 총계를 넘는다.
    assert rows[-1]["value"] == -110167 and rows[-1]["share"] == pytest.approx(-0.082)


def test_multiple_period_columns_all_come_out_labelled():
    """기간이 여러 개면 전부 낸다 - 어느 기간인지는 머리 문자열이 말한다."""
    rows = parse_table(HYNIX)
    labels = {r["period_label"] for r in rows}

    assert len(labels) == 3
    assert any("제79기" in x for x in labels) and any("제77기" in x for x in labels)
    seg = [r for r in rows if not r["is_total"] and "제79기" in r["period_label"]]
    assert seg[0]["segment"] == "반도체 부문" and seg[0]["value"] == 52576287


def test_the_parts_must_add_up_to_the_total_and_that_is_the_self_check():
    """부문 합 = 합계. **안 맞으면 행을 놓쳤거나 부호를 틀렸다.**"""
    for name, xml in (("삼성", SAMSUNG), ("하이닉스", HYNIX)):
        for label, c in check(parse_table(xml)).items():
            assert c["ok"], f"{name} {label}: parts={c['parts']} total={c['total']}"

    # POSCO 는 기간별로 따로 봐야 맞는다 - 라벨을 섞으면 서로 다른 기간을 더한다.
    got = check(parse_table(POSCO))
    assert len(got) == 2
    assert all(c["n_segments"] >= 4 for c in got.values())


def test_a_table_that_puts_data_rows_in_TH_is_not_swallowed_as_header():
    """실측 회귀(SK하이닉스). **자릿점이 머리와 데이터를 가른다.**

    DART 표는 데이터 행에도 `<TH>` 를 쓰는 곳이 있다. TH 만 세면 데이터가 머리에 먹혀
    `period_label` 이 `매출액 52,576,287` 이 되고 부문 행이 0개가 된다 - 조용한 결손이다.
    기간 라벨의 수(2026·59)에는 콤마가 없고 금액에는 있다.
    """
    th_data = """<TABLE>
    <TR><TH>사업부문</TH><TH>매출액</TH></TR>
    <TR><TH>반도체 부문</TH><TH>52,576,287</TH></TR>
    <TR><TH>합계</TH><TH>52,576,287</TH></TR>
    </TABLE>"""

    rows = [r for r in parse_table(th_data) if not r["is_total"]]

    assert [r["segment"] for r in rows] == ["반도체 부문"]
    assert rows[0]["period_label"] == "매출액" and rows[0]["value"] == 52576287


def test_a_subtotal_row_is_not_counted_as_a_segment():
    """실측 회귀(POSCO). **부문 칸만 보면 소계가 부문으로 세어진다.**

    부문 이름은 ROWSPAN 으로 채워지고 소계 여부는 품목 칸에 적힌다. 그래서 라벨 칸 전부를
    봐야 한다 - 안 보면 소계가 품목과 함께 더해져 합이 총계의 몇 배가 된다(관측 216%).
    """
    with_subtotal = """<TABLE>
    <TR><TH>사업부문</TH><TH>품목</TH><TH>매출액</TH></TR>
    <TR><TD ROWSPAN="3">철강부문</TD><TD>열연</TD><TD>30,000</TD></TR>
    <TR><TD>냉연</TD><TD>50,000</TD></TR>
    <TR><TD>소계</TD><TD>80,000</TD></TR>
    <TR><TD>인프라부문</TD><TD>무역</TD><TD>20,000</TD></TR>
    <TR><TD>합 계</TD><TD></TD><TD>100,000</TD></TR>
    </TABLE>"""

    rows = parse_table(with_subtotal)
    parts = [r for r in rows if not r["is_total"]]

    assert [r["value"] for r in parts] == [30000, 50000, 20000]
    assert all(c["ok"] for c in check(rows).values())   # 100,000 과 맞는다


def test_a_percent_column_under_a_revenue_span_is_not_stored_as_revenue():
    """실측 회귀(000500). **열의 정체는 가장 깊은 머리행이 정한다.**

    `매출액 (비율)` 이 COLSPAN 으로 두 칸을 덮으면 두 칸이 같은 머리 문자열을 물려받는다.
    합친 문자열만 보면 비율 칸도 매출 열이 되어 `50.05` 라는 퍼센트가 매출액으로 저장된다 -
    그러면 노출도 회귀가 퍼센트와 금액을 같은 변수로 섞는다.
    """
    spanned = """<TABLE>
    <TR><TH ROWSPAN="2">사업부문</TH><TH COLSPAN="2">매출액 (비율)</TH></TR>
    <TR><TH>매출액</TH><TH>비율</TH></TR>
    <TR><TD>전력사업부</TD><TD>429,309</TD><TD>50.05</TD></TR>
    <TR><TD>기타사업부</TD><TD>428,451</TD><TD>49.95</TD></TR>
    </TABLE>"""

    rows = parse_table(spanned)

    assert [r["value"] for r in rows] == [429309, 428451]
    assert all(r["value"] > 1000 for r in rows), "퍼센트가 매출로 저장됐다"


def test_a_table_without_a_total_row_is_checked_by_its_shares_instead():
    """검사가 둘인 이유는 표 모양이 달라서다 - 기준을 느슨하게 한 것이 아니다.

    합계 행이 없으면 부문 합을 견줄 대상이 없다. 그때 비중 열이 있으면 **비중 합 = 100%**
    가 같은 역할을 한다. 둘 다 없으면 검사할 수 없으므로 채택하지 않는다.
    """
    no_total = """<TABLE>
    <TR><TH>부문</TH><TH>매출액</TH><TH>비중</TH></TR>
    <TR><TD>가전</TD><TD>60,000</TD><TD>60.0%</TD></TR>
    <TR><TD>부품</TD><TD>40,000</TD><TD>40.0%</TD></TR>
    </TABLE>"""

    got = check(parse_table(no_total))["매출액"]
    assert got["ok"] and got["by"] == "share" and got["total"] is None

    # 비중이 100% 를 벗어나면 행을 놓친 것이다 - 통과시키지 않는다.
    partial = no_total.replace("40.0%", "20.0%")
    assert check(parse_table(partial))["매출액"]["ok"] is False

    # 합계도 비중도 없으면 검사 불가 - 침묵하지 않고 사유를 남긴다.
    bare = """<TABLE><TR><TH>부문</TH><TH>매출액</TH></TR>
              <TR><TD>가전</TD><TD>60,000</TD></TR></TABLE>"""
    bad = check(parse_table(bare))["매출액"]
    assert bad["ok"] is False and "비중" in bad["by"]


def test_a_table_without_a_revenue_header_yields_nothing_instead_of_guessing():
    """근거 없이 '숫자가 있는 마지막 열'을 쓰면 아무 표에서나 값이 나온다."""
    other = """<TABLE><TR><TH>구분</TH><TH>인원</TH></TR>
               <TR><TD>정규직</TD><TD>1,234</TD></TR></TABLE>"""

    assert parse_table(other) == []
    assert parse_table("") == []
    assert parse_table("<TABLE></TABLE>") == []


def test_a_revenue_breakdown_without_a_segment_header_is_not_adopted():
    """부문 머리가 없는 표는 **부문 표가 아니다.**

    WHY: 0번 열로 떨어뜨리면 매출을 종류별로 쪼갠 평범한 표(`구분 | 매출액`)가 부문 표로
    채택된다. 합계가 맞으니 자기검사도 통과시키고, 그러면 부문이 아닌 것이 부문 노출도로
    코호트에 흘러들어 노출도 회귀가 조용히 틀린 답을 낸다.
    """
    kinds = """<TABLE><TR><TH>구분</TH><TH>매출액</TH></TR>
               <TR><TD>제품</TD><TD>60,000</TD></TR>
               <TR><TD>상품</TD><TD>40,000</TD></TR>
               <TR><TD>합계</TD><TD>100,000</TD></TR></TABLE>"""

    assert parse_table(kinds) == [], "부문 선언이 없는 표가 채택됐다"


def test_a_segment_named_with_the_total_syllable_is_still_a_segment():
    """`기계부문` 은 합계가 아니다.

    WHY: `계` 를 부분문자열로 찾으면 그 음절을 품은 정상 부문명이 합계로 접혀 `parts` 에서
    빠진다. 그러면 부문 합이 총계보다 작아져 **정상 표가 자기검사에서 떨어진다**(60% 갭).
    단독 라벨 `계` 만 총계로 본다.
    """
    machines = """<TABLE><TR><TH>부문</TH><TH>매출액</TH></TR>
                  <TR><TD>기계부문</TD><TD>60,000</TD></TR>
                  <TR><TD>전자부문</TD><TD>40,000</TD></TR>
                  <TR><TD>합계</TD><TD>100,000</TD></TR></TABLE>"""

    rows = parse_table(machines)
    assert [r["segment"] for r in rows if not r["is_total"]] == ["기계부문", "전자부문"]
    assert check(rows)["매출액"]["ok"] is True


def test_a_standalone_total_label_is_still_detected():
    """반대 방향 - 단독 `계` 를 놓치면 총계가 부문으로 더해져 합이 두 배가 된다."""
    bare_total = """<TABLE><TR><TH>부문</TH><TH>매출액</TH></TR>
                    <TR><TD>기계부문</TD><TD>60,000</TD></TR>
                    <TR><TD>전자부문</TD><TD>40,000</TD></TR>
                    <TR><TD>계</TD><TD>100,000</TD></TR></TABLE>"""

    rows = parse_table(bare_total)
    assert [r["is_total"] for r in rows] == [False, False, True]
    assert check(rows)["매출액"]["ok"] is True


def test_a_share_column_without_percent_signs_is_still_read():
    """비율 칸에 `%` 가 없는 표(실측 000500)가 **채택 근거를 잃지 않아야 한다.**

    WHY: 합계 행이 없는 표는 "비중 합 = 100%" 가 유일한 자기검사다. `%` 를 요구하면 그
    표의 share 가 전부 None 이 되어 `by="share"` 채택이 통째로 죽는다 - 열의 정체는
    머리행이 이미 정했으므로 기호는 부수적이다.
    """
    no_total = """<TABLE><TR><TH>부문</TH><TH>매출액</TH><TH>비율</TH></TR>
                  <TR><TD>기계부문</TD><TD>60,000</TD><TD>60.00</TD></TR>
                  <TR><TD>전자부문</TD><TD>40,000</TD><TD>40.00</TD></TR></TABLE>"""

    rows = parse_table(no_total)
    assert [r["share"] for r in rows] == [pytest.approx(0.60), pytest.approx(0.40)]
    got = check(rows)["매출액"]
    assert got["ok"] is True and got["by"] == "share"


def test_an_amount_in_the_share_column_is_not_read_as_a_ratio():
    """반대 방향 - 자릿점 금액을 비율로 읽으면 6,000% 짜리 비중이 생긴다."""
    wrong = """<TABLE><TR><TH>부문</TH><TH>매출액</TH><TH>비율</TH></TR>
               <TR><TD>기계부문</TD><TD>60,000</TD><TD>60,000</TD></TR></TABLE>"""

    assert [r["share"] for r in parse_table(wrong)] == [None]


def test_entities_in_the_header_do_not_hide_the_segment_column():
    """`부 &nbsp;문` 처럼 자간을 엔티티로 벌린 머리행이 실측에 있다.

    WHY: 엔티티를 풀지 않으면 그 문자열은 공백 정규화로도 `부문` 과 안 맞고, 부문 머리를
    못 찾아 표가 통째로 버려진다 - 파서가 조용히 아무것도 안 내는 최악의 실패다.
    """
    spaced = """<TABLE><TR><TH>부 &nbsp;문</TH><TH>매출액</TH></TR>
                <TR><TD>기계부문</TD><TD>60,000</TD></TR>
                <TR><TD>전자부문</TD><TD>40,000</TD></TR>
                <TR><TD>합계</TD><TD>100,000</TD></TR></TABLE>"""

    rows = parse_table(spaced)
    assert [r["segment"] for r in rows if not r["is_total"]] == ["기계부문", "전자부문"]


def test_a_combined_amount_and_share_column_is_still_revenue():
    """`매출액(비율)` 한 칸에 금액과 비중이 함께 오는 표(실측)가 버려지면 안 된다.

    WHY: 비율 낱말 때문에 그 칸을 제외하면 매출 열이 하나도 남지 않아 표 전체가 포기된다.
    한 칸에서 금액과 비중을 각각 뽑을 수 있으므로, 순수 매출 열이 없을 때만 폴백한다.
    """
    combined = """<TABLE><TR><TH>사업부문</TH><TH>매출액(비율)</TH></TR>
                  <TR><TD>반도체 부문</TD><TD>60,000(60.0%)</TD></TR>
                  <TR><TD>기타 부문</TD><TD>40,000(40.0%)</TD></TR></TABLE>"""

    rows = parse_table(combined)
    assert [r["value"] for r in rows] == [60000.0, 40000.0]
    assert [r["share"] for r in rows] == [pytest.approx(0.60), pytest.approx(0.40)]
    assert check(rows)["매출액(비율)"]["ok"] is True
