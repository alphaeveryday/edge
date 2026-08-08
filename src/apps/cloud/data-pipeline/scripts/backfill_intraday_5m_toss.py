"""토스 1분봉 → 5분봉 canonical 과거 백필 (분석엔진 β 표본 복구용).

**왜 필요한가.** `analysis-engine` 의 구간 층분해는 같은 시각창을 과거 60거래일에서
회귀해 β 를 얻는다(`layers.BETA_WINDOW`·`MIN_BETA_N`). 이력이 그 최소 표본에 못 미치는
종목은 층이 통째로 빠지고 산문이 "시장 미계측 · 섹터 미계측 · 구성종목 0종목"을 낸다 —
재료 부재이지 분석 실패가 아닌데 화면에는 구분이 안 보인다. 요건 값을 여기 적지 않는다:
그건 저쪽 판단으로 움직인다(ALPHA-849 에서 40→20).

실측 2026-08-07: 1분 레인 유니버스 362종 중 **67종이 당시 요건(40거래일) 미만**이다. fmp 백필
(~2026-07-31)은 그 종목들을 안 담았고, 1분 롤업은 2026-08-05 에 시작했다. 그 사이를
메우는 원천이 없다 — 이 스크립트가 그 자리다.

**왜 토스인가.** 이 스크립트를 쓸 때 레포에 있던 KIS 분봉 어댑터는 당일 TR
(`FHKST03010200`) 하나였고 그 TR 에는 날짜 파라미터가 없다.
⚠️ **"KIS 는 과거를 못 준다"로 읽지 마라** — 소급 TR `FHKST03010230` 이 따로 있고
ALPHA-846 이 그걸 `KisHistoricalMinuteClient` 로 붙였다. 다만 그 경로의 유니버스는
S3 `config/minute/universe.json`(362종)이라 섹터 후보 48종을 못 담는다 — 그게
소유권 경계를 이 백필 쪽에 둔 이유다(ALPHA-836). 토스는 `before` 로 과거를 거슬러 준다(실측: 305720 이
2025-09 까지 나온다). 5분봉 API 는 어느 벤더에도 없으므로 1분을 받아 롤업한다 —
롤업 규칙은 `minute/rollup.py` 와 **같은 계약**이다(open=첫 봉 open · high=max ·
low=min · close=마지막 close · volume=합, ts=구간 시작, available_at=ts+5분).

**왜 별도 파일인가.** `part-0.parquet` 은 롤업이 통째로 덮는 파일이다 — 거기 끼워 넣으면
다음 재집계에서 지워진다. 같은 파티션에 `part-toss-backfill.parquet` 로 따로 쓴다 —
소비자(`duck._s3`)가 `**/*.parquet` 글롭이라 둘 다 읽는다. 행이 서로소인 것은 쓰기 전에
`part-0` 의 티커를 빼서 보장한다.

**소유권 경계는 `rollup.WRITER_SINCE` 하나다.** 그 앞은 이 백필이, 뒤는 롤업이 파티션을
소유한다. 날짜를 여기 박지 않고 `_trading_days` 가 그 상수를 읽어 달력을 자른다 —
경계는 옮겨진 적이 있고(ALPHA-836) 주석으로 강제하면 옮길 때 하나만 고쳐진다.

🔴 경계 **뒤** 파티션에 이 스크립트가 파일을 놓으면 `_rollup_day` 의 foreign 가드가 걸려
그날 5분 파생이 후크·EOD 양쪽에서 **영구 정지**한다. 그래서 가드가 주석이 아니라 코드다.
# ponytail: 파티션당 1파일 관례를 깬다. 합치려면 롤업 writer 가 이 원천을 알아야 하고
# 그건 이 백필의 수명(일회성)보다 큰 변경이다 — ALPHA-839 는 병합이 아니라 거부를 골랐다.

**겹치면 안 쓴다.** (ticker, day) 가 이미 `part-0` 에 있으면 건너뛴다 — 같은 봉이 두 벌
있으면 `_panel` 류 집계가 조용히 두 번 센다. 스킵은 콜도 아낀다.

실행:
    AWS_PROFILE=edge uv run python scripts/backfill_intraday_5m_toss.py --days 70
    ... --dry-run          # 무엇을 몇 콜 받을지만 계산하고 끝낸다
    ... --tickers 395270,0190G0
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from data_pipeline.minute.rollup import WRITER_SINCE  # noqa: E402
from data_pipeline.sources.toss import TossOpenApiClient  # noqa: E402

KST = timezone(timedelta(hours=9))
BUCKET_MINUTES = 5
# 정규장 경계. 봉의 **구간 시작** 기준이라 마지막 봉은 15:25(=15:25~15:30)다.
SESSION_OPEN, SESSION_CLOSE = time(9, 0), time(15, 30)
MARKET = "KR"
PREFIX = "canonical/market_data/intraday_5m/market=KR"
# 거래일의 **독립 증인**. 5분봉 파티션이 통째로 빠진 날을 이쪽이 안다(ALPHA-836).
PRICE_DAILY_PREFIX = "canonical/market_data/price_daily/market=KR"
BACKFILL_NAME = "part-toss-backfill.parquet"
SOURCE_VENDOR = "toss_backfill"
TOSS_SECRET = "edge-dev-data-pipeline-toss"
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
    return sorted(d for d in days if d < WRITER_SINCE)


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


def _write_day(s3, bucket: str, day: str, rows: list[dict], dry: bool) -> int:
    if not rows:
        return 0
    tbl = pa.Table.from_pylist(rows, schema=SCHEMA)
    if dry:
        return tbl.num_rows
    buf = io.BytesIO()
    pq.write_table(tbl, buf, compression="snappy")
    s3.put_object(Bucket=bucket, Key=f"{PREFIX}/trade_date={day}/{BACKFILL_NAME}",
                  Body=buf.getvalue())
    return tbl.num_rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--days", type=int, default=70,
                    help="거슬러 볼 파티션 수. β 는 60거래일 창이라 여유를 둔다")
    ap.add_argument("--min-days", type=int, default=20,
                    help="이만큼 이력이 있으면 대상에서 뺀다 (analysis-engine 의 "
                         "layers.MIN_BETA_N 과 같은 뜻. 그쪽이 움직이면 여기도 옮겨라 — "
                         "레포가 달라 import 로 못 묶는다)")
    ap.add_argument("--tickers", default="", help="쉼표 구분. 비우면 자동 판정")
    ap.add_argument("--call-budget", type=int, default=140,
                    help="종목당 최대 콜. 70거래일×390분÷200 ≈ 137")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repair-session-hours", action="store_true",
                    help="이미 쓴 백필 파일에서 정규장 밖 봉을 걷어낸다(재수집 없음)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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
            t = _read_day(s3, a.bucket, d, BACKFILL_NAME)
            if t is None:
                continue
            rows = [r for r in t.to_pylist()
                    if SESSION_OPEN <= r["ts"].time() < SESSION_CLOSE]
            if len(rows) == t.num_rows:
                continue
            cut += t.num_rows - len(rows)
            n = _write_day(s3, a.bucket, d, rows, a.dry_run)
            log.info("%s 정규장 밖 %d행 제거 → %d행%s",
                     d, t.num_rows - len(rows), n, " (dry-run)" if a.dry_run else "")
        log.info("복구 완료 — 총 %d행 제거%s", cut, " (dry-run)" if a.dry_run else "")
        return 0

    # 하루씩 읽어 (ticker→보유일수) 를 센다. 파일 하나가 ~370KB 라 70일이면 26MB 다.
    # **백필 파일도 같이 센다.** 정본만 세면 이미 채운 종목·날짜를 매번 다시 받고,
    # 그 재수집이 아래 병합을 거치지 않던 판에서는 앞선 착지분을 덮어 지웠다.
    present: dict[str, set[str]] = {}       # 정본(part-0)에 있는 종목 — 쓰기 스킵 기준
    covered: dict[str, set[str]] = {}       # 정본 ∪ 백필 — 결손일·커버리지 기준
    coverage: dict[str, int] = defaultdict(int)
    for d in days:
        present[d] = _tickers_present(_read_day(s3, a.bucket, d))
        covered[d] = present[d] | _tickers_present(_read_day(s3, a.bucket, d, BACKFILL_NAME))
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

    sec = json.loads(boto3.client("secretsmanager")
                     .get_secret_value(SecretId=TOSS_SECRET)["SecretString"])
    client = TossOpenApiClient(client_id=sec["client_id"], client_secret=sec["client_secret"])

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
                r |= {"ticker": tk, "source_symbol": tk, "source_vendor": SOURCE_VENDOR}
            per_day[d] += rows
            added += len(rows)
        log.info("[%d/%d] %s — 결손 %d일 · 캔들 %d · 5분봉 %d",
                 i, len(targets), tk, len(want), len(candles), added)

    total = 0
    for d in sorted(per_day):
        existing = _tickers_present(_read_day(s3, a.bucket, d))
        prior = _read_day(s3, a.bucket, d, BACKFILL_NAME)
        payload = _day_payload(prior.to_pylist() if prior is not None else [],
                               existing, per_day[d])
        if payload is None:
            continue
        keep_and_new, fresh = payload
        n = _write_day(s3, a.bucket, d, keep_and_new, a.dry_run)
        total += fresh
        log.info("%s ← %d행 (파일 총 %d행)%s", d, fresh, n,
                 " (dry-run)" if a.dry_run else "")
    log.info("완료 — %d일 · %d행 추가%s", len(per_day), total,
             " (dry-run)" if a.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
