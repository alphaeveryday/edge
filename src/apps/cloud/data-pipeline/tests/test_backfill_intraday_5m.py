"""backfill_intraday_5m — 결손을 결손으로 보는가 (ALPHA-836).

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

from data_pipeline.minute import rollup

_SPEC = importlib.util.spec_from_file_location(
    "backfill_intraday_5m",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_intraday_5m.py",
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
            "source_vendor": backfill.VENDORS["toss"]["vendor"],
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
    after = rollup.WRITER_SINCE
    s3 = _FakeS3({
        backfill.PREFIX: ["2026-07-31"],
        backfill.PRICE_DAILY_PREFIX: ["2026-07-31", after, "2099-01-01"],
    })

    days = backfill._trading_days(s3, "bkt")

    assert days == ["2026-07-31"], f"롤업 소유 구간이 대상에 들어왔다: {days}"


def test_calendar_also_drops_a_backfilled_day_before_the_boundary():
    """경계 **앞**이라도 롤업이 소유하게 된 날은 백필이 손을 뗀다.

    WHY: 소유권은 `writer_owns()` 하나가 정하고 경계는 그중 기본값일 뿐이다. 예외를
    여기서 안 보면 두 writer 가 같은 파티션을 다투고, 그러면 롤업 쪽 foreign 가드가
    걸려 그날 5분 파생이 영구 정지한다 — 위 테스트가 막으려던 사고가 경계 앞에서
    똑같이 난다(2026-08-03 이 ALPHA-846 의 KIS 소급 수집으로 그렇게 됐다).
    """
    owned = sorted(rollup.WRITER_OWNED_BEFORE_SINCE)[0]
    assert owned < rollup.WRITER_SINCE, "예외가 경계 앞이어야 의미가 있다"
    s3 = _FakeS3({
        backfill.PREFIX: ["2026-07-31"],
        backfill.PRICE_DAILY_PREFIX: ["2026-07-31", owned],
    })

    days = backfill._trading_days(s3, "bkt")

    assert days == ["2026-07-31"], f"롤업이 소유하게 된 날이 대상에 남았다: {days}"


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


# ── 벤더 축 (ALPHA-856) ─────────────────────────────────────────────────────

def test_vendor_files_do_not_collide():
    """벤더마다 **다른 파일**에 쓴다 — 같으면 나중 벤더가 앞 벤더를 덮어 지운다.

    파티션은 `*.parquet` 글롭으로 읽히므로 파일이 갈리면 두 벤더가 공존하고, 같으면
    한쪽이 사라진다. 토스 백필 71일이 이미 착지해 있는 상태에서 KIS 를 얹는 것이
    ALPHA-856 이라 이 축이 곧 데이터 보존이다.
    """
    files = [v["file"] for v in backfill.VENDORS.values()]
    vendors = [v["vendor"] for v in backfill.VENDORS.values()]
    assert len(set(files)) == len(files), f"파일명이 겹친다: {files}"
    assert len(set(vendors)) == len(vendors), f"source_vendor 가 겹친다: {vendors}"
    # `part-0` 은 롤업 소유다 — 어느 벤더도 그 이름을 쓰면 안 된다(재집계가 지운다)
    assert "part-0.parquet" not in files


def test_coverage_counts_every_vendor_not_just_mine(monkeypatch):
    """결손 판정이 **모든 벤더**의 백필 파일을 합쳐서 센다.

    내 벤더 파일만 세면 다른 벤더가 이미 채운 날짜를 다시 받아 나란히 쓴다 — 파일이
    갈려 있어 덮이지도 않으므로 같은 (ticker, ts) 가 두 벌로 남고 집계가 조용히 두 번
    센다. 2026-04-23~08-07 71일을 토스가 갖고 있는 채로 KIS 를 도는 것이 ALPHA-856 이라
    이 자리가 곧 이중계상 방지선이다.

    벤더가 늘어도 안 낡도록 **VENDORS 에서 기대값을 유도한다** — 목록을 테스트에
    베껴 적으면 새 벤더가 추가될 때 이 단언만 조용히 옛 목록을 지킨다.
    """
    per_file = {spec["file"]: f"T{i}" for i, spec in enumerate(backfill.VENDORS.values())}
    assert len(per_file) >= 2, "벤더가 하나면 이 회귀를 못 잡는다"

    import pyarrow as pa

    monkeypatch.setattr(
        backfill, "_read_day",
        lambda _s3, _b, _d, name="part-0.parquet": pa.table({"ticker": [per_file[name]]}),
    )
    assert backfill._backfilled_tickers(None, "bkt", "2026-04-23") == set(per_file.values())


def test_kis_asks_every_regular_session_minute():
    """소급 경로가 정규장 **390분을 빠짐없이** 묻는다.

    ⚠️ 하루 캐시를 직접 들여다보지 않고 `candles(window_end=…)` 를 하나씩 부르는 것이
    의도다 — 범위 가드·무거래 분 합성·결정적 실패 캐시가 전부 그 경로에 걸려 있어,
    우회하면 백필과 1분 수집 레인이 같은 벤더 응답에서 **다른 봉**을 만든다.

    **존재가 아니라 모양·개수·경계를 묻는다**: 첫 window 는 09:01(=09:00~09:01),
    마지막은 15:30(=15:25~15:30). 한 칸 밀리면 하루가 통째로 어긋난다.
    """
    asked: list[datetime] = []

    class _Client:
        def candles(self, symbol, *, window_end):
            asked.append(window_end)
            return ()

    got = backfill._day_candles_kis(_Client(), "091170", "2026-04-22")
    assert got == []
    assert len(asked) == backfill.SESSION_MINUTES == 390
    assert asked[0].strftime("%Y-%m-%d %H:%M") == "2026-04-22 09:01"
    assert asked[-1].strftime("%Y-%m-%d %H:%M") == "2026-04-22 15:30"
    # 1분 간격이고 건너뛴 분이 없다
    assert {(b - a).total_seconds() for a, b in zip(asked, asked[1:])} == {60.0}
    assert all(w.utcoffset().total_seconds() == 9 * 3600 for w in asked), "KST 축이 아니다"


def test_kis_issues_one_token_across_every_day(monkeypatch):
    """날짜가 바뀌어도 **토큰을 다시 발급하지 않는다**(ALPHA-856).

    KIS 는 토큰 발급이 **분당 1회**다. 날짜마다 클라이언트를 새로 만들면서 인증까지
    새로 만들면 하루당 ~70초를 순수 대기로 태우고(2026-08-08 dry-run 실측), 180거래일
    이면 3.5시간 — 실제 수집 시간(~46분)보다 길어진다. HTTP 클라이언트도 같이 공유해야
    pacing 상태가 날짜 경계에서 리셋되지 않는다(앱키 전역 한도라 버스트가 남의 예산이다).

    **개수가 아니라 동일성을 묻는다** — 인스턴스가 하나여도 auth 를 새로 달면 회귀다.
    """
    made = []

    class _Auth:
        pass

    class _Client:
        def __init__(self, key, secret, http, *, session_date):
            self.session_date = session_date
            self.http = http
            self.auth = _Auth()          # 진짜 클라이언트처럼 스스로 만든다
            made.append(self)

        def candles(self, symbol, *, window_end):
            return ()

    monkeypatch.setattr(backfill, "KisHistoricalMinuteClient", _Client)
    monkeypatch.setattr(backfill.boto3, "client", lambda *_a, **_k: _Secrets())
    days = ["2026-04-17", "2026-04-20", "2026-04-21"]
    list(backfill._collect_kis(days, ["091170"], {d: set() for d in days},
                               _Args(vendor="kis")))

    assert len(made) == len(days), "날짜마다 클라이언트를 만들어야 한다(봉 캐시가 날짜에 묶임)"
    assert len({id(c.auth) for c in made}) == 1, "날짜마다 인증을 새로 만들면 토큰을 재발급한다"
    assert len({id(c.http) for c in made}) == 1, "HTTP 클라이언트가 갈리면 pacing 이 리셋된다"


class _Secrets:
    def get_secret_value(self, SecretId):            # noqa: N803
        return {"SecretString": '{"app_key": "k", "app_secret": "s"}'}


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_kis_interval_tracks_the_minute_lane():
    """유량 간격이 1분 가격 워커의 정본과 **같은 값**이다.

    `KIS_MIN_INTERVAL` 은 그 정본을 베낀 값이다. 유량은 앱키 전역이라 두 값이 갈리면
    이 백필이 레포 예산선(15/s)을 모르는 채 돈다. 정본 주석이 "EGW00201 이 보이면
    여기(0.08)를 먼저 늘려라"라고 못박은 자리라 실제로 움직인다 — 그때 이 복제본만
    옛 값을 지키는 것을 막는다. 런타임 import 대신 테스트로 거는 이유는 스크립트가
    `config` 모듈 없이도 기동해야 하기 때문이다(`_configured_universe` 참조).
    """
    from data_pipeline.config.models import MinutePriceWorkerConfig

    canon = MinutePriceWorkerConfig.model_fields["min_interval_sec"].default
    assert backfill.KIS_MIN_INTERVAL == canon, (
        f"1분 레인이 {canon} 로 움직였는데 백필은 {backfill.KIS_MIN_INTERVAL} 이다"
    )


def test_kis_rows_carry_the_kis_vendor_tag():
    """소급 경로가 낸 행은 **kis 벤더**로 태깅된다.

    `source_vendor` 가 산출물에 남는 유일한 출처 표시다. 토스 값으로 태깅되면 나중에
    이음매를 정리할 사람이 어느 행이 어느 벤더인지 파일명으로만 알게 되고, 파일명은
    옮겨질 수 있다. 태깅 자체가 갈리면 이 PR 의 존재 이유(토스 종가 일치율 35%)를
    산출물에서 되짚을 수 없다.
    """
    from data_pipeline.sources.candle import build_candle

    day = "2026-04-22"

    class _Client:
        auth = object()

        def candles(self, symbol, *, window_end):
            # 09:01 window 하나만 준다 — 태깅 축만 보는 테스트다
            if window_end.strftime("%H:%M") != "09:01":
                return ()
            return (build_candle(symbol, window_end=window_end, span_seconds=60,
                                 values={"open": 1, "high": 1, "low": 1, "close": 1},
                                 volume=0),)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(backfill, "KisHistoricalMinuteClient",
                   lambda *a, **k: _Client())
    monkey.setattr(backfill.boto3, "client", lambda *_a, **_k: _Secrets())
    try:
        per_day = dict(backfill._collect_kis([day], ["091170"], {day: set()},
                                             _Args(vendor="kis")))
    finally:
        monkey.undo()

    rows = per_day[day]
    assert rows, "행이 하나도 안 나왔다 — 태깅 축을 못 본다"
    assert {r["source_vendor"] for r in rows} == {"kis_backfill"}
    assert {r["ticker"] for r in rows} == {"091170"}


def test_main_writes_the_requested_vendors_file_with_that_vendors_collector():
    """`--vendor` 가 **수집기와 파일명을 함께** 고른다.

    이 배선은 `main()` 에만 있고 잎 함수 테스트가 한 줄도 안 밟는다. 변이 실증으로
    셋 다 스위트를 통과했다: ① 수집기 디스패치 반전(kis 를 부탁했는데 토스가 돈다)
    ② 파일명을 늘 토스 것으로 ③ 태깅을 늘 토스 것으로. ①이 제일 아프다 — 운영자는
    KIS 를 받았다고 믿는데 `part-kis-backfill.parquet` 에 토스 값이 앉고, 그러면 이
    백필의 근거(토스는 KIS·fmp 어느 쪽과도 다르다)가 산출물에서 뒤집힌다.

    **누가 돌았는지와 어디에 썼는지를 따로 묻는다** — 한쪽만 보면 나머지 변이가 산다.
    """
    day = "2026-04-22"
    ran: list[str] = []
    wrote: list[tuple[str, str]] = []

    monkey = pytest.MonkeyPatch()
    monkey.setattr(backfill, "_s3", lambda: None)
    monkey.setattr(backfill, "_trading_days", lambda _s3, _b: [day])
    monkey.setattr(backfill, "_read_day",
                   lambda _s3, _b, _d, name="part-0.parquet": None)
    monkey.setattr(backfill, "_configured_universe", lambda: {"091170"})
    monkey.setattr(backfill, "_collect_toss",
                   lambda *a, **k: ran.append("toss") or [(day, [_row("091170")])])
    monkey.setattr(backfill, "_collect_kis",
                   lambda *a, **k: ran.append("kis") or [(day, [_row("091170")])])
    monkey.setattr(backfill, "_write_day",
                   lambda _s3, _b, d, rows, _dry, name: wrote.append((d, name)) or len(rows))

    try:
        for vendor in sorted(backfill.VENDORS):
            ran.clear(), wrote.clear()
            monkey.setattr("sys.argv",
                           ["backfill", "--vendor", vendor, "--dry-run"])
            assert backfill.main() == 0
            assert ran == [vendor], f"--vendor {vendor} 인데 {ran} 이 돌았다"
            assert wrote == [(day, backfill.VENDORS[vendor]["file"])], \
                f"--vendor {vendor} 가 {wrote} 에 썼다"
    finally:
        monkey.undo()


def test_vendor_must_be_chosen_explicitly():
    """`--vendor` 에 기본값이 없다 — 기본은 되돌릴 수 없는 선택이 된다.

    결손 판정이 두 벤더를 합쳐 세므로, 기본값으로 한 번 받아 버린 (종목, 날짜) 는
    이후 다른 벤더 실행에서 탈락한다. 되돌리는 길은 S3 수동 삭제뿐이라 "그냥 돌려
    보는" 실행이 데이터 결정이 된다.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr("sys.argv", ["backfill"])
    try:
        with pytest.raises(SystemExit) as e:
            backfill.main()
    finally:
        monkey.undo()
    assert e.value.code != 0, "--vendor 없이도 기동했다"


