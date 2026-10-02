"""ALPHA-1153: 확정 세션의 **미수집(MISSING) 창 일부**만 다시 받는다 — 운영 런북을 실 PostgreSQL 에서 그대로.

2026-10-02 가격 분 세션은 22개 창이 한 번도 수집되지 못한 채(generation 0·attempt 0) MISSING 으로
확정됐다. 회수는 `reopen-minute-session --windows` → `price-worker --session-date`(소급 TR) →
`drain-minute-session` → `qc-minute-session` → `rollup-minute-session` 순서의 수동 실행이다.

`test_minute_session_reopen` 은 원장 API 를 직접 부르고 **커밋됐던 창**을 다시 받는 경우만 본다.
한 번도 커밋되지 않은 창을 **CLI 진입점**(`data_pipeline.run.main`)으로 끝까지 도는 길은 운영에서
처음 밟게 된다 — 그 길을 여기서 먼저 밟는다. 가짜는 KIS HTTP 응답(`urlopen`)뿐이고 설정은 운영과
같은 env 축(`DATA_PIPELINE_*`, `content_v2`)으로 넣는다.

⚠️ 이 회수는 **봉 데이터**만 채운다. 지난 날짜 Worker 는 발행 event 를 만들지 않으므로 가격 판정
기록(`minute_price_judgment`)의 그 창들은 빈 채로 남는다 — 데이터 회수와 판정 공백은 별개다.
"""

import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")

# 지난 거래일 하나(화요일, 5분 롤업 소유 경계 08-10 이후). 날짜가 경로를 고른다 — 지난 날짜여야
# 재오픈이 열리고 Worker 가 소급 TR 을 쓴다.
DAY = "2026-09-22"
YMD = DAY.replace("-", "")
UNITS = {"etf_ids": ["500000"], "constituent_ids": ["100000", "100001"]}
# 10-02 세션에서 실제로 비었던 22개 창(시작 KST HHMM) — 한 구간이 아니라 VALID 창 사이에 흩어져 있다
TARGETS = ("1432,1434,1435,1436,1438,1439,1440,1442,1443,1444,1446,1447,1448,1450,1451,1453,"
           "1455,1456,1458,1503,1504,1506").split(",")
ORDER_ONLY = [f"{1520 + i}" for i in range(10)]  # 15:20~15:29 — 종가 단일가 접수 구간(전 종목 무거래)


