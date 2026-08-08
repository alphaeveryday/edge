"""골든패스 E2E — 뉴스 조립(data-pipeline) → 트리거 소비·분해·설명(analysis-engine) → 영속.

WHY: 두 코드베이스가 주석으로만 약속한 계약을 실제 Postgres 위에서 검증한다(ALPHA-534).
  1. ``PIPELINE_ID`` 결정적 ID 수렴 — assemble_events 가 적재한 source_event 를 엔진이
     같은 ID 로 소비해야 이행기 멱등·계보 연결이 성립한다(ADR-0028).
  2. 실 스키마 SQL — 두 앱의 INSERT/SELECT 가 Flyway 산출 스키마(FK·CHECK·enum)와
     실제로 맞물리는지는 단위 테스트(fake conn)가 증명하지 못한다.

실행 조건: ``E2E_PGHOST`` 환경변수 + Flyway(cloud 세트)가 적용된 ephemeral Postgres.
CI 의 e2e job 이 이 조건을 만든다(postgres:16 서비스 + flyway 컨테이너). 조건이 없으면
skip — 로컬 단위 실행을 막지 않는다. 외부(AWS·DeepSeek) 접속은 전무하다: S3 는 인메모리
fake, 분류·분석 LLM 은 주입 fake 다.
"""
from __future__ import annotations

import io
import json
import pathlib
import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

TRADE_DATE = "2026-07-15"
PREV_DATE = "2026-07-14"
# V202607150004__seed_entity_master_kr.sql 이 시드하는 실제 행 — E2E 는 시드를 재정의하지
# 않고 소비한다(엔진 resolve_etf_instrument/entity_index 가 같은 행을 읽는다).
ETF_TICKER = "091160"
ETF_INSTRUMENT = "inst_01KXJB6W2EFJF0AGPMWG967ZSZ"
SAMSUNG_TICKER = "005930"
SAMSUNG_INSTRUMENT = "inst_01KXJB6W2EFQRP1D5TBRF0EBEK"
TRIGGER_ID = "trg_e2e_0001"
# 발화 두 건 — 같은 날 다른 window 다. 첫 발화(09:00)로 게시·재실행 게이트를 보고,
# 둘째(10:30)로 **다른 발화는 재게시된다**를 본다. 게이트 축이 (etf, 거래일)이 아니라
# 발화(route)임을 두 축으로 조인다(ALPHA-710).
MINUTE_TRIGGER_OPEN = "mtrg_e2e_0000"
MINUTE_TRIGGER_ID = "mtrg_e2e_0001"
BUNDLE_VERSION = "e2e-bundle-1"
REQUEST_ID = "e2e-req-1"
ARTICLE_ID = "e2e-a1"
# 배당 결정은 identity_roles=[ISSUER] 라 edge 의 단일 entity 추출로 thread 가 선다 —
# EARNINGS.RESULT_RELEASE 는 identity=[ISSUER, REPORTING_PERIOD]여서 REPORTING_PERIOD 를
# 못 채워 UNKNOWN(thread NULL)이 된다(ALPHA-457, dev 테스트 픽스처와 동일 선택).
EVENT_TYPE = "COMPANY.CAPITAL.DIVIDEND_DECISION"
PREDICATE = "DECLARE"
IDENTITY_ROLE = "ISSUER"

_NEWS_COLUMNS = (
    "article_id", "source_vendor", "market", "title", "url", "normalized_url",
    "normalized_url_hash", "published_at", "publisher", "lead_text", "mentions",
    "fetched_at",
)


