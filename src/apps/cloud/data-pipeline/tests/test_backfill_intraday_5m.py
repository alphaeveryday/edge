"""backfill_intraday_5m_toss — 결손을 결손으로 보는가 (ALPHA-836).

이 백필이 못 채우던 두 자리는 뿌리가 같다: **이미 있는 것으로 있어야 할 것을 정의**했다.
날짜 축은 "파티션이 곧 달력", 종목 축은 "정본에 있는 종목이 곧 유니버스"였다. 둘 다
부재를 부재로 못 보므로 조용히 0건으로 끝난다 — 백필에서 그건 실패가 성공으로 위장되는
길이다(Rule 12).

각 테스트는 **이번 변경이 없으면 깨져야 한다**(Rule 9). 기존 가드를 찌르는 단언은
로직이 바뀌어도 안 깨지므로 회귀를 못 잡는다.
"""

import importlib.util
import logging
from datetime import datetime, time
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

    def list_objects_v2(self, **kw):
        prefix = kw["Prefix"].rstrip("/")
        days = self._partitions.get(prefix, [])
        return {"CommonPrefixes": [{"Prefix": f"{prefix}/trade_date={d}/"} for d in days]}


def _row(ticker: str, hour: int = 9, minute: int = 0) -> dict:
    ts = datetime(2026, 7, 30, hour, minute)
    return {"ticker": ticker, "source_symbol": ticker, "ts": ts,
            "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10,
            "source_vendor": backfill.SOURCE_VENDOR,
            "available_at": datetime(2026, 7, 30, hour, minute + 5)}


# ── 날짜 축 ────────────────────────────────────────────────────────────────

def test_calendar_sees_a_day_that_only_the_daily_ledger_knows():
    """5분봉 파티션이 통째로 빠진 거래일이 달력에 들어온다.

    WHY: 2026-07-17 은 거래일인데 intraday_5m 파티션이 없다고 하자. 파티션을 달력으로
    삼으면 그 날은 대상 목록에 들지도 못해 **영영 안 채워진다**. 일봉이 독립 증인이다.
    """
    s3 = _FakeS3({
        backfill.PREFIX: ["2026-07-16", "2026-07-20"],
        backfill.PRICE_DAILY_PREFIX: ["2026-07-16", "2026-07-17", "2026-07-20"],
    })

    assert backfill._trading_days(s3, "bkt") == ["2026-07-16", "2026-07-17", "2026-07-20"]


def test_calendar_unions_rather_than_replaces():
    """달력은 합집합이다 — 일봉으로 **교체**하면 창이 쪼그라든다.

    WHY: price_daily 는 파티션이 몇 주치뿐이다(실측 21일). 교체하면 70일 창이 21일이
    되고 전 종목이 백필 대상이 돼 콜 예산을 통째로 태운다.
    """
    s3 = _FakeS3({
        backfill.PREFIX: ["2026-05-13", "2026-06-01", "2026-07-31"],
        backfill.PRICE_DAILY_PREFIX: ["2026-07-31"],
    })

    days = backfill._trading_days(s3, "bkt")

    assert days[0] == "2026-05-13", "일봉 창 밖의 과거가 잘렸다 — 교체가 됐다"


def test_calendar_stops_at_the_rollup_ownership_boundary():
    """`WRITER_SINCE` 이후 날짜는 달력에서 빠진다 — 소유자가 롤업이다.

    WHY: 하필 파티션이 빠진 날이 곧 롤업 시대의 날이라, 가드가 없으면 백필이 **가장
    위험한 파티션을 정조준**한다. 거기 낯선 파일을 쓰면 `rollup._rollup_day` 의 foreign
    가드가 걸려 그날 5분 파생이 후크·EOD 양쪽에서 영구 정지한다. 이전 판은 "파티션이
    없으면 대상이 아니다"라는 **우연**에 기대 이 구간을 안 건드렸고, 달력이 합집합이
    되면서 그 우연이 사라졌다.
    """
    after = backfill.WRITER_SINCE
    s3 = _FakeS3({
        backfill.PREFIX: ["2026-07-31"],
        backfill.PRICE_DAILY_PREFIX: ["2026-07-31", after, "2099-01-01"],
    })

    days = backfill._trading_days(s3, "bkt")

    assert days == ["2026-07-31"], f"롤업 소유 구간이 대상에 들어왔다: {days}"


# ── 쓰기 판정 ──────────────────────────────────────────────────────────────

def test_no_payload_when_nothing_is_new():
    """새 행이 0 이면 **쓰지 않는다**(None).

    WHY: 같은 바이트로 파일을 덮는 PUT 이 라이브 소비자 읽기와 경합한다 — 실측
    2026-08-07: analysis-engine 이 백필 파일에서 HTTP 416 Range Not Satisfiable 로
    설명 발행에 실패했다. 재실행마다 재발한다. 남길 행이 있어도 쓰면 안 된다.
    """
    prior = [_row("091170")]                    # 남길 행은 있다
    candidates = [_row("069500")]               # 그런데 후보가 이미 정본에 있다

    assert backfill._day_payload(prior, {"069500"}, candidates) is None


