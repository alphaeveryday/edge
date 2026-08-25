"""Yahoo(yfinance) 가격 소스 어댑터 — **벤치마크 지수 시계열** 보강 (S004 가격 Step1).

**왜 이 벤더를 더 붙이는가.** KR 개별주·ETF 자체 종가는 KIS 가 이미 준다 —
`ingest_price_raw._kr_holdings_universe` 가 구성종목과 **ETF 자신**을 함께 유니버스에
넣는다(ALPHA-419). 빠져 있는 건 **지수 시계열** 하나다: `market_series` 마스터는 있는데
채워줄 소스가 없어 (1) L0 상대 게이트가 미적용이고(`load_price_triggers` 모듈 주석)
(2) 시장 성분 제거를 횡단면 평균으로 대신해야 한다. yfinance 는 키가 없고 `^KS11`·`^KQ11`
을 바로 준다 — **그 구멍만** 메우는 것이 이 어댑터의 목적이다.

FMP·KIS 어댑터와 같은 관례 인터페이스(`source_name`·`enabled`·`plan`·`fetch`·
`fetch_failures`·`planned_symbols`)를 지켜 기존 `ingest_price_raw` 스텝을 그대로
재사용한다(벤더 무관 duck typing, 스텝 수정 없음).

raw 행은 **FMP 형태**(date/open/high/low/close/volume/adjClose)로 낸다 — 정제
(`normalize_price`)가 `vendor == "kis"` 일 때만 KIS 맵으로 분기하므로 yahoo 는 새 맵
없이 FMP 경로를 탄다. 필드 선별·검증은 후속(canonical) 소관이라 여기선 이름만 맞춘다.

**로컬 전용 소스다.** `yfinance` 는 `local` dependency-group 이라 클라우드 이미지에 없고
(`Dockerfile` 은 그룹 없이 설치), SFN 수집 상태기계도 fmp·kis 만 편입한다. 이 어댑터는
로컬에서 분석엔진 실험용 데이터를 채우는 데 쓰고, **클라우드는 s3 canonical 레이크에서만
소비한다** — 로컬 수집분을 클라우드가 보게 하려면 레이크 백엔드를 s3 로 두고 돌린다:
`DATA_PIPELINE_STORAGE__BACKEND=s3 DATA_PIPELINE_STORAGE__BUCKET=…`.

**지수 행의 하류 거동(의도):** 지수는 `instrument` 마스터에 없으므로 `load_price_daily`
가 `skipped_unknown_instrument` 로 **세고 넘긴다**(FK 위반으로 배치를 죽이지 않는다).
분석엔진은 DB 가 아니라 canonical 레이크에서 수익률을 읽으므로(`adapters/lake.py`
`load_returns`) 지수는 레이크만으로 즉시 쓸 수 있다. 지수를 DB 에 두려면
`market_series_daily` 신설이 필요하고 그건 별건이다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

from ..config import YahooPriceSourceConfig

logger = logging.getLogger(__name__)

# yfinance 컬럼 → FMP raw 키. 정제가 FMP 맵으로 읽으므로 이름을 여기서 맞춘다.
_COLUMNS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "Adj Close": "adjClose",
}


def _number(value) -> float | None:
    """pandas 셀 → float. NaN/None/비수치는 None 으로 — 정제가 사유(missing/non_numeric)를 붙인다."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num == num else None  # NaN != NaN


