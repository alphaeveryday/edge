"""원천 관측 레인 로컬 통합 — DAG 가 내는 명령 그대로 plan-run → 9스텝 → reconcile (ALPHA-1130).

Airflow·ECS 없이, DAG 파일의 FAMILIES(태스크 정의·CLI)를 읽어 그 명령을 실제 CLI(`data_pipeline.run.main`)로
순서대로 돌린다. 공급자는 이 프로세스 안의 가짜 HTTP 서버다(설정의 base_url 을 env 로 바꾼다 — 실호출 없음).
확인하는 것: 새 레인의 원장 계획(Airflow 주체, SFN 없음)·스텝 계측·판정 보고가 실제 Postgres 에서 맞물리고,
DAG 명령만으로 세 원천이 DB 까지 착지해 기준시각 조회에 나온다.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)
sys.path.insert(0, str(Path(__file__).parents[1]))

KST = timezone(timedelta(hours=9))
DAG_FILE = Path(__file__).resolve().parents[3] / "airflow" / "dags" / "edge_source_daily.py"


def _families() -> dict:
    source = DAG_FILE.read_text(encoding="utf-8")
    return ast.literal_eval(re.search(r"^FAMILIES = (\{.*?\n\})$", source, re.M | re.S).group(1))


def _vendor_routes(today) -> dict:
    """경로 → 응답 바이트. 날짜는 정기 창(어제까지·최근 14일 접수) 안에 오게 만든다."""
    from source_observation_fakes import NAMES, KOSDAQ, KOSPI, SAMSUNG, shares, statement

    days = [(today - timedelta(days=n)).isoformat() for n in (3, 2, 1)]
    last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y%m")
    year = str(today.year if today.month > 7 else today.year - 1)
    rcept_no, rcept_dt = f"{today - timedelta(days=1):%Y%m%d}000404", f"{today - timedelta(days=1):%Y%m%d}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("CORPCODE.xml", f"<result><list><corp_code>{SAMSUNG['corp_code']}</corp_code>"
                                         f"<corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list></result>")
    flows = {"revenue": (90_000, 175_000), "operating_income": (11_000, 21_000),
             "eps_basic": (1_200, 2_300), "eps_diluted": (1_190, 2_280)}
    fin_statement = json.loads(statement(SAMSUNG, "2026", "11012", "CFS", flows=flows, equity=3_200_000,
                                         rcept_no=rcept_no))
    for line in fin_statement["list"]:
        line["bsns_year"] = year
    share_body = json.loads(shares(SAMSUNG, "2026", "11012", treasury=50))
    for row in share_body["list"]:
        row["rcept_no"] = rcept_no
    return {
        "/ecos/StatisticSearch/E/json/kr/1/10000/731Y003": json.dumps({"StatisticSearch": {"row": [
            {"STAT_CODE": "731Y003", "ITEM_CODE1": "0000003", "ITEM_NAME1": "원/달러(종가 15:30)", "UNIT_NAME": "원",
             "TIME": d.replace("-", ""), "DATA_VALUE": str(1400 + i)} for i, d in enumerate(days)]}}).encode(),
        "/fmp/treasury-rates": json.dumps([{"date": d, "year10": 4.1 + i / 100} for i, d in enumerate(days)]).encode(),
        "/ecos/StatisticSearch/E/json/kr/1/10000/817Y002": json.dumps({"StatisticSearch": {"row": [
            {"STAT_CODE": "817Y002", "ITEM_CODE1": "010210000", "ITEM_NAME1": "국고채(10년)", "UNIT_NAME": "연%",
             "TIME": d.replace("-", ""), "DATA_VALUE": "2.9"} for d in days]}}).encode(),
        "/kosis/Param/statisticsParameterData.do": json.dumps([
            {"ITM_ID": "T03", "ITM_NM": "전년동월비", "C1": "0", "UNIT_NM": "%", "PRD_DE": last_month,
             "DT": "2.2"}]).encode(),
        "/eia/petroleum/pri/spt/data/": json.dumps({"response": {"data": [
            {"period": d, "series": "RBRTE", "value": "70.1", "units": "$/BBL"} for d in days]}}).encode(),
        "/dart/corpCode.xml": buf.getvalue(),
        # 실응답처럼 소급 400일 목록엔 직전 사업보고서가 있다 — 결산월(12월)은 그 행으로 확인된다.
        "/dart/list.json": json.dumps({"status": "000", "total_page": 1, "list": [
            {"corp_code": SAMSUNG["corp_code"], "report_nm": f"반기보고서 ({year}.06)", "rcept_no": rcept_no,
             "rcept_dt": rcept_dt},
            {"corp_code": SAMSUNG["corp_code"], "report_nm": f"사업보고서 ({int(year) - 1}.12)",
             "rcept_no": f"{year}0310000001", "rcept_dt": f"{year}0310"}]}).encode(),
        "/dart/fnlttSinglAcntAll.json": json.dumps(fin_statement).encode(),
        "/dart/stockTotqySttus.json": json.dumps(share_body).encode(),
        "/kis/kospi_code.mst.zip": KOSPI, "/kis/kosdaq_code.mst.zip": KOSDAQ, "/kis/idxcode.mst.zip": NAMES,
    }


@pytest.fixture()
def vendor(tmp_path):
    routes = _vendor_routes(datetime.now(KST).date())
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server 규약
            url = urlparse(self.path)
            calls.append(url.path)
            key = next((k for k in routes if url.path.startswith(k)), None)
            if key == "/dart/fnlttSinglAcntAll.json" and parse_qs(url.query).get("fs_div") == ["OFS"]:
                body = json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."}).encode()
            else:
                body = routes.get(key)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()


def test_dag_commands_run_the_lane_end_to_end_with_the_ledger(tmp_path, vendor, monkeypatch, request):
    import psycopg

    from data_pipeline import db as dp_db
    from data_pipeline import run as dp_run
    from data_pipeline.lake import LocalStorage
    from source_observation_fakes import write_holdings

    base, calls = vendor
    pg = {"host": os.environ["E2E_PGHOST"], "port": int(os.environ.get("E2E_PGPORT", "5432")),
          "dbname": os.environ.get("E2E_PGDATABASE", "edge"), "user": os.environ.get("E2E_PGUSER", "edge"),
          "password": os.environ.get("E2E_PGPASSWORD", "edge")}
    env = {
        "DATA_PIPELINE_STORAGE__LOCAL_ROOT": str(tmp_path / "lake"),
        "DATA_PIPELINE_DB__HOST": pg["host"], "DATA_PIPELINE_DB__PORT": str(pg["port"]),
        "DATA_PIPELINE_DB__NAME": pg["dbname"], "DATA_PIPELINE_DB__USER": pg["user"],
        "DATA_PIPELINE_DB__PASSWORD": pg["password"], "DATA_PIPELINE_DB__SSLMODE": "disable",
        "DATA_PIPELINE_PRICE__SOURCE__API_KEY": "F",
        "DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__FMP_BASE_URL": f"{base}/fmp",
        "DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__ECOS_BASE_URL": f"{base}/ecos",
        "DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__KOSIS_BASE_URL": f"{base}/kosis",
        "DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__EIA_BASE_URL": f"{base}/eia",
        "DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__ECOS_API_KEY": "E",
        "DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__KOSIS_API_KEY": "K",
        "DATA_PIPELINE_SOURCE_OBSERVATIONS__MACRO__EIA_API_KEY": "A",
        "DATA_PIPELINE_SOURCE_OBSERVATIONS__SECTOR__BASE_URL": f"{base}/kis",
        "DATA_PIPELINE_DART_FINANCIAL__SOURCE__BASE_URL": f"{base}/dart",
        "DATA_PIPELINE_DART_FINANCIAL__SOURCE__API_KEY": "D",
        "OPS_KR_HOLIDAYS": "",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # 정기 run 의 슬롯 = 지금(분). DAG 의 edge_run_id 와 같은 규칙으로 run_id 를 만든다.
    slot = datetime.now(KST).replace(second=0, microsecond=0)
    run_key = f"source-daily:{slot:%Y-%m-%dT%H:%M}"
    rid = dp_db.stable_domain_id("run", run_key)
    write_holdings(LocalStorage(tmp_path / "lake"), slot.date().isoformat(), ["005930"])

    def cleanup():
        """이 run 의 판본 행만 지운다 — 같은 DB 를 쓰는 다른 e2e 의 기준시각 조회를 오염시키지 않게."""
        with psycopg.connect(**pg, autocommit=True) as conn:
            for table in ("macro_observation", "financial_metric", "financial_report_version", "sector_classification"):
                conn.execute(f"DELETE FROM {table} WHERE raw_run_id=%s", (rid,))

    cleanup()
    request.addfinalizer(cleanup)
    with psycopg.connect(**pg, autocommit=True) as conn:
        conn.execute("DELETE FROM ops_pipeline_run WHERE run_key=%s", (run_key,))

    for key, value in {"OPS_PIPELINE_TYPE": "source-daily", "OPS_ORCHESTRATOR": "AIRFLOW",
                       "OPS_ORCHESTRATOR_RUN_REF": "edge_source_daily/manual__e2e",
                       "OPS_SCHEDULED_TIME": slot.isoformat()}.items():
        monkeypatch.setenv(key, value)
    assert dp_run.main(["plan-run"]) == 0

    codes = {}
    trading = slot.weekday() < 5
    for family, stages in _families().items():
        for stage in ("collect", "normalize", "load"):
            _, _taskdef, cli = stages[stage]
            argv = [cli, "--run-id", rid] + ([] if stage == "collect" else ["--input-run-id", rid])
            if stage == "collect" and family in ("macro", "financial"):
                argv += ["--from", "", "--to", ""]        # DAG 가 params 를 비워 넘기는 정기 run
            # EdgeStep 이 붙이는 실행권 env 와 ECS 가 주는 태스크 ARN 의 대역(lab 과 같은 방식).
            monkeypatch.setenv("OPS_EXCLUSIVE_STEP", "1")
            monkeypatch.setenv("OPS_ECS_TASK_ARN", f"arn:local:ecs/{family}_{stage}")
            monkeypatch.setenv("OPS_ORCHESTRATOR_ATTEMPT_REF", f"airflow:edge_source_daily/e2e/{family}_{stage}/1")
            codes[f"{family}_{stage}"] = dp_run.main(argv)
            monkeypatch.delenv("OPS_EXCLUSIVE_STEP")
    assert codes == {k: 0 for k in codes}, codes

    for key, value in {"OPS_RUN_KEY": run_key, "OPS_ORCHESTRATION_STATUS": "SUCCEEDED",
                       "OPS_REPORT_RUN_REF": "edge_source_daily/manual__e2e"}.items():
        monkeypatch.setenv(key, value)

    class NoEcs:
        """AWS 에 닿지 않는 ECS 대역. 모든 시도의 exit 가 원장에 있으므로 조회가 필요 없어야 한다."""

        def describe_tasks(self, **kwargs):
            raise AssertionError(f"원장에 종료 증거가 있는데 ECS 를 조회했다: {kwargs}")

    class NoSfn:
        """이 레인은 SFN 이 없다 — 어떤 SFN 호출도 결함이다."""

        def __getattr__(self, name):
            raise AssertionError(f"Airflow 전용 레인 대조가 SFN.{name} 을 불렀다")

    from data_pipeline.ops import aws as ops_aws
    monkeypatch.setattr(ops_aws, "ecs_client", lambda: NoEcs())
    monkeypatch.setattr(ops_aws, "stepfunctions_client", lambda: NoSfn())
    assert dp_run.main(["reconcile"]) == 0

    now = datetime.now(timezone.utc)
    with psycopg.connect(**pg, autocommit=True) as conn:
        run = conn.execute("SELECT pipeline_type, orchestration_status FROM ops_pipeline_run WHERE run_key=%s",
                           (run_key,)).fetchone()
        assert run == ("source-daily", "SUCCEEDED")
        expected = dict(conn.execute(
            "SELECT e.task_key, e.plan_status FROM ops_expected_task e JOIN ops_pipeline_run r"
            " ON r.pipeline_run_id = e.pipeline_run_id WHERE r.run_key=%s", (run_key,)).fetchall())
        assert set(expected) == {spec[0] for stages in _families().values() for spec in stages.values()}
        assert conn.execute("SELECT count(*) FROM macro_observations_as_of(%s, 'usd_krw', 2)",
                            (now,)).fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM macro_observation WHERE raw_run_id=%s", (rid,)).fetchone()[0] == 13
        quarters = conn.execute("SELECT period, eps::text, fs_basis FROM financial_quarters_as_of(%s, '005930')",
                                (now,)).fetchall()
        assert ("1200", "CFS") in {(eps, basis) for _, eps, basis in quarters}
        found = conn.execute("SELECT found FROM sector_classification_as_of(%s, ARRAY['005930'])",
                             (now,)).fetchone()[0]
        assert found is trading      # 비거래일에는 업종 마스터를 받지 않는다
    assert not any(path.startswith("/kis/") for path in calls) or trading