def test_partial_day_is_flagged_against_that_days_grid(caplog):
    """부분본 판정의 격자는 **그날 관측된 최대값**이지 상수 78 이 아니다.

    개장이 밀리는 날이 실재한다 — 2025-11-13 은 10:00 개장이라 전 종목이 67봉이다
    (정본 `part-0` 와 KIS 소급이 일치). 78 로 박으면 그런 날 전 종목이 거짓 경고가
    되고, 경고가 늘 켜져 있으면 진짜 부분본을 아무도 안 본다.

    **세 축을 따로 묻는다**: ① 짧은 종목을 집는가 ② 격자가 그날에 맞춰 움직이는가
    ③ 전 종목이 똑같이 짧은 날(=개장 지연)은 조용한가.
    """
    # 정규장 하루(78봉)에서 한 종목만 짧다
    assert backfill._warn_partial_days("2025-09-01", {"A": 78, "B": 70, "C": 78}) == ["B"]

    # 개장 지연일 — 전 종목이 67봉이면 격자가 67 이라 아무도 짧지 않다
    caplog.clear()
    assert backfill._warn_partial_days("2025-11-13", {"A": 67, "B": 67}) == []
    assert not [r for r in caplog.records if r.levelname == "WARNING"], \
        "개장 지연일 전 종목을 부분본으로 신고했다"

    # 같은 67봉 격자 위에서 39봉짜리는 잡힌다 (실측 279540)
    caplog.clear()
    assert backfill._warn_partial_days("2025-11-13", {"A": 67, "B": 39}) == ["B"]
    assert any("격자 67" in r.message for r in caplog.records), \
        "격자를 그날 값으로 안 적었다"

    # 대상이 하나뿐이면 비교 대상이 없다 — 판정을 걸지 않는다
    assert backfill._warn_partial_days("2025-09-01", {"A": 12}) == []


