"""시세 — 키 없는 공개 시세. 포트폴리오 서비스와 같은 출처를 쓴다(Yahoo 차트 · Naver 증권).

- 상승률 순위: Naver 증권 `m.stock.naver.com/api/stocks/up/{KOSPI|KOSDAQ}` (상승률 내림차순, 실제 누적 거래대금 포함).
  현재가는 넥스트레이드(NXT) 체결을 포함한다 — 장 전(08:00~)·장 뒤(~20:00)에는 KRX 정규장 가격과 다를 수 있다.
- 당일 5분 시세·현재가: Yahoo 차트 `v8/finance/chart/{code}.KS|.KQ?interval=5m&range=1d` (봉 시각 = 구간 시작).
  한국 종목은 약 20분 늦게 나온다. 화면의 가격은 이것을 쓰고 그 시각을 함께 싣는다.
- 미국: 상승률 순위는 Naver 해외 `api.stock.naver.com/stock/exchange/{NASDAQ|NYSE|AMEX}/up`(한글 종목명·달러 거래대금·로고 주소),
  5분 시세는 Yahoo 차트 `{티커}`(접미어 없음, BRK.B → BRK-B). 시각은 모두 뉴욕 현지 시각이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .markets import US, of_exchange

KST = timezone(timedelta(hours=9))
NAVER_UP = "https://m.stock.naver.com/api/stocks/up/{market}?page={page}&pageSize={size}"
NAVER_WORLD_UP = "https://api.stock.naver.com/stock/exchange/{market}/up?page={page}&pageSize={size}"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=1d"
SUFFIX = {"KOSPI": "KS", "KOSDAQ": "KQ"}
_HEADERS = {"user-agent": "Mozilla/5.0 (compatible; etforca-truth/1.0)", "accept": "application/json"}


class QuoteError(RuntimeError):
    """시세 호출 실패(사유 포함)."""


@dataclass(frozen=True)
class Mover:
    code: str
    name: str
    market: str
    price: float
    prev_close: float
    change_pct: float
    trade_value: float  # 누적 거래대금: 국내 억원 · 미국 백만 달러
    logo_url: str | None = None  # 미국: Naver 가 주는 로고 PNG 주소(국내는 회사 홈페이지에서 찾는다)
    name_eng: str | None = None  # 미국: 영문 회사명 — 영어 원문이 이 회사를 다루는지 가릴 때 쓴다


@dataclass(frozen=True)
class Chart:
    series: list[list]      # [구간 끝 HH:MM, 종가]
    price: float | None     # 그날 마지막 체결가(Yahoo). 그날 체결이 없으면 None
    at: str | None          # 그 체결 시각(ISO, KST)


def is_common_stock(code: str, name: str, end_type: str = "stock") -> bool:
    """보통주인가. ETF·ETN 은 응답의 종목 유형으로, 우선주는 코드 끝자리(보통주는 0)로, 스팩은 이름으로 거른다."""
    return end_type == "stock" and len(code) == 6 and code.isalnum() and code.endswith("0") and "스팩" not in name


# 미국 순위에 보통주처럼 섞여 나오는 신주인수권·워런트·유닛·스팩(영문 이름으로 가린다)
_US_NOT_COMMON = re.compile(r"\b(rights?|warrants?|units?|acquisition (corp|co|company|limited|ltd)\b|spac)\b", re.I)
_US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,5}(\.[A-Z])?$")


def us_symbol(raw: str) -> str:
    """Naver 해외 티커 → 우리 표기. 반 클래스 주식은 'BRK B'·'BF A' 처럼 띄어 오므로 'BRK.B' 로 맞춘다
    (Yahoo·SEC 는 이것을 BRK-B 로 바꿔 쓴다). 우선주 'AHT PRD' 는 클래스가 한 글자가 아니라 그대로 두어 걸러진다."""
    return re.sub(r"^([A-Z][A-Z0-9]{0,5}) ([A-Z])$", r"\1.\2", raw.strip())


def is_us_common_stock(symbol: str, name_eng: str, end_type: str = "stock") -> bool:
    """미국 보통주인가(symbol 은 us_symbol 로 맞춘 것). ETF 는 종목 유형으로, 우선주는 티커 꼴로, 워런트·유닛·스팩은 영문 이름으로 거른다."""
    return end_type == "stock" and bool(_US_SYMBOL.match(symbol)) and not _US_NOT_COMMON.search(name_eng)


def _num(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


class Quotes:
    def __init__(self, *, timeout: float = 15.0):
        self.timeout = timeout

    def _get(self, url: str) -> dict:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=_HEADERS), timeout=self.timeout) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise QuoteError(f"{urllib.parse.urlsplit(url).netloc} HTTP {error.code}") from error
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise QuoteError(f"{urllib.parse.urlsplit(url).netloc} 연결 실패: {error}") from error
        raise QuoteError("시세 호출 실패")  # 도달하지 않는다

    def _up(self, market: str, page: int, size: int = 100) -> list[dict]:
        """상승률 순위 한 쪽. 첫 쪽이 비었거나 필드가 없으면 실패로 올린다 — 응답 형식이 바뀐 것을 '오늘 급등 없음'으로 게시하지 않는다."""
        us = of_exchange(market) is US
        rows = self._get((NAVER_WORLD_UP if us else NAVER_UP).format(market=market, page=page, size=size)).get("stocks") or []
        # 실제로 읽는 칸을 본다(미국은 원시값 칸) — 그 칸이 사라지면 전부 0% 로 걸러져 '오늘 급등 없음'이 게시된다
        need = ("symbolCode", "fluctuationsRatioRaw", "closePriceRaw") if us else ("itemCode", "fluctuationsRatio")
        if page == 1 and not (rows and all(r.get(need[0]) and all(r.get(k) is not None for k in need[1:]) for r in rows)):
            raise QuoteError(f"Naver {market} 상승률 순위 응답이 비었거나 형식이 다릅니다.")
        return rows

    def session_day(self, market: str = "KOSPI") -> str:
        """가장 최근 거래일(YYYYMMDD) — 순위 응답의 체결 시각으로 본다. 휴장일에는 전 거래일이 나온다.
        5분 시세로 보지 않는다: 시세 지연이 있으면 09:00 실행이 매일 휴장으로 빠진다."""
        days = [str(r.get("localTradedAt", ""))[:10].replace("-", "") for r in self._up(market, 1, 20)]
        if not any(days):
            raise QuoteError("Naver 순위 응답에 체결 시각이 없습니다.")
        return max(days)

    # ── 상승률 순위 ──
    def risers(self, market: str, min_change: float = 0.0, pages: int = 3) -> list[Mover]:
        """시장별 상승률 상위 보통주. 상승률이 min_change 아래로 내려가면 다음 쪽을 받지 않는다(ETF 가 절반쯤 섞여 있다)."""
        out: list[Mover] = []
        us = of_exchange(market) is US
        for page in range(1, pages + 1):
            rows = self._up(market, page)
            for row in rows:
                if us:
                    if (m := self._us_mover(market, row, min_change)) is not None:
                        out.append(m)
                    continue
                code, name = str(row.get("itemCode", "")).strip(), str(row.get("stockName", "")).strip()
                price, diff, pct = _num(row.get("closePriceRaw")), _num(row.get("compareToPreviousClosePriceRaw")), _num(row.get("fluctuationsRatio"))
                if not code or not name or price <= 0 or pct < min_change or not is_common_stock(code, name, str(row.get("stockEndType", ""))):
                    continue
                out.append(Mover(code=code, name=name, market=market, price=price, prev_close=price - diff, change_pct=pct,
                                 trade_value=_num(row.get("accumulatedTradingValueRaw")) / 1e8))
            if not rows or _num(rows[-1].get("fluctuationsRatio")) < min_change:
                break
        return out

    @staticmethod
    def _us_mover(market: str, row: dict, min_change: float) -> Mover | None:
        code, eng = us_symbol(str(row.get("symbolCode") or "")), str(row.get("stockNameEng") or "").strip()
        name = (str(row.get("stockName") or "").strip() or eng)[:40]
        price, diff, pct = _num(row.get("closePriceRaw")), _num(row.get("compareToPreviousClosePriceRaw")), _num(row.get("fluctuationsRatioRaw"))
        if not code or not name or price <= 0 or price - diff <= 0 or pct < min_change or not is_us_common_stock(code, eng, str(row.get("stockEndType", ""))):
            return None
        logo = str(row.get("itemLogoPngUrl") or "")
        return Mover(code=code, name=name, market=market, price=price, prev_close=price - diff, change_pct=pct,
                     trade_value=_num(row.get("accumulatedTradingValueRaw")) / 1e6,
                     logo_url=logo if logo.startswith("https://ssl.pstatic.net/") else None, name_eng=eng or None)

    # ── 당일 5분 시세 ──
    def chart(self, code: str, market: str, day_ymd: str) -> Chart:
        """그날(day_ymd) 정규장 5분 간격 [구간 끝 HH:MM, 종가]와 마지막 체결가·시각. 휴장일에는 Yahoo 가 전 거래일 봉을 주므로
        날짜가 다른 봉·체결가는 버린다(빈 목록·None = 그날 거래 없음). 라벨을 구간 끝으로 단다 — 09:00 봉의 종가는 09:05 시점 가격이다.
        시각은 그 시장 현지 시각(국내 KST · 미국 뉴욕)이다."""
        mk = of_exchange(market)
        symbol = code.replace(".", "-") if mk is US else f"{code}.{SUFFIX[market]}"
        data = self._get(YAHOO_CHART.format(symbol=urllib.parse.quote(symbol)))
        result = ((data.get("chart") or {}).get("result") or [None])[0]
        if not result:
            raise QuoteError(f"{code} 차트 응답이 비었습니다: {((data.get('chart') or {}).get('error') or {}).get('description', '사유 미상')}")
        closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        out = []
        for ts, price in zip(result.get("timestamp") or [], closes):
            at = datetime.fromtimestamp(ts, mk.tz)
            if price is None or at.strftime("%Y%m%d") != day_ymd or not mk.open <= at.strftime("%H%M") < mk.close:
                continue
            end = at + timedelta(minutes=5)
            out.append([end.strftime("%H:%M"), round(float(price), 2)])
        meta = result.get("meta") or {}
        last, ts = _num(meta.get("regularMarketPrice"), 0.0), meta.get("regularMarketTime")
        at = datetime.fromtimestamp(ts, mk.tz) if isinstance(ts, (int, float)) else None
        if last <= 0 or at is None or at.strftime("%Y%m%d") != day_ymd:
            return Chart(out, None, None)
        return Chart(out, round(last, 2), at.isoformat(timespec="seconds"))
