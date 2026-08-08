"""재무 **692 항목 전량**을 이름으로 골라 재는 도구 — 광의의 펀더멘털.

## 왜 이 도구가 필요한가

지금까지 재무 노출은 `fin.ITEMS` 의 12개(실제로 패널에 쓰이는 것은 6개)뿐이었고,
그 좁음이 검정을 죽였다. `dgwide` 도크스트링이 증상을 이렇게 적었다: *"검정
에이전트의 일은 거친 가설을 받아 구체화하는 것이다 — 그 좁힘의 재료가 12개면 좁힐
수 없다. 실제로 6+6 가설이 전부 죽고 성립-적용 엣지가 0개였다."*

해법으로 적혀 있던 `dgwide.build_fin`(692 항목을 한 parquet 으로 피벗)은 **한 번도
돌지 않았다** — 692 항목 × 3,800 티커 × 46 년을 넓은 표로 물질화하는 계획이라
누적 FULL JOIN 692 회가 끝나지 않는다. 그래서 692 항목은 "설계에는 있고 실물은
없는" 상태였다. 이 도구는 물질화를 **포기**하고 항목 하나씩 읽는다.

## 왜 항목 파일을 직접 읽는가 (뷰 `s3_dg_financials` 를 안 쓰고)

레이크 실측 구조는 **항목이 곧 파티션**이다:

    …/dataset=financial_statements/market=KR/as_of_date=2026-08-02/
        item=M000102001/M000102001_부채비율___.csv.gz   ← 항목당 파일 하나
        _report.csv                                     ← 항목 사전(692행 + 트레일러)

각 파일은 `year × ticker` **넓은 표**(첫 열 year, 나머지 3,800 열이 티커)다.
`s3_dg_financials` 뷰는 이 692 파일을 `read_csv('**/*.csv.gz')` 로 통째 union 한 것이라
(a) 스키마가 `year` + 티커 3,800 열이고 **item 은 hive 열로만 남아** 항목을 열로
구분할 수 없으며 (b) 비용이 나온다. 같은 결과를 내는 두 경로를 실측했다:

    뷰 경유 (item = 'M000102001' 로 필터)   40.1s   ← 글롭 692 파일 전부 열어본다
    항목 파일 직독 (item=…/*.csv.gz)         2.4s   ← 파일 하나
    (둘 다 삼성전자 부채비율 FY2025 = 29.94, 횡단면 1,298 종목 중 26.8%)

    참고: `SELECT count(*) FROM s3_dg_financials` = 31,832 행(= 692 × 46)에 **118초**.

그래서 커버리지 관문(`Need`)은 뷰로 걸고 — 뷰가 안 걸리면 재무 자체가 없는 것이니 —
실제 조회는 파티션 경로를 직접 짚는다. `duck.py` 는 건드리지 않는다.

## 왜 이름 사전이 `_report.csv` 인가

`dgwide.items_dict()`(=`s3_dg_items`, items_resolved.csv)에는 **재무 항목이 없다**.
실측 947 항목의 domain 은 flow 703 · price 244 이고 코드가 `CI*`·`S41*`·`S42*` 뿐 —
재무 코드(`M*` 634개 · `S43*` 58개 = 692)와 **교집합이 0** 이다.
재무 692 항목의 한글명을 가진 유일한 원천이 `_report.csv` 이고, 그 트레일러가 데이터
범위를 스스로 선언한다: `years,46,1981,2026` · `tickers,3800` · 항목 692 · 비어있지
않은 값 22,642,281 개(값이 하나도 없는 항목이 51개라 후보 정렬에서 뒤로 미룬다).
이름에 콤마가 있는 항목(`"DPS(보통주, 결산현금)(원)"` 등 27개) 때문에 콤마 파싱이
깨지므로 한 줄을 통째로 읽어 정규식으로 가른다.

## PIT 규율은 `fin_annual` 과 **같은 것을 쓴다**

`available_from = make_date(FY + 1, dgwide.REPORT_LAG_MONTH=4, 1)`. 파티션
`as_of_date` 는 수집일이지 공시일이 아니라서 FY Y 값을 Y 년 중에 쓰면 선견이다.
`fin_annual`(36,171행 · 1,941종목 · FY2000~2026)의 `available_from` 도 같은 식이고
`v_fin` 이 `available_from <= trade_date` 로 자른다 — **규율이 갈리지 않는다**.
갈릴 것이 없으므로 보수적인 쪽을 고를 일도 없었다. 이 도구의 커버리지는 그쪽의
상위집합이다(692 항목 대 12, 1981~ 대 FY2000~, 3,800 티커 대 1,941).

## 알려진 유니버스 구멍 (부재를 침묵으로 두지 않는다)

항목 파일의 티커 열은 **A005190 이상만** 존재한다(정렬 최소값 실측). 즉 `A000660`
(SK하이닉스)·`A000270` 같은 낮은 코드는 재무 전량에서 통째로 빠져 있고, 같은 원천을
쓰는 `fin_annual` 도 000660 이 0 행이다(수집 쪽 구멍이다). 이 경우 값을 지어내지
않고 "티커 열 부재" 를 사유로 판정불가를 낸다 — 조용한 0 행은 "그 종목엔 그 항목이
없다" 와 구분되지 않고, 그 혼동이 이 저장소가 가장 싫어하는 실패 양식이다.
"""
from __future__ import annotations

