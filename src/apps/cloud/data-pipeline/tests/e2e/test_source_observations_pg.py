"""원천 관측 수집 → 정제 → 적재 → 기준시각 조회 E2E — 실 PostgreSQL (ALPHA-1130).

단위 테스트는 레이크까지만 본다. 소비 계약은 DB 함수(`*_as_of`)이므로 "기준시각 T 에 무엇이 보이는가"는
TIMESTAMPTZ·DISTINCT ON·CHECK 가 실제로 도는 PostgreSQL 위에서 확인한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "source_observations"
RUN = "e2e_so_"     # 이 테스트의 run_id 접두 — 정리 범위


def _db():
    from data_pipeline.config import DbConfig

    return DbConfig(host=os.environ["E2E_PGHOST"], port=int(os.environ.get("E2E_PGPORT", "5432")),
                    name=os.environ.get("E2E_PGDATABASE", "edge"), user=os.environ.get("E2E_PGUSER", "edge"),
                    password=os.environ.get("E2E_PGPASSWORD", "edge"), sslmode="disable")


@pytest.fixture()
def conn():
    import psycopg

    db = _db()
    connection = psycopg.connect(host=db.host, port=db.port, dbname=db.name, user=db.user,
                                 password=db.password, autocommit=True)
    cleanup = ["DELETE FROM macro_observation WHERE raw_run_id LIKE %s"]
    for sql in cleanup:
        connection.execute(sql, (RUN + "%",))
    yield connection
    for sql in cleanup:
        connection.execute(sql, (RUN + "%",))
    connection.close()


class _Client:
    def __init__(self, routes):
        self.routes = routes

    def request(self, method, url, *, headers=None, data=None, decode=True):
        return next(v for k, v in self.routes.items() if k in url)


def _macro_source(usd_body: bytes):
    from data_pipeline.config import MacroObservationSource
    from data_pipeline.sources import macro_series

    routes = {"731Y003": usd_body,
              "treasury-rates": (FIXTURES / "fmp_treasury.json").read_bytes()}
    return macro_series.MacroSource(MacroObservationSource(ecos_api_key="E"), fmp_api_key="F", client=_Client(routes))


def _macro_run(storage, tag: str, usd_body: bytes) -> str:
    """수집·정제 한 번. 반환: 정제 run_id(적재 입력)."""
    from data_pipeline.steps import source_observations as so, source_observations_macro as so_macro

    now = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc)
    assert so_macro.collect_macro(storage, _macro_source(usd_body), f"{RUN}{tag}_raw",
                            series_ids=["usd_krw", "us_10y_yield"], from_date=None, to_date=None, now=now) == 0
    assert so.normalize(storage, so_macro.MACRO, f"{RUN}{tag}_norm", f"{RUN}{tag}_raw", producer="normalize_macro") == 0
    return f"{RUN}{tag}_norm"


def _as_of(conn, at, series="usd_krw", limit=21):
    return conn.execute(
        "SELECT observation_date::text, value::text, raw_run_id, raw_key, canonical_run_id, available_at"
        " FROM macro_observations_as_of(%s, %s, %s) ORDER BY observation_date", (at, series, limit)).fetchall()


def test_macro_chain_lands_versions_and_the_as_of_query_respects_receipt(tmp_path, conn):
    from data_pipeline.lake import LocalStorage, run_manifest_consumed_key
    from data_pipeline.steps import source_observations as so, source_observations_macro as so_macro

    storage = LocalStorage(tmp_path)
    usd = (FIXTURES / "ecos_usdkrw.json").read_bytes()
    first = _macro_run(storage, "a", usd)

    # 저장 뒤 적재 전에 멈췄다가 복구: 마커 없는 완료 manifest 를 --all 이 싣는다.
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}load1", input_run_id=None, pending=True,
                   producer="load_macro") == 0
    assert storage.list_keys(run_manifest_consumed_key("canonical", "macro_observation", first, so.CONSUMER))
    # 같은 실행 재적재는 행을 늘리지 않는다.
    count = conn.execute("SELECT count(*) FROM macro_observation WHERE raw_run_id LIKE %s", (RUN + "%",)).fetchone()[0]
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}load2", input_run_id=first, pending=False,
                   producer="load_macro") == 0
    assert conn.execute("SELECT count(*) FROM macro_observation WHERE raw_run_id LIKE %s",
                        (RUN + "%",)).fetchone()[0] == count == 6

    received = conn.execute("SELECT min(received_at) FROM macro_observation WHERE raw_run_id = %s",
                            (f"{RUN}a_raw",)).fetchone()[0]
    # 수신 전 기준시각에는 아무것도 보이지 않는다(미래 수신 제외).
    assert _as_of(conn, received - timedelta(seconds=1)) == []
    rows = _as_of(conn, received)
    assert [(d, v) for d, v, *_ in rows] == [("2026-07-24", "1459.66"), ("2026-07-27", "1464.671"),
                                            ("2026-07-28", "1461.35")]
    # DB 행이 manifest·원천 근거로 이어진다.
    manifest = json.loads(storage.get_bytes(
        f"operations_archive/canonical_run_manifests/dataset=macro_observation/run_id={first}/manifest.json"))
    assert {r[4] for r in rows} == {first} and rows[0][3].startswith("raw/source=ecos/dataset=macro_observation/")
    assert storage.get_bytes(rows[0][3])     # raw 원문이 그 키에 있다
    assert manifest["artifact"]["rows"] == 6
    # "최근 공개 2관측일" 요구는 limit 로 고른다 — 툴 기본 21 과 섞지 않는다.
    assert [d for d, *_ in _as_of(conn, received, limit=2)] == ["2026-07-27", "2026-07-28"]

    # 공급자 정정: 새 실행의 값은 그 수신 이후에만 보이고, 이전 기준시각은 옛 값을 그대로 본다.
    revised = json.loads(usd)
    revised["StatisticSearch"]["row"][1]["DATA_VALUE"] = "1465.0"
    second = _macro_run(storage, "b", json.dumps(revised).encode())
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}load3", input_run_id=second, pending=False,
                   producer="load_macro") == 0
    received_b = conn.execute("SELECT min(received_at) FROM macro_observation WHERE raw_run_id = %s",
                              (f"{RUN}b_raw",)).fetchone()[0]
    assert received_b > received
    assert dict((d, v) for d, v, *_ in _as_of(conn, received_b - timedelta(microseconds=1)))["2026-07-27"] == "1464.671"
    assert dict((d, v) for d, v, *_ in _as_of(conn, received_b))["2026-07-27"] == "1465.0"


def test_load_without_its_quality_log_stays_pending_for_the_next_all(tmp_path, conn, monkeypatch):
    # WHY(봇 P2): 커밋 뒤 소비 마커를 먼저 쓰고 검증 기록(quality_log)이 실패하면 `--all` 이 이 실행을 빼 버려
    # 검증 기록 없는 적재가 영영 남는다. 기록이 실패한 적재는 마커 없이 남아 다음 회차가 (멱등하게) 다시 싣는다.
    from data_pipeline.lake import LocalStorage, run_manifest_consumed_key
    from data_pipeline.steps import source_observations as so, source_observations_macro as so_macro

    storage = LocalStorage(tmp_path)
    run = _macro_run(storage, "q", (FIXTURES / "ecos_usdkrw.json").read_bytes())
    original = storage.put_bytes

    def failing(key, data, *a, **k):
        if key.startswith("operations_archive/data_quality_logs/"):
            raise OSError("quality log write failed")
        return original(key, data, *a, **k)

    monkeypatch.setattr(storage, "put_bytes", failing)
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}qload1", input_run_id=None, pending=True,
                   producer="load_macro") == 1
    monkeypatch.setattr(storage, "put_bytes", original)
    marker = run_manifest_consumed_key("canonical", "macro_observation", run, so.CONSUMER)
    assert not storage.list_keys(marker)
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}qload2", input_run_id=None, pending=True,
                   producer="load_macro") == 0
    assert storage.list_keys(marker)
    assert conn.execute("SELECT count(*) FROM macro_observation WHERE raw_run_id = %s",
                        (f"{RUN}q_raw",)).fetchone()[0] == 6


def test_db_rejects_mixed_units_and_early_receipt(conn):
    # WHY: 계열·단위·공급자 쌍과 "관측 기간이 끝난 뒤 수신"을 DB 가 강제한다 — 코드 한 곳이 틀려도 섞이지 않게.
    import psycopg

    base = {"series_id": "kr_10y_yield", "observation_date": "2026-07-28", "value": "2.9", "unit": "percent",
            "source_vendor": "ecos", "source_series": "ECOS 817Y002/D/010210000",
            "received_at": "2026-07-29T01:00:00+00:00", "available_at": "2026-07-29T01:00:00+00:00",
            "availability_basis": "received", "raw_run_id": f"{RUN}chk", "raw_key": "k", "raw_sha256": "0" * 64,
            "canonical_run_id": "c", "artifact_key": "a", "artifact_sha256": "0" * 64}
    sql = (f"INSERT INTO macro_observation ({', '.join(base)}) VALUES ({', '.join(['%s'] * len(base))})")
    conn.execute(sql, list(base.values()))
    for bad in ({"unit": "percentage_points", "raw_run_id": f"{RUN}u"},
                {"observation_date": "2026-07-29", "raw_run_id": f"{RUN}e"},       # 수신 당일(KST) 관측
                {"available_at": "2026-07-28T00:00:00+00:00", "raw_run_id": f"{RUN}v"},
                {"value": "NaN", "raw_run_id": f"{RUN}n"},                            # NaN 은 PG 에서 최댓값 — 유한성 CHECK
                {"value": "Infinity", "raw_run_id": f"{RUN}i"}):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(sql, list({**base, **bad}.values()))


def test_artifact_expiry_recovery_paths(tmp_path, conn):
    """실행별 artifact(`canonical_run_artifacts/`, S3 30일 만료) 가 사라진 뒤 무엇이 되고 무엇이 안 되는가(§10.3 복구 계약).

    ① 만료된 artifact 를 같은 정제 run 으로 다시 싣기 → 실패(exit 1)·DB 불변. 같은 run_id 재정제(DAG reprocess_slot)는
      "이미 정제 완료" no-op 이라 artifact 를 다시 만들지 않는다 → 이 경로로는 복구 불가.
    ② 같은 raw·같은 코드로 **새 정제 run_id** → artifact 바이트가 같다 → 적재 성공(이미 있던 행은 그대로).
    ③ 적재 전에 만료된 실행도 ②로 싣는다. 단 만료된 옛 정제 run 은 `load --all` 에서 계속 실패로 남는다(정리 도구 없음).
    ④ DB 조회는 artifact 와 무관하다.
    (다른 코드 판의 재정제는 test_renormalizing_the_same_raw_with_different_rules_is_refused 가 고정 — 적재 거부.)
    """
    from data_pipeline.lake import LocalStorage, canonical_run_manifest_key
    from data_pipeline.steps import source_observations as so, source_observations_macro as so_macro

    storage = LocalStorage(tmp_path)
    usd = (FIXTURES / "ecos_usdkrw.json").read_bytes()
    first = _macro_run(storage, "x", usd)
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}x_load", input_run_id=first, pending=False,
                   producer="load_macro") == 0
    count = lambda: conn.execute("SELECT count(*) FROM macro_observation WHERE raw_run_id = %s",
                                 (f"{RUN}x_raw",)).fetchone()[0]
    loaded = count()
    at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    before = _as_of(conn, at)
    manifest = json.loads(storage.get_bytes(canonical_run_manifest_key("macro_observation", first)))
    artifact = tmp_path / manifest["artifact"]["key"]
    artifact.unlink()                                                     # 30일 만료 흉내

    # ① 같은 run 재적재는 실패, 같은 run_id 재정제는 no-op 이라 artifact 가 돌아오지 않는다
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}x_load2", input_run_id=first, pending=False,
                   producer="load_macro") == 1
    assert so.normalize(storage, so_macro.MACRO, first, f"{RUN}x_raw", producer="normalize_macro") == 0
    assert not artifact.exists()
    # ④ DB 조회는 그대로
    assert _as_of(conn, at) == before and count() == loaded

    # ② 같은 raw·같은 코드로 새 정제 run → 같은 바이트 → 적재 성공, 행 수 불변
    assert so.normalize(storage, so_macro.MACRO, f"{RUN}x_norm_r", f"{RUN}x_raw", producer="normalize_macro") == 0
    again = json.loads(storage.get_bytes(canonical_run_manifest_key("macro_observation", f"{RUN}x_norm_r")))
    assert again["artifact"]["sha256"] == manifest["artifact"]["sha256"]
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}x_load3", input_run_id=f"{RUN}x_norm_r", pending=False,
                   producer="load_macro") == 0
    assert count() == loaded and _as_of(conn, at) == before

    # ③ 적재 전에 만료: 옛 정제 run 은 --all 에서 계속 실패, 새 정제 run 으로는 싣는다
    orphan = _macro_run(storage, "y", usd)
    m = json.loads(storage.get_bytes(canonical_run_manifest_key("macro_observation", orphan)))
    (tmp_path / m["artifact"]["key"]).unlink()
    assert so.normalize(storage, so_macro.MACRO, f"{RUN}y_norm_r", f"{RUN}y_raw", producer="normalize_macro") == 0
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}y_load", input_run_id=f"{RUN}y_norm_r", pending=False,
                   producer="load_macro") == 0
    assert conn.execute("SELECT count(*) FROM macro_observation WHERE raw_run_id = %s",
                        (f"{RUN}y_raw",)).fetchone()[0] > 0
    assert so.load(storage, so_macro.MACRO, _db(), f"{RUN}y_all", input_run_id=None, pending=True,
                   producer="load_macro") == 1                           # 만료된 옛 정제 run 이 --all 을 실패시킨다
