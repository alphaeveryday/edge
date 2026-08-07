"""토스 1분봉 → 5분봉 canonical 과거 백필 (분석엔진 β 표본 복구용).

**왜 필요한가.** `analysis-engine` 의 구간 층분해는 같은 시각창을 과거 60거래일에서
회귀해 β 를 얻는다(`layers.BETA_WINDOW`·`MIN_BETA_N=40`). 5분봉 이력이 40거래일에 못
미치는 종목은 층이 통째로 빠지고 산문이 "시장 미계측 · 섹터 미계측 · 구성종목 0종목"
을 낸다 — 재료 부재이지 분석 실패가 아닌데 화면에는 구분이 안 보인다.

실측 2026-08-07: 1분 레인 유니버스 362종 중 **67종이 40거래일 미만**이다. fmp 백필
(~2026-07-31)은 그 종목들을 안 담았고, 1분 롤업은 2026-08-05 에 시작했다. 그 사이를
메우는 원천이 없다 — 이 스크립트가 그 자리다.

**왜 토스인가.** KIS 분봉 TR(`FHKST03010200`)은 **당일 전용**이라 날짜 파라미터가
없다(`sources/kis_minute.py`). 토스는 `before` 로 과거를 거슬러 준다(실측: 305720 이
2025-09 까지 나온다). 5분봉 API 는 어느 벤더에도 없으므로 1분을 받아 롤업한다 —
롤업 규칙은 `minute/rollup.py` 와 **같은 계약**이다(open=첫 봉 open · high=max ·
low=min · close=마지막 close · volume=합, ts=구간 시작, available_at=ts+5분).

**왜 별도 파일인가.** `part-0.parquet` 은 그날의 정본이다 — 과거 파티션은 fmp 백필이
쓴 것이고 2026-08-04 이후는 롤업 writer 가 쓴다. 거기에 끼워 넣으면 롤업이 다음 버킷에서
지운다. 같은 파티션에 `part-toss-backfill.parquet` 로 따로 쓴다 — 소비자(`duck._s3`)가
`**/*.parquet` 글롭이라 둘 다 읽는다.

🔴 **`rollup.WRITER_SINCE`(2026-08-04) 이후 파티션에는 쓰지 마라** (ALPHA-839 이후).
"어느 쪽도 서로를 안 지운다"는 이제 **틀렸다** — 지우지는 않지만 **상대를 죽인다**.
`_rollup_day` 가 파티션에서 자기 것 아닌 파일을 보면 산출을 **거부**하므로, 그 날짜에
이 스크립트가 파일을 놓는 순간 그날 5분 파생이 **후크·EOD 배치 양쪽에서 영구 정지**한다
(장중에 돌리면 그 시점 이후 봉이 통째로 사라진다). 그 정지는 EOD 배치의
`unfilled_settled_days` 에 뜨지만, 푸는 것은 사람이 소유자를 정하는 일이다.
⚠️ `--days` 기본값이 최근 70파티션이고 타깃 선정은 **종목 이력 깊이** 기준이라 날짜를
안 가린다 — 최근 날짜에 쓰기 쉽다. `--days` 로 창을 좁혀 쓰거나 대상 날짜를 확인해라.
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

from data_pipeline.sources.toss import TossOpenApiClient  # noqa: E402

KST = timezone(timedelta(hours=9))
BUCKET_MINUTES = 5
# 정규장 경계. 봉의 **구간 시작** 기준이라 마지막 봉은 15:25(=15:25~15:30)다.
SESSION_OPEN, SESSION_CLOSE = time(9, 0), time(15, 30)
MARKET = "KR"
PREFIX = "canonical/market_data/intraday_5m/market=KR"
BACKFILL_NAME = "part-toss-backfill.parquet"
SOURCE_VENDOR = "toss_backfill"
TOSS_SECRET = "edge-dev-data-pipeline-toss"
DEFAULT_BUCKET = "edge-dev-pipeline-lake"
# 토스 count 상한(`toss.MAX_COUNT`). 하루 390분이라 종목당 2콜 남짓으로 하루가 닫힌다.
PAGE = 200

log = logging.getLogger("backfill5m")


def _s3():
    return boto3.client("s3")


def _partition_days(s3, bucket: str) -> list[str]:
    """존재하는 파티션 날짜 오름차순. 거래일 달력을 지어내지 않는다 —
    **이미 파티션이 있는 날**이 곧 우리가 아는 거래일이다."""
    out, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": f"{PREFIX}/", "Delimiter": "/"}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        out += [p["Prefix"].rstrip("/").split("trade_date=")[-1]
                for p in r.get("CommonPrefixes", [])]
        token = r.get("NextContinuationToken")
        if not token:
            return sorted(out)


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
    ap.add_argument("--min-days", type=int, default=40,
                    help="이만큼 이력이 있으면 대상에서 뺀다 (layers.MIN_BETA_N)")
    ap.add_argument("--tickers", default="", help="쉼표 구분. 비우면 자동 판정")
    ap.add_argument("--call-budget", type=int, default=140,
                    help="종목당 최대 콜. 70거래일×390분÷200 ≈ 137")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repair-session-hours", action="store_true",
                    help="이미 쓴 백필 파일에서 정규장 밖 봉을 걷어낸다(재수집 없음)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    s3 = _s3()
    days = _partition_days(s3, a.bucket)[-a.days:]
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

    universe = present[days[-1]]
    if a.tickers:
        targets = sorted(set(a.tickers.split(",")) & universe)
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
        rows = [r for r in per_day[d] if r["ticker"] not in existing]
        # **앞선 착지분과 합쳐서 쓴다.** 이 파일은 파티션당 하나이고 `put_object` 는
        # 통째로 덮는다 - 이번 실행분만 쓰면 지난 런이 채운 종목이 소리 없이 사라진다
        # (2026-08-07 실측: 9종만 돌린 재실행이 앞선 85종을 지웠다). 같은 종목은 이번
        # 값으로 갈아끼우고, 남기는 행에도 정규장 필터를 다시 건다 - 필터 없던 판이
        # 쓴 장전·장후 봉이 여기서 함께 걷힌다.
        mine = {r["ticker"] for r in rows}
        prior = _read_day(s3, a.bucket, d, BACKFILL_NAME)
        keep = [r for r in (prior.to_pylist() if prior is not None else [])
                if r["ticker"] not in mine
                and SESSION_OPEN <= r["ts"].time() < SESSION_CLOSE]
        n = _write_day(s3, a.bucket, d, keep + rows, a.dry_run)
        total += len(rows)
        if rows:
            log.info("%s ← %d행 (파일 총 %d행)%s", d, len(rows), n,
                     " (dry-run)" if a.dry_run else "")
    log.info("완료 — %d일 · %d행 추가%s", len(per_day), total,
             " (dry-run)" if a.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
