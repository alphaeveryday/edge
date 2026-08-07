"""backfill_intraday_5m_toss — 결손을 결손으로 보는가 (ALPHA-836).

이 백필이 못 채우던 두 자리는 뿌리가 같다: **이미 있는 것으로 있어야 할 것을 정의**했다.
날짜 축은 "파티션이 곧 달력", 종목 축은 "정본에 있는 종목이 곧 유니버스"였다. 둘 다
부재를 부재로 못 보므로 조용히 0건으로 끝난다 — 백필에서 그건 실패가 성공으로 위장되는
길이다(Rule 12). 각 테스트는 그 위장이 되살아나면 깨진다.
"""

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "backfill_intraday_5m_toss",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_intraday_5m_toss.py",
)
backfill = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backfill)


class _FakeS3:
    """프리픽스별 파티션 목록만 흉내낸다 — 달력 판정이 보는 유일한 표면이다."""

    def __init__(self, partitions: dict[str, list[str]]):
        self._partitions = partitions
        self.puts: list[str] = []

    def list_objects_v2(self, **kw):
        prefix = kw["Prefix"].rstrip("/")
        days = self._partitions.get(prefix, [])
        return {"CommonPrefixes": [{"Prefix": f"{prefix}/trade_date={d}/"} for d in days]}

    def put_object(self, **kw):
        self.puts.append(kw["Key"])


def test_trading_days_sees_a_day_that_only_the_daily_ledger_knows():
    """5분봉 파티션이 통째로 빠진 거래일이 달력에 들어온다.

    WHY: 2026-08-03·08-04 는 거래일인데 intraday_5m 파티션이 없었다. 파티션을 달력으로
    삼으면 그 날은 대상 목록에 들지도 못해 **영영 안 채워진다**. 일봉이 독립 증인이다.
    """
    s3 = _FakeS3({
        backfill.PREFIX: ["2026-07-31", "2026-08-05"],
        backfill.PRICE_DAILY_PREFIX: ["2026-07-31", "2026-08-03", "2026-08-04", "2026-08-05"],
    })

    days = backfill._trading_days(s3, "bkt")

    assert days == ["2026-07-31", "2026-08-03", "2026-08-04", "2026-08-05"]


def test_trading_days_unions_rather_than_replaces():
    """달력은 합집합이다 — 일봉으로 **교체**하면 창이 쪼그라든다.

    WHY: price_daily 는 파티션이 몇 주치뿐이다(실측 21일). 교체하면 70일 창이 21일이
    되고 전 종목이 백필 대상이 돼 콜 예산을 통째로 태운다.
    """
    s3 = _FakeS3({
        backfill.PREFIX: ["2026-05-13", "2026-06-01", "2026-07-31"],
        backfill.PRICE_DAILY_PREFIX: ["2026-07-31", "2026-08-03"],
    })

    days = backfill._trading_days(s3, "bkt")

    assert days[0] == "2026-05-13", "일봉 창 밖의 과거가 잘렸다 — 교체가 됐다"
    assert "2026-08-03" in days


@pytest.mark.parametrize("rows", [[], None])
def test_write_day_does_not_put_when_there_is_nothing_new(rows):
    """새 행이 없으면 PUT 이 안 나간다.

    WHY: 같은 바이트로 파일을 덮는 PUT 이 라이브 소비자 읽기와 경합한다 — 실측
    2026-08-07: analysis-engine 이 백필 파일에서 HTTP 416 Range Not Satisfiable 로
    설명 발행에 실패했다. 재실행마다 재발한다.
    """
    s3 = _FakeS3({})

    assert backfill._write_day(s3, "bkt", "2026-08-03", rows or [], dry=False) == 0
    assert s3.puts == [], "빈 쓰기가 PUT 을 냈다"


def test_write_day_puts_when_rows_exist():
    """반대 방향도 고정한다 — 위 가드가 모든 쓰기를 막아 버리면 백필이 죽는다."""
    s3 = _FakeS3({})
    ts = datetime(2026, 8, 3, 9, 0)
    row = {"ticker": "091170", "source_symbol": "091170", "ts": ts,
           "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10,
           "source_vendor": backfill.SOURCE_VENDOR,
           "available_at": datetime(2026, 8, 3, 9, 5)}

    assert backfill._write_day(s3, "bkt", "2026-08-03", [row], dry=False) == 1
    assert s3.puts == [f"{backfill.PREFIX}/trade_date=2026-08-03/{backfill.BACKFILL_NAME}"]


def test_configured_universe_survives_a_missing_config(monkeypatch):
    """설정을 못 읽어도 백필은 돌아야 한다 — 다만 조용하지 않게.

    WHY: 이 함수는 보강 축이다. 여기서 예외가 나가면 관측 유니버스만으로도 채울 수 있던
    종목까지 통째로 못 돌린다. 반대로 조용히 빈 집합을 내면 왜 대상이 줄었는지 안 남는다.
    """
    monkeypatch.setattr(backfill, "log", backfill.logging.getLogger("test-quiet"))
    monkeypatch.setitem(
        __import__("sys").modules, "data_pipeline.config.loader", None)

    assert backfill._configured_universe() == set()
