"""종목 마스터 적재 Step4 — canonical → entity/actor/instrument (ALPHA-372·830).

**Cloud Event Store 48테이블에 쓰는 첫 스텝이다.** `etf_holding_snapshot`·`price_daily` 가 둘 다
`instrument` FK 를 요구하므로 여기가 그 선행이다.

**주식 마스터의 입력은 둘이다**(ALPHA-830):
  1. `canonical/holdings/etf_holdings/market=KR` — ETF 구성종목
  2. `canonical/reference/instrument_profile/market=KR` — KRX 상장 **전종목**(ALPHA-829)

②가 없던 동안 마스터는 **ETF 에 담긴 종목만** 가졌다(08-06 런 329종 = 상장 전종목의 12%).
뉴스는 ETF 에 안 담긴 회사도 똑같이 다루는데, 마스터에 없으면 assertion argument 가 붙을
대상 행이 없어 구조적으로 미해소였다 — 그 구멍을 ②가 메운다.

둘 다 같은 모양으로 만든다:
  회사: entity(ACTOR) + actor(COMPANY/KR) + company_profile
  주식: entity(INSTRUMENT) + instrument(MIC/EQUITY/KRW) + equity_profile → issuer
겹치는 종목은 **①이 이긴다**(증분 0). 2026-08-06 실측상 겹치는 869종의 이름이 전건 동일하다.

**왜 마이그레이션이 아니라 로더인가**: ALPHA-362 가 KR 9종을 Flyway INSERT 로 박은 건 부트스트랩
이었다. 유니버스는 자란다 — KODEX 반도체만 35종이고 KODEX200 은 201종이라, 종목이 늘 때마다
스키마 PR + CD 를 도는 건 성립하지 않는다. 마스터는 canonical 에서 파생되는 데이터지 스키마가
아니다.

**멱등**: 자연키 `(market_code, ticker)` 로 찾아보고 없을 때만 새 ULID 를 발번한다(ADR-0027 이
정한 표준 절차). 이미 있으면 건드리지 않는다 — 재실행이 ID 를 바꾸면 그 ID 를 참조하는 FK 가
전부 끊긴다. ALPHA-362 가 시딩한 9종도 이 경로로 자연히 걸린다.

**주식만 마스터에 세운다**: canonical `constituent_asset_type=EQUITY`만 입력으로 받고,
현금·옵션은 지원 제외로 계측한다. 미지 유형과 주식의 MIC 결측은 실제 유실로 남긴다(ALPHA-1017).

`dart_corp_code` 는 채우지 않는다 — canonical 에 없고, 로더가 DART API 를 부르면 관심사가 섞인다.
NULL 로 두면 별도 `enrich-corp-code` 스텝이 corpCode.xml 매칭으로 채운다(ALPHA-491). nullable 이라
그 사이에도 무해하다(ALPHA-362 가 시딩한 9종은 이미 실값).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect, domain_id, ensure_etf_profile
from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_etf_profile_partition,
    canonical_instrument_profile_partition,
    latest_good_pointer_key,
    quality_log_key,
)
from ..lake.latest_good import LatestGoodError, PRODUCERS, parse_pointer

logger = logging.getLogger(__name__)

JOB_NAME = "load_instruments"
DATASET = "instrument_master"

# 적재 대상 시장(레이크 파티션의 지역 키). US 는 FMP 가 거래소를 안 줘 MIC 가 없어 시딩 불가다
# (ALPHA-371) — MVP 는 국내 ETF 라(ADR-0024) KR 만 대상으로 둔다.
LOADED_MARKETS = ("KR",)

_COUNTRY_BY_MARKET = {"KR": "KR"}

# 시장 → MIC. 구성종목은 canonical 행이 mic 을 갖고 오지만(KRX MKT_ID 흡수), ETF 자신은
# 프로필에 시장 코드가 없어(KIS `mket_id_cd` 는 null 실측) 여기서 정한다.
_MIC_BY_MARKET = {"KR": "XKRX"}

# 시장(지역) → 그 시장의 **전 MIC**. 정체성 조회(`_existing_tickers`)는 이 집합으로 한다 —
# 입력에 나온 MIC 만 물으면 **다른 시장에 서 있는 같은 티커를 못 본다**. 티커 단위로 보는
# 조회에선 그게 곧 "이미 있는데 없다고 판정"이라 종목이 두 번 선다(ALPHA-830).
# 형제 로더들이 같은 이유로 이미 이 상수를 둔다(load_etf_holdings·load_price_daily·load_etf_flow).
_MICS_BY_MARKET = {"KR": ("XKRX", "XKOS", "XKON")}

# 로그에 남길 생성 행 표본 상한. 전종목 확대 첫 런은 ~2,500종을 만드는데(ALPHA-830) 전량을
# 실으면 품질 로그 하나가 수백 KB 가 된다. 표본은 "무엇이 만들어졌나"를 눈으로 확인하는
# 용도고 전수는 DB 가 갖고 있다 — 개수(`created`)는 상한과 무관하게 정확하다.
_CREATED_SAMPLE_LIMIT = 200

_LATEST_GOOD_DATASETS = ("etf_holdings", "etf_profile", "instrument_profile")


def _fetched_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _read_latest_good_inputs(storage: Storage, input_io: dict[str, int | None]) -> dict[str, dict]:
    """세 KR pointer와 artifact를 DB 연결 전에 검증해 메모리에 고정한다.

    pointer 세 개를 먼저 읽는 이유는 첫 artifact를 읽은 뒤 셋째 pointer 결손을 발견하는
    순서조차 정상 계약과 다르기 때문이다. artifact는 SHA를 확인한 뒤에만 Parquet parser에
    넘긴다 — 손상 바이트가 parser 오류로 위장하면 pointer 무결성 위반이 가려진다.
    """
    inputs: dict[str, dict] = {}
    for dataset in _LATEST_GOOD_DATASETS:
        pointer_key = latest_good_pointer_key(dataset, "KR")
        input_io["pointer_gets"] += 1
        pointer_bytes, pointer_version = storage.get_bytes_with_version(pointer_key)
        if pointer_bytes is None:
            raise LatestGoodError(f"latest-good pointer가 없다: {pointer_key}")
        pointer = parse_pointer(
            pointer_bytes, expected_dataset=dataset,
            expected_producer=PRODUCERS[dataset], expected_market="KR",
        )
        object_keys = [obj["key"] for obj in pointer["objects"]]
        if object_keys != sorted(object_keys) or len(object_keys) != len(set(object_keys)):
            raise LatestGoodError(f"latest-good objects가 정렬·유일하지 않다: {dataset}")
        if any(obj["rows"] == 0 for obj in pointer["objects"]):
            # producer는 빈 정상 런에서 alias를 보존한다. 따라서 0행 pointer는 정상 상태가
            # 아니라 pointer와 artifact가 함께 변조된 경우까지 포함한 계약 위반이다.
            raise LatestGoodError(f"latest-good artifact가 비었다: {dataset}")
        inputs[dataset] = {
            "pointer": pointer,
            "pointer_key": pointer_key,
            "pointer_version": pointer_version,
            "pointer_bytes": pointer_bytes,
        }

    for dataset in _LATEST_GOOD_DATASETS:
        item = inputs[dataset]
        pointer = item["pointer"]
        rows: list[dict] = []
        object_metrics: list[dict] = []
        for obj in pointer["objects"]:
            try:
                input_io["artifact_gets"] += 1
                artifact_bytes = storage.get_bytes(obj["key"])
            except Exception as exc:
                raise LatestGoodError(
                    f"latest-good artifact를 읽을 수 없다: {obj['key']}"
                ) from exc
            actual_sha = hashlib.sha256(artifact_bytes).hexdigest()
            if actual_sha != obj["sha256"]:
                raise LatestGoodError(f"latest-good artifact SHA가 다르다: {obj['key']}")
            try:
                object_rows = _read_parquet_rows(artifact_bytes)
            except Exception as exc:
                raise LatestGoodError(
                    f"latest-good artifact Parquet가 손상됐다: {obj['key']}"
                ) from exc
            if len(object_rows) != obj["rows"]:
                raise LatestGoodError(
                    f"latest-good artifact 행 수가 다르다: {obj['key']} "
                    f"pointer={obj['rows']} actual={len(object_rows)}"
                )
            rows.extend(object_rows)
            object_metrics.append({
                "key": obj["key"], "sha256": actual_sha,
                "declared_rows": obj["rows"], "physical_rows": len(object_rows),
                "bytes": len(artifact_bytes),
            })

        partition_date = pointer["partition"]["as_of_date"]
        for index, row in enumerate(rows):
            if (not isinstance(row, dict) or row.get("market") != pointer["market"]
                    or row.get("as_of_date") != partition_date):
                raise LatestGoodError(
                    "latest-good artifact 행과 pointer partition이 일치하지 않는다: "
                    f"dataset={dataset} row={index}"
                )
        item["rows"] = rows
        item["quality"] = {
            "dataset": dataset,
            "pointer_key": item["pointer_key"],
            "pointer_version": item["pointer_version"],
            "pointer_bytes": len(item["pointer_bytes"]),
            "pointer_sha256": hashlib.sha256(item["pointer_bytes"]).hexdigest(),
            "source_run_id": pointer["source_run_id"],
            "partition": pointer["partition"],
            "object_count": len(pointer["objects"]),
            "objects": object_metrics,
            "physical_rows": len(rows),
            "logical_rows": 0,
        }
    return inputs


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """이 시장의 canonical 파티션 as_of_date 목록. 경로는 빌더로만 만든다(레이크 규약)."""
    marker = canonical_etf_holdings_partition(market, "")  # ".../as_of_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


def _is_iso_date(value: str) -> bool:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value
    except ValueError:
        return False


def _existing_tickers(conn, mics: set[str]) -> dict[tuple[str, str], str]:
    """이미 있는 (market_code, ticker) → instrument_id. 자연키 조회가 멱등의 근거다."""
    if not mics:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT market_code, ticker, instrument_id FROM instrument WHERE market_code = ANY(%s)",
            (sorted(mics),),
        )
        return {(m, t): i for m, t, i in cur.fetchall()}


def _insert_company(conn, name: str, country: str) -> str:
    """회사 1건 — entity(ACTOR) + actor + company_profile. actor_id 를 돌려준다.

    company_profile 은 선택이 아니다 — equity_profile.issuer_actor_id 의 FK 가 actor 가 아니라
    **company_profile(actor_id)** 을 가리킨다(ALPHA-362 에서 실측으로 드러났다).
    """
    actor_id = domain_id("actor")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO entity (entity_id, entity_type, display_name, status)"
            " VALUES (%s, 'ACTOR', %s, 'ACTIVE')",
            (actor_id, name),
        )
        cur.execute(
            "INSERT INTO actor (actor_id, actor_type, country_code) VALUES (%s, 'COMPANY', %s)",
            (actor_id, country),
        )
        # dart_corp_code 는 canonical 에 없어 NULL — enrich-corp-code 스텝이 채운다(ALPHA-491, nullable).
        cur.execute("INSERT INTO company_profile (actor_id) VALUES (%s)", (actor_id,))
    return actor_id


def _insert_equity(conn, *, name: str, mic: str, ticker: str, currency: str, issuer: str) -> str:
    """주식 1건 — entity(INSTRUMENT) + instrument + equity_profile. instrument_id 를 돌려준다."""
    instrument_id = domain_id("inst")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO entity (entity_id, entity_type, display_name, status)"
            " VALUES (%s, 'INSTRUMENT', %s, 'ACTIVE')",
            (instrument_id, f"{name} 보통주"),
        )
        cur.execute(
            "INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type,"
            " currency_code) VALUES (%s, %s, %s, 'EQUITY', %s)",
            (instrument_id, mic, ticker, currency),
        )
        # 전부 보통주다 — 두 입력 모두 보통주만 여기까지 온다. 구성종목은 ETF 유니버스에
        # 우선주가 없어서, 전종목은 **호출부가 `share_class != "보통주"` 를 걸러서**다
        # (ALPHA-830). 그 필터가 없으면 `CJ우` 가 COMMON 으로 실려, 회사명 키를 COMMON 에만
        # 거는 엔티티 해소가 우선주 약명을 회사 이름으로 등록한다.
        cur.execute(
            "INSERT INTO equity_profile (instrument_id, issuer_actor_id, share_class_code)"
            " VALUES (%s, %s, 'COMMON')",
            (instrument_id, issuer),
        )
    return instrument_id


def _latest_profile_rows(storage: Storage, market: str) -> list[dict]:
    """canonical ETF 프로필의 **최신 기준일** 스냅샷 행(ALPHA-462).

    개명이 일어나면 새 기준일 파티션이 최신을 말한다 — 과거 기준일까지 훑으면 옛 이름으로
    마스터를 만들 수 있어 최신 하나만 읽는다(구성종목이 최신 스냅샷만 보는 것과 같은 모델).
    """
    marker = canonical_etf_profile_partition(market, "")  # ".../as_of_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    if not dates:
        return []
    rows: list[dict] = []
    prefix = canonical_etf_profile_partition(market, max(dates))
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(_read_parquet_rows(storage.get_bytes(key)))
    return rows


def _latest_instrument_profile_rows(storage: Storage, market: str) -> list[dict]:
    """canonical 종목기본정보의 **최신 기준일** 스냅샷 행(ALPHA-829/830).

    ETF 프로필과 같은 모델이다 — 개명은 새 기준일 파티션이 말하고, 과거까지 훑으면 옛
    이름으로 마스터를 만든다. ⚠️ 여기 기준일은 **벤더 기준일(KRX basDd)**이지 수집일이
    아니다(KRX 가 당일 조회를 막아 둘이 갈린다).
    """
    marker = canonical_instrument_profile_partition(market, "")  # ".../as_of_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    if not dates:
        return []
    rows: list[dict] = []
    prefix = canonical_instrument_profile_partition(market, max(dates))
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(_read_parquet_rows(storage.get_bytes(key)))
    return rows


def _insert_etf(conn, *, name: str, mic: str, ticker: str, currency: str) -> str:
    """ETF 1건 — entity(INSTRUMENT) + instrument(type=ETF) + etf_profile. instrument_id 반환.

    주식(_insert_equity)과 달리 발행회사(actor)를 만들지 않는다 — ETF 의 '발행자'는 운용사지만
    우리 스키마의 `equity_profile.issuer_actor_id` 는 주식 전용이고, ETF 는 `etf_profile` 이
    자기 프로필을 갖는다. 운용사 마스터가 필요해지면 별건이다.

    `etf_type` 은 채우지 않는다 — 허용 어휘가 미정의라 ALPHA-378 이 NOT NULL 을 풀었다.
    임의 값을 넣으면 그게 사실상 계약이 돼 나중에 적재분이 오염된다.
    """
    instrument_id = domain_id("inst")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO entity (entity_id, entity_type, display_name, status)"
            " VALUES (%s, 'INSTRUMENT', %s, 'ACTIVE')",
            (instrument_id, name),
        )
        cur.execute(
            "INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type,"
            " currency_code) VALUES (%s, %s, %s, 'ETF', %s)",
            (instrument_id, mic, ticker, currency),
        )
    ensure_etf_profile(conn, instrument_id)
    return instrument_id


def run(storage: Storage, run_id: str, *, db: DbConfig,
        expected_etfs: frozenset[str] | None = None,
        latest_good: bool = False, all_partitions: bool = False) -> int:
    """canonical 구성종목 → 종목 마스터 적재. 성공 0, 장애 시 비0."""
    if latest_good and all_partitions:
        raise ValueError("latest-good와 all은 함께 쓸 수 없다")
    started_at = datetime.now(timezone.utc)
    read = skipped_no_mic = existing = created = skipped_foreign_etf = 0
    skipped_unsupported_asset = skipped_unknown_asset_type = 0
    skipped_missing_identity = skipped_bad_fetched_at = skipped_unknown_mic = 0
    deduplicated_rows = 0
    unsupported_asset_counts: dict[str, int] = {}
    instruments_read = profiles_no_mic = skipped_non_common = 0
    profiles_no_share_class = 0
    mic_conflicts: list[dict] = []
    etfs_read = etfs_existing = etfs_created = 0
    created_rows: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    latest_good_inputs: dict[str, dict] = {}
    input_io: dict[str, int | None] = {
        "pointer_gets": 0,
        "artifact_gets": 0,
        "canonical_prefix_lists": 0 if latest_good else None,
    }
    if latest_good:
        try:
            latest_good_inputs = _read_latest_good_inputs(storage, input_io)
        except Exception as exc:
            logger.exception("latest-good 입력 검증 실패")
            failures.append({"market": "KR", "reasons": ["latest_good_input_error"],
                             "error": str(exc)})
            exit_code = 1

    for market in (() if latest_good and exit_code else LOADED_MARKETS):
        country = _COUNTRY_BY_MARKET.get(market)
        if country is None:
            # 국가 매핑 없는 시장을 조용히 넘기면 actor.country_code 가 비어 적재된다.
            logger.warning("국가 매핑 없는 시장 — 건너뜀: %s", market)
            continue

        all_rows: dict[tuple[str, str], dict] = {}
        # 이 시장분만 세는 지역 카운터 — 아래 게이트가 쓴다. 위 누적값(read·skipped_*)을 쓰면
        # 시장이 둘 이상일 때 합계끼리 비교해 엉뚱한 시장을 지목한다(같은 파일의
        # instrument_profile_all_dropped 게이트가 이미 같은 이유로 market_* 를 따로 센다).
        market_read = market_no_mic = market_unknown_mic = market_foreign = 0
        market_unsupported = market_unknown_asset_type = 0
        logical_rows: dict[tuple[str, str], tuple[dict, datetime]] = {}
        if latest_good:
            holdings_input = latest_good_inputs["etf_holdings"]
            dates = [holdings_input["pointer"]["partition"]["as_of_date"]]
            holdings_objects = [(
                holdings_input["pointer"]["objects"][0]["key"], holdings_input["rows"],
            )]
        else:
            dates = _partition_dates(storage, market)
            holdings_objects = []
        bad_dates = [date for date in dates if not _is_iso_date(date)]
        if bad_dates:
            failures.append({"market": market, "reasons": ["bad_partition_date"],
                             "dates": bad_dates})
            exit_code = 1
            dates = []
        if dates:
            if not latest_good:
                prefix = canonical_etf_holdings_partition(market, dates[-1])
                holdings_objects = [
                    (key, _read_parquet_rows(storage.get_bytes(key)))
                    for key in storage.list_keys(prefix + "/") if key.endswith(".parquet")
                ]
            for key, object_rows in holdings_objects:
                for row in object_rows:
                    read += 1
                    market_read += 1
                    row_market, as_of = row.get("market"), row.get("as_of_date")
                    if any(
                        not isinstance(value, str) or not value.strip() or value != value.strip()
                        for value in (row_market, as_of)
                    ):
                        skipped_missing_identity += 1
                        continue
                    if row_market != market or as_of != dates[-1]:
                        failures.append({
                            "market": market,
                            "reasons": ["partition_identity_mismatch"],
                            "error": ("canonical 행과 파티션이 일치하지 않는다: "
                                      f"date={dates[-1]}, key={key}"),
                        })
                        exit_code = 1
                        continue
                    etf_id, ticker = row.get("etf_id"), row.get("constituent_ticker")
                    identities = (etf_id, ticker)
                    if any(
                        not isinstance(value, str) or not value.strip() or value != value.strip()
                        for value in identities
                    ):
                        skipped_missing_identity += 1
                        continue
                    mic = row.get("constituent_mic")
                    parsed_fetched_at = _fetched_at(row.get("fetched_at"))
                    if parsed_fetched_at is None:
                        skipped_bad_fetched_at += 1
                        continue
                    candidate_key = (etf_id, ticker)
                    previous = logical_rows.get(candidate_key)
                    if previous is not None:
                        deduplicated_rows += 1
                        if parsed_fetched_at < previous[1]:
                            continue
                    logical_rows[candidate_key] = (row, parsed_fetched_at)
            if latest_good:
                latest_good_inputs["etf_holdings"]["quality"]["logical_rows"] += len(
                    logical_rows
                )
            for row, parsed_fetched_at in logical_rows.values():
                etf_id, ticker = row["etf_id"], row["constituent_ticker"]
                raw_asset_type = row.get("constituent_asset_type")
                asset_type = raw_asset_type if isinstance(raw_asset_type, str) else "UNKNOWN"
                if asset_type in {"CASH", "OPTION"}:
                    skipped_unsupported_asset += 1
                    market_unsupported += 1
                    unsupported_asset_counts[asset_type] = (
                        unsupported_asset_counts.get(asset_type, 0) + 1
                    )
                    continue
                if asset_type != "EQUITY":
                    skipped_unknown_asset_type += 1
                    market_unknown_asset_type += 1
                    continue
                if expected_etfs is not None and etf_id not in expected_etfs:
                    skipped_foreign_etf += 1
                    market_foreign += 1
                    continue
                mic = row.get("constituent_mic")
                if not isinstance(mic, str) or not mic.strip() or mic != mic.strip():
                    skipped_no_mic += 1
                    market_no_mic += 1
                    continue
                if mic not in _MICS_BY_MARKET[market]:
                    skipped_unknown_mic += 1
                    market_unknown_mic += 1
                    continue
                master_key = (mic, ticker)
                previous = all_rows.get(master_key)
                if previous is None or parsed_fetched_at >= previous["_fetched_at"]:
                    all_rows[master_key] = {**row, "_fetched_at": parsed_fetched_at}
            # 읽었는데 **한 행도 못 쓴** 상태 — 아래 instrument_profile_all_dropped 와 같은
            # 게이트고, 그쪽 도크스트링의 두 규율을 그대로 따른다: (1) 탈락 축을 **다 더한다**
            # (2) 시장별 지역 카운터로 본다. 지원 제외 현금·옵션은 판정 분모에서도 빼야
            # 정상 제외만 있는 파티션을 전량 유실로 오인하지 않는다.
            #
            # 왜 필요한가: etf_id 어휘가 config 와 갈리면(오타·정규화 변경) 구성종목 축이 통째로
            # 빠지는데, KRX 전종목 축이 마스터를 덮어 줘 런은 초록으로 끝난다 — 그때 "구성종목이
            # 이긴다"는 이름 우선순위가 말없이 사라진다. 침묵이 결함이다(Rule 12).
            # 비0으로 끝내지 않는 것도 형제 게이트와 같은 정책이다(한 파일에 두 정책 금지, Rule 7).
            eligible_read = (len(logical_rows) - market_unsupported - market_unknown_asset_type
                             - market_unknown_mic)
            unusable = market_no_mic + market_foreign
            if eligible_read and eligible_read == unusable:
                failures.append({"market": market, "reasons": ["constituents_all_foreign"],
                                 "error": f"구성종목 {market_read}건이 전부 사용 불가"
                                          f"(MIC 결측 {market_no_mic}·MIC 미지원 {market_unknown_mic}"
                                          "·유니버스 뿌리 밖 "
                                          f"{market_foreign}) — etf_id 어휘가 "
                                          "krx_etf.source.etf_map 과 갈렸는지 확인"})

        # KRX 상장 전종목(ALPHA-829/830) — 구성종목과 **독립된 두 번째 입력**이다.
        #
        # 왜 필요한가: 구성종목만 보면 마스터가 **ETF 에 담긴 종목만** 갖는다(08-06 런 329종).
        # 뉴스는 ETF 에 안 담긴 회사도 똑같이 다루는데, 마스터에 없으면 assertion argument 가
        # 붙을 대상 행 자체가 없어 **구조적으로 미해소**다.
        #
        # `setdefault` 라 **구성종목이 이긴다** — 두 경로가 같은 종목을 주면 기존 경로의
        # 이름이 남아 이 변경의 증분이 0 이 된다. 2026-08-06 실측상 겹치는 869종의 이름이
        # **전건 동일**해서 오늘은 선택이 무의미하지만, 나중에 갈릴 때 조용히 뒤집히지
        # 않도록 순서를 고정하고 테스트로 못박는다.
        mic_by_ticker = {t: m for (m, t) in all_rows}  # 구성종목이 말한 시장(불일치 검출용)
        # ⚠️ 아래 게이트는 **이 시장분만** 봐야 한다. 위 카운터들은 시장 루프 밖에
        # 선언된 누적값이라, 시장이 둘 이상이 되면 합계끼리 비교해 엉뚱한 시장을
        # 지목한다(오늘 LOADED_MARKETS 가 KR 하나뿐이라 안 드러날 뿐이다).
        market_read = market_no_mic = market_no_share_class = market_non_common = 0
        instrument_profile_rows = (
            latest_good_inputs["instrument_profile"]["rows"]
            if latest_good else _latest_instrument_profile_rows(storage, market)
        )
        instrument_logical_keys: set[str] = set()
        for profile in instrument_profile_rows:
            instruments_read += 1
            market_read += 1
            mic, ticker = profile.get("market_code"), profile.get("ticker")
            if isinstance(ticker, str) and ticker:
                instrument_logical_keys.add(ticker)
            if not mic or not ticker:
                # 정제단이 이미 걸렀어야 하는 행. 구성종목의 지원 제외 자산과 **사유가
                # 다르므로** 카운터를 따로 둔다.
                profiles_no_mic += 1
                market_no_mic += 1
                continue

            # **보통주만 마스터에 세운다**(ALPHA-830). `stk_isu_base_info` 는 상장 *종목*
            # 서비스라 우선주까지 준다 — 실측 2,872종 중 113종(구형우선주 78·신형 23·
            # 종류주권 12). 그대로 넣으면 (1) `CJ우`·`SK우` 같은 **존재하지 않는 회사**가
            # actor 로 서고 (2) `equity_profile.share_class_code` 가 전부 COMMON 이라
            # 엔티티 해소가 그 이름을 **회사명 키로** 등록해(`entity_resolution` 이 COMMON
            # 에만 회사명 키를 건다) "회사명 → 그 회사 보통주" 약속이 깨진다.
            # 우선주를 제대로 세우려면 발행사 actor 로 이어야 하는데 그건 별건이다.
            share_class = profile.get("share_class")
            if share_class is None:
                # 컬럼 **부재**는 우선주가 아니라 스키마 드리프트다(이 컬럼이 생기기 전
                # 파티션). 우선주와 한 카운터에 넣으면 2,872 가 "정상값이 커진 것"으로
                # 보여 아무도 안 본다 — 위 전량 탈락 게이트도 이 축을 봐야 걸린다.
                profiles_no_share_class += 1
                market_no_share_class += 1
                continue
            if share_class != "보통주":
                skipped_non_common += 1
                market_non_common += 1
                continue

            # 두 입력이 같은 티커를 **다른 시장**으로 말하면 키가 갈려 같은 종목이 두 번
            # 선다(자연키가 (market_code, ticker)). 이전상장(코스닥→유가)이 실제 경로다:
            # 구성종목은 마지막 ETF 스냅샷의 옛 시장을, 전종목은 새 시장을 말한다.
            # 조용히 둘 다 만들면 해소기가 그 티커를 ambiguous 로 보고 **영구 미해소**가
            # 된다 — 이 티켓이 없애려던 바로 그 결과다. 이름을 남기고 전종목 쪽을 버린다
            # (구성종목이 이기는 규칙과 같은 방향).
            prior_mic = mic_by_ticker.get(ticker)
            if prior_mic and prior_mic != mic:
                mic_conflicts.append({"source": "lake", "ticker": ticker,
                                      "known_mic": prior_mic, "input_mic": mic})
                continue

            # 구성종목 행과 같은 키 모양으로 맞춰 아래 소비 루프를 그대로 쓴다. 통화는
            # 소비부가 `or "KRW"` 로 받으므로 싣지 않는다(KR 상장분은 전부 원화).
            all_rows.setdefault((mic, ticker), {"constituent_name": profile.get("display_name")})
        if latest_good:
            latest_good_inputs["instrument_profile"]["quality"]["logical_rows"] += len(
                instrument_logical_keys
            )

        # 전종목 입력을 **읽었는데 한 건도 못 쓴** 상태는 사유와 함께 남긴다. 실제 경로가
        # 있다: `market_code`·`share_class` 컬럼이 생기기 전에 쓰인 canonical 파티션을 읽으면
        # 전 행이 그 축에서 떨어진다.
        #
        # ⚠️ **비0으로 끝내지 않는다.** 이 입력은 SFN 밖·ops 카탈로그 밖의 수동 전용이라
        # (README "수집 — 상태머신 밖") 낡아 있는 것이 정상 상태일 수 있다. 그런데
        # `LoadInstrumentsCheckExitCode` 의 Default 는 NotifyFailure 라, 여기서 비0을 내면
        # EnrichCorpCode 와 FeatureParallel 전체(TagNews·LoadDocuments·LoadEtfNav·
        # LoadPriceTriggers·LoadEtfHoldings·LoadEtfFlow)가 **사람이 손으로 정제를 돌릴
        # 때까지 매일 밤 안 돈다**. 선택 입력의 낡음이 다섯 로더를 인질로 잡는 건 과하다.
        # 드러남은 `failures` + 전용 카운터(`instrument_profiles_no_mic` 등)가 책임진다 —
        # 둘 다 `ops.failed_records` 에 들어가 원장에서 보인다(같은 함수의
        # `etf_profile_incomplete` 와 같은 정책. Rule 7 — 한 파일에 두 정책을 두지 않는다).
        # 이걸 치명으로 만들려면 먼저 이 입력을 SFN·카탈로그에 넣어 필수 작업으로 세워야 한다.
        # 세 탈락 축을 **다 더한다**. `non_common` 을 빼면 KRX 가 주식종류 어휘를
        # 바꿨을 때(보통주→보통주식) 전 행이 그 축으로 떨어지는데 게이트가 조용하고,
        # 로그엔 `non_common: 2872` 가 '정상값이 커진 것'처럼 남는다.
        unusable = market_no_mic + market_no_share_class + market_non_common
        if market_read and market_read == unusable:
            failures.append({"market": market, "reasons": ["instrument_profile_all_dropped"],
                             "error": f"전종목 {market_read}건이 전부 사용 불가"
                                      f"(MIC 결측 {market_no_mic}·주식종류 결측 "
                                      f"{market_no_share_class}·비보통주 {market_non_common}) — "
                                      "정제 canonical 이 낡았거나 어휘가 바뀌었는지 "
                                      "확인(normalize-instrument-profile)"})

        # ETF 마스터(ALPHA-462)는 구성종목과 **독립된 입력**(프로필 canonical)에서 온다 —
        # 구성종목이 비어도(수집 실패·신규 레이크) ETF 는 만들 수 있어야 한다. 둘 다 비었을
        # 때만 이 시장을 건너뛴다. 예전엔 구성종목만 보고 continue 해서, 프로필만 있는 런이
        # 조용히 아무것도 안 했다(테스트가 잡음).
        mic_for_market = _MIC_BY_MARKET.get(market)
        profile_rows = (
            latest_good_inputs["etf_profile"]["rows"]
            if latest_good and mic_for_market else
            (_latest_profile_rows(storage, market) if mic_for_market else [])
        )
        if latest_good:
            latest_good_inputs["etf_profile"]["quality"]["logical_rows"] += len({
                row.get("etf_id") for row in profile_rows
                if isinstance(row.get("etf_id"), str) and row.get("etf_id")
            })
        if not all_rows and not profile_rows:
            logger.info("적재 대상 없음: market=%s", market)
            continue

        # 롤백 기준선. **표본 리스트가 아니라 카운터를 되감는다** — `created_rows` 는 상한이
        # 걸린 표본이라(_CREATED_SAMPLE_LIMIT) 거기서 세면 상한을 넘긴 런에서 되감기가
        # 모자라고, 롤백된 트랜잭션이 만든 게 있다고 로그가 주장한다(관대한 방향으로 거짓).
        created_at_market_start = created
        etfs_created_at_market_start = etfs_created
        sample_at_market_start = len(created_rows)

        # DB 실패를 잡아 사유와 함께 드러낸다 — 안 잡으면 트레이스백으로 죽어 **이 런이 뭘 했는지
        # 로그가 안 남는다**(Rule 12 — 결과는 항상 로그, 형제 정제 스텝과 같은 규약).
        # 커밋 경계는 시장 단위다: connect() 가 예외면 롤백하므로 이 시장은 전무가 되고, 다른
        # 시장은 계속 시도한다 — 부분 커밋으로 FK 로 얽힌 마스터가 반쪽으로 남지 않는다.
        try:
            with connect(db) as conn:
                # ⚠️ 입력의 MIC 집합이 아니라 **이 시장의 전 MIC** 로 묻는다. 아래 정체성
                # 검사가 티커 단위라(`db_mic_by_ticker`), 입력에 없는 MIC 로 서 있는 행을
                # 못 받으면 그 검사가 조용히 무력해진다 — 넓히는 비용은 행 몇 개뿐이다.
                # `.get(market, ())` 를 쓰지 않는다 — 빈 집합이면 `_existing_tickers` 가
                # 질의도 없이 {} 를 돌려주고, 그러면 **이미 있는 종목이 하나도 없다**고
                # 판정해 전 종목을 새 ULID 로 다시 만든다(그 ID 를 참조하던 FK 가 전부
                # 끊긴다 — ADR-0027 이 금지하는 바로 그 결과). KeyError 로 죽는 편이 낫다:
                # 아래 except 가 받아 load_error + 비0 + 로그로 드러난다. 형제 매핑
                # (_COUNTRY_BY_MARKET·_MIC_BY_MARKET)은 빠지면 안전하게 no-op 인데 이것만
                # 위험한 쪽으로 실패한다.
                have = _existing_tickers(conn, set(_MICS_BY_MARKET[market]))
                # 이미 적재된 종목이 **어느 시장으로** 서 있는지. 레이크 쪽 비교(위 mic_by_ticker)
                # 만으로는 못 잡는 경로가 있다: 어떤 종목이 모든 ETF 바스켓에서 빠지면 오늘의
                # 구성종목 스냅샷에 없어 비교 대상이 사라지는데, 그 뒤 이전상장하면 전종목이
                # 새 MIC 를 말하고 자연키가 달라 **두 번째 instrument 가 선다**. 정체성이
                # 사는 곳은 레이크가 아니라 DB 라, 마지막 판정은 DB 로 한다.
                db_mic_by_ticker = {t: m for (m, t) in have}
                for (mic, ticker), row in sorted(all_rows.items()):
                    if (mic, ticker) in have:
                        existing += 1
                        continue
                    db_mic = db_mic_by_ticker.get(ticker)
                    if db_mic and db_mic != mic:
                        # 같은 티커가 DB 엔 다른 시장으로 있다 — 새로 만들면 해소 인덱스가
                        # 그 티커를 ambiguous 로 보아 그 회사가 영구 미해소가 된다.
                        # ⚠️ 기존 행의 MIC 를 **고치지는 않는다**: instrument 의 정체성 축을
                        # 바꾸는 건 ADR-0027 소관이라 별건이다. 그래서 실제 이전상장이면 이
                        # 충돌이 **매 런 반복 기록**되고 ops.failed_records 가 상시 비0이 된다
                        # — 버그가 아니라 알려진 천장이다.
                        mic_conflicts.append({"source": "db", "ticker": ticker,
                                              "known_mic": db_mic, "input_mic": mic})
                        continue
                    name = row.get("constituent_name") or ticker
                    currency = row.get("currency") or "KRW"
                    actor_id = _insert_company(conn, name, country)
                    instrument_id = _insert_equity(
                        conn, name=name, mic=mic, ticker=ticker, currency=currency, issuer=actor_id
                    )
                    created += 1
                    if len(created_rows) < _CREATED_SAMPLE_LIMIT:
                        created_rows.append({"ticker": ticker, "market_code": mic,
                                             "name": name, "actor_id": actor_id,
                                             "instrument_id": instrument_id})

                # ETF 자신의 마스터(ALPHA-462). 구성종목과 같은 트랜잭션에 둔다 — 둘 다
                # 마스터라 반쪽 커밋이 나면 FK 로 얽힌 상태가 남는다.
                etf_have = _existing_tickers(conn, {mic_for_market}) if profile_rows else {}
                for profile in sorted(profile_rows, key=lambda r: str(r.get("etf_id"))):
                    etfs_read += 1
                    etf_ticker = profile.get("etf_id")
                    etf_name = profile.get("display_name")
                    if not etf_ticker or not etf_name:
                        # 게이트가 이미 걸렀어야 하는 행 — 넣으면 NOT NULL 위반이다.
                        failures.append({"market": market, "etf_id": etf_ticker,
                                         "reasons": ["etf_profile_incomplete"]})
                        continue
                    if (mic_for_market, etf_ticker) in etf_have:
                        # 이미 있으면 건드리지 않는다 — 재실행이 ID 를 바꾸면 그 ID 를 참조하는
                        # NAV·구성종목·트리거 FK 가 전부 끊긴다(ADR-0027).
                        etfs_existing += 1
                        continue
                    etf_instrument_id = _insert_etf(
                        conn, name=etf_name, mic=mic_for_market, ticker=etf_ticker,
                        currency=profile.get("currency") or "KRW",
                    )
                    etfs_created += 1
                    created_rows.append({"ticker": etf_ticker, "market_code": mic_for_market,
                                         "name": etf_name, "instrument_type": "ETF",
                                         "instrument_id": etf_instrument_id})
        except Exception as exc:
            logger.exception("적재 실패(롤백): market=%s", market)
            failures.append({"market": market, "reasons": ["load_error"], "error": str(exc)})
            # 롤백됐으므로 이 시장에서 만든 건 없다 — 카운터를 되돌려 로그가 거짓말하지 않게.
            created = created_at_market_start
            etfs_created = etfs_created_at_market_start
            del created_rows[sample_at_market_start:]
            exit_code = 1

    finished_at = datetime.now(timezone.utc)
    latest_good_quality = [
        latest_good_inputs[dataset]["quality"]
        for dataset in _LATEST_GOOD_DATASETS if dataset in latest_good_inputs
    ]
    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "input_mode": ("latest_good" if latest_good else
                       ("all" if all_partitions else "legacy_implicit_all")),
        "latest_good_inputs": latest_good_quality,
        # 운영 증명은 "pointer/artifact direct GET + canonical LIST 0"을 숫자로 남긴다.
        # latest-good 경로는 위 검증 함수 하나만 입력 I/O를 담당하므로 정적 추론값이 아니라
        # 실제 검증을 끝낸 입력 수와 object 수를 센 값이다.
        "input_io": input_io,
        "physical_rows_read": sum(item["physical_rows"] for item in latest_good_quality),
        "logical_rows_read": sum(item["logical_rows"] for item in latest_good_quality),
        "markets": list(LOADED_MARKETS),
        "constituents_read": read, "skipped_no_mic": skipped_no_mic,
        "skipped_unsupported_asset": skipped_unsupported_asset,
        "unsupported_asset_counts": dict(sorted(unsupported_asset_counts.items())),
        "skipped_unknown_asset_type": skipped_unknown_asset_type,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_bad_fetched_at": skipped_bad_fetched_at,
        "skipped_unknown_mic": skipped_unknown_mic,
        "deduplicated_rows": deduplicated_rows,
        "skipped_foreign_etf": skipped_foreign_etf,
        "instrument_profiles_read": instruments_read,
        "instrument_profiles_no_mic": profiles_no_mic,
        # 0 이 정상. 0 이 아니면 canonical 에 share_class 컬럼이 없다(정제 재실행 필요).
        "instrument_profiles_no_share_class": profiles_no_share_class,
        # 우선주 계열은 마스터에 세우지 않는다(정상값 — 실측 113/2,872).
        "instrument_profiles_non_common": skipped_non_common,
        # 0 이 정상이다. 0 이 아니면 두 입력이 같은 티커를 다른 시장으로 말한 것.
        "mic_conflicts": mic_conflicts,
        "already_present": existing, "created": created,
        # ⚠️ 표본이다(상한 _CREATED_SAMPLE_LIMIT) — 전수는 `created` 가 센다.
        "created_rows": created_rows, "created_rows_limit": _CREATED_SAMPLE_LIMIT,
        "etfs_read": etfs_read, "etfs_already_present": etfs_existing, "etfs_created": etfs_created,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). 마스터는 구성종목·ETF 두 축을 한 테이블에 적재하므로
        # 둘을 합친다. 지원 제외 현금·옵션은 유실이 아니지만, 미지 유형과 주식 MIC 미해소는 유실이다.
        "ops": {
            "records_out": existing + created + etfs_existing + etfs_created,
            "failed_records": (len(failures) + skipped_no_mic + skipped_unknown_asset_type
                               + skipped_missing_identity
                               + skipped_bad_fetched_at
                               + skipped_unknown_mic
                               + profiles_no_mic
                               + profiles_no_share_class + len(mic_conflicts)),
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_instruments: read=%d profiles=%d(no_mic=%d no_share_class=%d non_common=%d "
        "conflict=%d) "
        "skipped_no_mic=%d already=%d created=%d "
        "etfs_read=%d etfs_already=%d etfs_created=%d failures=%d",
        read, instruments_read, profiles_no_mic, profiles_no_share_class,
        skipped_non_common, len(mic_conflicts),
        skipped_no_mic, existing, created,
        etfs_read, etfs_existing, etfs_created, len(failures),
    )
    return exit_code
