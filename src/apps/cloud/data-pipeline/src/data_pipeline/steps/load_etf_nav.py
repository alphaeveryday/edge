"""ETF NAV 적재 — canonical ETF NAV → etf_nav_daily (ALPHA-383).

`canonical/market_data/etf_nav/market=…/trade_date=…` 을 읽어 ETF·거래일 grain 의 공식 NAV
관측값을 Cloud Event Store 에 적재한다. 수집 ALPHA-380 → 정제 ALPHA-382 → 이 스텝이 체인의 끝이다.

**멱등**: PK `(etf_instrument_id, trade_date)` 가 곧 멱등의 근거다 — 같은 값 재적재는
`ON CONFLICT … DO UPDATE … WHERE nav IS DISTINCT FROM EXCLUDED.nav` 의 WHERE 가 걸러내
아무 행도 반환하지 않고 already 로 세어진다. 사전 스냅샷 조회 방식과 달리 동시 실행에도
원자적이다(load_documents 와 같은 모델).

**정정은 반영한다** — canonical 은 같은 (etf, trade_date) 를 최신 `fetched_at` 으로 수렴시키므로
(normalize 의 `_merge_partition`), 마트가 DO NOTHING 이면 벤더 정정 후에도 첫 값이 영구히 남아
**두 계층이 영구 불일치**한다(edge-review 지적). 그래서 값이 실제로 달라졌을 때만 UPDATE 한다 —
`WHERE nav IS DISTINCT FROM EXCLUDED.nav` 라 같은 값 재적재는 rowcount 0 으로 already 에 세어져
멱등 집계가 그대로 의미를 갖는다. 이 테이블은 PK 가 (etf, trade_date) 라 이력을 담을 축이 없다 —
정정 이력이 필요해지면 그건 SCD 설계라 별건이고, 그때까지는 마트가 canonical 을 따라간다.

**etf_instrument_id 해소**: canonical 의 `(market, etf_id)` → `instrument` 조회다. 시장별 MIC
매핑을 거쳐 `(market_code, ticker, instrument_type='ETF')` 로 찾는다. 마스터에 없는 ETF 는
**적재하지 않고 센다** — FK 위반으로 배치를 죽이는 대신 "마스터가 아직 이 ETF 를 모른다"는
사실을 수치로 드러낸다(Rule 12). 실제로 지금 시드에는 ETF instrument 가 091160 하나뿐이라
대부분이 여기 걸린다 — 그 수가 곧 ETF 마스터 생성(ALPHA-379 계열)이 필요하다는 근거다.

**etf_profile 선행 생성**: `etf_nav_daily.etf_instrument_id` 는 `etf_profile(instrument_id)` 를
참조하는데, `etf_profile` 행을 만드는 코드가 저장소에 없었다(ALPHA-378 이 `etf_type` NOT NULL 을
푼 이유). 그래서 **이미 instrument 가 있는 ETF 에 한해** `db.ensure_etf_profile` 로 프로필 행을
보장한다. 새 instrument 를 만들지는 않는다 — 마스터 생성은 이름 출처 문제가 얽혀 있어
(canonical holdings 에 ETF 표시명이 없다) ALPHA-379 소관이다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect, ensure_etf_profile
from ..lake import (
    Storage,
    canonical_etf_nav_partition,
    canonical_run_manifest_key,
    canonical_run_partition_key,
    quality_log_key,
)

logger = logging.getLogger(__name__)

JOB_NAME = "load_etf_nav"
DATASET = "etf_nav_load"
_PARTIAL_EXIT_CODE = 2

# 적재 대상 시장 → MIC(ISO 10383). NAV 수집이 KR 전용이라 지금은 하나다 —
# US 는 instrument 마스터가 없어(ALPHA-371) 여기 넣어도 전량 미등록으로 걸린다.
_MIC_BY_MARKET = {"KR": "XKRX"}

_CREATED_SAMPLE_LIMIT = 5


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """canonical NAV 파티션의 trade_date 목록(정렬)."""
    marker = canonical_etf_nav_partition(market, "")  # ".../trade_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    return sorted(dates)


def _manifest_partitions(
    storage: Storage, input_run_id: str,
) -> list[tuple[str, str, str, str, frozenset[str]]]:
    """NormalizeEtfNav가 승인한 직접 key와 현재 실행 winner 범위."""
    manifest = json.loads(storage.get_bytes(
        canonical_run_manifest_key("etf_nav", input_run_id)
    ).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("run_id") != input_run_id:
        raise ValueError(f"요청한 run_id의 manifest가 아니다: run_id={input_run_id}")
    if (manifest.get("producer") != "normalize_etf_nav"
            or manifest.get("canonical_written") is not True):
        raise ValueError(f"완료된 normalize-etf-nav manifest가 아니다: run_id={input_run_id}")
    raw = manifest.get("canonical_partitions")
    if not isinstance(raw, list):
        raise ValueError(f"canonical_partitions가 없는 manifest다: run_id={input_run_id}")

    partitions: list[tuple[str, str, str, str, frozenset[str]]] = []
    seen: set[tuple[str, str]] = set()
    previous: tuple[str, str] | None = None
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"canonical_partitions 항목이 객체가 아니다: {item!r}")
        market, trade_date = item.get("market"), item.get("trade_date")
        try:
            valid_date = (
                isinstance(trade_date, str)
                and datetime.strptime(trade_date, "%Y-%m-%d").strftime("%Y-%m-%d") == trade_date
            )
        except ValueError:
            valid_date = False
        partition = (market, trade_date)
        if market not in _MIC_BY_MARKET or not valid_date:
            raise ValueError(f"canonical_partitions 항목이 유효하지 않다: {item!r}")
        if partition in seen or (previous is not None and partition <= previous):
            raise ValueError(f"canonical_partitions가 정렬·고유 목록이 아니다: {item!r}")
        seen.add(partition)
        previous = partition

        key = item.get("key")
        expected_key = canonical_run_partition_key("etf_nav", input_run_id, trade_date)
        if key != expected_key:
            raise ValueError(f"canonical 직접 키가 파티션과 일치하지 않는다: {item!r}")
        digest = item.get("sha256")
        if not (isinstance(digest, str) and len(digest) == 64
                and all(char in "0123456789abcdef" for char in digest)):
            raise ValueError(f"canonical sha256이 유효하지 않다: {item!r}")
        raw_winners = item.get("winner_ids")
        if not isinstance(raw_winners, list) or not raw_winners:
            raise ValueError(f"winner_ids가 없는 manifest 파티션이다: {item!r}")
        winner_ids: list[str] = []
        for winner in raw_winners:
            if not isinstance(winner, dict) or set(winner) != {"etf_id"}:
                raise ValueError(f"winner_ids 항목이 유효하지 않다: {item!r}")
            etf_id = winner.get("etf_id")
            if not (isinstance(etf_id, str) and etf_id.strip() == etf_id and etf_id):
                raise ValueError(f"winner_ids 항목이 유효하지 않다: {item!r}")
            winner_ids.append(etf_id)
        if winner_ids != sorted(set(winner_ids)):
            raise ValueError(f"winner_ids가 정렬·고유 목록이 아니다: {item!r}")
        partitions.append((market, trade_date, key, digest, frozenset(winner_ids)))
    return partitions


def _input_rows(
    storage: Storage, *, input_run_id: str | None,
    from_date: str | None, to_date: str | None,
) -> tuple[int, int, Iterator[tuple[str, str, dict, bool]]]:
    """manifest 범위 또는 명시 복구 범위의 물리 행과 논리 선택 여부를 낸다."""
    if input_run_id is not None:
        partitions = _manifest_partitions(storage, input_run_id)

        def manifest_rows() -> Iterator[tuple[str, str, dict, bool]]:
            """직접 parquet의 물리 행을 읽고 manifest winner 정합성을 검증한다."""
            for market, trade_date, key, expected_sha, winner_ids in partitions:
                found: set[str] = set()
                data = storage.get_bytes(key)
                if hashlib.sha256(data).hexdigest() != expected_sha:
                    raise ValueError(f"canonical 바이트가 manifest 이후 바뀌었다: key={key}")
                for row in _read_parquet_rows(data):
                    etf_id = row.get("etf_id")
                    selected = etf_id in winner_ids
                    if selected:
                        if etf_id in found:
                            raise ValueError(f"manifest winner가 canonical에 중복됐다: {etf_id!r}")
                        if row.get("market") != market or row.get("trade_date") != trade_date:
                            raise ValueError(f"manifest winner의 canonical 파티션 정체성이 다르다: {etf_id!r}")
                        found.add(etf_id)
                    yield market, trade_date, row, selected
                missing = winner_ids - found
                if missing:
                    raise ValueError(f"manifest winner가 canonical에 없다: {sorted(missing)!r}")

        return len(partitions), sum(len(part[4]) for part in partitions), manifest_rows()

    def recovery_rows() -> Iterator[tuple[str, str, dict, bool]]:
        """명시된 기존 복구 범위의 canonical parquet 행을 낸다."""
        for market in _MIC_BY_MARKET:
            dates = [
                date for date in _partition_dates(storage, market)
                if (from_date is None or date >= from_date)
                and (to_date is None or date <= to_date)
            ]
            for trade_date in dates:
                prefix = canonical_etf_nav_partition(market, trade_date)
                for key in storage.list_keys(prefix + "/"):
                    if key.endswith(".parquet"):
                        for row in _read_parquet_rows(storage.get_bytes(key)):
                            yield market, trade_date, row, True

    return 0, 0, recovery_rows()


def _etf_instrument_ids(conn, mic: str) -> dict[str, str]:
    """(그 시장의) ticker → instrument_id. ETF 만.

    `(market_code, ticker)` 가 식별 자연키라 시장을 고정하면 ticker 로 유일하다 —
    load_price_triggers 가 ticker 만으로 조회해 복수 후보를 걱정해야 했던 것과 달리(ALPHA-448)
    여기선 canonical 이 market 을 함께 주므로 그 모호성이 없다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, instrument_id FROM instrument"
            " WHERE market_code = %s AND instrument_type = 'ETF'",
            (mic,),
        )
        return {str(t): str(i) for t, i in cur.fetchall()}


