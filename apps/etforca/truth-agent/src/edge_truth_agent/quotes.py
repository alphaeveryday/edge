"""시세 — 키 없는 공개 시세. 포트폴리오 서비스와 같은 출처를 쓴다(Yahoo 차트 · Naver 증권).

- 상승률 순위: Naver 증권 `m.stock.naver.com/api/stocks/up/{KOSPI|KOSDAQ}` (상승률 내림차순, 실제 누적 거래대금 포함)
- 당일 5분 시세: Yahoo 차트 `v8/finance/chart/{code}.KS|.KQ?interval=5m&range=1d` (봉 시각 = 구간 시작)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import time
import urllib.error
import urllib.parse
import urllib.request

KST = timezone(timedelta(hours=9))
NAVER_UP = "https://m.stock.naver.com/api/stocks/up/{market}?page={page}&pageSize=100"
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
    trade_value: float  # 억원(누적 거래대금)


def is_common_stock(code: str, name: str, end_type: str = "stock") -> bool:
    """보통주인가. ETF·ETN 은 응답의 종목 유형으로, 우선주는 코드 끝자리(보통주는 0)로, 스팩은 이름으로 거른다."""
    return end_type == "stock" and len(code) == 6 and code.isalnum() and code.endswith("0") and "스팩" not in name


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

    # ── 상승률 순위 ──
    def risers(self, market: str, min_change: float = 0.0, pages: int = 3) -> list[Mover]:
        """시장별 상승률 상위 보통주. 상승률이 min_change 아래로 내려가면 다음 쪽을 받지 않는다(ETF 가 절반쯤 섞여 있다)."""
        out: list[Mover] = []
        for page in range(1, pages + 1):
            rows = self._get(NAVER_UP.format(market=market, page=page)).get("stocks") or []
            for row in rows:
                code, name = str(row.get("itemCode", "")).strip(), str(row.get("stockName", "")).strip()
                price, diff, pct = _num(row.get("closePriceRaw")), _num(row.get("compareToPreviousClosePriceRaw")), _num(row.get("fluctuationsRatio"))
                if not code or not name or price <= 0 or pct < min_change or not is_common_stock(code, name, str(row.get("stockEndType", ""))):
                    continue
                out.append(Mover(code=code, name=name, market=market, price=price, prev_close=price - diff, change_pct=pct,
                                 trade_value=_num(row.get("accumulatedTradingValueRaw")) / 1e8))
            if not rows or _num(rows[-1].get("fluctuationsRatio")) < min_change:
                break
        return out

    # ── 당일 5분 시세 ──
    def series(self, code: str, market: str, day_ymd: str) -> list[list]:
        """그날(day_ymd) 정규장 5분 간격 [구간 끝 HH:MM, 종가]. 휴장일에는 Yahoo 가 전 거래일 봉을 주므로 날짜가 다른 봉은 버린다
        (빈 목록 = 그날 거래 없음). 라벨을 구간 끝으로 단다 — 09:00 봉의 종가는 09:05 시점 가격이다."""
        data = self._get(YAHOO_CHART.format(symbol=f"{code}.{SUFFIX[market]}"))
        result = ((data.get("chart") or {}).get("result") or [None])[0]
        if not result:
            raise QuoteError(f"{code} 차트 응답이 비었습니다: {((data.get('chart') or {}).get('error') or {}).get('description', '사유 미상')}")
        closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        out = []
        for ts, price in zip(result.get("timestamp") or [], closes):
            at = datetime.fromtimestamp(ts, KST)
            if price is None or at.strftime("%Y%m%d") != day_ymd or not "0900" <= at.strftime("%H%M") <= "1530":
                continue
            end = at + timedelta(minutes=5)
            out.append([end.strftime("%H:%M"), round(float(price), 2)])
        return out