# --------------------------------------------------------------------------- #
# Fakes — 외부 경계(S3·LLM)만 대체한다. DB 는 실물이다.
# --------------------------------------------------------------------------- #
class FakeS3:
    """엔진 LakeReader/archive 가 쓰는 최소 S3 표면(list/get/put)의 인메모리 구현."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_: object) -> None:
        self.objects[Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", **kwargs: object) -> dict:
        if kwargs.get("Delimiter") == "/":
            firsts = {
                Prefix + key[len(Prefix):].split("/", 1)[0] + "/"
                for key in self.objects
                if key.startswith(Prefix) and "/" in key[len(Prefix):]
            }
            return {"CommonPrefixes": [{"Prefix": p} for p in sorted(firsts)]}
        return {
            "Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)],
            "IsTruncated": False,
        }


class FakeAnalysisClient:
    """엔진 분석 LLM fake — 고정 verdict 로 enum 매핑·영속 경로를 결정적으로 만든다."""

    def complete_json(self, system: str, user: str) -> dict:
        return {
            "verdict": "공식 이벤트 선행",
            "headline": "삼성전자 배당 결정이 지수를 끌어올렸습니다.",
            "explain": "삼성전자 배당 결정 이벤트와 기여도 상위 종목이 일치합니다.",
            "confidence": "높음",
            "key_evidence": [{"signal": "배당 결정", "why": "기여 1위 종목의 공식 이벤트"}],
            "unexplained": "",
        }


def _fake_classify(system: str, user: str) -> str:
    """조립 LLM fake — v4 2콜(게이트→타입별 추출) 계약(JSON 문자열). user 페이로드에
    event_type_code 가 있으면 추출 콜, 없으면 게이트 콜(단위 테스트와 같은 구분 축)."""
    payload = json.loads(user)
    if "event_type_code" in payload:
        return json.dumps({"items": [{
            "id": ARTICLE_ID, "predicate": PREDICATE, "stage": None,
            "arguments": [], "measures": [], "confidence": "H",
        }]})
    return json.dumps({"items": [{
        "id": ARTICLE_ID, "doc_class": "EVENT", "event_type_code": EVENT_TYPE,
        "primary_ticker": SAMSUNG_TICKER, "confidence": 0.9,
    }]})


# --------------------------------------------------------------------------- #
# Fixtures — 레이크 산출물(파케이)과 Cloud Event Store 시드
# --------------------------------------------------------------------------- #
def _parquet(columns: dict[str, list]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.table(columns), buf)
    return buf.getvalue()


def _seed_lake_news(tmp_path):
    from data_pipeline.lake import LocalStorage, canonical_news_articles_partition

    storage = LocalStorage(tmp_path / "lake")
    rows = [{
        "article_id": ARTICLE_ID, "source_vendor": "bigkinds", "market": "KR",
        "title": "삼성전자 배당 결정", "publisher": "매일경제",
        "published_at": f"{TRADE_DATE}T09:00:00+09:00",
        "mentions": json.dumps([{"market": "KR", "ticker": SAMSUNG_TICKER}]),
    }]
    storage.put_bytes(
        f"{canonical_news_articles_partition('ko', TRADE_DATE)}/part-00000.parquet",
        _parquet({c: [r.get(c) for r in rows] for c in _NEWS_COLUMNS}),
    )
    return storage

def _seed_engine_lake(s3: FakeS3) -> None:
    price_base = "canonical/market_data/price_daily/market=KR"
    s3.objects[f"{price_base}/trade_date={PREV_DATE}/part-00000.parquet"] = _parquet(
        {"ticker": [SAMSUNG_TICKER], "close": [68000.0]}
    )
    s3.objects[f"{price_base}/trade_date={TRADE_DATE}/part-00000.parquet"] = _parquet(
        {"ticker": [SAMSUNG_TICKER], "close": [70000.0]}
    )
    s3.objects[
        f"canonical/holdings/etf_holdings/market=KR/as_of_date={TRADE_DATE}/part-00000.parquet"
    ] = _parquet({
        "etf_id": [ETF_TICKER],
        "constituent_ticker": [SAMSUNG_TICKER],
        "constituent_name": ["삼성전자"],
        "weight_pct": [20.0],
    })


def _pg_kwargs() -> dict:
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def _seed_event_store(conn) -> None:
    """트리거(파이프라인 소관 행)와 explanation FK 전제를 시드한다.

    트리거는 load-price-triggers 의 산출물 계약이라 여기서 '주어진 것'으로 두고, 이 E2E 는
    그 소비(엔진)와 상류 조립(assemble)만 실행한다.
    """
    with conn.cursor() as cur:
        # 재실행 격리 — 시드 마스터(instrument 등)는 남기고 런 산출물만 비운다.
        cur.execute(
            "TRUNCATE document, source_event, event_thread, price_movement_trigger,"
            " minute_ingestion_session,"
            " explanation_run, release_bundle, tenant_delivery, tenant CASCADE"
        )
        # fan-out(ALPHA-493) 대상 테넌트 — 없으면 게시만 되고 발번 0건이 된다.
        # 2건 시드: '전 테넌트' 계약은 단일 테넌트로는 반례(LIMIT 1·첫 행만 선택 회귀)를
        # 못 잡는다 — 테넌트마다 1행·각자 cursor=1 을 단언해야 한다(Rule 9).
        cur.execute(
            "INSERT INTO tenant (tenant_name, environment, status)"
            " VALUES ('e2e-tenant-a', 'DEV', 'ACTIVE'), ('e2e-tenant-b', 'DEV', 'ACTIVE')"
            " ON CONFLICT (tenant_name) DO NOTHING"
        )
        # FK: price_movement_trigger.etf_instrument_id → etf_profile — 프로파일이 먼저다.
        cur.execute(
            "INSERT INTO etf_profile (instrument_id, etf_type) VALUES (%s, 'SECTOR')"
            " ON CONFLICT (instrument_id) DO NOTHING",
            (ETF_INSTRUMENT,),
        )
        cur.execute(
            "INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id,"
            " trade_date, detected_at, observed_return, absolute_gate_triggered,"
            " relative_gate_triggered, detection_policy_version, detection_reason)"
            " VALUES (%s, %s, %s, now(), 0.0294, TRUE, FALSE, 'l0-abs-v1', 'e2e seed')",
            (TRIGGER_ID, ETF_INSTRUMENT, TRADE_DATE),
        )
        cur.execute(
            "INSERT INTO release_bundle (bundle_version, component_versions, component_hash,"
            " status, published_at) VALUES (%s, '{}'::jsonb, %s, 'PUBLISHED', now())",
            (BUNDLE_VERSION, "0" * 64),
        )
    conn.commit()


# --------------------------------------------------------------------------- #
# 골든패스
# --------------------------------------------------------------------------- #
def _seed_layers_backfill(root: pathlib.Path) -> None:
    """`layers_daily` 백필을 심는다 — **없으면 골든패스가 아무것도 검증하지 않는다.**

    실측(CI): 백필이 없으면 `statics.layers.failed` 로 층 분해가 죽고 라우팅이
    `PRICE_ONLY`, 판정이 `UNCERTAIN` 으로 떨어진다. 그 상태의 골든패스는 '데이터가
    없을 때 정직하게 모른다고 한다' 만 확인한다 - 새 배포 경로(층 분해 -> 라우팅 ->
    시행)는 한 줄도 안 지난다.

    층 회계(ALPHA-862, β=1)는 당일과 직전 종가만 요구한다. 시장(069500)·ETF(091160)·
    구성종목(005930)을 같은 요인으로 만들어 층이 실제로 서게 한다.
    """
    import datetime as dt
    import math

    from edge_analysis.statics.layers import MARKET_CODE

    d0 = dt.date.fromisoformat(TRADE_DATE)
    days = [d0 - dt.timedelta(days=k) for k in range(80, -1, -1)]
    rows: dict[str, list] = {c: [] for c in
                             ("symbol", "name", "date", "close", "volume", "kind")}

    def add(sym: str, nm: str, kind: str, f):
        for i, d in enumerate(days):
            rows["symbol"].append(sym)
            rows["name"].append(nm)
            rows["date"].append(d)
            rows["close"].append(float(f(i)))
            rows["volume"].append(1_000_000.0)
            rows["kind"].append(kind)

    # 시장은 완만한 사인, ETF·구성종목은 그 위에 β 를 얹는다 - 층이 서려면 공통요인이
    # 실제로 있어야 한다(합성이라도 구조는 진짜여야 검증이 뜻을 가진다).
    mkt = [100.0 * (1.0 + 0.01 * math.sin(i / 5.0)) for i in range(len(days))]
    add(MARKET_CODE, "KODEX 200", "market", lambda i: mkt[i])
    add(ETF_TICKER, "KODEX 반도체", "sector",
        lambda i: 50.0 * (1.0 + 1.2 * (mkt[i] / 100.0 - 1.0)))
    add(SAMSUNG_TICKER, "삼성전자", "stock",
        lambda i: 70.0 * (1.0 + 1.1 * (mkt[i] / 100.0 - 1.0)))

    root.mkdir(parents=True, exist_ok=True)
    (root / "layers_daily.parquet").write_bytes(_parquet(rows))
    # holdings 도 심는다. S3 자격증명이 없는 CI 에서는 `s3_etf_holdings` 가 미등록이고
    # `holdings()` 는 이 백필로 폴백한다 - 이게 없으면 구성종목 귀속이 서지 않는다.
    (root / "etf_holdings_fmp.parquet").write_bytes(_parquet({
        "etf_id": [ETF_TICKER],
        "constituent_ticker": [SAMSUNG_TICKER],
        "constituent_name": ["삼성전자"],
        "weight_pct": [100.0],
        "as_of": [d0]}))

    # 5분봉 백필(`bars/`) — **분봉 트리거 경로가 이걸 읽는다.** 설명이 분봉 트리거로만
    # 시작하게 된 뒤(ALPHA-806) 층 분해는 늘 **구간 모드**로 돌고, 구간 모드의 가격 축은
    # `layers_daily` 가 아니라 `bars_5m` 이다(`layers._CLOCK_SQL` — 심볼 명단만 layers_daily
    # 에서 오고 가격은 5분봉에서 온다). 이게 없으면 `statics.layers.failed` 로 층이 죽어
    # 판정이 UNCERTAIN 으로 떨어지고, 골든패스가 배포 경로를 한 줄도 안 지난다.
    #
    # 구간 집계는 하루·심볼마다 `ln(마지막 close / 첫 open)` 이다. 그래서 첫 슬롯의 open 을
    # 전일 수준에, 마지막 슬롯의 close 를 당일 수준에 맞추면 **일봉 계열과 같은 로그수익률**
    # 이 나온다 — 두 축이 같은 요인을 보게 해야 층이 실제로 선다.
    slots = [dt.time(9, 0)]
    while slots[-1] < dt.time(10, 35):
        prev = dt.datetime.combine(d0, slots[-1]) + dt.timedelta(minutes=5)
        slots.append(prev.time())
    bars: dict[str, list] = {c: [] for c in
                             ("symbol", "datetime", "open", "high", "low", "close", "volume")}

    def add_bars(sym: str, level):
        for i, d in enumerate(days):
            if i == 0:
                continue                       # 전일 수준이 없으면 구간 수익을 못 만든다
            lo, hi = float(level(i - 1)), float(level(i))
            n = len(slots)
            for j, t in enumerate(slots):
                o = lo + (hi - lo) * j / n
                c = lo + (hi - lo) * (j + 1) / n
                bars["symbol"].append(sym)
                bars["datetime"].append(dt.datetime.combine(d, t))
                bars["open"].append(o)
                bars["high"].append(max(o, c))
                bars["low"].append(min(o, c))
                bars["close"].append(c)
                # volume 0 은 거래정지로 읽혀 그 날이 계열에서 빠진다(`halt`).
                bars["volume"].append(1_000.0)

    add_bars(MARKET_CODE, lambda i: mkt[i])
    add_bars(ETF_TICKER, lambda i: 50.0 * (1.0 + 1.2 * (mkt[i] / 100.0 - 1.0)))
    add_bars(SAMSUNG_TICKER, lambda i: 70.0 * (1.0 + 1.1 * (mkt[i] / 100.0 - 1.0)))
    (root / "bars").mkdir(parents=True, exist_ok=True)
    (root / "bars" / "bars_5m.parquet").write_bytes(_parquet(bars))


def test_news_assembly_to_persisted_explanation(tmp_path, monkeypatch):
    """뉴스 1건이 조립→소비→설명→영속까지 한 계보로 이어져야 한다.

    실패가 의미하는 것: 두 앱의 결정적 ID 산식이 갈라졌거나(수렴 파괴), 어느 한쪽 SQL 이
    실 스키마(FK·CHECK)와 어긋난다 — 둘 다 클라우드에서만 터질 부류의 회귀다.
    """
    import psycopg2
    from data_pipeline.config import DbConfig
    from data_pipeline.steps import assemble_events
    from edge_analysis.adapters.eventstore import EventStore
    from edge_analysis.adapters.lake import LakeReader
    from edge_analysis.config import PgConfig, Settings
    from edge_analysis.pipeline import run

    pg = _pg_kwargs()
    seed_conn = psycopg2.connect(**pg)
    try:
        _seed_event_store(seed_conn)

        # -- 1) 상류: 뉴스 조립(data-pipeline) → 실 Postgres 에 계보 적재 ----------
        storage = _seed_lake_news(tmp_path)
        assert assemble_events.run(
            storage, "e2e-assemble",
            db=DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                        user=pg["user"], password=pg["password"], sslmode="disable"),
            complete_fn=_fake_classify,
            from_date=TRADE_DATE, to_date=TRADE_DATE,
        ) == 0

        doc_id = assemble_events._stable_id("doc", "bigkinds", ARTICLE_ID)
        asrt_id = assemble_events._stable_id("asrt", doc_id, EVENT_TYPE, PREDICATE)
        evt_id = assemble_events._stable_id("evt", asrt_id, SAMSUNG_INSTRUMENT)
        thread_key, missing_roles = assemble_events._thread_key(
            EVENT_TYPE, {IDENTITY_ROLE: SAMSUNG_INSTRUMENT})
        assert missing_roles == [], "배당 결정의 identity(ISSUER)가 단일 entity 로 안 채워졌다"
        thread_id = assemble_events._stable_id("thr", thread_key)
        with seed_conn.cursor() as cur:
            cur.execute(
                "SELECT event_status, source_class FROM source_event WHERE source_event_id = %s",
                (evt_id,),
            )
            assert cur.fetchone() == ("ACTIVE", "NEWS"), "조립 계보가 결정적 ID 로 서지 않았다"

        # -- 2) 하류: 엔진이 트리거·이벤트를 소비해 설명을 영속(RDS 경로) ----------
        s3 = FakeS3()
        _seed_engine_lake(s3)
        # 백필을 심고 물린다 - 이게 없으면 아래 단언이 '데이터 없음' 을 고정한다
        _seed_layers_backfill(tmp_path / "backfill")
        monkeypatch.setenv("CAUSAL_BACKFILL_DIR", str(tmp_path / "backfill"))
        settings = Settings(
            trade_date=date.fromisoformat(TRADE_DATE),
            request_id=REQUEST_ID,
            region="ap-northeast-2",
            lake_bucket="e2e-lake",
            etf_ticker=ETF_TICKER,
            pg=PgConfig(host=pg["host"], port=pg["port"], dbname=pg["dbname"],
                        user=pg["user"], password=pg["password"], schema="public"),
            deepseek_api_key="e2e-not-used",
            deepseek_model="deepseek-chat",
            release_bundle_version=BUNDLE_VERSION,
            result_s3_prefix="s3://e2e-lake/operations_archive/etf_explanations/",
            aws_profile=None,
            # 이 골든패스는 이전(비인과) 경로의 계약을 고정한다 — fake 가 classic
            # explanation JSON 을 반환하므로 인과 하네스(설계 제안 계약)와 맞지 않는다.
            # 인과 경로 e2e 는 ALPHA-620/622 소관(공인 OFF 모드, config.py 주석 참조).
        )
        # 통계 표면 산출을 주입한다. 이 픽스처엔 **5분봉이 없고**(로컬 백필에 `bars/` 가
        # 없고 CI 엔 S3 자격증명도 없다) 합성 이력으로는 어느 검정도 못 세운다 — 위
        # 주석이 이미 그렇게 적었다. 그대로 두면 `run_statics` 가 표면 부재로 죽고,
        # ALPHA-795 이후 그런 런은 **게시도 발번도 하지 않아** 이 골든패스가 지키려는
        # 계보(게시 → 전 테넌트 outbox)를 통째로 못 밟는다.
        #
        # 그래서 statics 를 흉내내지 않고 **경계로 취급해** 고정 산출을 준다 — S3·LLM 을
        # fake 로 두는 것과 같은 축이다. 표면 부재 자체의 계약은 단위 테스트가 지킨다.
        monkeypatch.setattr("edge_analysis.statics.etfcell.run",
                            lambda *_a, **_k: "[ETF] 091160 시장 주도 · e2e 고정 산출")

        # 분봉 원장·artifact 시드 — 설명의 유일한 진입점이 분봉 트리거다(ALPHA-806).
        # 두 window(09:00·10:30)를 깔고 각각에 발화를 매단다.
        open_window = f"{TRADE_DATE}T00:00:00+00:00"     # 09:00 KST — 세션 시가 window
        trigger_window = f"{TRADE_DATE}T01:30:00+00:00"  # 10:30 KST
        # 분봉 canonical artifact — **data-pipeline 의 키 빌더로 쓴다**: 엔진 리더의
        # 미러 전사(lake.minute_artifact_key)와의 수렴을 이 읽기가 고정한다(PIPELINE_ID
        # 수렴과 같은 축). 삼성 장중 수익률 73500/70000−1 = 5% — 일봉 시드(≈2.94%)와
        # 다른 값이라, 아래 분해 단언이 분봉 축을 실제로 탔음을 구분해 증명한다.
        # 원장 checksum = 커밋된 바이트의 sha256 — 엔진이 대조하므로 실물과 같이 시드.
        import hashlib

        from data_pipeline.lake.storage import canonical_price_minute_artifact_key

        def _bars(*rows: dict) -> bytes:
            return ("\n".join(json.dumps(r) for r in rows) + "\n").encode()

        # 설명이 **09:00~발화 분 전 구간**의 1분봉을 요구한다(ALPHA-854 두 축) — 트리거
        # window 두 개만 깔면 원장 결손으로 서고, 그건 이 골든패스가 지키려는 계보를
        # 못 밟는다. 09:00~10:30 91분을 실물처럼 전부 깐다.
        #
        # 발화 분을 **넘겨 깔지 않는다**: 창이 발화 분에서 끝나므로 그 뒤 분은 실물에서
        # 아직 수집 전이다. 넉넉히 깔면 미래를 읽어도 초록인 픽스처가 된다.
        #
        # 가격은 두 발화를 **구분되게** 준다: 09:00 창은 삼성 70100, 10:30 창은 73500.
        # 아래 분해 단언(73500/68000)이 두 번째 발화의 축을 실제로 탔음을 증명한다.
        MINUTE_COUNT = 91  # 09:00 ~ 10:30 (발화 분 포함)
        TRIGGER_MINUTE = 90  # 10:30

        def _price_at(minute: int) -> dict[str, str]:
            # 시장 프록시(069500)도 artifact 에 싣는다 — 실물 수집 universe 가 그렇고,
            # 층 회계의 시장 층은 **커밋 봉만** 본다(ALPHA-866, 레이크 폴백 금지).
            # 가격은 ETF 의 정확히 ×20 — 구간 log 수익이 같아져 층 회계가 시장 100%
            # 로 서고, 라우팅이 PRICE_ONLY 로 남는 계보를 고정한다.
            if minute >= TRIGGER_MINUTE:
                return {ETF_TICKER: "10300.0", SAMSUNG_TICKER: "73500.0",
                        "069500": "206000.0"}
            return {ETF_TICKER: "10050.0", SAMSUNG_TICKER: "70100.0",
                    "069500": "201000.0"}

        minute_windows: list[tuple[str, str, bytes]] = []
        for minute in range(MINUTE_COUNT):
            # 09:00 KST = 그날 00:00 UTC — 원장 window_start 와 artifact ts 의 정본.
            stamp = datetime.combine(
                date.fromisoformat(TRADE_DATE), datetime.min.time(),
                tzinfo=timezone.utc) + timedelta(minutes=minute)
            hhmm = (stamp + timedelta(hours=9)).strftime("%H%M")
            body = _bars(*[
                # strict reader 는 ts·OHLCV 전부를 요구한다 — 원장 window 와 ts 가
                # 어긋나거나 필드가 빠지면 형상 위반으로 죽는다(관대한 구 reader 와 다름).
                {"unit_id": unit, "ts": stamp.isoformat().replace("+00:00", "Z"),
                 "open": price, "high": price, "low": price, "close": price,
                 "volume": "10"}
                for unit, price in _price_at(minute).items()
            ])
            s3.objects[
                canonical_price_minute_artifact_key("KR", TRADE_DATE, hhmm, 1)] = body
            minute_windows.append((stamp.isoformat(), hhmm, body))

        open_window = minute_windows[0][0]
        trigger_window = minute_windows[TRIGGER_MINUTE][0]
        with seed_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO minute_ingestion_session (session_id, dataset, source_group,"
                " session_date, universe_version, universe_hash, expected_window_count)"
                " VALUES ('ses-e2e', 'price_minute', 'KR', %s, 'u-e2e', %s, %s)",
                (TRADE_DATE, "0" * 64, MINUTE_COUNT),
            )
            # 분봉 분해 입력의 원장 전제 — 트리거 window 의 세대·checksum 은
            # minute_ingestion_window 가 정본이다. 분모는 원장이 아니라 canonical
            # price_daily 직전 파티션(PREV_DATE)이다(ALPHA-747) — 그래서 여기에
            # minute_session_open 시드가 **없는 것이 계약**이다: 있으면 시가 축으로
            # 되돌아간 회귀가 초록으로 통과한다.
            for window_start, _hhmm, bars_bytes in minute_windows:
                cur.execute(
                    "INSERT INTO minute_ingestion_window (session_id, window_start,"
                    " window_end, scheduled_at, data_status, generation, checksum)"
                    " VALUES ('ses-e2e', %s, %s::timestamptz + interval '1 minute',"
                    " %s, 'VALID', 1, %s)",
                    (window_start, window_start, window_start,
                     hashlib.sha256(bars_bytes).hexdigest()),
                )
            # window_start 가 대상·거래일의 정본이다 — 엔진이 여기서 파생한다.
            # 정책 버전은 운영 판정기와 같아야 한다(ALPHA-745 anchor v2) — v1 을
            # 시드한 채 전일 종가 분해를 기대하면 픽스처가 축 불일치를 인증한다.
            for trigger_id, window, open_px, close_px, bucket in (
                (MINUTE_TRIGGER_OPEN, open_window, 10000, 10050, 0),
                (MINUTE_TRIGGER_ID, trigger_window, 10000, 10300, 1),
            ):
                cur.execute(
                    "INSERT INTO minute_price_trigger (trigger_id, entity_id, session_id,"
                    " window_start, generation, detection_policy_version, open_price,"
                    " close_price, change_rate, threshold, cooldown_bucket)"
                    " VALUES (%s, %s, 'ses-e2e', %s, 1, 'intraday-anchor-v2',"
                    " %s, %s, 0.03, 0.02, %s)",
                    (trigger_id, ETF_TICKER, window, open_px, close_px, bucket),
                )
        seed_conn.commit()

        first_run = replace(settings, trigger_id=MINUTE_TRIGGER_OPEN)
        store = EventStore.connect(first_run)
        try:
            assert run(first_run, lake=LakeReader(s3, first_run.lake_bucket),
                       store=store, client=FakeAnalysisClient(), s3=s3) == 0
        finally:
            store.close()

        # -- 3) 계약 단언: 영속·계보 연결·ID 수렴 --------------------------------
        with seed_conn.cursor() as cur:
            cur.execute(
                "SELECT r.explanation_result_id, r.explanation_type, r.confidence_level,"
                " r.publication_status, r.primary_thread_id, r.trade_date, n.bundle_version"
                " FROM explanation_result r JOIN explanation_run n"
                " ON n.explanation_run_id = r.explanation_run_id"
                " WHERE r.etf_instrument_id = %s",
                (ETF_INSTRUMENT,),
            )
            rows = cur.fetchall()
            assert len(rows) == 1, "설명은 정확히 1건 RDS 로 영속돼야 한다"
            result_id, etype, confidence, status, primary_thread, tdate, bundle = rows[0]
            # **판정 라벨은 데이터가 정한다 - 골든패스가 정하지 않는다.**
            # 옛 계약(`EVENT_SUPPORTED`/`HIGH`)은 fake LLM 이 classic JSON 으로 그렇게
            # 선언했기 때문에 성립했다. 지금은 `record.Verdicts` 가 게이트 산출에서
            # 라벨을 낸다: `EVENT_SUPPORTED` 는 적용된 엣지·성립 함의·유의한 시장사건이
            # **하나라도 있어야** 나온다. 합성 픽스처(구성종목 1·뉴스 1·패널 이력 없음)
            # 로는 어느 검정도 설 수 없으므로 도달 불가다 - 그걸 단언하면 통계가 아니라
            # 픽스처의 빈약함을 고정하는 것이다.
            #
            # 그래서 이 골든패스가 지키는 것은 **계보와 정직성**이다: 층 회계가 서고
            # (시장 100%) 라우팅이 그것을 반영해 `PRICE_ONLY` 로 기록되는가.
            # 백필이 없으면 층 분해가 죽어 `UNCERTAIN` 이 되므로 이 단언이 그 회귀를
            # 잡는다(실측: CI 에서 정확히 그렇게 실패했다).
            assert status == "PUBLISHED"
            assert etype == "PRICE_ONLY", (
                f"층 회계가 서면 PRICE_ONLY 다 - {etype} 는 층 분해가 죽었다는 뜻이다"
                " (백필 부재 -> statics.layers.failed -> UNCERTAIN)")
            assert confidence in ("LOW", "MEDIUM"), (
                f"사건 근거가 없으면 HIGH 가 될 수 없다: {confidence}")
            assert primary_thread == thread_id, "엔진이 소비한 thread 가 조립 산출물과 다르다"
            assert (tdate.isoformat(), bundle) == (TRADE_DATE, BUNDLE_VERSION)

            # write-time fan-out(ALPHA-493) — 게시와 같은 트랜잭션에서 **전 테넌트**에
            # NEW 1행씩, 각자 cursor=1(테넌트별 단조 시작). NEW 는 target/reason 없음(CHECK).
            cur.execute(
                "SELECT t.tenant_name, d.cursor, d.delivery_type, d.explanation_result_id,"
                " d.target_explanation_result_id, d.reason"
                " FROM tenant_delivery d JOIN tenant t ON t.tenant_id = d.tenant_id"
                " ORDER BY t.tenant_name"
            )
            assert cur.fetchall() == [
                ("e2e-tenant-a", 1, "NEW", result_id, None, None),
                ("e2e-tenant-b", 1, "NEW", result_id, None, None),
            ], "게시된 설명이 전 테넌트 outbox 로 발번되지 않았다"

            # 근거 lineage — 설명이 무엇을 보고 쓰였는지 되짚을 수 있어야 한다(ALPHA-603).
            # 조립이 쓴 event_evidence 까지 조인해서 확인한다: 링크만 서고 실체를 못 가리키면
            # 콘솔 근거는 여전히 0건이다.
            cur.execute(
                "SELECT ree.stage_code, ev.source_event_id"
                " FROM explanation_run_event_evidence ree"
                " JOIN explanation_run n ON n.explanation_run_id = ree.explanation_run_id"
                " JOIN explanation_result r ON r.explanation_run_id = n.explanation_run_id"
                " JOIN event_evidence ev ON ev.evidence_id = ree.evidence_id"
                " WHERE r.etf_instrument_id = %s",
                (ETF_INSTRUMENT,),
            )
            assert cur.fetchall() == [("PROMPT", evt_id)], (
                "설명 실행이 프롬프트에 실은 사건의 근거를 lineage 로 남기지 않았다"
            )

            cur.execute(
                "SELECT o.minute_price_trigger_id, m.constituent_instrument_id"
                " FROM etf_contribution_observation o"
                " JOIN etf_contribution_member m"
                " ON m.contribution_observation_id = o.contribution_observation_id"
            )
            assert cur.fetchall() == [(MINUTE_TRIGGER_OPEN, SAMSUNG_INSTRUMENT)], (
                "분해 계보가 소비한 분봉 트리거 행에 매달리지 않았다"
            )

        [archive_key] = [k for k in s3.objects if "/runs/" in k]
        assert archive_key == (
            "operations_archive/etf_explanations/runs/"
            f"etf={ETF_TICKER}/trade_date={TRADE_DATE}/{REQUEST_ID}.json"
        )
        archive = json.loads(s3.objects[archive_key])
        assert archive["outcome"] == "explained"
        assert archive["persistence"]["persisted"] == "rds"
        assert archive["persistence"]["publication_status"] == "PUBLISHED"
        assert [e["source_event_id"] for e in archive["events"]] == [evt_id], (
            "엔진이 소비한 이벤트가 조립 단계의 결정적 ID 와 수렴하지 않는다"
        )

        # -- 4) 같은 발화(route) 재실행: 게시 게이트(ALPHA-493·710) ---------------
        # as_of 가 새로워 grain 유니크로는 못 막는 이중 게시·이중 NEW 발번을 앱 게이트가
        # 막아야 한다 — 재실행분은 DRAFT 보존, outbox 는 불변이어야 정정이 아닌 재실행이
        # 온프렘에 중복 전달되지 않는다. as_of 는 마이크로초 정밀이라 같은 초 재실행도
        # 새 result grain 으로 게이트를 태운다(sleep 불요).
        rerun = replace(first_run, request_id="e2e-req-2")
        store2 = EventStore.connect(rerun)
        try:
            assert run(rerun, lake=LakeReader(s3, rerun.lake_bucket),
                       store=store2, client=FakeAnalysisClient(), s3=s3) == 0
        finally:
            store2.close()
        with seed_conn.cursor() as cur:
            cur.execute(
                "SELECT publication_status, count(*) FROM explanation_result"
                " WHERE etf_instrument_id = %s GROUP BY publication_status",
                (ETF_INSTRUMENT,),
            )
            assert dict(cur.fetchall()) == {"PUBLISHED": 1, "DRAFT": 1}, (
                "재실행이 그날 두 번째 PUBLISHED 를 만들었다 — 게시 게이트 회귀"
            )
            cur.execute("SELECT count(*) FROM tenant_delivery")
            assert cur.fetchone() == (2,), "재실행이 outbox 에 중복 NEW 를 발번했다"

        # -- 5) 같은 날 **다른 발화**(10:30 window): 발화 축 재게시(ALPHA-710) ------
        # 게시 게이트 축은 (etf, trade_date)가 아니라 발화(route)다 — 같은 날 두 번째로
        # 발화하면 새 PUBLISHED 가 나가고 outbox 도 발번돼야 한다. 같은 날 다건 PUBLISHED
        # 는 서빙층(publication-api)이 최근 게시 시각 우선으로 흡수한다. 이 단언이 깨지면
        # 장중 설명이 생성만 되고 MTS 에 안 뜬다.
        #
        # 삼성 장중 수익률 73500/70000−1 = 5% — 첫 발화(70100/70000)와 다른 값이라
        # 아래 분해 단언이 이 window 를 실제로 탔음을 구분해 증명한다.
        #
        # sleep 없이 곧장 실행 — 직전 게시와 같은 초에 게시돼도 as_of 마이크로초
        # 정밀이라 grain 부분 유니크와 충돌하지 않아야 한다(같은 초 두 발화 게시).
        minute_run = replace(settings, request_id="e2e-req-3",
                             trigger_id=MINUTE_TRIGGER_ID)
        store3 = EventStore.connect(minute_run)
        try:
            assert run(minute_run, lake=LakeReader(s3, minute_run.lake_bucket),
                       store=store3, client=FakeAnalysisClient(), s3=s3) == 0
        finally:
            store3.close()
        with seed_conn.cursor() as cur:
            cur.execute(
                "SELECT publication_status, count(*) FROM explanation_result"
                " WHERE etf_instrument_id = %s GROUP BY publication_status",
                (ETF_INSTRUMENT,),
            )
            assert dict(cur.fetchall()) == {"PUBLISHED": 2, "DRAFT": 1}, (
                "같은 날 다른 발화가 재게시되지 않았다 — 게이트가 여전히 일 축이다"
            )
            cur.execute(
                "SELECT t.tenant_name, max(d.cursor), count(*) FROM tenant_delivery d"
                " JOIN tenant t ON t.tenant_id = d.tenant_id GROUP BY t.tenant_name"
                " ORDER BY t.tenant_name"
            )
            assert cur.fetchall() == [("e2e-tenant-a", 2, 2), ("e2e-tenant-b", 2, 2)], (
                "두 번째 발화의 게시가 전 테넌트 outbox 로 발번되지 않았다"
            )
            # 분해 입력이 분봉 축 + **전일 종가 분모**(ALPHA-747)임을 값으로 증명 —
            # 삼성 수익률은 트리거 window close/전일 종가 = 73500/68000−1 ≈ 8.088%.
            # 세 축이 전부 다른 값이라 이 단언 하나가 셋을 가른다:
            #   일봉 축      70000/68000−1 ≈ 2.94%
            #   분봉·시가 축 73500/70000−1 = 5%    (구 ALPHA-710 축)
            #   분봉·전일 축 73500/68000−1 ≈ 8.09% (지금)
            cur.execute(
                "SELECT m.constituent_return FROM etf_contribution_observation o"
                " JOIN etf_contribution_member m"
                " ON m.contribution_observation_id = o.contribution_observation_id"
                " WHERE o.minute_price_trigger_id = %s",
                (MINUTE_TRIGGER_ID,),
            )
            [(minute_ret,)] = cur.fetchall()
            assert abs(float(minute_ret) - (73500 / 68000 - 1)) < 1e-9, (
                f"분봉 분해가 전일 종가 분모를 안 썼다: {minute_ret}"
            )

        # -- 6) 1분 추출 → event 단건 조립(ALPHA-727) -----------------------------
        # 뉴스 1분 레인의 추출 결과가 배치(assemble)와 **같은 결정적 ID·같은 스레드**로
        # event 계보에 서야 한다 — 갈리면 같은 기사에 두 계보가 생기고, 설명엔진의
        # 근거 조회(event_date 축)가 단건 조립분을 못 본다.
        from data_pipeline.minute.event_assembly import NewsEventAssembler

        assembler = NewsEventAssembler(db=DbConfig(
            host=pg["host"], port=pg["port"], name=pg["dbname"],
            user=pg["user"], password=pg["password"], sslmode="disable"))
        second_article = {
            "title": "삼성전자, 분기 배당 확대 결정",
            "published_at": f"{TRADE_DATE}T02:00:00+00:00",
            "language_code": "ko",
        }
        dividend_assertion = {
            "event_type_code": EVENT_TYPE,
            "predicate_code": PREDICATE,
            "arguments": [
                {"role_code": IDENTITY_ROLE, "text": "삼성전자", "entity_id": None},
            ],
            "confidence": 0.9,
            "completeness": "complete",
            "missing_required_roles": [],
        }
        extraction = {
            "status": "ok",
            # 같은 (type, predicate, primary) 중복 assertion(LLM 말더듬) — 같은 결정적
            # evt id 라 첫 건만 적재돼야 한다. 두 번 실리면 threading 이 자기 자신을
            # prior 로 세거나 DB 인자와 thread 계보가 갈린다.
            "assertions": [dividend_assertion, dict(dividend_assertion)],
        }
        outcome = assembler.assemble(source_code="bigkinds", article_id="e2e-a2",
                                     article=second_article, result=extraction)
        assert outcome == {"assembled": 1, "unresolved_primary": 0}, (
            f"단건 조립이 중복 assertion 을 접지 못했다: {outcome}"
        )
        doc2 = assemble_events._stable_id("doc", "bigkinds", "e2e-a2")
        asrt2 = assemble_events._stable_id("asrt", doc2, EVENT_TYPE, PREDICATE)
        evt2 = assemble_events._stable_id("evt", asrt2, SAMSUNG_INSTRUMENT)
        with seed_conn.cursor() as cur:
            cur.execute(
                "SELECT se.event_status, se.event_date, etl.thread_id"
                " FROM source_event se"
                " LEFT JOIN event_thread_link etl ON etl.source_event_id = se.source_event_id"
                " WHERE se.source_event_id = %s", (evt2,))
            [(status2, event_date2, thread2)] = cur.fetchall()
            assert (status2, event_date2.isoformat()) == ("ACTIVE", TRADE_DATE)
            # 같은 발행사(ISSUER=삼성)·같은 타입 — 배치가 세운 스레드(thread_id)에
            # 단건 조립분이 **같은 키로** 엮여야 계보가 한 줄이다.
            assert thread2 == thread_id, "단건 조립이 배치와 다른 스레드를 세웠다"
        # 멱등 — 재호출은 document_entity 자국 게이트에서 no-op 이다(재태깅 순서
        # 흔들림이 event_measure 에 다른 행을 남기는 것도 이 게이트가 막는다).
        rerun_outcome = assembler.assemble(source_code="bigkinds", article_id="e2e-a2",
                                           article=second_article, result=extraction)
        assert rerun_outcome["skipped"] == "already_assembled"
    finally:
        seed_conn.close()
