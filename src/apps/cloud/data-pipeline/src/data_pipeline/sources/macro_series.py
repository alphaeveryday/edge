"""매크로 5계열 원천 어댑터 (ALPHA-1130) — 계열 식별자·요청·응답 판정·파싱의 정본.

계약 정본은 docs/design/etf-data-storage-plan.md §10 이다. 여기 `SERIES` 표가 "어느 공급자의 어느 계열을
어느 단위로" 의 유일한 출처다 — 문서·DB CHECK(`ck_macro_observation_series`)가 이 표와 같아야 한다.

| series_id | 공급자 · 계열 | 단위 | 주기 |
|---|---|---|---|
| usd_krw | ECOS `731Y003`(원화의 대미달러 환율) 항목 `0000003` 원/달러(종가 15:30) — 서울외환시장 현물 종가 | KRW_per_USD | 일 |
| us_10y_yield | FMP `treasury-rates` 의 `year10` — 미 재무부 par yield 10년 | percent | 일 |
| kr_10y_yield | ECOS `817Y002`(시장금리 일별) 항목 `010210000` 국고채(10년) | percent | 일 |
| kr_cpi_yoy | KOSIS `101/DT_1J22042`(월별 소비자물가 등락률) `T03` 전년동월비 · 총지수(`objL1=0`) | percent | 월 |
| brent_spot_usd | EIA `petroleum/pri/spt` 계열 `RBRTE` — Europe Brent Spot Price FOB | USD_per_barrel | 일 |

**실응답 확인 상태 (2026-09-30 소량 실호출, 설계 §10.8)**: ECOS 두 계열(817Y002·731Y003)과 FMP treasury-rates 는
실응답으로 필드·단위·날짜 형식을 확인했다(fixture `tests/fixtures/source_observations/*_live.json` 이 그 원문 축약본).
FMP USD/KRW(`historical-price-eod/full?symbol=USDKRW`)는 현재 구독에서 **HTTP 402(심볼 미제공)** 라 쓸 수 없어 ECOS 로 바꿨다.
KOSIS·EIA 는 키가 없어 **미확인**이다 — 파서는 기대와 다른 형태를 조용히 넘기지 않고 사유와 함께 거부한다. 특히 KOSIS
`T03`=전년동월비는 공개 사례로만 확인했으므로 `ITM_NM` 에 '전년동월'이 없으면 거부한다.
⚠️ ECOS 731Y003 은 KRX 휴장일(2026-09-24·25 추석)에도 행이 있었다 — 그 값의 출처(휴일 거래 여부)는 미확인이다.
소비자는 이 계열을 KR 거래일 달력과 같은 것으로 전제하지 않는다.

**값을 바꾸지 않는다.** 한국 CPI 는 공급자가 공표한 전년동월비를 그대로 쓴다 — 지수 수준에서 자체
계산하지 않는다. USD/KRW 는 1달러당 원 방향 그대로다(역수 금지). ECOS 731Y003 에는 `0000013` 원/달러(종가, 2024-07
익일 02:00 마감 체제)도 있으나 이력이 짧아 15:30 종가(`0000003`, 1990~)를 쓴다 — 바꾸려면 계열을 갈라야 한다.
브렌트는 현물(Spot FOB)이며 선물(FMP `BZUSD` 등)으로 대체하지 않는다. 미 국채 FMP `treasury-rates` 는 재무부 par yield curve 이며 시장
종가 수익률과 다를 수 있다(계열 정의는 공급자 문서 기준, 값 대조 미확인).

**시각**: 다섯 공급자 모두 API 로 공표 시각을 주지 않는다. 그래서 공개시각 열을 만들지 않고, 분석
가시시각은 실제 수신시각이다(2026-09-30 결정 — 설계 §10). 관측 기간이 끝나기 전에 받은 값(진행 중
세션·미완 월)은 확정 관측이 아니어서 정제가 거부한다(`incomplete_period`).
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from ..failures import SafeFailureError
from .http import PoliteClient, StopFetch

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class MacroSeries:
    """계열 하나의 고정 속성. `max_window_days` 는 한 요청에 담는 관측 기간 상한(공급자 한도)이다."""

    series_id: str
    vendor: str
    frequency: str            # D 일별 · M 월별
    unit: str
    source_series: str        # 공급자 계열 식별자 — DB 에 그대로 남는다
    lookback_days: int        # 정기 수집이 다시 훑는 관측 기간(늦은 게시·정정 흡수)
    max_window_days: int


SERIES: dict[str, MacroSeries] = {s.series_id: s for s in (
    MacroSeries("usd_krw", "ecos", "D", "KRW_per_USD",
                "ECOS 731Y003/D/0000003", 14, 3650),
    # FMP treasury-rates 는 한 요청 기간이 짧게 제한된다고 문서화돼 있다(3개월) — 백필은 잘라 부른다.
    MacroSeries("us_10y_yield", "fmp", "D", "percent",
                "FMP treasury-rates year10", 14, 90),
    MacroSeries("kr_10y_yield", "ecos", "D", "percent",
                "ECOS 817Y002/D/010210000", 14, 3650),
    # 통계청 공표는 다음 달 초다. 정정·늦은 게시를 흡수하려 4개월을 다시 훑는다.
    MacroSeries("kr_cpi_yoy", "kosis", "M", "percent",
                "KOSIS 101/DT_1J22042 T03 objL1=0", 124, 3650),
    # EIA 현물가 표는 주 1회 갱신된다 — 한 주 넘는 지연을 흡수하려 4주를 다시 훑는다.
    MacroSeries("brent_spot_usd", "eia", "D", "USD_per_barrel",
                "EIA petroleum/pri/spt RBRTE", 28, 3650),
)}

# 응답이 단위를 줄 때 기대하는 문자열(문서 기준, 실응답 미확인). 다르면 값을 버리지 않고 거부로 드러낸다.
_VENDOR_UNITS = {"usd_krw": "원", "kr_10y_yield": "연%", "kr_cpi_yoy": "%", "brent_spot_usd": "$/BBL"}
# ECOS 계열 → (통계표, 항목). 요청 URL 과 응답 정체성 검사가 같은 표를 본다.
_ECOS = {"usd_krw": ("731Y003", "0000003", "원/달러(종가 15:30)"),
         "kr_10y_yield": ("817Y002", "010210000", "국고채(10년)")}


@dataclass(frozen=True)
class FetchResult:
    """한 요청의 결과. body 는 HTTP 200 일 때만 있다. request 에는 인증키를 담지 않는다."""

    series_id: str
    request: dict
    status: str               # ok · empty · error
    detail: str | None
    body: bytes | None
    fetched_at: str


def request_windows(series: MacroSeries, from_date: date, to_date: date) -> list[tuple[date, date]]:
    """관측 기간을 공급자 한도로 자른 요청 창들(오름차순, 겹침 없음)."""
    if from_date > to_date:
        raise ValueError(f"관측 기간이 역전됐다: {from_date} > {to_date}")
    windows, start = [], from_date
    while start <= to_date:
        end = min(to_date, start + timedelta(days=series.max_window_days - 1))
        windows.append((start, end))
        start = end + timedelta(days=1)
    return windows


class MacroSource:
    """계열별 요청을 만들고 응답 바이트를 그대로 돌려준다(해석은 `classify`·`parse`)."""

    def __init__(self, config, *, fmp_api_key: str | None, client: PoliteClient | None = None):
        self.config = config
        self._keys = {"fmp": fmp_api_key, "ecos": config.ecos_api_key,
                      "kosis": config.kosis_api_key, "eia": config.eia_api_key}
        # 실행당 최대 수십 콜이라 유량 제약은 없다. 공급자마다 한도가 달라 하나의 예산으로 묶지 않는다.
        self.client = client or PoliteClient(min_interval=1.0, timeout=30.0)

    def missing_credentials(self, series_ids) -> list[str]:
        """인증키가 없어 부를 수 없는 계열. 키 없는 호출은 4xx 로 끝나 수집 장애로 위장된다."""
        return sorted(s for s in series_ids if not self._keys[SERIES[s].vendor])

    def _url(self, series: MacroSeries, start: date, end: date) -> tuple[str, dict]:
        key = self._keys[series.vendor]
        c = self.config
        if series.series_id == "us_10y_yield":
            public = {"endpoint": "treasury-rates", "from": start.isoformat(), "to": end.isoformat()}
            url = f"{c.fmp_base_url}/treasury-rates?" + urllib.parse.urlencode(
                {"from": public["from"], "to": public["to"], "apikey": key})
        elif series.vendor == "ecos":
            stat, item, _ = _ECOS[series.series_id]
            public = {"endpoint": "StatisticSearch", "stat_code": stat, "cycle": "D", "item_code1": item,
                      "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d")}
            # 키가 경로에 들어가는 API 다 — 공개 기록(public)에는 경로를 남기지 않는다.
            # 행 상한 10000 = 일별 약 40년. 공개 샘플 키는 10행까지만 받는다(검증용).
            url = (f"{c.ecos_base_url}/StatisticSearch/{urllib.parse.quote(key or '', safe='')}"
                   f"/json/kr/1/10000/{stat}/D/{public['start']}/{public['end']}/{item}")
        elif series.series_id == "kr_cpi_yoy":
            public = {"endpoint": "statisticsParameterData", "org_id": "101", "tbl_id": "DT_1J22042",
                      "itm_id": "T03", "obj_l1": "0", "prd_se": "M",
                      "start": start.strftime("%Y%m"), "end": end.strftime("%Y%m")}
            # 공개 사례의 표기 그대로 항목 끝에 '+' 를 둔다(KOSIS 다중 선택 구분자).
            url = (f"{c.kosis_base_url}/Param/statisticsParameterData.do?method=getList"
                   f"&apiKey={urllib.parse.quote(key or '', safe='')}&itmId=T03+&objL1=0+"
                   f"&format=json&jsonVD=Y&prdSe=M&startPrdDe={public['start']}"
                   f"&endPrdDe={public['end']}&orgId=101&tblId=DT_1J22042")
        elif series.series_id == "brent_spot_usd":
            public = {"endpoint": "petroleum/pri/spt/data", "series": "RBRTE", "frequency": "daily",
                      "start": start.isoformat(), "end": end.isoformat()}
            url = f"{c.eia_base_url}/petroleum/pri/spt/data/?" + urllib.parse.urlencode(
                {"api_key": key, "frequency": "daily", "data[0]": "value",
                 "facets[series][]": "RBRTE", "start": public["start"], "end": public["end"],
                 "sort[0][column]": "period", "sort[0][direction]": "asc", "length": "5000"},
                safe="[]")
        else:  # pragma: no cover - SERIES 표 밖은 호출자가 먼저 거른다
            raise ValueError(f"모르는 계열: {series.series_id}")
        return url, {"vendor": series.vendor, **public}

    def fetch(self, series_id: str, start: date, end: date) -> FetchResult:
        """한 요청. 4xx·재시도 소진은 error 로 돌려준다 — 한 계열 실패가 다른 계열을 막지 않게."""
        series = SERIES[series_id]
        url, public = self._url(series, start, end)
        try:
            body = self.client.request("GET", url, headers={"Accept": "application/json"}, decode=False)
        except StopFetch as exc:
            # 본문에 키가 되돌아올 수 있어 원문을 남기지 않는다 — 상태코드만.
            return FetchResult(series_id, public, "error", f"http_{exc.status}", None, _now())
        except SafeFailureError as exc:
            return FetchResult(series_id, public, "error", str(exc), None, _now())
        # 수신시각은 응답을 다 받은 뒤 — 이 값이 곧 가시시각이라 요청 전에 찍으면 받기 전부터 보인다.
        fetched_at = _now()
        status, detail = classify(series_id, body)
        return FetchResult(series_id, public, status, detail, body, fetched_at)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(body: bytes):
    # 숫자를 float 로 먼저 읽으면 공급자 소수 자릿수가 사라진다 — Decimal 로 바로 읽는다.
    return json.loads(body.decode("utf-8"), parse_float=Decimal)


def classify(series_id: str, body: bytes) -> tuple[str, str | None]:
    """HTTP 200 본문 → ok·empty·error. **공급자가 "데이터 없음"이라 말한 것만 empty** 다.

    빈 응답(정상 0건)과 수집 실패를 가르는 곳이 여기다. 모르는 형태는 error 로 둔다 — empty 로 두면
    오류가 "그 기간엔 관측이 없었다"로 위장된다(휴장·공표 지연과 구분이 안 된다).
    """
    try:
        return _classify(SERIES[series_id].vendor, _json(body))
    except (ValueError, UnicodeDecodeError):
        return "error", "unparseable_json"
    except (AttributeError, TypeError, KeyError):
        # 기대와 다른 중첩(RESULT 가 목록 등) — 수집을 죽이지 않고 이 응답만 오류로 둔다.
        return "error", "unexpected_shape"


def _classify(vendor: str, data) -> tuple[str, str | None]:
    """classify 의 공급자별 판정(형태 예외는 호출부가 unexpected_shape 로 접는다)."""
    if vendor == "fmp":
        if isinstance(data, list):
            return ("ok", None) if data else ("empty", None)
        return "error", "unexpected_shape"            # {"Error Message": …} 등
    if vendor == "ecos":
        if isinstance(data, dict) and isinstance(data.get("StatisticSearch"), dict):
            rows = data["StatisticSearch"].get("row")
            # 데이터 없음은 INFO-200 으로 온다. row 가 없거나 목록이 아니면 파손이지 정상 0건이 아니다.
            if isinstance(rows, list):
                return ("ok", None) if rows else ("empty", "no_rows")
            return "error", "missing_rows"
        code = (data.get("RESULT") or {}).get("CODE") if isinstance(data, dict) else None
        if code == "INFO-200":                        # 해당하는 데이터가 없습니다
            return "empty", code
        return "error", code or "unexpected_shape"
    if vendor == "kosis":
        if isinstance(data, list):
            return ("ok", None) if data else ("empty", None)
        if isinstance(data, dict) and str(data.get("err")) == "30":   # 데이터가 존재하지 않습니다
            return "empty", "err_30"
        return "error", f"err_{data.get('err')}" if isinstance(data, dict) else "unexpected_shape"
    if vendor == "eia":
        response = data.get("response") if isinstance(data, dict) else None
        if isinstance(response, dict) and isinstance(response.get("data"), list):
            return ("ok", None) if response["data"] else ("empty", None)
        return "error", "unexpected_shape"
    return "error", "unknown_vendor"  # pragma: no cover


def _decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _iso(text: str, fmt: str) -> str | None:
    try:
        return datetime.strptime(text, fmt).date().isoformat()
    except (TypeError, ValueError):
        return None


def parse(series_id: str, body: bytes) -> tuple[list[dict], list[dict]]:
    """ok 본문 → (관측 [{observation_date, value}], 거부 [{reasons, …}]).

    값은 공급자 10진 문자열을 Decimal 로 확인한 뒤 **문자열 그대로** 넘긴다(반올림·부동소수 변환 없음).
    월별 관측일은 기준월 1일이다. 계열 정체성(항목명·계열 ID)과 단위 문자열이 기대와 다르면 거부한다.
    """
    data = _json(body)
    rows: list[tuple[str | None, object, dict | None]] = []   # (관측일, 값, 검사할 필드) — 필드 None = 행 형태 불량
    if series_id == "us_10y_yield":
        for item in data:
            if not isinstance(item, dict):
                rows.append((None, None, None))
                continue
            rows.append((_iso(item.get("date"), "%Y-%m-%d"), item.get("year10"), {}))
    elif series_id in _ECOS:
        for item in data["StatisticSearch"]["row"]:
            if not isinstance(item, dict):
                rows.append((None, None, None))
                continue
            rows.append((_iso(item.get("TIME"), "%Y%m%d"), item.get("DATA_VALUE"),
                         {"unit": item.get("UNIT_NAME"), "identity": item.get("ITEM_CODE1"),
                          "identity_name": item.get("ITEM_NAME1"), "table": item.get("STAT_CODE")}))
    elif series_id == "kr_cpi_yoy":
        for item in data:
            if not isinstance(item, dict):
                rows.append((None, None, None))
                continue
            prd = item.get("PRD_DE")
            # 단위 근거(실응답 2026-09-30): T03 행과 공식 항목 메타(getMeta ITM) 모두 UNIT_NM 이 없고 항목명
            # "전년동월비(%)" 끝에 단위가 있다(T02 전월비만 UNIT_NM=%). UNIT_NM 이 있으면 그것을, 없으면 이름의
            # "(%)" 만 단위 증거로 본다 — 둘 다 없으면 거부(임의 보정 없음).
            name = item.get("ITM_NM")
            unit = item.get("UNIT_NM") or ("%" if isinstance(name, str) and name.endswith("(%)") else None)
            rows.append((_iso(f"{prd}01", "%Y%m%d") if isinstance(prd, str) else None, item.get("DT"),
                         {"unit": unit, "identity": item.get("ITM_ID"), "identity_name": name, "c1": item.get("C1")}))
    elif series_id == "brent_spot_usd":
        for item in data["response"]["data"]:
            if not isinstance(item, dict):
                rows.append((None, None, None))
                continue
            rows.append((_iso(item.get("period"), "%Y-%m-%d"), item.get("value"),
                         {"unit": item.get("units"), "identity": item.get("series")}))
    else:
        raise ValueError(f"모르는 계열: {series_id}")

    good, rejects = [], []
    for observation_date, value, fields in rows:
        if fields is None:
            rejects.append({"observation_date": None, "reasons": ["malformed_row"]})
            continue
        reasons = []
        if observation_date is None:
            reasons.append("bad_observation_date")
        number = _decimal(value)
        if number is None:
            reasons.append("missing_value")
        expected_unit = _VENDOR_UNITS.get(series_id)
        if expected_unit and fields.get("unit") != expected_unit:
            reasons.append("unit_mismatch")
        if series_id in _ECOS and (fields.get("table") != _ECOS[series_id][0]
                                   or fields.get("identity") != _ECOS[series_id][1]
                                   or str(fields.get("identity_name")) != _ECOS[series_id][2]):
            reasons.append("series_identity_mismatch")
        # 식별자가 빠진 줄은 요청한 T03·총지수라는 근거가 없다 — 이름만 보고 받지 않는다.
        if series_id == "kr_cpi_yoy" and (fields.get("identity") != "T03"
                                          or "전년동월" not in str(fields.get("identity_name"))
                                          or fields.get("c1") != "0"):
            reasons.append("series_identity_mismatch")
        if series_id == "brent_spot_usd" and fields.get("identity") != "RBRTE":
            reasons.append("series_identity_mismatch")
        if number is not None and series_id in ("usd_krw", "brent_spot_usd") and number <= 0:
            reasons.append("non_positive_price")
        if reasons:
            rejects.append({"observation_date": observation_date, "reasons": reasons})
        else:
            good.append({"observation_date": observation_date, "value": str(number)})
    return good, rejects


def period_complete(series_id: str, observation_date: str, received_at: str) -> bool:
    """관측 기간이 수신 전에 끝났는가(KST 날짜 기준). 진행 중 세션·미완 월은 확정 관측이 아니다.
    DB `ck_macro_observation_complete` 와 같은 규칙이다."""
    received_day = datetime.fromisoformat(received_at).astimezone(KST).date()
    observed = date.fromisoformat(observation_date)
    if SERIES[series_id].frequency == "M":
        next_month = (observed.replace(day=28) + timedelta(days=4)).replace(day=1)
        return next_month <= received_day
    return observed < received_day