def test_source_global_failure_is_not_folded_into_a_ticker_skip():
    """`KisSourceError` 는 **올라간다** — 종목 하나의 실패로 접지 않는다.

    어댑터가 두 축을 일부러 갈라 놨다: 전역(자격증명·권한·유량) vs 그 종목 하나.
    전역을 여기서 삼키면 앱키가 틀렸을 때 48종 × N일이 전부 ERROR 로 흐른 뒤 "완료 —
    0행" 과 **exit 0** 이 된다. 고칠 것은 설정 하나인데 재시도 대상처럼 보이면 아무도
    고치러 가지 않는다(Rule 12). unit 실패(=상장 전 날짜 포함)는 반대로 건너뛴다.
    """
    from data_pipeline.sources.kis_minute import KisDayIncompleteError, KisSourceError

    def _client_raising(exc):
        class _C:
            auth = object()

            def candles(self, symbol, *, window_end):
                raise exc
        return _C()

    def _run(exc):
        monkey = pytest.MonkeyPatch()
        monkey.setattr(backfill, "KisHistoricalMinuteClient",
                       lambda *a, **k: _client_raising(exc))
        monkey.setattr(backfill.boto3, "client", lambda *_a, **_k: _Secrets())
        try:
            return dict(backfill._collect_kis(["2026-04-22"], ["091170"],
                                              {"2026-04-22": set()}, _Args(vendor="kis")))
        finally:
            monkey.undo()

    with pytest.raises(KisSourceError):
        _run(KisSourceError("앱키 권한 없음"))

    # unit 축은 건너뛴다 — 상장 전 날짜가 정확히 이 경로다(실측: 48종 중 12종/2025-07-29)
    assert _run(KisDayIncompleteError("페이지 예산 소진")) == {}

    # 🔴 **맨 `ValueError` 도 unit 축이다.** 어댑터는 봉투 수준만 `KisUnitError` 로
    # 승격하고 **행 형상 위반**(비-dict 행·날짜 필드 타입·분 격자 밖·같은 window 에 다른
    # 봉)은 그대로 올린다 — `kis_minute` 가 *"collector 는 ValueError 만 unit INVALID 로
    # 접어서"* 라고 계약을 적어 놨다. 이걸 안 잡으면 손상 행 하나가 런을 죽이는데 그
    # 예외는 응답 형상의 함수라 **결정적**이라 재실행이 같은 날짜에서 또 죽는다 —
    # 그 뒤 날짜를 영영 못 넘는다.
    assert _run(ValueError("A 가 window 09:01 에 다른 봉 2건을 줬다 — 유일성 위반")) == {}