class YahooPriceSource:
    """야후 일봉 어댑터(무인증) — KR 종목 + 지수 대조축 전용(US 는 FMP 소관,
    지수는 plan 이 항상 포함한다)."""

    source_name = "yahoo"

    def __init__(self, config: YahooPriceSourceConfig):
        self.config_enabled = config.enabled
        self.index_map = dict(config.index_map)
        self.symbol_map = dict(config.symbol_map)
        self.suffix = config.suffix
        self.fetch_failures: list[dict] = []
        self.planned_symbols: int | None = None

    @property
    def enabled(self) -> bool:
        """수집 가능 여부 — 설정 플래그가 유일한 스위치(인증 없음)."""
        # 인증이 없다 — 설정 플래그가 유일한 스위치.
        return self.config_enabled

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, yahoo_symbol)].

        지수는 **targets/holdings 와 무관하게 항상 포함**한다 — 지수는 우리 유니버스의
        종목이 아니라 대조축이라 symbols 에 들어올 길이 없다. 그래서 여기서만 들어온다.

        종목은 KR 만 다룬다(US 는 FMP 담당). `symbol_map` 오버라이드가 우선이고, 없으면
        `{ticker}{suffix}`(기본 `.KS`). KOSDAQ 상장분은 `symbol_map` 에 `.KQ` 로 둔다 —
        접미사를 추정하면 조용히 다른 시장 종목을 붙일 수 있다.
        """
        planned: list[tuple[str, str]] = [
            (our, yahoo) for our, yahoo in sorted(self.index_map.items())
        ]
        seen = {our for our, _ in planned}
        for ticker in symbols:
            if ticker in seen:
                continue
            mapped = self.symbol_map.get(ticker)
            if mapped is None:
                # KR 단축코드만 접미사 규칙으로 유도한다 — 6자 US 심볼을 KR 로 주워담지
                # 않도록 '숫자로 시작하는 6자'로 좁힌다(parse.krx_short_code 와 같은 취지).
                if not (len(ticker) == 6 and ticker[0].isdigit()):
                    continue
                mapped = f"{ticker}{self.suffix}"
            planned.append((ticker, mapped))
            seen.add(ticker)
        self.planned_symbols = len(planned)
        return planned

    def _note_failure(self, yahoo_symbol: str, our_ticker: str, reason: str) -> None:
        """심볼 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("yahoo 가격 수집 실패 symbol=%s ticker=%s: %s",
                       yahoo_symbol, our_ticker, reason)
        self.fetch_failures.append(
            {"symbol": yahoo_symbol, "our_ticker": our_ticker, "reason": reason}
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """심볼별로 [from_date, to_date] 창의 일봉을 낸다(심볼당 1콜).

        한 심볼이 죽어도 나머지는 계속한다 — 실패는 `fetch_failures` 로 드러난다.
        """
        try:
            import yfinance as yf  # 로컬 전용 선택 의존(local 그룹) — 이 벤더를 쓸 때만 로드
        except ModuleNotFoundError as exc:
            # 클라우드 이미지엔 없는 게 정상이다 — 조용한 빈 수집 대신 사유를 드러낸다.
            raise RuntimeError(
                "yfinance 미설치 — yahoo 는 로컬 전용 소스다"
                " (uv sync --package data-pipeline --group local)."
                " 클라우드는 s3 canonical 레이크에서 소비한다."
            ) from exc

        fetched_at = datetime.now(timezone.utc).isoformat()
        # yfinance 의 end 는 **배타**다 — to_date 당일을 포함하려면 하루 더한다.
        end = None
        if to_date:
            end = (date.fromisoformat(to_date) + timedelta(days=1)).isoformat()

        for our_ticker, yahoo_symbol in self.plan(symbols):
            try:
                frame = yf.download(
                    yahoo_symbol,
                    start=from_date,
                    end=end,
                    auto_adjust=False,   # adjClose 를 별도 컬럼으로 받는다(FMP 와 같은 계약)
                    progress=False,
                    threads=False,
                )
            except Exception as exc:  # 네트워크·심볼 오류 — 심볼 단위 격리
                self._note_failure(yahoo_symbol, our_ticker, repr(exc))
                continue
            if frame is None or frame.empty:
                self._note_failure(yahoo_symbol, our_ticker, "empty_frame")
                continue
            # 단일 심볼도 MultiIndex 컬럼으로 오는 버전이 있다 — 상위 레벨만 남긴다.
            if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
                frame = frame.droplevel(-1, axis=1)
            for timestamp, row in frame.iterrows():
                record = {
                    "date": timestamp.date().isoformat(),
                    "our_ticker": our_ticker,
                    "market": "KR",          # 이 어댑터는 KRX·KR 지수 전용(US 는 FMP)
                    "yahoo_symbol": yahoo_symbol,
                    "fetched_at": fetched_at,
                }
                for column, key in _COLUMNS.items():
                    record[key] = _number(row.get(column))
                yield record
