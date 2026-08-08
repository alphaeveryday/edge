"""사업 구조 — 광의의 펀더멘탈 중 **관측 가능한 매출 노출**.

왜 이 도구가 있는가. "고객사가 투자를 줄여서 빠졌어요" 는 설명처럼 들리지만
그 고객사가 이 회사 매출의 몇 %인지가 없으면 검정 불가능한 서사다. 금융권
크레딧 심사가 보는 두 숫자가 정확히 이것이다 — **매출 집중도**(한 거래처에
얼마나 걸려 있나)와 **계약 만기 구조**(그 매출이 언제 끊기나). 이 저장소는
이미 '노출은 관측된 것이어야 한다'를 규율로 삼는다. 사업 구조가 그 관측이다.

원천은 DART 단일판매·공급계약 체결 공시(`s3_supply_fact`)다. 이 표의
`ratio_pct` 는 우리가 만든 파생치가 아니라 **공시자가 신고한 최근 매출액 대비
비율**이다 — 그래서 이 도구의 1차 지표는 매출 비율이고, 재무제표
(`s3_statement_line`)로 내는 `contract_to_revenue` 는 독립 교차검증일 뿐이다.

**커버리지가 얇다. 그것을 숨기지 않는 것이 이 모듈의 절반이다.**
2026-08-04 실측: `s3_supply_fact` 53행 · 26종목. `pit_daily` 의 8,707종목
기준 0.3%다. 그러니 이 도구가 답할 수 있는 종목은 26개뿐이고, 나머지에 대해
"집중도 낮음" 이 아니라 **판정불가 + 몇 종목만 덮는지**를 돌려준다. 부재를
'효과 없음'으로 읽히게 두는 것이 이 저장소가 가장 싫어하는 실패다.

더 나쁜 실측이 하나 더 있다: `s3_statement_line`(598,429행)이 덮는 20종목과
공급계약 26종목의 **교집합이 0이다**(corp_code 기준 실측 0건). 즉 오늘
라이브에서 `revenue_covered` 는 26종목 전부에 대해 False다. 이 사실을 코드가
조용히 None 으로 흘리면 "매출 대비 비율을 못 냈다"가 "계약이 매출에서
차지하는 몫이 없다"로 읽힌다. 그래서 `note` 에 두 소스의 교집합 부재를
명시적으로 적는다.
"""
from __future__ import annotations

from datetime import date, timedelta

from .surface import register

# 만기 지평 90일. 새 임계를 발명한 게 아니라 **공시 주기에 맞춘다** — 분기
# 보고서 간격이 90일이므로, 이 창 안에 끝나는 계약은 다음 실적 발표 전에
# 매출에서 빠지는 것이 확정된 몫이다. 그보다 긴 창은 재계약 여지가 섞여
# '만료'가 아니라 '갱신 예정'을 세게 된다.
EXPIRY_DAYS = 90

# 매출액 계정명. DART XBRL 표준계정이 업종마다 갈린다(제조 '매출액' ·
# IFRS 표기 '수익(매출액)' · 금융/서비스 '영업수익'). 실측 상위 빈도 3종.
_REVENUE_ACCOUNTS = ("매출액", "수익(매출액)", "영업수익")


def _lit(s: str) -> str:
    """SQL 문자열 리터럴. instrument_id·ticker 는 에이전트가 주는 값이라
    작은따옴표 하나로 질의가 갈라질 수 있다 — 이스케이프해서 막는다."""
    return "'" + str(s).replace("'", "''") + "'"


def _d(v) -> date | None:
    """`contract_start`·`contract_end` 는 VARCHAR 다(실측). 못 읽는 값은
    None 으로 떨어뜨리되 **행을 버리지는 않는다** — 날짜를 못 읽은 계약도
    존재는 하므로, 유효/만료 판정에서만 빠지고 공시 건수에는 남는다."""
    if v is None or isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _sql_ticker(day: str, iid: str) -> str:
    """instrument_id → ticker. `v_instrument` 는 상시 뷰가 아니라 `_base(day)`
    가 앞에 붙이는 PIT 클램프 CTE 다 — 빼먹으면 CatalogException 이다."""
    from .paneltest import _base
    return _base(day) + (f"SELECT ticker, display_name FROM v_instrument "
                         f"WHERE instrument_id = {_lit(iid)}")