def test_collect_kis_actually_runs_the_partial_day_check(caplog):
    """부분본 판정이 **수집 루프에 배선돼** 있다.

    앞 테스트는 판정 함수만 직접 부른다 — 변이 실증에서 `_collect_kis` 안의 호출을
    통째로 지워도 스위트가 초록이었다. 잎만 잠그고 배선을 안 잠그는 것이 이 파일에서
    이미 한 번 난 결함이라(벤더 디스패치) 같은 자리를 두 번 비우지 않는다.
    """
    from data_pipeline.sources.candle import build_candle

    day = "2026-04-22"

    class _Client:
        auth = object()

        def candles(self, symbol, *, window_end):
            # A 는 하루를 채우고, B 는 13:00 이후에만 체결된다(늦은 첫 체결)
            if symbol == "B" and window_end.strftime("%H:%M") < "13:00":
                return ()
            return (build_candle(symbol, window_end=window_end, span_seconds=60,
                                 values={"open": 1, "high": 1, "low": 1, "close": 1},
                                 volume=0),)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(backfill, "KisHistoricalMinuteClient", lambda *a, **k: _Client())
    monkey.setattr(backfill.boto3, "client", lambda *_a, **_k: _Secrets())
    caplog.clear()
    try:
        with caplog.at_level(logging.WARNING):
            per_day = dict(backfill._collect_kis([day], ["A", "B"], {day: set()},
                                                 _Args(vendor="kis")))
    finally:
        monkey.undo()

    per_ticker = {}
    for r in per_day[day]:
        per_ticker[r["ticker"]] = per_ticker.get(r["ticker"], 0) + 1
    assert per_ticker["A"] == 78 and per_ticker["B"] < 78, per_ticker
    assert any("부분본" in r.message and "B" in r.message
               for r in caplog.records if r.levelname == "WARNING"), \
        "수집 루프가 부분본 판정을 안 걸었다"