import numpy as np

from .dgwide import BUCKET, FIN, REPORT_LAG_MONTH
from .surface import Need, register
from .vocab import MIN_N

# DataGuide 티커 표기는 `A` + 6자리(`A000660`)이고 `v_instrument.ticker` 는 `000660` 이다.
# 이 한 글자를 안 붙이면 모든 조회가 **조용히 0행**이 된다.
DG_PREFIX = "A"

# 재무 파티션은 market=KR 하나뿐이다(실측). 다른 시장이 생기면 여기서 갈라야 한다 —
# 경로를 하드코딩한 것이 아니라 **지금 있는 것 전부**를 짚는다.
MARKET = "market=KR/"

# 자기 이력 z 의 최소 표본(과거 연도 수). `MIN_N`(=30)을 그대로 쓰면 46년 데이터에서
# 30년 이력을 가진 회사만 통과해 사실상 영구 판정불가가 된다(fin_annual 은 FY2000~ 라
# 최대 26년). 연간 재무의 표준편차는 5점이면 자릿수는 말할 수 있고 그 이하는 표본이
# 아니라 잡음이다 — 그래서 5, 그리고 미달이면 z 만 침묵하고 값·YoY 는 그대로 낸다.
MIN_YEARS_Z = 5

# 후보 목록 상한. 이 목록은 프롬프트로 들어가므로 무한하면 에이전트가 항목 고르기를
# 하게 된다(=채굴). 넘치면 "좁혀서 다시 불러라" 라고 말하는 게 맞다.
MAX_CAND = 12

# 항목 사전 캐시. 692행이지만 S3 왕복이 붙으므로 한 번 읽어 돌려쓴다. lake 별로 따로
# 잡는다 — 한 프로세스에 레이크는 하나지만, 테스트가 여러 가짜 레이크를 쓰므로 전역
# 하나면 첫 사전이 다음으로 새어 나간다. **lake 자체를 값에 넣어 붙잡는다**: id 만
# 키로 쓰면 앞의 레이크가 수거된 뒤 같은 주소가 재사용돼 남의 사전을 읽는다.
_CACHE: dict[int, tuple[object, str, list[tuple[str, str, int]]]] = {}

# `_report.csv` 한 줄 → (코드, 이름, 비어있지 않은 값 수). 이름에 콤마가 있는 항목이
# 27개라 콤마 분할이 불가능하다: 앞뒤를 코드·숫자 두 개로 못 박고 가운데를 탐욕적으로
# 먹인다. 헤더·빈 줄·트레일러(`years,46,1981,2026`·`tickers,3800`)는 코드 패턴에서
# 자연히 탈락한다 — 692 항목만 남는 것을 테스트가 지킨다.
_RX = r"'^([A-Z0-9]+),(.*),([0-9]+),([0-9]+)$'"

