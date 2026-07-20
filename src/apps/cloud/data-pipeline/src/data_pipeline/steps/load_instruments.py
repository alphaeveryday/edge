"""종목 마스터 적재 Step4 — canonical ETF 구성종목 → entity/actor/instrument (ALPHA-372).

**Cloud Event Store 48테이블에 쓰는 첫 스텝이다.** `etf_holding_snapshot`·`price_daily` 가 둘 다
`instrument` FK 를 요구하므로 여기가 그 선행이다.

`canonical/holdings/etf_holdings/market=KR` 을 읽어 구성종목마다 회사·주식 마스터를 만든다:
  회사: entity(ACTOR) + actor(COMPANY/KR) + company_profile
  주식: entity(INSTRUMENT) + instrument(MIC/EQUITY/KRW) + equity_profile → issuer

**왜 마이그레이션이 아니라 로더인가**: ALPHA-362 가 KR 9종을 Flyway INSERT 로 박은 건 부트스트랩
이었다. 유니버스는 자란다 — KODEX 반도체만 35종이고 KODEX200 은 201종이라, 종목이 늘 때마다
스키마 PR + CD 를 도는 건 성립하지 않는다. 마스터는 canonical 에서 파생되는 데이터지 스키마가
아니다.

**멱등**: 자연키 `(market_code, ticker)` 로 찾아보고 없을 때만 새 ULID 를 발번한다(ADR-0027 이
정한 표준 절차). 이미 있으면 건드리지 않는다 — 재실행이 ID 를 바꾸면 그 ID 를 참조하는 FK 가
전부 끊긴다. ALPHA-362 가 시딩한 9종도 이 경로로 자연히 걸린다.

**MIC 가 없는 행은 종목이 아니다**: KRX 는 원화현금(`KRD010010001`) 같은 비상장 보유분을 함께
준다(MKT_ID·SECUGRP_ID 가 빈 문자열 → canonical `constituent_mic` = null, ALPHA-370). 우리 스키마는
`instrument.market_code NOT NULL` 이라 **스키마 자신의 규칙으로** 제외된다 — 별도 증권유형 어휘를
만들지 않는다.

`dart_corp_code` 는 채우지 않는다 — canonical 에 없고, 로더가 DART API 를 부르면 관심사가 섞인다
(공시 적재는 KR 9 타깃 한정이고 그 9종은 ALPHA-362 가 이미 실값을 채웠다). nullable 이라 무해하다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect, domain_id, ensure_etf_profile
from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_etf_profile_partition,
    quality_log_key,
)

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


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """이 시장의 canonical 파티션 as_of_date 목록. 경로는 빌더로만 만든다(레이크 규약)."""
    marker = canonical_etf_holdings_partition(market, "")  # ".../as_of_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


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
        # dart_corp_code 는 canonical 에 없어 NULL — 공시 적재 때 채운다(nullable).
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
        # 수집 유니버스에 우선주가 없어 전부 보통주다(우선주는 별도 instrument 가 된다).
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


def run(storage: Storage, run_id: str, *, db: DbConfig) -> int:
    """canonical 구성종목 → 종목 마스터 적재. 성공 0, 장애 시 비0."""
    started_at = datetime.now(timezone.utc)
    read = skipped_no_mic = existing = created = 0
    etfs_read = etfs_existing = etfs_created = 0
    created_rows: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    for market in LOADED_MARKETS:
        country = _COUNTRY_BY_MARKET.get(market)
        if country is None:
            # 국가 매핑 없는 시장을 조용히 넘기면 actor.country_code 가 비어 적재된다.
            logger.warning("국가 매핑 없는 시장 — 건너뜀: %s", market)
            continue

        all_rows: dict[tuple[str, str], dict] = {}
        dates = _partition_dates(storage, market)
        if dates:
            prefix = canonical_etf_holdings_partition(market, dates[-1])
            for key in storage.list_keys(prefix + "/"):
                if not key.endswith(".parquet"):
                    continue
                for row in _read_parquet_rows(storage.get_bytes(key)):
                    read += 1
                    mic, ticker = row.get("constituent_mic"), row.get("constituent_ticker")
                    if not mic or not ticker:
                        skipped_no_mic += 1
                        continue
                    all_rows.setdefault((mic, ticker), row)

        # ETF 마스터(ALPHA-462)는 구성종목과 **독립된 입력**(프로필 canonical)에서 온다 —
        # 구성종목이 비어도(수집 실패·신규 레이크) ETF 는 만들 수 있어야 한다. 둘 다 비었을
        # 때만 이 시장을 건너뛴다. 예전엔 구성종목만 보고 continue 해서, 프로필만 있는 런이
        # 조용히 아무것도 안 했다(테스트가 잡음).
        mic_for_market = _MIC_BY_MARKET.get(market)
        profile_rows = _latest_profile_rows(storage, market) if mic_for_market else []
        if not all_rows and not profile_rows:
            logger.info("적재 대상 없음: market=%s", market)
            continue

        created_before = len(created_rows)

        # DB 실패를 잡아 사유와 함께 드러낸다 — 안 잡으면 트레이스백으로 죽어 **이 런이 뭘 했는지
        # 로그가 안 남는다**(Rule 12 — 결과는 항상 로그, 형제 정제 스텝과 같은 규약).
        # 커밋 경계는 시장 단위다: connect() 가 예외면 롤백하므로 이 시장은 전무가 되고, 다른
        # 시장은 계속 시도한다 — 부분 커밋으로 FK 로 얽힌 마스터가 반쪽으로 남지 않는다.
        try:
            with connect(db) as conn:
                have = _existing_tickers(conn, {m for m, _ in all_rows})
                for (mic, ticker), row in sorted(all_rows.items()):
                    if (mic, ticker) in have:
                        existing += 1
                        continue
                    name = row.get("constituent_name") or ticker
                    currency = row.get("currency") or "KRW"
                    actor_id = _insert_company(conn, name, country)
                    instrument_id = _insert_equity(
                        conn, name=name, mic=mic, ticker=ticker, currency=currency, issuer=actor_id
                    )
                    created += 1
                    created_rows.append({"ticker": ticker, "market_code": mic, "name": name,
                                         "actor_id": actor_id, "instrument_id": instrument_id})

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
            created -= sum(1 for r in created_rows[created_before:]
                           if r.get("instrument_type") != "ETF")
            etfs_created -= sum(1 for r in created_rows[created_before:]
                                if r.get("instrument_type") == "ETF")
            del created_rows[created_before:]
            exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "markets": list(LOADED_MARKETS),
        "constituents_read": read, "skipped_no_mic": skipped_no_mic,
        "already_present": existing, "created": created, "created_rows": created_rows,
        "etfs_read": etfs_read, "etfs_already_present": etfs_existing, "etfs_created": etfs_created,
        "failures": failures, "exit_code": exit_code,
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_instruments: read=%d skipped_no_mic=%d already=%d created=%d "
        "etfs_read=%d etfs_already=%d etfs_created=%d failures=%d",
        read, skipped_no_mic, existing, created,
        etfs_read, etfs_existing, etfs_created, len(failures),
    )
    return exit_code