def test_days_already_collected_survive_a_mid_run_global_failure():
    """중간에 전역 실패가 올라와도 **이미 받은 날은 착지한다**.

    수집기가 하루씩 내고 `main` 이 받는 대로 쓰기 때문이다. 모아서 돌려주던 판은
    마지막에야 썼으므로 180거래일 × 48종(~46분) 런이 중간에 죽으면 통째로 잃었다 —
    티켓 "할 일" 의 *재개 가능 — 중간에 죽으면 받은 날은 남기고 이어서* 가 이 자리다.
    다음 실행은 `covered` 가 착지한 날을 걸러 이어받는다.

    **예외가 올라오는 것과 앞 날이 쓰이는 것을 따로 묻는다** — 하나만 보면 "조용히
    삼키고 다 쓴다"와 "시끄럽게 죽고 다 잃는다"를 구분 못 한다.
    """
    from data_pipeline.sources.kis_minute import KisSourceError

    wrote: list[str] = []

    def _two_days_then_die(*_a, **_k):
        yield "2026-04-20", [_row("091170")]
        yield "2026-04-21", [_row("091170")]
        raise KisSourceError("유량 승격 — 앱키 전역")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(backfill, "_s3", lambda: None)
    monkey.setattr(backfill, "_trading_days", lambda _s3, _b: ["2026-04-20"])
    monkey.setattr(backfill, "_read_day",
                   lambda _s3, _b, _d, name="part-0.parquet": None)
    monkey.setattr(backfill, "_configured_universe", lambda: {"091170"})
    monkey.setattr(backfill, "_collect_kis", _two_days_then_die)
    monkey.setattr(backfill, "_write_day",
                   lambda _s3, _b, d, rows, _dry, _name: wrote.append(d) or len(rows))
    monkey.setattr("sys.argv", ["backfill", "--vendor", "kis", "--dry-run"])
    try:
        with pytest.raises(KisSourceError):
            backfill.main()
    finally:
        monkey.undo()

    assert wrote == ["2026-04-20", "2026-04-21"], \
        f"전역 실패 앞에서 받은 날이 안 쓰였다: {wrote}"