_KEYS = ("item_code", "name", "candidates", "latest", "latest_year", "prev", "yoy",
         "z", "n_years", "cs_pct_rank", "cs_n", "available_from", "signed",
         "supports", "note")


def _lit(s: str) -> str:
    """SQL 문자열 리터럴. 항목코드·티커가 외부에서 오므로 이스케이프는 선택이 아니다."""
    return "'" + str(s).replace("'", "''") + "'"


def _nope(reason: str, **kw) -> dict:
    """부재는 **사유와 함께**. 빈 dict·0·None 을 조용히 돌려주면 호출자는 그것을
    '재무적으로 특별할 게 없다' 로 읽는다 — 부재는 기각이 아니다."""
    out = {"verdict": "판정불가", "reason": reason}
    out.update({k: None for k in _KEYS})
    out["candidates"] = []
    out["note"] = ""
    out.update(kw)
    return out


def _dict_sql() -> str:
    """항목 사전 + **가장 최신 as_of 파티션**. as_of 를 하드코딩하면 다음 수집분이
    들어와도 영구히 옛 파티션을 읽는다(그 침묵이 `s3_dg_items` 에서 이미 일어났다).
    `_report.csv` 는 여러 as_of 에 걸쳐 글롭하고 hive 열로 최신만 고른다."""
    return f"""
WITH r AS (
  SELECT as_of_date,
         regexp_extract(line, {_RX}, 1) AS code,
         trim(regexp_extract(line, {_RX}, 2), '"') AS name,
         TRY_CAST(regexp_extract(line, {_RX}, 3) AS BIGINT) AS nn
  FROM read_csv('s3://{BUCKET}/{FIN}{MARKET}as_of_date=*/_report.csv',
                columns = {{'line': 'VARCHAR'}}, delim = '\\x07', header = false,
                quote = '', hive_partitioning = true)
)
SELECT CAST(as_of_date AS VARCHAR), code, name, nn FROM r
WHERE code <> '' AND as_of_date = (SELECT max(as_of_date) FROM r)
ORDER BY code"""


def _uri(as_of: str, code: str) -> str:
    """항목 하나의 파일. 파일명은 `<코드>_<한글명>.csv.gz` 로 이름이 슬러그되어 있어
    글롭으로 짚는다 — 이름을 재조립하려 하면 특수문자에서 갈린다."""
    return f"s3://{BUCKET}/{FIN}{MARKET}as_of_date={as_of}/item={code}/*.csv.gz"


