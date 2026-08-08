"""KRX 업종지수 + 종목→업종 연동 (PIT).

섹터층이 "삼성전자(시장직교)"를 섹터라고 부르고 있었다. `decompose` 가 설명력으로
후보를 고르다가 단일 종목을 집은 것이다 - 섹터라는 이름이 붙었을 뿐 섹터가 아니다.
KRX 가 실제로 산출하는 **업종지수**로 바꾼다.

두 조각을 받는다:
  1. 업종지수 일봉 - KOSPI·KOSDAQ 의 **업종** 지수만. 규모별(대형/중형/소형)·
     본지수(코스피/코스닥)·파생지수(200/150 계열)·기업부(우량/벤처/…)는 섹터가
     아니므로 제외한다. 지수 목록에서 이름으로 걸러내지 않고 **분류가 실제로
     가리키는 지수만** 남긴다 - 아무도 속하지 않은 지수는 섹터로 쓸 일이 없다.
  2. 종목→업종 분류 - `get_market_sector_classifications(날짜, 시장)` 이 날짜를
     받으므로 **PIT** 다. 분기 스냅샷을 쌓아 as-of 조인으로 쓴다(업종은 느리게 변한다).

표기 정합: 분류의 업종명과 지수명이 어긋난다(`전기·전자` vs `전기전자`). 가운뎃점·
공백을 지워 맞추고, 그래도 안 맞는 것만 별칭으로 명시한다. 별칭이 없으면 그 종목은
**섹터지수 없음**이다 - 억지로 붙이지 않는다(부재가 정직하다).

사용:  python -m edge_analysis.statics.krxsector <out_dir> [시작일 YYYYMMDD]
       (KRX_ID · KRX_PW 환경변수 필요)
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

MARKETS = ("KOSPI", "KOSDAQ")
START = "20221101"      # RDB price_daily 가 2022-11-10 부터라 그 앞은 패널이 못 쓴다

# 분류 업종명 → 지수명. 이름 정규화로 안 붙는 것만 적는다.
#   기타금융·은행 → 금융 지수 (KRX 가 셋을 한 지수로 묶는다)
#   기타제조      → 제조 지수 (다른 제조 업종에 안 들어가는 잔여)
#   농업임업및어업·부동산(코스닥)·전기가스수도(코스닥) → 대응 지수가 없다
#   코스닥 **구 분류명**(2024 개편 전)은 현행 지수에 이름이 없다 - 가장 가까운
#   현행 업종지수로 흡수한다. 안 하면 초기 분기의 반도체 285·소프트웨어 242 종목이
#   섹터지수 없이 남아 그 구간 패널이 통째로 빈다.
ALIAS = {
    "기타금융": "금융", "은행": "금융", "기타제조": "제조",
    "반도체": "전기전자", "IT부품": "전기전자", "통신장비": "전기전자",
    "정보기기": "전기전자",
    "소프트웨어": "IT서비스", "컴퓨터서비스": "IT서비스", "인터넷": "IT서비스",
    "디지털컨텐츠": "IT서비스",
    "방송서비스": "통신", "통신서비스": "통신",
    "광업": "비금속",
}

_norm = lambda s: re.sub(r"[·\s]", "", str(s))


def _quarters(start: str, end: str) -> list[str]:
    """분기 스냅샷 날짜(각 분기 말 근처의 영업일 추정). 업종은 느리게 변한다."""
    y0, m0 = int(start[:4]), int(start[4:6])
    y1, m1 = int(end[:4]), int(end[4:6])
    out = []
    y, m = y0, ((m0 - 1) // 3) * 3 + 1
    while (y, m) <= (y1, m1):
        out.append(f"{y}{m:02d}15")   # 분기 중순 - 1일은 휴장이 잦다
        m += 3
        if m > 12:
            y, m = y + 1, 1
    return out


def collect(start: str = START, end: str | None = None) -> tuple[list, list, dict]:
    """(지수 일봉 행, 멤버십 행, 코드→이름). pykrx 호출은 여기 한 곳에만 둔다."""
    import warnings

    from pykrx import stock
    warnings.filterwarnings("ignore")
    end = end or date.today().strftime("%Y%m%d")

    # ── 1. 지수명 → 코드 (시장별) ──
    idx_of: dict[str, dict[str, str]] = {}
    name_of: dict[str, str] = {}
    for mk in MARKETS:
        m = {}
        for c in stock.get_index_ticker_list(end, market=mk):
            nm = stock.get_index_ticker_name(c)
            m[_norm(nm)] = c
            name_of[c] = nm
        idx_of[mk] = m

    # ── 2. 분류 스냅샷 → 멤버십. 여기서 실제로 쓰이는 지수 코드가 결정된다 ──
    members: list[tuple] = []
    used: set[str] = set()
    unmapped: dict[str, int] = {}
    for as_of in _quarters(start, end):
        for mk in MARKETS:
            try:
                df = stock.get_market_sector_classifications(as_of, mk)
            except Exception:                      # noqa: BLE001 - 휴장·미제공은 건너뛴다
                continue
            if "업종명" not in getattr(df, "columns", []):
                continue                           # 휴장일은 빈 표를 준다
            for tk, sec in zip(df.index, df["업종명"]):
                key = _norm(ALIAS.get(str(sec).strip(), sec))
                code = idx_of[mk].get(key)
                if code is None:
                    unmapped[str(sec)] = unmapped.get(str(sec), 0) + 1
                    continue
                used.add(code)
                members.append((as_of, str(tk), code, mk))

    # ── 3. 쓰이는 지수만 일봉을 받는다 ──
    bars: list[tuple] = []
    for code in sorted(used):
        try:
            df = stock.get_index_ohlcv(start, end, code)
        except Exception:                          # noqa: BLE001
            continue
        for d, close in zip(df.index, df["종가"]):
            bars.append((d.date(), code, float(close)))
    return bars, members, {"names": name_of, "used": sorted(used), "unmapped": unmapped}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    import duckdb
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    bars, members, meta = collect(sys.argv[2] if len(sys.argv) > 2 else START)
    con = duckdb.connect()
    con.execute("CREATE TABLE b(trade_date DATE, code VARCHAR, close DOUBLE)")
    con.executemany("INSERT INTO b VALUES (?,?,?)", bars)
    con.execute("CREATE TABLE m(as_of DATE, ticker VARCHAR, code VARCHAR, market VARCHAR)")
    con.executemany("INSERT INTO m VALUES (?,?,?,?)",
                    [(f"{a[:4]}-{a[4:6]}-{a[6:]}", t, c, k) for a, t, c, k in members])
    con.execute(f"COPY b TO '{(out / 'sector_index.parquet').as_posix()}' (FORMAT PARQUET)")
    con.execute(f"COPY (SELECT DISTINCT * FROM m) TO "
                f"'{(out / 'sector_member.parquet').as_posix()}' (FORMAT PARQUET)")
    n_idx = con.execute("SELECT count(*), count(DISTINCT code), min(trade_date), max(trade_date) FROM b").fetchone()
    n_mem = con.execute("SELECT count(DISTINCT (as_of, ticker)), count(DISTINCT ticker), "
                        "count(DISTINCT as_of) FROM m").fetchone()
    print(f"sector_index.parquet   {n_idx[0]:,}행 · 업종지수 {n_idx[1]}종 · {n_idx[2]}~{n_idx[3]}")
    print(f"sector_member.parquet  종목 {n_mem[1]:,} · 스냅샷 {n_mem[2]}분기 · 쌍 {n_mem[0]:,}")
    print("업종지수: " + " · ".join(meta["names"][c] for c in meta["used"]))
    if meta["unmapped"]:
        print("대응 지수 없음(섹터지수 부재로 남긴다): "
              + " · ".join(f"{k}({v})" for k, v in sorted(meta["unmapped"].items())))


if __name__ == "__main__":
    main()


# 코드 → 업종명. KRX 가 정한 이름이라 정적 참조표로 둔다(수집물엔 코드만 싣는다).
SECTOR_NAMES = {
    "1005": "음식료·담배", "1006": "섬유·의류", "1007": "종이·목재", "1008": "화학",
    "1009": "제약", "1010": "비금속", "1011": "금속", "1012": "기계·장비",
    "1013": "전기전자", "1014": "의료·정밀기기", "1015": "운송장비·부품", "1016": "유통",
    "1017": "전기·가스", "1018": "건설", "1019": "운송·창고", "1020": "통신",
    "1021": "금융", "1024": "증권", "1025": "보험", "1026": "일반서비스",
    "1027": "제조", "1045": "부동산", "1046": "IT 서비스", "1047": "오락·문화",
    "2012": "일반서비스", "2024": "제조", "2026": "건설", "2027": "유통",
    "2029": "운송·창고", "2031": "금융", "2037": "오락·문화", "2056": "음식료·담배",
    "2058": "섬유·의류", "2062": "종이·목재", "2063": "출판·매체복제", "2065": "화학",
    "2066": "제약", "2067": "비금속", "2068": "금속", "2070": "기계·장비",
    "2072": "전기전자", "2074": "의료·정밀기기", "2075": "운송장비·부품",
    "2077": "기타제조", "2114": "통신", "2118": "IT 서비스",
}


def sector_name(code: str) -> str:
    """코드 → '업종명(시장)'. 같은 업종명이 두 시장에 있어 시장을 붙여 구분한다."""
    mk = "코스피" if str(code).startswith("1") else "코스닥"
    return f"{SECTOR_NAMES.get(str(code), code)}({mk})"