def test_kis_yields_each_day_before_collecting_the_next():
    """수집기가 **하루를 내고 나서** 다음 날을 시작한다.

    앞 테스트는 `main` 의 소비 쪽만 본다(수집기를 통째로 가짜로 바꾼다). 생산 쪽이
    모아 뒀다가 끝에 한 번에 내면 즉시 쓰기가 무의미해지는데 그 변이가 스위트를
    통과했다 — 두 쪽을 따로 잠근다.

    **다음 날 클라이언트가 아직 안 만들어졌는지**로 판정한다. 산출만 보면 모아서 낸
    것과 구분이 안 된다(둘 다 같은 목록을 낸다) — 구분되는 것은 **시점**뿐이다.
    """
    from data_pipeline.sources.candle import build_candle

    days = ["2026-04-20", "2026-04-21", "2026-04-22"]
    made: list[str] = []

    class _Client:
        auth = object()

        def candles(self, symbol, *, window_end):
            if window_end.strftime("%H:%M") != "09:01":
                return ()
            return (build_candle(symbol, window_end=window_end, span_seconds=60,
                                 values={"open": 1, "high": 1, "low": 1, "close": 1},
                                 volume=0),)

    def _make(_k, _s, _http, *, session_date):
        made.append(session_date.isoformat())
        return _Client()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(backfill, "KisHistoricalMinuteClient", _make)
    monkey.setattr(backfill.boto3, "client", lambda *_a, **_k: _Secrets())
    try:
        gen = backfill._collect_kis(days, ["091170"], {d: set() for d in days},
                                    _Args(vendor="kis"))
        day, rows = next(gen)
        assert day == days[0] and rows
        assert made == [days[0]], \
            f"첫 날을 내기 전에 다음 날을 수집했다 — 모아서 내고 있다: {made}"

        next(gen)
        assert made == days[:2], f"두 번째를 낼 때까지 만든 날: {made}"
    finally:
        gen.close()
        monkey.undo()