def _series_sql(as_of: str, code: str, tk: str, day: str) -> str:
    """그 항목의 (연도, 값) 시계열 + 그 해 횡단면 두 수를 **한 번의 스캔**으로.

    넓은 표를 UNPIVOT 으로 녹인 뒤 티커로 고른다. 티커 열을 직접 참조(`SELECT
    A000660`)하면 열이 없을 때 BinderException 이 나 도구가 통째로 죽는다 — 녹여서
    `WHERE` 로 고르면 없는 티커는 0 행이고, 0 행은 사유를 붙여 판정불가로 말할 수 있다.

    `m` 을 MATERIALIZED 로 못 박는 이유: 시계열과 횡단면이 같은 CTE 를 두 번 참조하고
    그러면 S3 파일을 두 번 읽는다. 원격 파일이 곧 비용이다.

    PIT: `available_from = FY+1년 4월 1일 <= day`. 오늘 이후에 공시될 FY 는 아예
    `m` 에 들어오지 못한다 — 게이트를 파이썬이 아니라 SQL 에 두면 질의가 못 어긴다.
    """
    return f"""
WITH m AS MATERIALIZED (
  SELECT TRY_CAST(year AS INTEGER) AS fy, TRIM(name) AS tk,
         nullif(TRIM(value), '') AS raw, TRY_CAST(value AS DOUBLE) AS v
  FROM (UNPIVOT (SELECT * FROM read_csv({_lit(_uri(as_of, code))},
                                        all_varchar = true, hive_partitioning = false))
        ON COLUMNS(* EXCLUDE year) INTO NAME name VALUE value)
  WHERE TRY_CAST(year AS INTEGER) IS NOT NULL
    AND make_date(TRY_CAST(year AS INTEGER) + 1, {REPORT_LAG_MONTH}, 1) <= DATE '{day}'
),
s AS (SELECT fy, raw, v FROM m WHERE tk = {_lit(tk)}),
ly AS (SELECT max(fy) AS fy FROM s WHERE v IS NOT NULL),
cs AS (SELECT count(v) AS n,
              avg(CASE WHEN v <= (SELECT v FROM s JOIN ly USING (fy)) THEN 1.0
                       ELSE 0.0 END) AS pct
       FROM m WHERE fy = (SELECT fy FROM ly) AND v IS NOT NULL)
SELECT s.fy, s.v, cs.n, cs.pct FROM s, cs WHERE s.raw IS NOT NULL ORDER BY s.fy"""


def _col_exists_sql(as_of: str, code: str, tk: str) -> str:
    """그 항목 파일에 티커 **열이 있는가**. 0 행의 두 원인('유니버스 밖' 과 'PIT
    게이트가 다 잘랐다')을 가르는 데만 쓴다 — 스키마만 보므로 값은 안 읽는다."""
    return (f"SELECT count(*) FROM (DESCRIBE SELECT * FROM "
            f"read_csv({_lit(_uri(as_of, code))}, all_varchar = true, "
            f"hive_partitioning = false)) WHERE column_name = {_lit(tk)}")


def _dict(lake) -> tuple[str, list[tuple[str, str, int]]]:
    """(최신 as_of, [(코드, 이름, 비어있지 않은 값 수)]). 실패는 ("", []) 로 말한다."""
    key = id(lake)
    if key not in _CACHE:
        rows = lake.sql(_dict_sql())
        as_of = str(rows[0][0]) if rows else ""
        _CACHE[key] = (lake, as_of,
                       [(str(c), str(n), int(nn or 0)) for _, c, n, nn in rows])
    _, as_of, items = _CACHE[key]
    return as_of, items


def _ticker(lake, day: str, instrument_id: str) -> tuple[str, str]:
    """instrument_id → 티커. `v_instrument` 는 상시 뷰가 아니라 `_base(day)` 가 앞에
    붙이는 PIT 클램프 CTE 다 — 빼먹으면 CatalogException 이다."""
    from .paneltest import _base
    try:
        rows = lake.sql(_base(day) + f"SELECT ticker FROM v_instrument "
                                     f"WHERE instrument_id = {_lit(instrument_id)}")
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        return "", f"instrument_id 해소 실패: {type(e).__name__}: {str(e)[:120]}"
    if not rows:
        return "", (f"instrument_id={instrument_id!r} 를 티커로 못 풀었다 — "
                    f"v_instrument 에 {day} 유효한 행이 없다")
    return str(rows[0][0]), ""


def _match(items: list[tuple[str, str, int]], q: str) -> list[dict]:
    """이름·코드 부분일치. 값이 하나도 없는 항목(`non_null=0`)은 뒤로 미룬다 —
    실측으로 `DPS(2우선주…)` 처럼 전 종목 0 인 항목이 있고, 그것을 상위에 올리면
    에이전트가 빈 항목을 고르고 '데이터 없음' 을 재무 판단으로 착각한다."""
    ql = q.strip().lower()
    hit = [(c, n, nn) for c, n, nn in items if ql in n.lower() or ql in c.lower()]
    hit.sort(key=lambda r: (r[2] == 0, -r[2], r[0]))
    return [{"item_code": c, "name": n, "non_null": nn} for c, n, nn in hit]