class FakeKis:
    """KIS 과거 분봉 TR 대역 — 요청 라벨에서 거슬러 최대 120행, 하루가 끝나면 전 거래일 행이 섞인다."""

    def __init__(self):
        self.absent: set[str] = set()   # 이 라벨(HHMM)의 행을 벤더가 주지 않는다
        self.calls = 0

    def _rows(self, symbol: str) -> list[dict]:
        rows = []
        minute = datetime.strptime("0900", "%H%M")
        while minute.strftime("%H%M") < "1520":
            label = minute.strftime("%H%M")
            # 100001 은 저유동 — 7분에 한 번만 체결된다(나머지 분은 어댑터가 직전가 flat 으로 채운다)
            traded = symbol != "100001" or int(label) % 7 == 0 or label == "0900"
            if traded and label not in self.absent:
                rows.append({"stck_bsop_date": YMD, "stck_cntg_hour": label + "00",
                             "stck_prpr": "1000", "stck_oprc": "1000", "stck_hgpr": "1000",
                             "stck_lwpr": "1000", "cntg_vol": str(int(label)),
                             "acml_tr_pbmn": "1"})
            minute += timedelta(minutes=1)
        return rows

    def urlopen(self, req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        if "tokenP" in url.path:
            return io.BytesIO(json.dumps({"access_token": "tok", "expires_in": 86400}).encode())
        self.calls += 1
        query = dict(urllib.parse.parse_qsl(url.query))
        assert query["FID_INPUT_DATE_1"] == YMD  # 당일 TR 로 물으면 오늘 봉이 온다 — 소급 TR 이어야 한다
        page = [row for row in reversed(self._rows(query["FID_INPUT_ISCD"]))
                if row["stck_cntg_hour"] <= query["FID_INPUT_HOUR_1"]][:120]
        if len(page) < 120:
            page.append({**page[-1], "stck_bsop_date": "20260921"})  # 거래일 경계 = 하루를 다 받았다
        return io.BytesIO(json.dumps({"rt_cd": "0", "msg_cd": "MCA00000", "output2": page}).encode())


def _kst(hhmm: str) -> datetime:
    from data_pipeline.minute.models import KST

    return datetime.strptime(DAY + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=KST)


@pytest.fixture
def sealed(tmp_path, monkeypatch, capsys):
    """10-02 와 같은 모양으로 봉인된 세션: VALID 358 · VALID_EMPTY 10 · MISSING 22(흩어짐).

    전부 CLI 로 만든다. 22개 창만 예정 시각을 미뤄 Worker 가 집지 못하게 한 뒤(운영에선 적체로 못
    집었다) drain → QC 가 MISSING 으로 확정하게 둔다 — 원장을 직접 MISSING 으로 쓰지 않는다.
    ⚠️ 운영의 368창은 당일 경로가 받았지만 여기선 소급 경로다(지난 날짜는 소급 TR 만 쓴다). 그래서
    발행 event 는 처음부터 0건이고, 단언은 "회수 전후로 늘지 않는다"다. 판정 기록도 Consumer 가
    없어 생기지 않으므로, 운영처럼 **이미 판정된 이웃 창** 하나(14:33)에 기록 한 줄을 심어 둔다.
    """
    from data_pipeline.config import DbConfig
    from data_pipeline.db import connect, stable_domain_id
    from data_pipeline.run import main

    db = DbConfig(host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
                  name="edge", user="edge", password="edge", sslmode="disable")
    sid = stable_domain_id("msn", "price_minute", "kis", DAY)
    lake = tmp_path / "lake"
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"universe_version": "recovery-e2e", **UNITS}), encoding="utf-8")
    for key, value in {
        "DB__HOST": db.host, "DB__PORT": str(db.port), "DB__PASSWORD": "edge",
        "DB__SSLMODE": "disable",
        "STORAGE__BACKEND": "local", "STORAGE__LOCAL_ROOT": str(lake),
        "MINUTE_ARTIFACT_FORMAT": "content_v2",   # dev 배포 설정과 같은 형식
        "MINUTE_PRICE_WORKER__APP_KEY": "k", "MINUTE_PRICE_WORKER__APP_SECRET": "s",
        "MINUTE_PRICE_WORKER__TRIGGER_SCHEMA_VERSION": "e2e",
        "MINUTE_PRICE_WORKER__TICK_SECONDS": "0.01",
        "MINUTE_PRICE_WORKER__MIN_INTERVAL_SEC": "0.001",
    }.items():
        monkeypatch.setenv("DATA_PIPELINE_" + key, value)
    monkeypatch.delenv("KIS_TOKEN_CACHE_PARAM", raising=False)
    # CLI 가 거는 SIGTERM 핸들러가 테스트 프로세스에 남지 않게 한다
    monkeypatch.setattr("signal.signal", lambda *args: None)
    kis = FakeKis()
    monkeypatch.setattr("urllib.request.urlopen", kis.urlopen)

    def run(*argv) -> tuple[int, dict]:
        """CLI 한 번 → (exit code, stdout 의 마지막 JSON 줄)."""
        capsys.readouterr()
        code = main(list(argv))
        lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
        return code, (json.loads(lines[-1]) if lines else {})

    def query(sql: str, *params):
        with connect(db) as c:
            return c.execute(sql, params).fetchall()

    def worker() -> int:
        """지난 날짜 Worker 를 DRAINED 까지 돌린다. 운영은 `--max-ticks` 없이 산다 — 여기 상한은
        drain 이 안 걸렸을 때 테스트가 멈추지 않게 하는 것뿐이다."""
        return run("price-worker", "--session-date", DAY, "--universe", str(universe),
                   "--max-ticks", "20000")[0]

    def collect_then_drain(after_drain=lambda: None) -> None:
        """런북의 가운데 토막: Worker 를 띄워 두고, 집을 창이 없어지면 drain 을 건다.

        Worker 가 살아 있는 동안 drain 을 걸어야 한다 — 상한으로 먼저 끝난 Worker 는 세션 fence 를
        반납하지 않아, lease(300초)가 풀릴 때까지 다음 Worker 가 drain 을 ack 하지 못한다.
        """
        drained: list[int] = []

        def drain_when_idle():
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if not query("SELECT 1 FROM minute_ingestion_window WHERE session_id=%s AND "
                             "(data_status='CLAIMED' OR (data_status='DUE' AND scheduled_at <= now())) "
                             "LIMIT 1", sid):
                    drained.append(main(["drain-minute-session", "--session-id", sid]))
                    after_drain()
                    return
                time.sleep(0.02)

        thread = threading.Thread(target=drain_when_idle)
        thread.start()
        code = worker()
        thread.join()
        assert (code, drained) == (0, [0])

    def cleanup():
        with connect(db) as c:
            c.execute("DELETE FROM dataset_commit_outbox WHERE payload->>'session_id'=%s", (sid,))
            for table in ("minute_price_judgment", "price_window_job",
                          "minute_window_artifact_commit", "minute_ingestion_window",
                          "minute_ingestion_session"):
                c.execute(f"DELETE FROM {table} WHERE session_id=%s", (sid,))

    cleanup()
    targets = [_kst(hhmm) for hhmm in TARGETS]
    kis.absent = set(ORDER_ONLY)
    try:
        assert run("plan-minute-session", "--dataset", "price_minute", "--source-group", "kis",
                   "--session-date", DAY, "--universe", str(universe))[0] == 0
        def shift_targets(sign: str) -> None:
            with connect(db) as c:
                c.execute(f"UPDATE minute_ingestion_window SET scheduled_at = scheduled_at {sign} "
                          "interval '100 years' WHERE session_id=%s AND window_start = ANY(%s)",
                          (sid, targets))

        shift_targets("+")
        # drain 뒤에 되돌린다 — DRAINING 은 DUE 를 새로 집지 않으므로 22창은 한 번도 안 집힌 채 남는다
        collect_then_drain(after_drain=lambda: shift_targets("-"))
        code, qc = run("qc-minute-session", "--session-id", sid)
        assert (code, qc["phase"], qc["missing_confirmed"]) == (0, "FINALIZED", len(TARGETS))
        assert run("rollup-minute-session", "--dataset", "price_minute", "--source-group", "kis",
                   "--session-date", DAY)[0] == 0
        with connect(db) as c:
            c.execute(
                "INSERT INTO minute_price_judgment (job_id, redrive_generation, attempt, session_id, "
                "window_start, generation, detection_policy_version, abs_threshold, revert_threshold, "
                "baseline_set_id, summary, anchors_used, tx_anchor, tx_anchor_locked) "
                "SELECT job_id, 0, 1, session_id, window_start, generation, 'e2e', 0.02, 0.01, 'e2e', "
                "'{}', '{}', '{}', false FROM price_window_job WHERE session_id=%s AND window_start=%s",
                (sid, _kst("1433")))
        yield SimpleNamespace(sid=sid, run=run, query=query, kis=kis, lake=lake, targets=targets,
                              worker=worker, collect_then_drain=collect_then_drain)
    finally:
        cleanup()