def test_main_feeds_every_vendors_coverage_in_and_merges_prior_rows_out():
    """`main` 의 배선 두 가닥 — **들어가는 커버리지**와 **나오는 병합**.

    둘 다 잎 함수는 잠겨 있는데 `main` 이 그걸 부르는지는 아무도 안 봤다(변이 실증에서
    각각 살아남았다). 이 파일에서 세 번째로 나온 같은 모양이라 여기서 쓸어 둔다.

    ① 결손 판정에 **모든 벤더 파일**이 들어간다 — 안 들어가면 토스가 이미 채운 71일을
       KIS 가 다시 받아 나란히 쓰고, 소비자 글롭이 같은 (ticker, ts) 를 두 번 센다.
    ② 쓰기 전에 **앞선 착지분을 읽어 병합**한다 — 안 하면 실측 사고가 재발한다
       (9종만 돌린 재실행이 앞선 85종을 지웠다).
    """
    import pyarrow as pa

    day = "2026-04-22"
    seen: dict = {}
    wrote: list[list[dict]] = []

    def _read(_s3, _b, _d, name="part-0.parquet"):
        if name == "part-0.parquet":
            return pa.table({"ticker": ["X"]})               # 정본이 가진 종목
        if name == backfill.VENDORS["toss"]["file"]:
            return pa.table({"ticker": ["Y"]})               # **상대 벤더**가 채운 종목
        return pa.Table.from_pylist([_row("P")], schema=backfill.SCHEMA)  # 내 앞선 착지분

    def _collect(_days, targets, covered, _a):
        seen["covered"] = {d: set(v) for d, v in covered.items()}
        yield day, [_row("Z")]

    monkey = pytest.MonkeyPatch()
    monkey.setattr(backfill, "_s3", lambda: None)
    monkey.setattr(backfill, "_trading_days", lambda _s3, _b: [day])
    monkey.setattr(backfill, "_read_day", _read)
    monkey.setattr(backfill, "_configured_universe", lambda: {"Z"})
    monkey.setattr(backfill, "_collect_kis", _collect)
    monkey.setattr(backfill, "_write_day",
                   lambda _s3, _b, _d, rows, _dry, _name: wrote.append(rows) or len(rows))
    monkey.setattr("sys.argv", ["backfill", "--vendor", "kis", "--dry-run"])
    try:
        assert backfill.main() == 0
    finally:
        monkey.undo()

    assert "Y" in seen["covered"][day], \
        "상대 벤더가 채운 종목이 결손 판정에 안 들어갔다 — 같은 날을 두 벌 받는다"
    assert "X" in seen["covered"][day], "정본 보유 종목이 결손 판정에 안 들어갔다"

    landed = {r["ticker"] for r in wrote[0]}
    assert landed == {"P", "Z"}, f"앞선 착지분이 병합되지 않았다: {landed}"