@register("fin_item",
          "재무 692 항목 전량에서 항목을 이름(query)으로 찾고, 고른 항목(item_code)의 "
          "그 종목 연도별 값·YoY·자기이력 z·그날 횡단면 분위수를 PIT(FY+1년 4월 1일 "
          "가용) 규율로 낸다.",
          needs=(Need("s3_dg_financials"),), vocab=())
def _fin_item(lake, *, day: str, instrument_id: str = "", ticker: str = "",
              item_code: str = "", query: str = "", **kw) -> dict:
    """항목 하나를 그 종목의 시계열·횡단면 두 축으로 잰다.

    두 축을 같이 내는 이유: 재무 수준은 업종 상수에 가깝다. "부채비율 200%" 는 그
    자체로는 문장이 안 되고, **자기 과거에 비해(YoY·z) 어떻게 변했는지**와 **그해 남들
    사이에서 어디인지(횡단면 분위수)** 가 붙어야 취약성 진술이 된다. 하나만 내면
    에이전트가 나머지를 추정으로 메우고, 그 추정이 곧 이야기다.

    `query` 만 주면 **고르지 않고 후보를 낸다**. 692 항목에서 "부채" 로 47개가 걸리는데
    도구가 하나를 골라 주면 그 선택이 근거 없이 결론에 들어간다 — 좁히기는 호출자의
    일이고, 그래서 이 경로의 판정은 항상 판정불가다.

    `signed` = YoY 변화(부호가 뜻을 갖는다: +면 그 항목이 늘었다). `supports` 는 None —
    이 도구는 값을 재기만 하고 어떤 주장을 지지·부정하는지는 부르는 쪽이 정한다.
    "부채비율이 올랐다" 가 취약성을 지지하는지 부정하는지는 항목마다 반대라, 도구가
    임의로 방향을 정하면 신뢰성 검사가 틀린 부호로 통과한다.
    """
    try:
        as_of, items = _dict(lake)
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        return _nope(f"재무 항목 사전(_report.csv)을 못 읽었다: "
                     f"{type(e).__name__}: {str(e)[:160]}")
    if not items:
        return _nope("재무 항목 사전이 0행이다 — curated financial_statements 파티션이 "
                     "비었거나 _report.csv 가 없다")

    cand = _match(items, query) if query.strip() else []
    if not item_code:
        if not query.strip():
            return _nope(f"item_code 도 query 도 없다 — 재무 {len(items)}개 항목 중 "
                         f"무엇을 재야 하는지 정해지지 않았다", candidates=[])
        if not cand:
            return _nope(f"이름·코드에 {query!r} 를 담은 항목이 {len(items)}개 중 0개다",
                         candidates=[])
        return _nope(f"query={query!r} 로 후보 {len(cand)}개 — 하나를 골라 item_code 로 "
                     f"다시 불러라(도구가 대신 고르지 않는다)",
                     candidates=cand[:MAX_CAND],
                     note=f"후보 {len(cand)}개 중 {min(len(cand), MAX_CAND)}개만 보인다"
                          if len(cand) > MAX_CAND else "")

    named = {c: n for c, n, _ in items}
    if item_code not in named:
        return _nope(f"item_code={item_code!r} 는 재무 {len(items)}개 항목에 없다"
                     + (f" — query={query!r} 후보를 보라" if cand else ""),
                     item_code=item_code, candidates=cand[:MAX_CAND])
    name = named[item_code]

    tk = f"{DG_PREFIX}{ticker.strip()}" if ticker.strip() else ""
    if not tk:
        if not instrument_id:
            return _nope("instrument_id 도 ticker 도 없다 — 어느 종목의 재무인지 "
                         "정해지지 않았다", item_code=item_code, name=name)
        raw, err = _ticker(lake, day, instrument_id)
        if err:
            return _nope(err, item_code=item_code, name=name)
        tk = f"{DG_PREFIX}{raw}"

    try:
        rows = lake.sql(_series_sql(as_of, item_code, tk, day))
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        return _nope(f"재무 항목 조회 실패({item_code}·{tk}): "
                     f"{type(e).__name__}: {str(e)[:160]}",
                     item_code=item_code, name=name)

    if not rows:
        return _nope(_empty_reason(lake, as_of, item_code, tk, day),
                     item_code=item_code, name=name)

    nums = [(int(fy), float(v)) for fy, v, _, _ in rows if v is not None]
    if not nums:
        return _nope(f"{name}({item_code}) 는 이 종목에서 **비수치**다 — 값이 "
                     f"{len(rows)}개 있으나 전부 숫자로 캐스팅되지 않는다(범주형·"
                     f"결산월·문자 코드 항목). 수치 항목으로 다시 불러라",
                     item_code=item_code, name=name, n_years=0)

    cs_n = int(rows[0][2] or 0)
    cs_pct = rows[0][3]
    latest_year, latest = nums[-1]
    hist = [v for fy, v in nums[:-1]]
    prior = dict(nums).get(latest_year - 1)

    note = []
    yoy = None if prior is None else latest - prior
    if prior is None:
        note.append(f"FY{latest_year - 1} 값이 없어 YoY 미산출(연속 연도만 YoY 로 본다)")

    z = None
    if len(hist) >= MIN_YEARS_Z:
        a = np.asarray(hist, dtype=float)
        sd = float(a.std(ddof=1))
        if sd > 0:
            z = float((latest - a.mean()) / sd)
        else:
            note.append(f"이력 {len(hist)}년이 전부 같은 값이라 z 미산출(표준편차 0)")
    else:
        note.append(f"이력 {len(hist)}년 < {MIN_YEARS_Z}년이라 z 미산출")

    pct = None
    if cs_pct is not None and cs_n >= MIN_N:
        pct = float(cs_pct)
    else:
        note.append(f"FY{latest_year} 횡단면 {cs_n}종목 < {MIN_N} 이라 분위수 미산출")

    return {"verdict": "계산됨", "reason": "", "item_code": item_code, "name": name,
            "candidates": cand[:MAX_CAND], "latest": latest, "latest_year": latest_year,
            "prev": prior, "yoy": yoy, "z": z, "n_years": len(nums),
            "cs_pct_rank": pct, "cs_n": cs_n,
            "available_from": f"{latest_year + 1:04d}-{REPORT_LAG_MONTH:02d}-01",
            "signed": yoy, "supports": None, "note": " · ".join(note)}