def _load_violation(fact: dict) -> str | None:
    nav = fact.get("nav")
    if isinstance(nav, bool) or not isinstance(nav, (int, float)):
        return "invalid_nav"
    if not math.isfinite(nav) or nav <= 0:
        return "invalid_nav"
    return None


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    input_run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical NAV → etf_nav_daily. 성공 0, 행 부분 실패 2, 치명 장애 1."""
    started_at = datetime.now(timezone.utc)
    physical_read = read = 0
    manifest_partitions = manifest_winners = 0
    skipped_missing_identity = skipped_unknown_etf = skipped_load_violation = 0
    skipped_stale_manifest = 0
    already = created = updated = profiles_created = 0
    created_sample: list[dict] = []
    unknown_etfs: set[str] = set()
    load_violations: list[dict] = []
    failures: list[dict] = []
    exit_code = 0
    confirmed_data_version = input_run_id or run_id

    try:
        if input_run_id is not None and (from_date is not None or to_date is not None):
            raise ValueError("input_run_id와 from/to는 함께 쓸 수 없다")
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date와 to_date는 함께 써야 한다")
        # (market, etf_id, trade_date) → 적재 후보. 같은 키가 여러 parquet(과거 잔존 part 등)에
        # 걸리면 **최신 fetched_at 이 이긴다** — canonical 병합(_merge_partition)과 같은 규칙이다.
        # 파일 순서로 마지막 값을 집으면 오래된 NAV 가 마트에 고착될 수 있다(edge-review 지적).
        candidates: dict[tuple[str, str, str], dict] = {}
        manifest_partitions, manifest_winners, input_rows = _input_rows(
            storage, input_run_id=input_run_id, from_date=from_date, to_date=to_date,
        )
        for market, partition_date, row, selected in input_rows:
            physical_read += 1
            if not selected:
                continue
            read += 1
            etf_id, trade_date = row.get("etf_id"), row.get("trade_date")
            if not (isinstance(etf_id, str) and etf_id.strip() == etf_id and etf_id
                    and isinstance(trade_date, str) and trade_date.strip()):
                skipped_missing_identity += 1
                continue
            if row.get("market") != market or trade_date != partition_date:
                skipped_missing_identity += 1
                continue
            fetched_at = row.get("fetched_at")
            cand_key = (market, etf_id, trade_date)
            prev = candidates.get(cand_key)
            if prev is not None and (fetched_at or "") < prev["fetched_at_raw"]:
                continue
            candidates[cand_key] = {
                "nav": row.get("nav"),
                "available_at": fetched_at or started_at.isoformat(),
                "fetched_at_raw": fetched_at or "",
            }

        with connect(db) as conn:
            instruments = {
                market: _etf_instrument_ids(conn, mic) for market, mic in _MIC_BY_MARKET.items()
            }
            profiled: set[str] = set()
            for (market, etf_id, trade_date), fact in sorted(candidates.items()):
                instrument_id = instruments[market].get(etf_id)
                if instrument_id is None:
                    # 마스터 미등록 — FK 위반으로 배치를 죽이는 대신 사실을 수치로 남긴다.
                    skipped_unknown_etf += 1
                    unknown_etfs.add(f"{market}:{etf_id}")
                    continue
                violation = _load_violation(fact)
                if violation is not None:
                    skipped_load_violation += 1
                    load_violations.append({
                        "market": market, "etf_id": etf_id,
                        "trade_date": trade_date, "reason": violation,
                    })
                    continue
                with conn.cursor() as cur:
                    cur.execute("SAVEPOINT etf_nav_row")
                    profile_created = False
                    try:
                        if instrument_id not in profiled:
                            profile_created = ensure_etf_profile(conn, instrument_id)
                        cur.execute(
                            "INSERT INTO etf_nav_daily (etf_instrument_id, trade_date, nav,"
                            " available_at, data_version) VALUES (%s, %s, %s, %s, %s)"
                            " ON CONFLICT (etf_instrument_id, trade_date) DO UPDATE"
                            " SET nav = EXCLUDED.nav, available_at = EXCLUDED.available_at,"
                            "     data_version = EXCLUDED.data_version"
                            " WHERE etf_nav_daily.nav IS DISTINCT FROM EXCLUDED.nav"
                            "   AND etf_nav_daily.available_at < EXCLUDED.available_at"
                            " RETURNING (xmax <> 0) AS was_update",
                            (instrument_id, trade_date, fact["nav"], fact["available_at"],
                             confirmed_data_version),
                        )
                        row = cur.fetchone()
                        stale_manifest = False
                        if row is None:
                            cur.execute(
                                "UPDATE etf_nav_daily SET available_at = %s, data_version = %s"
                                " WHERE etf_instrument_id = %s AND trade_date = %s"
                                "   AND nav IS NOT DISTINCT FROM %s AND available_at <= %s"
                                " RETURNING TRUE",
                                (fact["available_at"], confirmed_data_version,
                                 instrument_id, trade_date, fact["nav"], fact["available_at"]),
                            )
                            stale_manifest = cur.fetchone() is None
                    except Exception as exc:
                        cur.execute("ROLLBACK TO SAVEPOINT etf_nav_row")
                        cur.execute("RELEASE SAVEPOINT etf_nav_row")
                        failures.append({
                            "market": market, "etf_id": etf_id, "trade_date": trade_date,
                            "reasons": ["row_load_error"], "error": str(exc),
                        })
                        exit_code = _PARTIAL_EXIT_CODE
                        continue
                    cur.execute("RELEASE SAVEPOINT etf_nav_row")
                    if instrument_id not in profiled:
                        profiled.add(instrument_id)
                        if profile_created:
                            profiles_created += 1
                    if row is None:
                        if stale_manifest:
                            # 더 최신 관측이 이미 적재됐다. 오래된 immutable manifest 재시도가
                            # 값·available_at·data_version을 되돌리지 못하게 단조성을 지킨다.
                            skipped_stale_manifest += 1
                            continue
                        # 값이 같아 UPDATE 조건이 걸러낸 경우 — 재실행의 정상 경로다.
                        already += 1
                        continue
                    if row[0]:
                        # xmax<>0 = 기존 행을 갱신했다(벤더 정정이 마트까지 흘렀다).
                        updated += 1
                        continue
                created += 1
                if len(created_sample) < _CREATED_SAMPLE_LIMIT:
                    created_sample.append({
                        "etf_instrument_id": instrument_id, "ticker": etf_id,
                        "trade_date": trade_date, "nav": fact["nav"],
                    })
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("NAV 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        already, created, updated, created_sample, profiles_created = 0, 0, 0, [], 0
        skipped_stale_manifest = 0
        exit_code = 1

    if exit_code == 0 and (
        skipped_missing_identity + skipped_unknown_etf + skipped_load_violation
    ):
        exit_code = _PARTIAL_EXIT_CODE

    finished_at = datetime.now(timezone.utc)
    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "markets": list(_MIC_BY_MARKET), "input_run_id": input_run_id,
        "from_date": from_date, "to_date": to_date,
        "manifest_partitions": manifest_partitions, "manifest_winners": manifest_winners,
        "physical_rows_read": physical_read, "logical_rows_read": read, "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_unknown_etf": skipped_unknown_etf,
        # 마스터가 모르는 ETF 목록 — ETF 마스터 생성(ALPHA-379)이 얼마나 필요한지의 근거다.
        "unknown_etfs": sorted(unknown_etfs),
        "skipped_load_violation": skipped_load_violation,
        "skipped_stale_manifest": skipped_stale_manifest,
        "load_violations": load_violations,
        "etf_profiles_created": profiles_created,
        "already_present": already, "created": created, "updated": updated,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). 미등록 ETF·정체성 결측은 그 행이 DB 에 안 들어간
        # 것이라 유실이다(마스터 갭이 원인이어도 유실은 유실).
        "ops": {
            "records_out": already + created + updated,
            "failed_records": (len(failures) + skipped_missing_identity
                               + skipped_unknown_etf + skipped_load_violation),
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_etf_nav 완료: read=%d created=%d updated=%d already=%d profiles_created=%d "
        "unknown_etf=%d(%d종) load_violation=%d skipped_identity=%d",
        read, created, updated, already, profiles_created,
        skipped_unknown_etf, len(unknown_etfs), skipped_load_violation,
        skipped_missing_identity,
    )
    return exit_code
