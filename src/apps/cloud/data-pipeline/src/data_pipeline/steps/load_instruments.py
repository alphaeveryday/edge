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

**MIC 가 없는 행은 종목이 아니다**: KRX 는 원화현금(`KRD010010001`) 같은 비상장 보유분을 함께
준다(MKT_ID·SECUGRP_ID 가 빈 문자열 → canonical `constituent_mic` = null, ALPHA-370). 우리 스키마는
`instrument.market_code NOT NULL` 이라 **스키마 자신의 규칙으로** 제외된다 — 별도 증권유형 어휘를
만들지 않는다.

`dart_corp_code` 는 채우지 않는다 — canonical 에 없고, 로더가 DART API 를 부르면 관심사가 섞인다.
NULL 로 두면 별도 `enrich-corp-code` 스텝이 corpCode.xml 매칭으로 채운다(ALPHA-491). nullable 이라
그 사이에도 무해하다(ALPHA-362 가 시딩한 9종은 이미 실값).
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
    canonical_instrument_profile_partition,
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

# 로그에 남길 생성 행 표본 상한. 전종목 확대 첫 런은 ~2,500종을 만드는데(ALPHA-830) 전량을
# 실으면 품질 로그 하나가 수백 KB 가 된다. 표본은 "무엇이 만들어졌나"를 눈으로 확인하는
# 용도고 전수는 DB 가 갖고 있다 — 개수(`created`)는 상한과 무관하게 정확하다.
_CREATED_SAMPLE_LIMIT = 200


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


def run(storage: Storage, run_id: str, *, db: DbConfig) -> int:
    """canonical 구성종목 → 종목 마스터 적재. 성공 0, 장애 시 비0."""
    started_at = datetime.now(timezone.utc)
    read = skipped_no_mic = existing = created = 0
    instruments_read = profiles_no_mic = skipped_non_common = 0
    mic_conflicts: list[dict] = []
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
        for profile in _latest_instrument_profile_rows(storage, market):
            instruments_read += 1
            mic, ticker = profile.get("market_code"), profile.get("ticker")
            if not mic or not ticker:
                # 정제단이 이미 걸렀어야 하는 행. 구성종목의 원화현금(MIC 없음)과 **사유가
                # 다르므로** 카운터를 따로 둔다 — 저쪽은 매 런 정상적으로 나오는 값이라
                # 합치면 이쪽의 이상을 원장이 못 본다.
                profiles_no_mic += 1
                continue

            # **보통주만 마스터에 세운다**(ALPHA-830). `stk_isu_base_info` 는 상장 *종목*
            # 서비스라 우선주까지 준다 — 실측 2,872종 중 113종(구형우선주 78·신형 23·
            # 종류주권 12). 그대로 넣으면 (1) `CJ우`·`SK우` 같은 **존재하지 않는 회사**가
            # actor 로 서고 (2) `equity_profile.share_class_code` 가 전부 COMMON 이라
            # 엔티티 해소가 그 이름을 **회사명 키로** 등록해(`entity_resolution` 이 COMMON
            # 에만 회사명 키를 건다) "회사명 → 그 회사 보통주" 약속이 깨진다.
            # 우선주를 제대로 세우려면 발행사 actor 로 이어야 하는데 그건 별건이다.
            if profile.get("share_class") != "보통주":
                skipped_non_common += 1
                continue

            # 두 입력이 같은 티커를 **다른 시장**으로 말하면 키가 갈려 같은 종목이 두 번
            # 선다(자연키가 (market_code, ticker)). 이전상장(코스닥→유가)이 실제 경로다:
            # 구성종목은 마지막 ETF 스냅샷의 옛 시장을, 전종목은 새 시장을 말한다.
            # 조용히 둘 다 만들면 해소기가 그 티커를 ambiguous 로 보고 **영구 미해소**가
            # 된다 — 이 티켓이 없애려던 바로 그 결과다. 이름을 남기고 전종목 쪽을 버린다
            # (구성종목이 이기는 규칙과 같은 방향).
            prior_mic = mic_by_ticker.get(ticker)
            if prior_mic and prior_mic != mic:
                mic_conflicts.append({"ticker": ticker, "holdings_mic": prior_mic,
                                      "profile_mic": mic})
                continue

            # 구성종목 행과 같은 키 모양으로 맞춰 아래 소비 루프를 그대로 쓴다. 통화는
            # 소비부가 `or "KRW"` 로 받으므로 싣지 않는다(KR 상장분은 전부 원화).
            all_rows.setdefault((mic, ticker), {"constituent_name": profile.get("display_name")})

        # 전종목 입력을 **읽었는데 한 건도 못 쓴** 상태는 조용한 0건이다(Rule 12). 실제
        # 경로가 있다: 이 컬럼(market_code)이 생기기 전에 쓰인 canonical 파티션을 읽으면
        # 전 행이 MIC 결측으로 떨어지는데, 그때 exit 0 이면 마스터가 안 자란 채로 초록이다.
        if instruments_read and instruments_read == profiles_no_mic:
            failures.append({"market": market, "reasons": ["instrument_profile_all_dropped"],
                             "error": f"전종목 {instruments_read}건이 전부 MIC 결측 — "
                                      "정제 canonical 이 낡았는지 확인(normalize-instrument-profile)"})
            exit_code = 1

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
        "instrument_profiles_read": instruments_read,
        "instrument_profiles_no_mic": profiles_no_mic,
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
        # 둘을 합친다. MIC 미해소(skipped_no_mic)는 그 종목이 마스터에 안 들어간 유실이다.
        "ops": {
            "records_out": existing + created + etfs_existing + etfs_created,
            "failed_records": (len(failures) + skipped_no_mic + profiles_no_mic
                               + len(mic_conflicts)),
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_instruments: read=%d profiles=%d(no_mic=%d non_common=%d conflict=%d) "
        "skipped_no_mic=%d already=%d created=%d "
        "etfs_read=%d etfs_already=%d etfs_created=%d failures=%d",
        read, instruments_read, profiles_no_mic, skipped_non_common, len(mic_conflicts),
        skipped_no_mic, existing, created,
        etfs_read, etfs_existing, etfs_created, len(failures),
    )
    return exit_code