def _sql_contracts(ticker: str) -> str:
    """그 종목의 공급계약 **전량**. 여기서 `report_date <= day` 를 걸지 않는
    것은 의도다: 걸어 버리면 "이 표에 없는 종목"과 "아직 공시 전인 종목"이
    둘 다 0행으로 보여 사유를 구분할 수 없다. 시점 게이트는 파이썬에서
    명시적으로 건다(종목당 최대 11행 — 실측 삼성물산)."""
    return (
        "SELECT corp_code, corp_name, counterparty, counterparty_withheld, "
        "       object, amount_krw, ratio_pct, contract_start, contract_end, "
        "       report_date, rcept_no "
        f"FROM s3_supply_fact WHERE ticker = {_lit(ticker)} "
        "ORDER BY report_date, rcept_no")


def _sql_revenue(corp_code: str, day: str) -> str:
    """가장 최근 **공시된** 연간 매출액. PIT 게이트는 `available_at`(실측
    `available_lag_days` 는 전 행 0 — 공시일 = 가용일)이고, 사업보고서
    (`reprt_code='11011'`)의 당기(`period_kind='THSTRM'`) 값만 쓴다. 같은
    행 묶음에 전기(FRMTRM)·전전기(BFEFRMTRM) 비교치가 함께 실려 있어
    구분하지 않으면 3년 전 매출을 올해 값으로 집는다(실측 경방 2025년
    사업보고서: THSTRM 412.5십억 vs BFEFRMTRM 393.5십억).

    연결(CFS) 우선, 없으면 별도(OFS). 공급계약 공시의 매출 기준이 연결이다.
    """
    accounts = ",".join(_lit(a) for a in _REVENUE_ACCOUNTS)
    return (
        "SELECT bsns_year, fs_div, account_nm, CAST(amount AS DOUBLE), available_at "
        f"FROM s3_statement_line WHERE corp_code = {_lit(corp_code)} "
        f"  AND available_at <= DATE {_lit(day)} "
        "  AND reprt_code = '11011' AND period_kind = 'THSTRM' "
        f"  AND account_nm IN ({accounts}) "
        "ORDER BY bsns_year DESC, CASE WHEN fs_div = 'CFS' THEN 0 ELSE 1 END, "
        "         available_at DESC LIMIT 1")


def _latest_per_contract(rows: list[dict]) -> tuple[list[dict], int]:
    """정정·변경 공시 접기. 같은 (거래처, 계약목적, 개시일)에 여러 공시가
    오면 **가장 나중 공시만** 유효하다 — 실측 삼성물산 ABU DHABI 건은
    2026-04-20 2,769십억 → 04-22 2,876십억으로 정정됐고, 삼성전자 천안
    C-PJT 건은 03-30 1,064십억 → 04-24 1,109십억으로 증액됐다. 접지 않고
    합하면 같은 계약을 두 번 세어 집중도가 부풀려진다(실측 53행 → 50건).

    `object`(계약 목적)를 키에 넣는 이유: 삼성전기는 익명 거래처('글로벌
    대형기업') 3건이 같은 개시일을 쓰는데, Silicon Capacitor 와 MLCC 는
    별개 계약이다. 목적까지 같은 잔여 1쌍(MLCC 4.0% · 2.6%)은 정정인지
    2차 수주인지 데이터로 갈리지 않아 **보수적으로 접고, 접은 수를 note 에
    낸다** — 조용히 접는 것이 아니라 접었다고 말한다.
    """
    keep: dict[tuple, dict] = {}
    for r in rows:
        k = (r["counterparty"], r["object"], r["contract_start"])
        prev = keep.get(k)
        if prev is None or (r["report_date"], r["rcept_no"]) > (prev["report_date"],
                                                                prev["rcept_no"]):
            keep[k] = r
    return list(keep.values()), len(rows) - len(keep)


def _by_counterparty(live: list[dict]) -> list[tuple[str, float, float, int]]:
    """거래처별 (이름, 매출비율 합%, 계약금액 합, 계약 수), 비율 내림차순.
    한 거래처가 여러 계약을 들고 있으면 그것이 곧 집중이므로 **합쳐서** 센다
    (실측 삼성물산 ↔ 삼성전자 주식회사: 별건 3개가 한 거래처다)."""
    agg: dict[str, list[float]] = {}
    for r in live:
        a = agg.setdefault(r["counterparty"], [0.0, 0.0, 0.0])
        a[0] += float(r["ratio_pct"] or 0.0)
        a[1] += float(r["amount_krw"] or 0.0)
        a[2] += 1
    return sorted(((k, v[0], v[1], int(v[2])) for k, v in agg.items()),
                  key=lambda x: (-x[1], x[0]))


@register("business_mix",
          "그 종목의 공시된 공급계약으로 매출 집중도(최대 거래처 비중·HHI)와 "
          "계약 만기 구조(유효 계약, 90일 내 만료)를 낸다. 고객사 서사를 매출 %로 "
          "바꾸는 유일한 관측 — 덮는 종목이 26개뿐이라 나머지는 판정불가로 말한다.",
          needs=("s3_supply_fact",), vocab=())
