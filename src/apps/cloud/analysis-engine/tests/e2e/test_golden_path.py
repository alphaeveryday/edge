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
import os
from datetime import date

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
            "participants": [], "measures": [], "confidence": "H",
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
            " explanation_run, release_bundle CASCADE"
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
def test_news_assembly_to_persisted_explanation(tmp_path):
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
        )
        store = EventStore.connect(settings)
        try:
            assert run(settings, lake=LakeReader(s3, settings.lake_bucket),
                       store=store, client=FakeAnalysisClient(), s3=s3) == 0
        finally:
            store.close()

        # -- 3) 계약 단언: 영속·계보 연결·ID 수렴 --------------------------------
        with seed_conn.cursor() as cur:
            cur.execute(
                "SELECT r.explanation_type, r.confidence_level, r.publication_status,"
                " r.primary_thread_id, r.trade_date, n.bundle_version"
                " FROM explanation_result r JOIN explanation_run n"
                " ON n.explanation_run_id = r.explanation_run_id"
                " WHERE r.etf_instrument_id = %s",
                (ETF_INSTRUMENT,),
            )
            rows = cur.fetchall()
            assert len(rows) == 1, "설명은 정확히 1건 RDS 로 영속돼야 한다(S3 폴백 아님)"
            etype, confidence, status, primary_thread, tdate, bundle = rows[0]
            assert (etype, confidence, status) == ("EVENT_SUPPORTED", "HIGH", "DRAFT")
            assert primary_thread == thread_id, "엔진이 소비한 thread 가 조립 산출물과 다르다"
            assert (tdate.isoformat(), bundle) == (TRADE_DATE, BUNDLE_VERSION)

            cur.execute(
                "SELECT o.price_movement_trigger_id, m.constituent_instrument_id"
                " FROM etf_contribution_observation o"
                " JOIN etf_contribution_member m"
                " ON m.contribution_observation_id = o.contribution_observation_id"
            )
            assert cur.fetchall() == [(TRIGGER_ID, SAMSUNG_INSTRUMENT)], (
                "분해 계보가 파이프라인 트리거 행에 매달리지 않았다"
            )

        [archive_key] = [k for k in s3.objects if "/runs/" in k]
        assert archive_key == (
            "operations_archive/etf_explanations/runs/"
            f"etf={ETF_TICKER}/trade_date={TRADE_DATE}/{REQUEST_ID}.json"
        )
        archive = json.loads(s3.objects[archive_key])
        assert archive["outcome"] == "explained"
        assert archive["persistence"]["persisted"] == "rds"
        assert [e["source_event_id"] for e in archive["events"]] == [evt_id], (
            "엔진이 소비한 이벤트가 조립 단계의 결정적 ID 와 수렴하지 않는다"
        )
    finally:
        seed_conn.close()