_WINDOW_FACTS = ("data_status", "generation", "attempt_count", "checksum", "manifest_uri",
                 "manifest_checksum", "expected_unit_count", "succeeded_unit_count",
                 "failed_unit_count", "record_count", "missing_units", "stage_timestamps")


def _windows(h) -> dict:
    """창 시작 → 원장이 그 창에 대해 말하는 것 전부(회수 전후 비교 축). claim·시각 열만 뺀다."""
    return {row[0]: dict(zip(_WINDOW_FACTS, row[1:])) for row in h.query(
        f"SELECT window_start, {', '.join(_WINDOW_FACTS)} FROM minute_ingestion_window "
        "WHERE session_id=%s", h.sid)}


def _files(h) -> dict:
    return {str(path.relative_to(h.lake)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in h.lake.rglob("*") if path.is_file()}


def _session(h):
    return h.query("SELECT phase, final_checksum, final_generation FROM minute_ingestion_session "
                   "WHERE session_id=%s", h.sid)[0]


def _counts(h, table: str) -> int:
    if table == "dataset_commit_outbox":
        return h.query("SELECT count(*) FROM dataset_commit_outbox "
                       "WHERE payload->>'session_id'=%s", h.sid)[0][0]
    return h.query(f"SELECT count(*) FROM {table} WHERE session_id=%s", h.sid)[0][0]


def _judgments(h) -> list:
    return h.query("SELECT * FROM minute_price_judgment WHERE session_id=%s "
                   "ORDER BY job_id, redrive_generation, attempt", h.sid)


def _five_minute_volume(h, unit: str, bucket: str) -> int:
    import pyarrow.parquet as pq

    (path,) = [p for p in h.lake.rglob("*.parquet") if "intraday_5m" in str(p)]
    rows = pq.read_table(path).to_pylist()
    # `ts` 는 naive KST 다(구간 시작 라벨 — `canonical_intraday_5m_key`). 시간대 변환을 걸면 실행
    # 머신의 로컬 시간대로 읽혀 UTC 러너에서 9시간 밀린다.
    (row,) = [r for r in rows if r["ticker"] == unit and r["ts"].strftime("%H%M") == bucket]
    return int(row["volume"])


def _reopen_targets(h) -> dict:
    code, out = h.run("reopen-minute-session", "--session-id", h.sid,
                      "--windows", ",".join(TARGETS), "--reason", "ALPHA-1153 e2e")
    assert code == 0
    return out


def test_scattered_missing_windows_are_recovered_and_nothing_else_moves(sealed):
    """런북 그대로 두 번: 한 번은 연 뒤 중단하고, 다시 열어 끝까지 회수한다.

    회수가 지켜야 하는 것은 셋이다 — ⓐ 멈춰도 안전하다(연 뒤 한 창도 못 받았으면 drain → QC 로
    처음 상태로 돌아간다. 재오픈을 되돌리는 명령이 없어서 이 길이 유일한 되돌림이다) ⓑ 비어 있던
    창이 채워진다 ⓒ **이미 확정된 368창은 그대로**다. ⓒ 가 깨지면 회수가 그날의 다른 창을 새
    세대로 바꾸거나(같은 바이트의 중복 이력) 발행 event 를 내어 지난 날짜의 판정·LLM 을 다시 돌린다.
    """
    h = sealed
    before_windows, before_files = _windows(h), _files(h)
    # 픽스처가 10-02 와 같은 모양인가 — 22창이 "한 번 집혔다 실패한 창"이면 운영의 그 길이 아니다
    statuses = [row["data_status"] for row in before_windows.values()]
    assert {s: statuses.count(s) for s in set(statuses)} == {
        "VALID": 358, "VALID_EMPTY": 10, "MISSING": 22}
    never_collected = {**dict.fromkeys(_WINDOW_FACTS), "data_status": "MISSING", "generation": 0,
                       "attempt_count": 0}
    assert all(before_windows[start] == never_collected for start in h.targets)
    phase, old_final_checksum, _ = _session(h)
    assert phase == "FINALIZED" and old_final_checksum
    before = {table: _counts(h, table) for table in ("dataset_commit_outbox", "price_window_job")}
    judgments = _judgments(h)
    assert len(judgments) == 1                                # 픽스처가 심은 이웃 창의 판정 기록
    # 14:30 버킷은 14:32·14:34 가 비어 3분만으로 만든 부분본이다
    assert _five_minute_volume(h, "100000", "1430") == 1430 + 1431 + 1433

    # ── ⓐ 중단: 연 뒤 수집 없이 drain → QC. 원장도 저장 객체도 처음과 같아야 한다 ──────────────
    reopened = _reopen_targets(h)
    # 옛 final_checksum 이 출력에 남아야 한다 — 확정을 깬 수동 개입의 유일한 감사 근거다
    assert reopened["reopened_windows"] == len(TARGETS)
    assert reopened["previous_final_checksum"] == old_final_checksum
    statuses = [row["data_status"] for row in _windows(h).values()]
    assert statuses.count("DUE") == len(TARGETS) and statuses.count("MISSING") == 0
    assert h.run("drain-minute-session", "--session-id", h.sid)[0] == 0
    assert h.worker() == 0                                    # 한 창도 못 집고 drain 을 ack 만 한다
    code, qc = h.run("qc-minute-session", "--session-id", h.sid)
    assert (code, qc["phase"], qc["missing_confirmed"]) == (0, "FINALIZED", len(TARGETS))
    assert _windows(h) == before_windows and _files(h) == before_files
    assert _session(h)[:2] == ("FINALIZED", old_final_checksum)   # 같은 내용이면 같은 봉인이다
    assert _judgments(h) == judgments

    # ── ⓑⓒ 회수: reopen(22창) → Worker(살아 있는 동안 drain) → QC → rollup ────────────────────
    calls_before = h.kis.calls
    _reopen_targets(h)
    h.collect_then_drain()
    code, qc = h.run("qc-minute-session", "--session-id", h.sid)
    assert (code, qc["phase"], qc["missing_confirmed"]) == (0, "FINALIZED", 0)
    assert h.run("rollup-minute-session", "--dataset", "price_minute", "--source-group", "kis",
                 "--session-date", DAY)[0] == 0

    after_windows, after_files = _windows(h), _files(h)
    # ① 비어 있던 창이 첫 세대로 채워진다. 기대 종목 전부(3)가 실려야 한다 — VALID 만 보면
    #    종목이 빠진 채 허용 결손 안에서 통과한 창을 못 가른다.
    for start in h.targets:
        row = after_windows[start]
        assert (row["data_status"], row["generation"], row["attempt_count"]) == ("VALID", 1, 1)
        assert row["checksum"] and row["manifest_checksum"] and row["manifest_uri"]
        assert (row["expected_unit_count"], row["succeeded_unit_count"], row["failed_unit_count"],
                row["record_count"]) == (3, 3, 0, 3)
    # ② 나머지 368창은 원장도 저장 객체도 그대로다(상태·세대·checksum·시도 수, 파일 바이트).
    untouched = {start: row for start, row in before_windows.items() if start not in h.targets}
    assert {start: after_windows[start] for start in untouched} == untouched
    changed = {path for path, digest in before_files.items() if after_files.get(path) != digest}
    assert all("intraday_5m" in path for path in changed), changed   # 다시 쓰인 것은 5분 파생뿐
    added = set(after_files) - set(before_files)
    assert added and all(any(f"window={hhmm}" in path for hhmm in TARGETS) for path in added), added
    # ③ 지난 날짜는 발행하지 않는다 — job 은 22건 늘되 전달 대상이 아니고, outbox 는 늘지 않는다.
    assert _counts(h, "price_window_job") == before["price_window_job"] + len(TARGETS)
    assert h.query("SELECT DISTINCT delivery_expected FROM price_window_job WHERE session_id=%s "
                   "AND window_start = ANY(%s)", h.sid, h.targets) == [(False,)]
    assert _counts(h, "dataset_commit_outbox") == before["dataset_commit_outbox"]
    # ⑥ 가격 판정 기록은 이 경로로 채워지지도 바뀌지도 않는다 — 봉 회수와 판정 공백은 별개다.
    #    이웃 창의 기존 기록은 행 그대로 남고, 회수한 22창에는 기록이 생기지 않는다.
    assert _judgments(h) == judgments
    # ④ 세션이 새 내용으로 다시 봉인된다. 옛 값이 남으면 QC 가 "이미 확정"으로 읽은 것이다.
    phase, final_checksum, _ = _session(h)
    assert phase == "FINALIZED" and final_checksum not in (None, old_final_checksum)
    # ⑤ 5분 파생이 회수분을 싣는다 — 안 실리면 1분은 고쳐졌는데 소비자는 부분본을 계속 읽는다.
    assert _five_minute_volume(h, "100000", "1430") == sum(range(1430, 1435))
    # 소급 클라이언트는 종목당 하루치를 한 번 받아 캐시한다 — 22창이어도 종목 수 × 페이지 수만 부른다
    assert h.kis.calls - calls_before == 4 + 4 + 1   # 380행 2종 각 4쪽, 저유동 1종 1쪽