def _business_mix(lake, *, day: str, instrument_id: str, **kw) -> dict:
    """공급계약 공시로 매출 집중도와 만기 구조를 잰다.

    **왜 이 방식인가 — 세 가지 시점을 분리한다.** 이 표에는 날짜가 셋 있고
    셋을 섞으면 전부 거짓이 된다.
      report_date    공시일. **선견 금지의 진짜 게이트**다. 실측 53행 중 40행이
                     계약 개시일보다 **나중에** 공시됐다 — 개시일로 자르면 아직
                     시장이 모르는 계약을 오늘 아는 것처럼 쓰게 된다.
      contract_start 개시일. 오늘 매출을 만들고 있는지를 정한다. 실측 4건은
                     개시일이 미래다(삼성전기 3건 2027-01-01) — 공시는 됐지만
                     아직 매출이 아니다. 이것을 유효 계약에 넣으면 집중도가
                     '앞으로 그럴 예정'을 '지금 그렇다'로 바꾼다. 수주잔고
                     (`backlog`)로 따로 센다.
      contract_end   종료일. NULL 2건은 기한 미확정이지 만료가 아니다.

    **왜 유효 계약이 0이면 판정불가인가.** 공시된 계약이 있어도 전부 종료됐거나
    전부 미개시면 오늘의 집중도는 **못 잰 것**이지 0이 아니다. 0.0 을 돌려주면
    "이 회사는 특정 거래처에 안 걸려 있다"로 읽히는데, 삼성전기가 정확히 그
    경우다(3건 전부 2027년 개시, 합 20.4%). 그래서 판정불가로 내고 backlog 를
    note 에 실어 정보를 잃지 않는다.

    **집중도를 두 숫자로 내는 이유.** `concentration` 은 유효 계약 매출비율의
    단순 합 = **공시로 관측된 매출 노출**이다. `hhi` 는 그 책 안에서의 허핀달
    지수이므로 계약이 하나면 10,000 이 나온다 — 매출 전부가 한 곳이라는 뜻이
    아니라 '공시된 책이 한 건'이라는 뜻이다. 하나만 내면 반드시 오독되므로
    둘을 같이 내고 note 에 분모를 적는다.

    **커버리지 한계(정직하게).** 이 표는 26종목만 덮는다. 그리고 매출 교차검증
    소스인 `s3_statement_line` 은 다른 20종목을 덮어 **교집합이 0**이다 —
    `revenue_covered=False` 는 이 종목의 매출을 못 구했다는 뜻이지 계약이
    작다는 뜻이 아니며, 그 문장을 `note` 가 직접 말한다.
    """
    d0 = _d(day)
    if d0 is None:
        return {"verdict": "판정불가", "reason": f"day 를 날짜로 못 읽었다: {day!r}",
                **_blank()}

    tk = lake.sql(_sql_ticker(day, instrument_id))
    if not tk:
        return {"verdict": "판정불가",
                "reason": f"instrument_id 를 티커로 못 풀었다({instrument_id!r}) — "
                          "v_instrument 에 그날 유효한 행이 없다",
                **_blank()}
    ticker, name = str(tk[0][0]), str(tk[0][1] or "")

    cols = ("corp_code", "corp_name", "counterparty", "counterparty_withheld",
            "object", "amount_krw", "ratio_pct", "contract_start", "contract_end",
            "report_date", "rcept_no")
    rows = [dict(zip(cols, r)) for r in lake.sql(_sql_contracts(ticker))]
    if not rows:
        return {"verdict": "판정불가",
                "reason": f"이 종목은 공급계약 표에 없다({ticker} {name}) — "
                          "s3_supply_fact 는 26종목만 덮는다(53행, 2026-08-04 실측)",
                **_blank()}

    # PIT: 오늘까지 **공시된** 것만 본다. 공시 전 계약은 존재해도 관측이 아니다.
    seen = [r for r in rows if (_d(r["report_date"]) or d0) <= d0]
    if not seen:
        return {"verdict": "판정불가",
                "reason": f"{ticker} {name} 의 공급계약 {len(rows)}건은 전부 "
                          f"{day} 이후 공시분이다 — 그날 관측 가능한 계약이 없다",
                **_blank()}

    uniq, restated = _latest_per_contract(seen)
    for r in uniq:
        r["c0"], r["c1"] = _d(r["contract_start"]), _d(r["contract_end"])
    live = [r for r in uniq
            if r["c0"] is not None and r["c0"] <= d0 and (r["c1"] is None or r["c1"] >= d0)]
    backlog = [r for r in uniq if r["c0"] is not None and r["c0"] > d0]

    if not live:
        bl = sum(float(r["ratio_pct"] or 0.0) for r in backlog)
        return {"verdict": "판정불가",
                "reason": f"{ticker} {name}: 공시된 계약 {len(uniq)}건 중 {day} 기준 "
                          f"유효한 것이 0건이다(미개시 {len(backlog)} · 종료 "
                          f"{len(uniq) - len(backlog)}) — 오늘의 집중도는 0 이 아니라 "
                          "관측 부재다",
                **_blank(),
                "note": (f"수주잔고: 미개시 계약 {len(backlog)}건 · 매출비율 합 "
                         f"{round(bl, 2)}% (개시일 도래 전이라 오늘 매출이 아니다)"
                         + (f" · 정정·변경 공시 {restated}건을 최신 공시로 접었다"
                            f"(원본 {len(seen)}행 → {len(uniq)}건)." if restated else ""))}

    per = _by_counterparty(live)
    total_ratio = sum(p[1] for p in per)
    hhi = round(sum((p[1] / total_ratio) ** 2 for p in per) * 10000.0, 1) if total_ratio else None
    live_amt = sum(float(r["amount_krw"] or 0.0) for r in live)

    horizon = d0 + timedelta(days=EXPIRY_DAYS)
    exp = [r for r in live if r["c1"] is not None and r["c1"] <= horizon]

    corp_code = str(rows[0]["corp_code"] or "")
    rev = lake.sql(_sql_revenue(corp_code, day)) if corp_code else []
    covered = bool(rev) and bool(rev[0][3])
    c2r = round(live_amt / float(rev[0][3]), 4) if covered else None

    notes = [f"분모 안내: concentration {round(total_ratio, 2)}% 는 공시된 유효 계약 "
             f"{len(live)}건의 매출비율 합이고, hhi 는 그 책 **내부** 지수다"
             f"(거래처 {len(per)}곳)."]
    if restated:
        notes.append(f"정정·변경 공시 {restated}건을 최신 공시로 접었다(원본 {len(seen)}행 "
                     f"→ {len(uniq)}건).")
    if backlog:
        notes.append(f"미개시 수주잔고 {len(backlog)}건(매출비율 합 "
                     f"{round(sum(float(r['ratio_pct'] or 0.0) for r in backlog), 2)}%)은 "
                     "유효 계약에서 뺐다 — 오늘 매출이 아니다.")
    if any(r["counterparty_withheld"] for r in live):
        notes.append("거래처명 비공개 계약이 섞여 있다 — 같은 익명 라벨이 서로 다른 "
                     "거래처일 수 있어 집중도가 과대일 수 있다.")
    if not covered:
        notes.append(f"매출 대비 비율 미산출: s3_statement_line 은 20종목만 덮고 공급계약 "
                     f"26종목과 교집합이 0이다(2026-08-04 실측, corp_code {corp_code}). "
                     "커버리지 부재이지 계약이 작다는 뜻이 아니다.")

    return {
        "verdict": "계산됨", "reason": "",
        "ticker": ticker, "corp_name": str(rows[0]["corp_name"] or name),
        "n_contracts": len(live),
        "n_counterparties": len(per),
        "top_share": round(per[0][1], 2),
        "top_counterparty": per[0][0],
        "concentration": round(total_ratio, 2),
        "hhi": hhi,
        "live_amount_krw": live_amt,
        "expiring_90d": len(exp),
        "expiring_90d_ratio_pct": round(sum((float(r["ratio_pct"] or 0.0) for r in exp), 0.0), 2),
        "expiring_90d_amount_krw": sum((float(r["amount_krw"] or 0.0) for r in exp), 0.0),
        "backlog": len(backlog),
        "restated": restated,
        "revenue_covered": covered,
        "contract_to_revenue": c2r,
        "counterparties": [{"name": n, "ratio_pct": round(r, 2), "amount_krw": a,
                            "n": c} for n, r, a, c in per],
        "note": " ".join(notes),
    }


def _blank() -> dict:
    """판정불가일 때의 수치 자리. **0 이 아니라 None** 이다 — 0 은 '쟀는데
    없었다'로 읽히고 그것이 이 도구가 막으려는 바로 그 거짓이다."""
    return {"n_contracts": None, "n_counterparties": None, "top_share": None,
            "top_counterparty": "", "concentration": None, "hhi": None,
            "live_amount_krw": None, "expiring_90d": None,
            "expiring_90d_ratio_pct": None, "expiring_90d_amount_krw": None,
            "backlog": None, "restated": None, "revenue_covered": False,
            "contract_to_revenue": None, "counterparties": [], "note": ""}


__all__ = ["EXPIRY_DAYS", "_business_mix"]