def test_payload_merges_prior_landings_instead_of_replacing_them():
    """앞선 착지분을 **합쳐서** 돌려준다 — 이번 실행분만 쓰면 지난 런이 지워진다.

    WHY: 실측 2026-08-07, 9종만 돌린 재실행이 앞선 85종을 지웠다. 파일이 파티션당
    하나이고 put_object 가 통째로 덮기 때문이다.
    """
    prior = [_row("091170"), _row("091180")]
    candidates = [_row("102970")]

    rows, fresh = backfill._day_payload(prior, set(), candidates)

    assert fresh == 1
    assert {r["ticker"] for r in rows} == {"091170", "091180", "102970"}


def test_payload_drops_prior_rows_that_the_authoritative_file_now_owns():
    """정본이 나중에 가져간 티커는 백필 파일에서 뺀다.

    WHY: 소비자는 파티션을 `*.parquet` 글롭으로 읽는다. 지난 런이 백필 파일에 쓴 티커가
    나중에 `part-0` 에도 생기면 같은 행이 두 파일에 남아 **두 번 세어진다**. 서로소
    보장은 쓰기 시점의 한 겹뿐이므로 그 겹이 과거분까지 봐야 한다.
    """
    prior = [_row("091170"), _row("091180")]     # 091170 을 정본이 가져갔다
    candidates = [_row("102970")]

    rows, _ = backfill._day_payload(prior, {"091170"}, candidates)

    assert {r["ticker"] for r in rows} == {"091180", "102970"}, \
        "정본과 겹치는 행이 백필 파일에 남았다 — 글롭이 두 번 센다"


def test_payload_replaces_the_same_ticker_and_drops_off_session_bars():
    """같은 종목은 이번 값으로 갈아끼우고, 남기는 행에 정규장 필터를 다시 건다.

    WHY: 필터 없던 판이 쓴 장전·장후 봉이 남아 있으면 `interval._gap` 이 시간 필터
    없이 `first(open ORDER BY ts)` 를 잡아 **그날 시가가 통째로 틀린다**.
    """
    prior = [_row("102970", hour=8, minute=30),      # 장전 — 걷혀야 한다
             _row("091170", hour=16, minute=0),      # 장후 — 걷혀야 한다
             _row("091180", hour=10, minute=0)]      # 정규장 — 남아야 한다
    candidates = [_row("102970", hour=9, minute=5)]  # 같은 종목 → 갈아끼움

    rows, fresh = backfill._day_payload(prior, set(), candidates)

    assert fresh == 1
    kept = {(r["ticker"], r["ts"].time()) for r in rows}
    assert kept == {("091180", time(10, 0)), ("102970", time(9, 5))}


# ── 종목 축 ────────────────────────────────────────────────────────────────

def test_configured_universe_reads_the_declared_sector_candidates():
    """설정이 **선언한** 섹터 후보를 읽는다 — 관측이 아니라 선언이 기준이다.

    WHY: ALPHA-842 가 얹은 48종은 한 번도 수집된 적이 없어 관측 유니버스에 안 잡힌다.
    선언을 못 읽으면 그 종목들이 백필 대상에서 또 빠진다.
    """
    declared = backfill._configured_universe()

    assert len(declared) >= 40, f"선언된 섹터 후보가 비었다: {len(declared)}종"
    assert all(isinstance(t, str) and t for t in declared)


def test_configured_universe_does_not_swallow_a_schema_violation(monkeypatch):
    """설정 **검증 실패**는 삼키지 않는다 — 로더의 fail-loud 계약을 되돌리지 않는다.

    WHY: `except Exception` 으로 감싸면 스키마 위반(중복 코드 등)이 WARNING 한 줄 뒤
    exit 0 으로 지나가고, 종료코드로는 정상 실행과 구분이 안 된다(Rule 7·12).
    """
    import data_pipeline.config.loader as loader

    def boom():
        raise ValueError("sector_etf_ids 에 중복 코드가 있다")

    monkeypatch.setattr(loader, "load_settings", boom)

    with pytest.raises(ValueError, match="중복"):
        backfill._configured_universe()


def test_configured_universe_survives_a_missing_module_but_says_so(caplog):
    """설정 모듈 부재는 견디되 **조용하지 않게**.

    WHY: 이 함수는 보강 축이라 여기서 죽으면 관측만으로 채울 수 있던 종목까지 못 돌린다.
    반대로 조용히 빈 집합을 내면 왜 대상이 줄었는지 아무 데도 안 남는다.
    """
    import sys

    real = sys.modules.pop("data_pipeline.config.loader", None)
    sys.modules["data_pipeline.config.loader"] = None            # import 시 ImportError
    try:
        with caplog.at_level(logging.WARNING, logger=backfill.log.name):
            assert backfill._configured_universe() == set()
    finally:
        if real is not None:
            sys.modules["data_pipeline.config.loader"] = real
        else:
            sys.modules.pop("data_pipeline.config.loader", None)

    assert any("관측 유니버스만" in r.message for r in caplog.records), \
        "설정 부재가 무음으로 지나갔다"
