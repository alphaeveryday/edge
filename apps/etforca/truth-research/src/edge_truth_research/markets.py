"""시장별 설정 — 국내(KRX)와 미국. 흐름·판정 규칙은 같고 출처·시간대·기준·저장 자리만 다르다(docs/design/truth-movers.md §6).

국내는 예전 저장 자리(movers/·state/)를 그대로 쓴다. 미국은 movers/us/·state/us/ 에 따로 둔다 — 화면이 시장을 골라 읽는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Market:
    key: str                     # kr · us (명령줄 --market)
    exchanges: tuple[str, ...]   # 순위를 받는 거래소. 첫 거래소로 그날 체결이 있었는지 본다
    tz: tzinfo                   # 거래소 현지 시각 — 날짜·장 시간·그래프 라벨이 모두 이것이다
    open: str                    # 정규장 HHMM(현지)
    close: str
    data: str                    # 게시·원본 접두어(공개 버킷 truth/data/ 아래, 비공개 저장소도 같은 이름)
    state: str                   # 비공개 상태 접두어(판정 기록·잠금·감시 기록)
    min_trade_value: float       # 누적 거래대금 하한: 국내 억원 · 미국 백만 달러
    min_price: float             # 현재가 하한(미국은 1달러 미만 동전주를 뺀다)
    filings: str                 # 공시 근거 id 접두어: kind-… · sec-…

    @property
    def lease_key(self) -> str:
        return f"{self.state}/lease-movers.json"

    @property
    def launch_key(self) -> str:
        return f"{self.state}/watch-launch.json"

    @property
    def dates_key(self) -> str:
        return f"{self.data}/dates.json"

    def seen_key(self, day: str) -> str:
        return f"{self.state}/movers-seen/{day}.json"


KR = Market("kr", ("KOSPI", "KOSDAQ"), timezone(timedelta(hours=9)), "0900", "1530", "movers", "state", 30.0, 0.0, "kind")
# 미국: 30억원 ≈ 200만 달러. 서머타임은 시간대가 맡는다(스케줄도 America/New_York)
US = Market("us", ("NASDAQ", "NYSE", "AMEX"), ZoneInfo("America/New_York"), "0930", "1600", "movers/us", "state/us", 2.0, 1.0, "sec")
MARKETS = {m.key: m for m in (KR, US)}


def of_exchange(exchange: str) -> Market:
    """거래소 이름 → 시장."""
    return US if exchange in US.exchanges else KR
