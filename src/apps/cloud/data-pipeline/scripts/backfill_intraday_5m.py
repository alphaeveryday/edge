"""1분봉 → 5분봉 canonical 과거 백필. 벤더는 `--vendor`.

🔴 **원래 근거는 사라졌다(2026-08-09 확인).** 이 파일은 *"구간 층분해가 과거 60거래일
회귀로 β 를 얻는데 섹터 계열 이력이 최소 표본에 못 미쳐 층이 조용히 빠진다"* 를 근거로
섰다(`layers.BETA_WINDOW`·`layers.MIN_BETA_N`). **그 두 심볼은 지금 없다** — ALPHA-862
(#617)가 층 계수를 전부 1 로 고정하며 회귀·ρ 게이트를 걷어냈고, ALPHA-877 이 **프록시
섹터 ETF 후보 풀 자체를 폐기**했다(`layers._series` 는 이제 대상·시장 두 심볼만 읽는다).
섹터의 최종형은 KRX 업종지수이고, 그 1분봉은 ALPHA-887 이 수집하지만(dataset
`sector_index_minute`) 5분 롤업 배선도 소급도 없어 구간 모드에서는 여전히 정직 부재다.

⚠️ **그러니 "β 표본 복구"로 이 파일을 정당화하지 마라.** `attribute.MIN_BETA_N` 이 아직
있지만 그건 **갭 β**(직전 미국 세션 대비)의 상수이고 대상 종목 **자신의** 전 이력을 센다.
참조 계열 48종은 `sector_etf_ids` 축이라 `price_consumer` 의 판정 대상이 아니므로
(`config/sources.toml` 의 그 목록 주석) 그 경로를 안 탄다 — **이 48종 5분봉 이력의 살아
있는 소비자는 2026-08-09 현재 확인되지 않았다.**

**그럼 왜 받아 두나 — 보관이 롤링이라서다.** KIS 소급 TR 보관은 **251거래일 롤링**이고
매일 하루씩 밀린다(실측 경계 2025-07-29 · 종목 무관). 안 받으면 오래된 날부터 **영구
소실**이라 소비자가 뒤에 서면 그때는 못 받는다. ALPHA-856 실런이 2025-07-29~2026-04-24
**181거래일 · 48종 · 668,080행**을 받아 둔 근거가 이것이다 — 현재 효용이 아니라
**비가역성**이다(칼만 ALPHA-803 이 시변 β 를 세우며 이력을 요구하면 그때 이미 있다).

**벤더가 둘인 이유.** 처음엔 토스뿐이었다 — 그때 레포의 KIS 어댑터는 당일 TR
(`FHKST03010200`) 하나였고 그 TR 에는 날짜 파라미터가 없다. 지금은 소급 TR
`FHKST03010230` 이 `KisHistoricalMinuteClient` 로 붙어 있고(ALPHA-846), 그쪽이 더
정확하다 — 토스는 fmp·KIS 어느 쪽과도 값이 다르다(종가 일치율 35%). 그래서 새로
받는 구간은 KIS 로 간다(ALPHA-856).

⚠️ **이미 착지한 토스 파일은 건드리지 않는다.** 벤더마다 파일이 갈리므로(`VENDORS`)
서로 덮지 않고, 결손 판정(`_backfilled_tickers`)이 두 벤더를 **합쳐서** 세므로 같은
날짜를 두 벌 받지도 않는다. 남는 것은 벤더가 갈리는 날짜 경계의 **이음매**인데, 그건
데이터 품질 판단이라 코드가 아니라 사람이 정한다.

페이징 축이 벤더마다 다르다 — 토스는 `before` 로 과거를 거슬러 주고(실측: 305720 이
2025-09 까지), 소급 TR 은 **하루에 묶여** 그날을 통째로 캐시한다. `_collect_toss` 는
티커-major, `_collect_kis` 는 날짜-major 인 이유가 그것뿐이다. 5분봉 API 는 어느 벤더에도 없으므로 1분을 받아 롤업한다 —
롤업 규칙은 `minute/rollup.py` 와 **같은 계약**이다(open=첫 봉 open · high=max ·
low=min · close=마지막 close · volume=합, ts=구간 시작, available_at=ts+5분).

**왜 별도 파일인가.** `part-0.parquet` 은 롤업이 통째로 덮는 파일이다 — 거기 끼워 넣으면
다음 재집계에서 지워진다. 같은 파티션에 `part-<vendor>-backfill.parquet` 로 따로 쓴다 —
소비자(`duck._s3`)가 `**/*.parquet` 글롭이라 둘 다 읽는다. 행이 서로소인 것은 쓰기 전에
`part-0` 의 티커를 빼서 보장한다.

**소유권은 `rollup.writer_owns()` 하나가 정한다.** 기본은 `WRITER_SINCE` 경계고(앞은 이
백필, 뒤는 롤업), 경계 앞이라도 롤업이 온전한 계열을 갖게 된 날은 예외로 롤업 소유다
(`WRITER_OWNED_BEFORE_SINCE` — 2026-08-03 이 ALPHA-846 의 KIS 소급 수집으로 그렇게 됐다). 날짜를 여기 박지 않고 `_trading_days` 가 그 상수를 읽어 달력을 자른다 —
경계는 옮겨진 적이 있고(ALPHA-836) 주석으로 강제하면 옮길 때 하나만 고쳐진다.

🔴 경계 **뒤** 파티션에 이 스크립트가 파일을 놓으면 `_rollup_day` 의 foreign 가드가 걸려
그날 5분 파생이 후크·EOD 양쪽에서 **영구 정지**한다. 그래서 가드가 주석이 아니라 코드다.
# ponytail: 파티션당 1파일 관례를 깬다. 합치려면 롤업 writer 가 이 원천을 알아야 하고
# 그건 이 백필의 수명(일회성)보다 큰 변경이다 — ALPHA-839 는 병합이 아니라 거부를 골랐다.

**겹치면 안 쓴다.** (ticker, day) 가 이미 `part-0` 에 있으면 건너뛴다 — 같은 봉이 두 벌
있으면 `_panel` 류 집계가 조용히 두 번 센다. 스킵은 콜도 아낀다.

실행: `--vendor` 에 **기본값을 두지 않는다**. 위가 "KIS 가 더 정확하다"로 끝나는데
기본이 토스면 문서를 그대로 친 사람이 방금 틀렸다고 적은 벤더로 받고, 결손 판정이 두
벤더를 합쳐 세므로(`_backfilled_tickers`) 그 (종목, 날짜)는 이후 KIS 실행에서 탈락한다
— 되돌리는 길이 S3 수동 삭제뿐이다. 고르게 만든다(ALPHA-863 의 `emit_outbox` 와 같은 이유).
    AWS_PROFILE=edge uv run python scripts/backfill_intraday_5m.py --vendor kis --days 70
    ... --dry-run          # **쓰기만** 건너뛴다 — 수집은 그대로 돌아 콜을 다 태운다
    ... --tickers 395270,0190G0
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from data_pipeline.minute.rollup import writer_owns  # noqa: E402
from data_pipeline.sources.http import PoliteClient  # noqa: E402
from data_pipeline.sources.kis_minute import (  # noqa: E402
    KisHistoricalMinuteClient,
    KisUnitError,
)
from data_pipeline.sources.toss import TossOpenApiClient  # noqa: E402

KST = timezone(timedelta(hours=9))
BUCKET_MINUTES = 5
# 정규장 경계. 봉의 **구간 시작** 기준이라 마지막 봉은 15:25(=15:25~15:30)다.
SESSION_OPEN, SESSION_CLOSE = time(9, 0), time(15, 30)
# 정규장 분 수(09:00~15:30). 소급 클라이언트에 window 를 하나씩 물을 때의 횟수다.
SESSION_MINUTES = 390
MARKET = "KR"
PREFIX = "canonical/market_data/intraday_5m/market=KR"
# 거래일의 **독립 증인**. 5분봉 파티션이 통째로 빠진 날을 이쪽이 안다(ALPHA-836).
PRICE_DAILY_PREFIX = "canonical/market_data/price_daily/market=KR"
# 벤더 축(ALPHA-856). 파일명이 곧 소유자 표시다 — 파티션에 나란히 놓이므로 누가 쓴
# 행인지 산출물만 보고 갈릴 수 있어야 한다. `part-0` 은 롤업 소유라 어느 벤더도 안 쓴다.
VENDORS = {
    "toss": {"file": "part-toss-backfill.parquet", "vendor": "toss_backfill"},
    "kis": {"file": "part-kis-backfill.parquet", "vendor": "kis_backfill"},
}
TOSS_SECRET = "edge-dev-data-pipeline-toss"
KIS_SECRET = "edge-dev-data-pipeline/kis/oauth"
# 소급 TR 은 한 콜 120봉이라 하루 4콜이다(실측). 간격이 곧 유량 상한이고 앱키 전역이라
# 다른 KIS 스텝과 나눠 쓴다. **1분 가격 워커와 같은 간격**이다 — 0.08 → 12.5 req/s 이고
# 레포 예산선은 15/s, 벤더 한도(EGW00201)는 20/s 다(`config/models.py:min_interval_sec`).
# 장중이면 1분 레인이 이미 12.5 를 쓰므로 이걸 얹는 순간 예산선을 넘는다 — 돌리지 마라.
# 값을 베낀 것이라 저쪽이 움직이면 여기만 옛 값을 지킨다. 그 드리프트는 코드가 아니라
# 테스트가 잡는다(`test_kis_interval_tracks_the_minute_lane`) — 런타임 import 를 더하면
# `_configured_universe` 의 ImportError 관용이 죽는다.
KIS_MIN_INTERVAL = 0.08
DEFAULT_BUCKET = "edge-dev-pipeline-lake"
# 토스 count 상한(`toss.MAX_COUNT`). 하루 390분이라 종목당 2콜 남짓으로 하루가 닫힌다.
PAGE = 200

log = logging.getLogger("backfill5m")


def _s3():
    return boto3.client("s3")


def _partition_days(s3, bucket: str, prefix: str = PREFIX) -> list[str]:
    """`prefix` 아래 존재하는 파티션 날짜 오름차순."""
    out, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": f"{prefix}/", "Delimiter": "/"}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        out += [p["Prefix"].rstrip("/").split("trade_date=")[-1]
                for p in r.get("CommonPrefixes", [])]
        token = r.get("NextContinuationToken")
        if not token:
            return sorted(out)


def _trading_days(s3, bucket: str) -> list[str]:
    """거래일 달력 = 5분봉 파티션 **∪** 일봉 파티션 (ALPHA-836).

    5분봉 파티션만으로 달력을 삼던 판이 **통째로 빠진 날을 영영 못 봤다**: 2026-08-03·
    08-04 는 `price_daily` 에 있는 거래일인데 `intraday_5m` 파티션이 아예 없어 대상
    목록에 들지도 못했다. 있는 것으로 있어야 할 것을 정의하면 부재가 부재로 안 보인다.

    **교체가 아니라 합집합이다.** `price_daily` 는 파티션이 몇 주치뿐이라(실측 21일)
    교체하면 창이 그만큼 쪼그라들어 전 종목이 대상이 된다.
    """
    days = (set(_partition_days(s3, bucket))
            | set(_partition_days(s3, bucket, PRICE_DAILY_PREFIX)))
    # **소유권 경계를 코드로 건다.** 이전 판은 "파티션이 없으면 대상이 아니다"라는
    # 우연에 기대 롤업 구간을 안 건드렸는데, 달력이 합집합이 되면서 그 우연이 사라졌다 —
    # 하필 파티션이 빠진 날이 곧 롤업 시대의 날이라, 가드가 없으면 백필이 가장 위험한
    # 파티션을 정조준한다. 거기 낯선 파일을 쓰면 `rollup._rollup_day` 의 foreign 가드가
    # 걸려 그날 5분 파생이 후크·EOD 양쪽에서 **영구 정지**한다.
    return sorted(d for d in days if not writer_owns(d))


def _configured_universe() -> set[str]:
    """설정이 **수집하기로 선언한** 1분 유니버스 (ALPHA-836).

    S3 `config/minute/universe.json` 이 1분 레인의 운영 정본이지만 백필의 기준으로는
    쓰지 않는다 — 그건 빌더가 만들어 올려야 갱신되는 파생물이고, 실측 2026-08-07 에
    그 객체는 08-04 자라 ALPHA-842 가 얹은 섹터 후보 48종이 아직 없었다. 아직 안 올라간
    것을 기준으로 삼으면 **선언된 대상이 백필에서 또 빠진다** — 이 티켓이 닫으려는
    바로 그 결함이다. 그래서 선언 자체(`sources.toml`)를 읽는다.

    **방어적 getattr 로 감싸지 않는다.** 섹션·필드가 사라지면 AttributeError 로 터지게
    두는 편이 낫다 — `or ()` 로 접으면 선언된 종목이 **경고 한 줄 없이** 백필에서 빠지고,
    그건 이 티켓이 닫으려는 결함이 다른 문으로 되돌아오는 것이다.

    잡는 것은 **ImportError 뿐**이다(스크립트가 패키지 밖에서 돌 수 있다). 로더는
    검증 실패를 `ConfigError` 로 드러내는 계약이므로(`config/loader.load_settings`)
    그것까지 삼키면 스키마 위반이 WARNING 한 줄 뒤 exit 0 으로 지나간다(Rule 7·12).
    """
    try:
        from data_pipeline.config.loader import load_settings
    except ImportError as e:
        log.warning("설정 모듈을 못 읽었다 — 관측 유니버스만 쓴다: %s", e)
        return set()
    return set(load_settings().minute_universe.sector_etf_ids)


def _read_day(s3, bucket: str, day: str, name: str = "part-0.parquet"):
    try:
        body = s3.get_object(Bucket=bucket, Key=f"{PREFIX}/trade_date={day}/{name}")["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None
    return pq.read_table(io.BytesIO(body))


def _tickers_present(table) -> set[str]:
    return set(table.column("ticker").to_pylist()) if table is not None else set()


def _backfilled_tickers(s3, bucket: str, day: str) -> set[str]:
    """그날 백필 파일들이 이미 가진 종목 — **모든 벤더를 합친다**(ALPHA-856).

    내 벤더 파일만 세면 다른 벤더가 이미 채운 날짜를 다시 받아 나란히 쓴다. 파일이
    갈려 있어 덮이지도 않으므로 같은 (ticker, ts) 가 두 벌로 남고, 소비자는 파티션을
    `*.parquet` 글롭으로 읽으니 집계가 **조용히 두 번 센다**(거래량 이중계상 → β 오염).
    2026-04-23~08-07 을 토스가 이미 갖고 있는 채로 KIS 를 도는 것이 그 자리다.
    """
    found: set[str] = set()
    for spec in VENDORS.values():
        found |= _tickers_present(_read_day(s3, bucket, day, spec["file"]))
    return found


def _roll(candles) -> list[dict]:
    """1분 캔들 → 5분 버킷. `minute/rollup.py` 와 같은 규칙.

    `Candle.window_start` 를 쓴다 — 벤더 timestamp 가 구간의 **끝**이라는 보정은 어댑터가
    이미 했다(`sources/candle.py`). 여기서 다시 빼면 두 벌이 되고 한쪽만 고쳐진다.
    버킷은 그 시작을 5분으로 내림한 값이고 canonical `ts` 도 버킷 시작이다 — 이 축을
    뒤집으면 전 구간이 한 칸 밀린 채 조용히 커밋된다(행 수는 그대로라 게이트가 안 걸린다).

    정렬은 **window 축**이다(`rollup.py` 와 같은 이유): 도착 순서로 접으면 open/close 가
    뒤바뀐다. 토스 응답은 최신→과거 역순이라 그냥 접으면 정확히 그 사고가 난다.
    """
    buckets: dict[datetime, list] = defaultdict(list)
    for c in candles:
        start = c.window_start.astimezone(KST).replace(tzinfo=None)
        # **정규장만 남긴다.** 토스는 개별주에 하루 720분(장전 08:00~·장후 ~19:55)을
        # 주는데 ETF·지수는 390분이다. 그대로 쓰면 같은 표 안에서 종목마다 하루 길이가
        # 달라지고, `interval._gap` 이 `first(open ORDER BY ts)` 를 **시간 필터 없이**
        # 잡으므로 장전 봉이 그날 시가가 된다 - 갭이 통째로 틀린다. canonical 계약도
        # 정규장이다(`lake/storage.py:canonical_intraday_5m_key`: 하루 78봉 · 09:00 행이 첫 행).
        if not (SESSION_OPEN <= start.time() < SESSION_CLOSE):
            continue
        key = start.replace(minute=start.minute - start.minute % BUCKET_MINUTES,
                            second=0, microsecond=0)
        buckets[key].append((start, c))
    rows = []
    for key in sorted(buckets):
        seq = [c for _s, c in sorted(buckets[key], key=lambda x: x[0])]
        rows.append({
            "ts": key,
            "open": float(seq[0].open), "close": float(seq[-1].close),
            "high": float(max(c.high for c in seq)),
            "low": float(min(c.low for c in seq)),
            "volume": int(sum(c.volume for c in seq)),
            "available_at": key + timedelta(minutes=BUCKET_MINUTES),
        })
    return rows


def _fetch_back(client, ticker: str, newest: datetime, oldest_day: str,
                budget: int) -> list:
    """`before` 로 거슬러 올라가며 `oldest_day` 까지 모은다. 콜 예산을 넘기면 멈춘다."""
    got, before, calls = [], newest, 0
    while calls < budget:
        page = client.candles(ticker, interval="1m", count=PAGE, before=before)
        calls += 1
        if not page:
            break
        got += page
        before = min(c.window_end for c in page)
        if before.astimezone(KST).date().isoformat() < oldest_day:
            break
    return got


SCHEMA = pa.schema([
    ("ticker", pa.string()), ("source_symbol", pa.string()),
    ("ts", pa.timestamp("us")), ("open", pa.float64()), ("high", pa.float64()),
    ("low", pa.float64()), ("close", pa.float64()), ("volume", pa.int64()),
    ("source_vendor", pa.string()), ("available_at", pa.timestamp("us")),
])


def _day_payload(prior_rows: list[dict], existing: set[str],
                 candidates: list[dict]) -> tuple[list[dict], int] | None:
    """그날 쓸 행과 **새로 추가되는 수**. 새 행이 없으면 `None` — 쓰지 않는다는 뜻이다.

    **새 행이 0 이면 안 쓴다**(ALPHA-836). 예전엔 남길 행(`keep`)이 비지 않으면 그대로
    PUT 이 나가 같은 바이트로 파일을 덮었고, 그 PUT 이 라이브 소비자의 읽기와 경합했다 —
    실측 2026-08-07: analysis-engine 이 07-02 백필 파일에서 HTTP 416 Range Not
    Satisfiable 로 설명 발행에 실패했다. 재실행마다 재발한다. (정규장 밖 봉 재필터는
    `--repair-session-hours` 가 독립적으로 담당하므로 그 경로는 안 잃는다.)

    **앞선 착지분과 합쳐서 쓴다.** 이 파일은 파티션당 하나이고 `put_object` 는 통째로
    덮는다 — 이번 실행분만 쓰면 지난 런이 채운 종목이 소리 없이 사라진다(실측: 9종만
    돌린 재실행이 앞선 85종을 지웠다). 같은 종목은 이번 값으로 갈아끼우고, 남기는 행에도
    정규장 필터를 다시 건다 — 필터 없던 판이 쓴 장전·장후 봉이 여기서 함께 걷힌다.
    """
    rows = [r for r in candidates if r["ticker"] not in existing]
    if not rows:
        return None
    mine = {r["ticker"] for r in rows}
    # `existing` 도 뺀다 — 지난 런이 백필 파일에 쓴 티커가 나중에 `part-0` 에도 생기면
    # 두 파일에 같은 행이 남아 소비자 글롭이 **두 번 센다**. 서로소 보장은 쓰기 시점의
    # 한 겹뿐이므로 그 겹이 과거분까지 봐야 한다.
    keep = [r for r in prior_rows
            if r["ticker"] not in mine and r["ticker"] not in existing
            and SESSION_OPEN <= r["ts"].time() < SESSION_CLOSE]
    return keep + rows, len(rows)


def _write_day(s3, bucket: str, day: str, rows: list[dict], dry: bool,
               name: str) -> int:
    if not rows:
        return 0
    tbl = pa.Table.from_pylist(rows, schema=SCHEMA)
    if dry:
        return tbl.num_rows
    buf = io.BytesIO()
    pq.write_table(tbl, buf, compression="snappy")
    s3.put_object(Bucket=bucket, Key=f"{PREFIX}/trade_date={day}/{name}",
                  Body=buf.getvalue())
    return tbl.num_rows


def _collect_toss(days, targets, covered, a):
    """티커-major — 토스는 `before` 커서 하나로 날짜를 가로질러 거슬러 올라간다.

    `(날짜, 행)` 을 내는 **제너레이터**다(`_collect_kis` 와 같은 계약). 다만 커서가
    날짜를 가로지르므로 티커를 다 돌기 전에는 어느 날짜도 확정되지 않는다 — 그래서
    끝에서 한 번에 낸다. 즉시성은 KIS 경로에서만 실효가 있다.
    """
    sec = json.loads(boto3.client("secretsmanager")
                     .get_secret_value(SecretId=TOSS_SECRET)["SecretString"])
    client = TossOpenApiClient(client_id=sec["client_id"], client_secret=sec["client_secret"])
    vendor = VENDORS["toss"]["vendor"]

    newest = datetime.fromisoformat(days[-1]).replace(hour=15, minute=31, tzinfo=KST)
    per_day: dict[str, list[dict]] = defaultdict(list)
    for i, tk in enumerate(targets, 1):
        want = [d for d in days if tk not in covered[d]]
        if not want:
            continue
        try:
            candles = _fetch_back(client, tk, newest, want[0], a.call_budget)
        except Exception as e:                                  # noqa: BLE001
            # 한 종목의 실패로 나머지를 버리지 않는다. 조용히 넘기지도 않는다.
            log.error("[%d/%d] %s 수집 실패 — 건너뛴다: %s: %s",
                      i, len(targets), tk, type(e).__name__, str(e)[:120])
            continue
        by_day: dict[str, list] = defaultdict(list)
        for c in candles:
            by_day[c.window_end.astimezone(KST).date().isoformat()].append(c)
        added = 0
        for d in want:
            rows = _roll(by_day.get(d, []))
            for r in rows:
                r |= {"ticker": tk, "source_symbol": tk, "source_vendor": vendor}
            per_day[d] += rows
            added += len(rows)
        log.info("[%d/%d] %s — 결손 %d일 · 캔들 %d · 5분봉 %d",
                 i, len(targets), tk, len(want), len(candles), added)
    yield from sorted(per_day.items())


def _day_candles_kis(client, symbol: str, day: str) -> list:
    """그 하루의 1분 캔들 전부. 첫 호출이 하루를 받아 캐시하고 나머지는 조회다.

    window 를 하나씩 묻는 이유는 **워커와 같은 길을 타기 위해서다** — 범위 가드(시간외
    거부)·무거래 분 합성(`fill_no_trade_minutes`)·결정적 실패 캐시가 전부 그 경로에
    걸려 있다. 하루 캐시를 직접 들여다보면 그 셋을 우회하고, 그러면 백필과 수집 레인이
    같은 벤더 응답에서 다른 봉을 만든다.
    """
    base = datetime.fromisoformat(day).replace(hour=9, minute=0, tzinfo=KST)
    out = []
    for i in range(SESSION_MINUTES):
        out += client.candles(symbol, window_end=base + timedelta(minutes=i + 1))
    return out


def _warn_partial_days(day: str, counts: dict[str, int]) -> list[str]:
    """그날 격자보다 봉이 적게 나온 종목을 드러낸다. 반환은 그 종목들(테스트용).

    🔴 소급 TR 은 **첫 체결 앞 분을 안 준다** — `fill_no_trade_minutes` 도 "첫 관측 앞은
    안 채운다"가 의도다(없는 값을 지어내지 않는다). 토스는 무거래도 flat 봉을 주므로
    하루가 늘 꽉 찼고, 그래서 "그 종목이 그날 파일에 있다 = 그날이 온전하다"가 참이었다.
    벤더 축이 그 전제를 깼다: 한산한 종목은 부분본으로 착지하는데 `coverage` 는 그걸
    온전한 하루로 세므로 ① 재실행이 영영 안 고치고(`covered` 가 막는다) ② `--min-days`
    판정이 부분본 20일을 이력으로 인정한다.

    ⚠️ **격자는 상수 78 이 아니다.** 그날 나온 최대값으로 잡는다 — 개장이 밀리는 날이
    실재하고(실측 2025-11-13: 10:00 개장·67봉. 그날 fmp 시대 `part-0` 도 10:00 시작
    67격자라 두 원천이 일치한다), 78 로 박으면 그런 날 전 종목이 거짓 경고가 된다.

    🔴 **반대로 전 종목이 균일하게 짧은 날은 이 판정이 구조상 못 본다.** 격자를 관측에서
    유도하므로 결손이 균일하면 같이 내려간다 — 폐장이 밀리는 날이 그 자리다(우리는
    15:30 까지만 묻고 어댑터도 그 위를 거부한다). 이 경고의 침묵을 "온전하다"로 읽지
    마라. `counts` 가 **성공한 종목만** 담는 것도 같은 축이다(실패는 위에서 따로 센다).

    떨어뜨리지는 않는다 — 받은 봉은 사실이고, 얼마나 짧아야 버릴지는 β 를 쓰는 쪽
    판단이다(ALPHA-869·이음매와 같은 자리). 여기서는 보이게만 한다(Rule 12).
    실측 2026-08-08: 섹터 48종 중 279540 이 2025-11-13 에 39/67, 2025-09-01 에 70/78.
    """
    if not counts:
        # 전 종목이 실패한 날. `max(())` 를 막는 것이 이 가드의 전부다 — 1종만 남은
        # 날은 그 값이 곧 격자라 `short` 가 정의상 비므로 따로 걸 것이 없다.
        return []
    grid = max(counts.values())
    short = sorted((n, tk) for tk, n in counts.items() if n < grid)
    if short:
        log.warning("%s 부분본 %d종 (격자 %d봉) — 최소 %s",
                    day, len(short), grid,
                    ", ".join(f"{tk}:{n}" for n, tk in short[:3]))
    return [tk for _n, tk in short]


def _collect_kis(days, targets, covered, a):
    """날짜-major — 소급 클라이언트가 **하루에 묶여 있다**(`session_date` 고정).

    토스처럼 티커-major 로 돌면 종목마다 날짜 수만큼 클라이언트를 만들어야 하고, 하루
    캐시가 매번 버려져 콜이 페이지 수만큼 곱해진다. 날짜를 바깥에 두면 클라이언트 하나가
    그날 48종을 다 담고, 그날이 끝나면 통째로 버려 메모리도 하루치로 묶인다.

    ⭐ **하루가 끝날 때마다 `(날짜, 행)` 을 낸다** — 모아서 돌려주면 호출부가 마지막에야
    쓰므로, 중간에 전역 실패(`KisSourceError`)가 올라오는 순간 그때까지 받은 날이 통째로
    사라진다. 180거래일 × 48종은 ~46분짜리 런이라 그 손실이 크고, 티켓의 "재개 가능 —
    중간에 죽으면 받은 날은 남기고 이어서"가 바로 이 자리다. 다음 실행은 `covered` 가
    이미 쓴 날을 걸러 주므로 이어받는다.
    """
    sec = json.loads(boto3.client("secretsmanager")
                     .get_secret_value(SecretId=KIS_SECRET)["SecretString"])
    vendor = VENDORS["kis"]["vendor"]
    # 🔴 **HTTP 클라이언트와 인증은 날짜를 가로질러 공유한다.** 둘 다 클라이언트마다
    # 새로 만들면 하루가 바뀔 때 (1) `KisAuth._token` 메모리 캐시가 버려져 토큰을
    # 재발급하는데 KIS 는 **발급이 분당 1회**라 하루당 ~70초를 순수 대기로 태우고
    # (실측 2026-08-08 dry-run: 매 날짜마다 "분당 1회 제한" 경고), 180일이면 3.5시간이
    # 되어 실제 수집 시간(~46분)보다 길어진다. (2) `PoliteClient` 의 pacing 상태도
    # 리셋돼 날짜 경계마다 간격 없이 버스트가 나간다 — 앱키 전역 한도라 그 버스트가
    # 다른 KIS 스텝의 예산을 먹는다.
    http = PoliteClient(min_interval=KIS_MIN_INTERVAL)
    auth = None
    # 실패한 (종목, 날짜) 수. 상장 전 날짜가 여기 쌓인다 — 실측 2026-08-08: 섹터 48종을
    # 2025-07-29 에 물으면 **12종**이 `KisDayIncompleteError` 로 떨어진다(2026-04-22 는
    # 48/48 성공). 그 종목들은 산출이 0이라 `covered` 에도 안 들어가므로 재실행마다
    # 같은 자리에서 (종목,날짜)당 MAX_DAY_PAGES 콜을 다시 태운다. 합계를 안 내면 그
    # 낭비가 ERROR 로그 사이에 흩어져 안 보인다.
    failed = 0
    try:
        for i, d in enumerate(days, 1):
            want = [tk for tk in targets if tk not in covered[d]]
            if not want:
                continue
            # 클라이언트 자체는 하루마다 새로 만든다 — 봉 캐시가 날짜에 묶여 있어 재사용하면
            # 남의 날 봉이 된다. ⚠️ 클라이언트의 `session_date` 가드는 `ValueError` 라
            # **아래 catch 가 이제 그걸 삼킨다**(전 판은 안 삼켰다) — 그 가드를 안전망으로
            # 세지 마라. 아래 catch 주석에 그 대가를 적어 뒀다.
            client = KisHistoricalMinuteClient(
                sec["app_key"], sec["app_secret"], http,
                session_date=date.fromisoformat(d),
            )
            if auth is None:
                auth = client.auth
            else:
                client.auth = auth
            added = 0
            counts: dict[str, int] = {}
            day_rows: list[dict] = []
            for tk in want:
                try:
                    candles = _day_candles_kis(client, tk, d)
                except (KisUnitError, ValueError) as e:
                    # 종목 하나의 실패로 그날을 버리지 않는다. 조용히 넘기지도 않는다.
                    #
                    # ⚠️ **`ValueError` 도 unit 축이다** — 어댑터가 그렇게 설계했다
                    # (`kis_minute`: *"collector 는 ValueError 만 unit INVALID 로 접어서"*).
                    # 행 형상 위반(비-dict 행·날짜 필드 타입·분 격자 밖·같은 window 에 다른
                    # 봉)은 **맨 ValueError 로 올라온다**. 봉투 수준만 `KisUnitError` 로
                    # 승격된다. 이 둘을 갈라 잡으면 손상 행 하나가 런을 죽이는데, 그
                    # 예외는 응답 형상의 함수라 **결정적**이라서 재실행이 같은 날짜에서
                    # 다시 죽는다 — 그 뒤 날짜를 영영 못 넘는다.
                    #
                    # 🔴 대가: 이 catch 는 위 열거보다 **한 칸 넓다**. 같은 경로의
                    # `session_date` 불일치 가드도 `ValueError` 라 여기 걸리는데 그건
                    # unit 이 아니라 **우리 배선 오류**다 — 걸리면 전 종목이 "건너뛴다"로
                    # 흘러 아래 합계가 "상장 전 날짜"라고 오진한다. 지금은 도달 불가다
                    # (클라이언트를 `d` 로 만들고 같은 `d` 만 묻는다). 그 둘이 갈리는
                    # 코드가 생기면 여기부터 좁혀라. 형제 가드인 범위 밖 window 는
                    # `KisSourceError` 라 지금도 올라간다 — 같은 부류가 다르게 행동한다.
                    #
                    # 🔴 **`KisSourceError` 는 안 잡는다.** 어댑터가 그 둘을 일부러 갈라 놨다
                    # (`kis_minute`: 전역 = 자격증명·권한·유량, unit = 그 종목 하나). 전역을
                    # 여기서 접으면 앱키 하나가 틀렸을 때 48종 × N일이 전부 ERROR 로 흐른 뒤
                    # "완료 — 0일 · 0행 추가" 와 **exit 0** 이 된다 — 스케줄러도 운영자도
                    # 성공한 런과 구분하지 못한다. 고칠 것은 설정 하나인데 재시도 대상처럼
                    # 보이면 아무도 고치러 가지 않는다(Rule 12).
                    #
                    # 상장 전 날짜는 `KisDayIncompleteError`(=unit)라 여기로 온다 — 그건
                    # 정말 그 종목·그날의 사실이므로 건너뛰는 것이 맞다.
                    log.error("[%d/%d] %s %s 수집 실패 — 건너뛴다: %s: %s",
                              i, len(days), d, tk, type(e).__name__, str(e)[:120])
                    failed += 1
                    continue
                rows = _roll(candles)
                for r in rows:
                    r |= {"ticker": tk, "source_symbol": tk, "source_vendor": vendor}
                day_rows += rows
                added += len(rows)
                counts[tk] = len(rows)
            log.info("[%d/%d] %s — 대상 %d종 · 5분봉 %d", i, len(days), d, len(want), added)
            _warn_partial_days(d, counts)
            if day_rows:
                yield d, day_rows
    finally:
        # 🔴 `finally` 다. 전역 실패가 올라오거나 호출부가 제너레이터를 버리면 이
        # 요약이 **가장 알고 싶은 순간에** 안 나온다 — 루프 뒤에 두던 판이 그랬다.
        if failed:
            log.warning("수집 실패 (종목,날짜) %d건 — 상장 전 날짜가 대부분이다. 산출이 "
                        "0이라 다음 실행도 같은 자리에서 콜을 태운다", failed)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--vendor", choices=sorted(VENDORS), required=True,
                    help="어느 벤더로 받고 어느 파일에 쓸지. 벤더마다 파일이 갈리므로 "
                         "(part-<vendor>-backfill.parquet) 서로 덮지 않는다. kis 는 "
                         "소급 TR(FHKST03010230)이라 앱키 전역 유량을 쓴다 — 같은 앱키를 "
                         "쓰는 15:40 시장 런이 끝난 뒤에 돌려라(48종 180거래일 전량이면 "
                         "~46분이라 15:31 에 시작하면 그 창을 관통한다)")
    ap.add_argument("--days", type=int, default=70,
                    help="거슬러 볼 파티션 수. 보관 경계(251거래일)까지 받으려면 그 날짜의 "
                         "달력 위치를 직접 뽑아라 — 파티션 목록이 아니라 `_trading_days` "
                         "기준이다(일봉 합집합·롤업 소유일 제외라 집합이 다르다)")
    ap.add_argument("--min-days", type=int, default=20,
                    help="이만큼 이력이 있으면 대상에서 뺀다. **이 백필 안에서만 뜻이 있는 "
                         "값이다** — 예전엔 analysis-engine 의 layers.MIN_BETA_N 을 따라갔지만 "
                         "그 게이트는 ALPHA-862 로 폐지됐다. ⚠️ 기본값에서는 다른 벤더가 이미 "
                         "채운 계열이 통째로 대상에서 빠진다(실측: 섹터 48종이 토스 72일 "
                         "때문에 대상 0종) — 특정 계열을 받으려면 --tickers 로 명시해라")
    ap.add_argument("--tickers", default="", help="쉼표 구분. 비우면 자동 판정")
    ap.add_argument("--call-budget", type=int, default=140,
                    help="종목당 최대 콜 — **toss 전용**이다(70거래일×390분÷200 ≈ 137). "
                         "kis 는 하루 단위로 받아 (종목, 날짜)당 상한이 소급 클라이언트의 "
                         "MAX_DAY_PAGES 라 이 값을 안 본다")
    ap.add_argument("--dry-run", action="store_true",
                    help="**쓰기만** 건너뛴다. 수집은 그대로 돌아 콜을 다 태운다")
    ap.add_argument("--repair-session-hours", action="store_true",
                    help="이미 쓴 백필 파일에서 정규장 밖 봉을 걷어낸다(재수집 없음)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    backfill_name = VENDORS[a.vendor]["file"]
    s3 = _s3()
    days = _trading_days(s3, a.bucket)[-a.days:]
    if not days:
        log.error("파티션이 없다 — 버킷/프리픽스를 확인해라")
        return 1
    log.info("파티션 %d일 (%s ~ %s)", len(days), days[0], days[-1])

    if a.repair_session_hours:
        # 초기 판이 정규장 필터 없이 써서 개별주에 장전·장후 봉이 실렸다. 재수집 없이
        # 걷어낸다 - 원본 응답이 아니라 파생물이라 같은 규칙으로 다시 유도하면 그만이다.
        cut = 0
        for d in days:
            t = _read_day(s3, a.bucket, d, backfill_name)
            if t is None:
                continue
            rows = [r for r in t.to_pylist()
                    if SESSION_OPEN <= r["ts"].time() < SESSION_CLOSE]
            if len(rows) == t.num_rows:
                continue
            cut += t.num_rows - len(rows)
            n = _write_day(s3, a.bucket, d, rows, a.dry_run, backfill_name)
            log.info("%s 정규장 밖 %d행 제거 → %d행%s",
                     d, t.num_rows - len(rows), n, " (dry-run)" if a.dry_run else "")
        log.info("복구 완료 — 총 %d행 제거%s", cut, " (dry-run)" if a.dry_run else "")
        return 0

    # 하루씩 읽어 (ticker→보유일수) 를 센다. 파티션당 파일이 최대 3개다
    # (`part-0` + 벤더별 백필) — 파일 하나 ~370KB 라 70일이면 GET 210회·~78MB.
    # **백필 파일도 같이 센다.** 정본만 세면 이미 채운 종목·날짜를 매번 다시 받고,
    # 그 재수집이 아래 병합을 거치지 않던 판에서는 앞선 착지분을 덮어 지웠다.
    present: dict[str, set[str]] = {}       # 정본(part-0)에 있는 종목 — 쓰기 스킵 기준
    covered: dict[str, set[str]] = {}       # 정본 ∪ 백필 — 결손일·커버리지 기준
    coverage: dict[str, int] = defaultdict(int)
    for d in days:
        present[d] = _tickers_present(_read_day(s3, a.bucket, d))
        covered[d] = present[d] | _backfilled_tickers(s3, a.bucket, d)
        for tk in covered[d]:
            coverage[tk] += 1

    # 유니버스는 **수집하기로 선언한 것**이지 이미 받아 둔 것이 아니다(ALPHA-836).
    # `present[days[-1]]` 만 쓰던 판은 한 번도 수집된 적 없는 종목을 영영 못 봤다 —
    # ALPHA-842 가 얹은 섹터 후보 48종이 정확히 그 자리였다(실측 2026-08-07: 섹터
    # 계열 80종 중 정본 보유가 FMP 시대 7종·롤업 시대 32종). 날짜 축과 같은 결함이다.
    #
    # 관측 쪽은 **실제로 정본이 있는 마지막 날**을 쓴다. 달력이 일봉과의 합집합이 된 뒤로
    # `days[-1]` 이 5분봉 파티션 없는 날일 수 있고(일봉이 더 최신인 창), 그러면 관측
    # 유니버스가 통째로 비어 상장 폐지분까지 되살아난 목록만 남는다.
    latest = next((d for d in reversed(days) if present[d]), None)
    if latest is None:
        # 관측 축이 통째로 비었다 — 좁은 창이 전부 일봉-only 인 경우다. 설정 선언분만
        # 남으므로 대상이 조용히 쪼그라든다. 날짜 축엔 가드가 있는데(위 `if not days`)
        # 여기만 무음이면 정상 실행과 구분이 안 된다.
        log.warning("창 %d일에 5분봉 정본이 하나도 없다 — 설정 선언분만 대상이 된다", len(days))
    universe = (present[latest] if latest else set()) | _configured_universe()
    if a.tickers:
        # **명시 요청은 명시 대상이다.** 교집합을 걸면 아직 안 받아 본 종목을 지정해도
        # 조용히 0건으로 끝난다 — 백필에서 그건 결손을 성공으로 위장하는 길이다.
        # 교집합이 하던 공백·빈 값 스크러빙은 여기서 명시적으로 한다.
        targets = sorted({t.strip() for t in a.tickers.split(",") if t.strip()})
    else:
        targets = sorted(tk for tk in universe if coverage[tk] < a.min_days)
    log.info("유니버스 %d종 · 백필 대상 %d종 (기준 %d일 미만)",
             len(universe), len(targets), a.min_days)
    if not targets:
        log.info("채울 것이 없다")
        return 0

    # **하루가 나오는 대로 쓴다.** 수집기가 제너레이터라 여기서 소비하는 동안 쓰기가
    # 섞여 들어간다 — 중간에 전역 실패가 올라와도 이미 착지한 날은 남고, 다음 실행이
    # `covered` 로 그날들을 걸러 이어받는다. 모아서 쓰던 판은 ~46분짜리 런이 마지막에
    # 죽으면 전부 잃었다.
    total = days_written = 0
    for d, rows in (_collect_kis if a.vendor == "kis" else _collect_toss)(
            days, targets, covered, a):
        existing = _tickers_present(_read_day(s3, a.bucket, d))
        prior = _read_day(s3, a.bucket, d, backfill_name)
        payload = _day_payload(prior.to_pylist() if prior is not None else [],
                               existing, rows)
        if payload is None:
            continue
        keep_and_new, fresh = payload
        n = _write_day(s3, a.bucket, d, keep_and_new, a.dry_run, backfill_name)
        total += fresh
        days_written += 1
        log.info("%s ← %d행 (파일 총 %d행)%s", d, fresh, n,
                 " (dry-run)" if a.dry_run else "")
    log.info("완료 — %d일 · %d행 추가%s", days_written, total,
             " (dry-run)" if a.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