def test_toss_collector_yields_day_row_pairs():
    """토스 경로도 `(날짜, 행)` 스트림 계약을 진다.

    제너레이터 전환으로 **구조가 바뀌었는데** 이 경로를 실제로 도는 테스트가 없었다
    (유일한 등장인 main 테스트에서는 몽키패치로 치워진다). 커서가 날짜를 가로질러서
    끝에서 한 번에 낸다는 성질 때문에 KIS 처럼 하루씩 내도록 고치면 값이 틀어지는데,
    그 계약을 산문만 지고 있었다.

    ⚠️ `sorted()` 자체는 단언하지 않는다 — `_trading_days` 가 이미 오름차순을 주고
    `main` 이 그대로 넘기므로 삽입 순서가 곧 정렬이다. 그 자리에 변이를 심어도 **행동이
    같아서** 죽지 않는다(가드 결함이 아니라 잉여다). 없는 차이를 단언하면 항등식이 된다.
    """
    from data_pipeline.sources.candle import build_candle

    days = ["2026-04-20", "2026-04-21"]

    def _fake_fetch(_client, _tk, _newest, _oldest, _budget):
        return [build_candle(_tk, window_end=datetime.fromisoformat(f"{d}T09:01:00+09:00"),
                             span_seconds=60,
                             values={"open": 1, "high": 1, "low": 1, "close": 1},
                             volume=0)
                for d in days]

    monkey = pytest.MonkeyPatch()
    monkey.setattr(backfill, "_fetch_back", _fake_fetch)
    monkey.setattr(backfill, "TossOpenApiClient", lambda **_k: object())
    monkey.setattr(backfill.boto3, "client", lambda *_a, **_k: _TossSecrets())
    try:
        out = list(backfill._collect_toss(days, ["091170"], {d: set() for d in days},
                                          _Args(vendor="toss", call_budget=10)))
    finally:
        monkey.undo()

    assert [d for d, _rows in out] == days
    for _d, rows in out:
        assert {r["source_vendor"] for r in rows} == {"toss_backfill"}


class _TossSecrets:
    def get_secret_value(self, SecretId):            # noqa: N803
        return {"SecretString": '{"client_id": "i", "client_secret": "s"}'}


def test_failure_tally_is_reported_even_when_the_run_aborts(caplog):
    """실패 (종목,날짜) 합계가 **중단된 런에서도** 나온다.

    루프 뒤에 두면 전역 실패가 올라오는 순간 제너레이터가 그 지점에 도달하지 못해,
    가장 알고 싶은 상황에서 요약이 사라진다. 그래서 `finally` 다.

    합계 자체도 여기서 잠근다 — 카운터를 지워도 스위트가 초록이던 자리다. 상장 전
    날짜가 이 경로로 쌓이고(실측: 섹터 48종 중 12종/2025-07-29), 산출이 0이라 다음
    실행이 같은 콜을 다시 태우므로 규모가 안 보이면 아무도 안 고친다.
    """
    from data_pipeline.sources.kis_minute import KisDayIncompleteError, KisSourceError

    days = ["2026-04-20", "2026-04-21"]

    class _Client:
        auth = object()

        def candles(self, symbol, *, window_end):
            # 첫날은 unit 실패로 합계에 쌓이고, 둘째 날에 전역 실패가 런을 끊는다
            if symbol == "A":
                raise KisDayIncompleteError("페이지 예산 소진")
            raise KisSourceError("유량 승격 — 앱키 전역")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(backfill, "KisHistoricalMinuteClient", lambda *a, **k: _Client())
    monkey.setattr(backfill.boto3, "client", lambda *_a, **_k: _Secrets())
    caplog.clear()
    try:
        with caplog.at_level(logging.WARNING), pytest.raises(KisSourceError):
            list(backfill._collect_kis(days, ["A", "B"], {d: set() for d in days},
                                       _Args(vendor="kis")))
    finally:
        monkey.undo()

    tally = [r.getMessage() for r in caplog.records
             if "수집 실패 (종목,날짜)" in r.getMessage()]
    assert tally, "중단된 런에서 실패 합계가 사라졌다"
    # **개수를 묻는다** — 존재만 보면 카운터를 상수로 박아도 통과한다
    assert "1건" in tally[0], tally[0]