def _empty_reason(lake, as_of: str, code: str, tk: str, day: str) -> str:
    """0 행의 원인을 **가른다**. '유니버스 밖' 과 'PIT 게이트가 다 잘랐다' 는 다른
    사실이고, 하나로 뭉치면 수집 구멍이 날짜 문제로 보인다(또는 그 반대)."""
    try:
        has = int(lake.sql(_col_exists_sql(as_of, code, tk))[0][0])
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        return (f"{tk} 시계열이 0행이고 스키마 확인도 실패했다: "
                f"{type(e).__name__}: {str(e)[:120]}")
    if not has:
        return (f"{tk} 는 재무 항목 파일({code})에 **열 자체가 없다** — DataGuide 재무 "
                f"유니버스 밖이다(실측: 티커 열이 A005190 이상만 존재하고, 같은 원천을 "
                f"쓰는 fin_annual 도 000660 이 0행이다). 수집 쪽 구멍이므로 값을 "
                f"지어내지 않는다")
    return (f"{tk} 는 열이 있으나 {day} 기준 가용 연도가 0개다 — available_from"
            f"(FY+1년 {REPORT_LAG_MONTH}월 1일) <= {day} 를 만족하는 값이 없다"
            f"(상장 이전이거나 값이 전부 비었다)")


__all__ = ["DG_PREFIX", "MAX_CAND", "MIN_YEARS_Z"]
