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

⚠️ 소급 TR 은 무거래 분의 행을 주지 않고 어댑터는 그 분을 채우지 않는다(ALPHA-1153 — 종가 단일가
접수 구간도 예외 없음). 그래서 저유동 종목은 회수 뒤에도 대상 분에서 **결손**이고, 그 창은 INCOMPLETE 로
커밋된다. 그 결손은 manifest·원장·artifact 에 남고, 같은 창을 다시 열어 덮을 수 있다. 15:29 창은
15:30 단일가 봉만으로 만들어진다.
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
ORDER_ONLY = [f"{1520 + i}" for i in range(10)]  # 15:20~15:29 — 종가 단일가 접수 구간(전 종목 체결 없음)
AUCTION_PRICE = "1100"    # 15:30 종가 단일가 — 다른 분(1000)과 갈라 놓아야 마감 봉이 어디서 왔는지 보인다
LOW_LIQUIDITY = "100001"
# 대상 분 가운데 저유동 종목이 체결한 분(7의 배수) — 그 밖의 대상 분은 그 종목의 행이 없다
LOW_LIQUIDITY_TRADED = {hhmm for hhmm in TARGETS if int(hhmm) % 7 == 0}


class FakeKis:
    """KIS 과거 분봉 TR 대역 — 요청 라벨에서 거슬러 최대 120행, 하루가 끝나면 전 거래일 행이 섞인다."""

    def __init__(self):
        self.calls = 0
        # True 면 당일 TR 처럼 무거래 분(저유동 종목의 빈 분·접수 구간 15:20~15:29)도 거래량 0·flat
        # 행으로 주고, 15:30 단일가 행은 주지 않는다(당일 TR 은 세션 안에 확정 층으로 안 준다) —
        # **실제 소급 TR 은 이렇게 주지 않는다**. 봉인 단계는 운영의 368창(당일 경로가 벤더의 flat 행을
        # 받았다)을 흉내 내려고 켜고, 회수 단계는 실제 소급 TR 처럼 끈다(10-02 원문: 09:00~15:19 체결 분
        # + 15:30). 후속 회수 시험에선 "그 분의 근거가 나중에 생긴 경우"를 이걸로 표현한다.
        self.explicit_no_trade = False

    def _rows(self, symbol: str) -> list[dict]:
        rows = []
        minute = datetime.strptime("0900", "%H%M")
        while minute.strftime("%H%M") <= "1530":
            label = minute.strftime("%H%M")
            minute += timedelta(minutes=1)
            if label == "1530":
                if self.explicit_no_trade:
                    continue
                traded, price = True, AUCTION_PRICE
            else:
                # 100001 은 저유동 — 7분에 한 번만 체결된다(15:19 는 1519 % 7 == 0 이라 체결이 있다).
                # 접수 구간은 전 종목 체결이 없다
                traded = label not in ORDER_ONLY and (
                    symbol != "100001" or int(label) % 7 == 0 or label == "0900")
                price = "1000"
            if traded or self.explicit_no_trade:
                rows.append({"stck_bsop_date": YMD, "stck_cntg_hour": label + "00",
                             "stck_prpr": price, "stck_oprc": price, "stck_hgpr": price,
                             "stck_lwpr": price, "cntg_vol": str(int(label)) if traded else "0",
                             "acml_tr_pbmn": "1"})
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
    kis.explicit_no_trade = True    # 봉인 단계 = 운영의 당일 수집(벤더가 무거래 분도 flat 행을 준다)
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
        kis.explicit_no_trade = False   # 회수는 실제 소급 TR 처럼 — 무거래 분은 행이 없다
        yield SimpleNamespace(sid=sid, run=run, query=query, kis=kis, lake=lake, targets=targets,
                              worker=worker, collect_then_drain=collect_then_drain, db=db)
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
    return int(_five_minute_bar(h, unit, bucket)["volume"])


def _five_minute_bar(h, unit: str, bucket: str) -> dict:
    import pyarrow.parquet as pq

    (path,) = [p for p in h.lake.rglob("*.parquet") if "intraday_5m" in str(p)]
    rows = pq.read_table(path).to_pylist()
    # `ts` 는 naive KST 다(구간 시작 라벨 — `canonical_intraday_5m_key`). 시간대 변환을 걸면 실행
    # 머신의 로컬 시간대로 읽혀 UTC 러너에서 9시간 밀린다.
    (row,) = [r for r in rows if r["ticker"] == unit and r["ts"].strftime("%H%M") == bucket]
    return row


def _reopen_targets(h, windows=TARGETS) -> dict:
    code, out = h.run("reopen-minute-session", "--session-id", h.sid,
                      "--windows", ",".join(windows), "--reason", "ALPHA-1153 e2e")
    assert code == 0
    return out


def _artifact(h, start, row) -> tuple[dict, dict]:
    """소비 경로 그대로(`read_window_artifact`) 그 창의 봉 → {종목: 레코드}, 그리고 manifest 의 분류."""
    from data_pipeline.lake.storage import LocalStorage
    from data_pipeline.minute.artifact_reader import read_window_artifact

    storage = LocalStorage(h.lake)
    window_end = start + timedelta(minutes=1)
    body = read_window_artifact(
        storage, dataset="price_minute", market="KR", session_id=h.sid, window_start=start,
        window_end=window_end, generation=row["generation"], checksum=row["checksum"],
        manifest_uri=row["manifest_uri"], manifest_checksum=row["manifest_checksum"])
    records = {json.loads(line)["unit_id"]: json.loads(line)
               for line in body.decode("utf-8").splitlines()}
    units = json.loads(storage.get_bytes(row["manifest_uri"]))["units"]
    return records, units


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
    # 결손(INCOMPLETE)은 QC 위반이 아니다 — 세션은 봉인되고, MISSING(한 번도 못 받은 창)은 0 이다
    assert (code, qc["phase"], qc["missing_confirmed"]) == (0, "FINALIZED", 0)
    assert h.run("rollup-minute-session", "--dataset", "price_minute", "--source-group", "kis",
                 "--session-date", DAY)[0] == 0

    after_windows, after_files = _windows(h), _files(h)
    # ① 비어 있던 창이 첫 세대로 커밋된다 — **벤더가 행을 준 종목만** 실린다. 저유동 100001 은
    #    7분에 한 번만 체결되므로 그 밖의 대상 분은 행이 없고, 그 종목은 그 창에서 결손이다
    #    (ALPHA-1153 — 직전가 flat 으로 채워 VALID 로 만들지 않는다). 결손 1/3 은 허용(1%·3종)을
    #    넘으므로 INCOMPLETE 다. 숫자를 창마다 본다 — 상태만 보면 결손을 못 가른다.
    for hhmm, start in zip(TARGETS, h.targets):
        row = after_windows[start]
        assert row["generation"] == 1 and row["attempt_count"] == 1, hhmm
        assert row["checksum"] and row["manifest_checksum"] and row["manifest_uri"], hhmm
        if hhmm in LOW_LIQUIDITY_TRADED:
            assert (row["data_status"], row["succeeded_unit_count"], row["failed_unit_count"],
                    row["record_count"], row["missing_units"]) == ("VALID", 3, 0, 3, None), hhmm
        else:
            assert (row["data_status"], row["succeeded_unit_count"], row["failed_unit_count"],
                    row["record_count"], row["missing_units"]) == (
                "INCOMPLETE", 2, 1, 2, [LOW_LIQUIDITY]), hhmm
        # 소비 경로(`read_window_artifact`)와 manifest 도 같은 말을 해야 한다 — 결손 종목의 봉은
        # 실리지 않고(거래량 0 flat 으로 둔갑하지 않는다), manifest `missing` 에 이름이 남는다.
        records, units = _artifact(h, start, row)
        assert (LOW_LIQUIDITY in records) == (hhmm in LOW_LIQUIDITY_TRADED), hhmm
        assert units["missing"] == ([] if hhmm in LOW_LIQUIDITY_TRADED else [LOW_LIQUIDITY]), hhmm
        assert all(record["volume"] != "0" for record in records.values()), hhmm
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
    assert h.kis.calls - calls_before == 4 + 4 + 1   # 381행(09:00~15:19 + 15:30) 2종 각 4쪽, 저유동 1종 1쪽



def test_partial_recovery_keeps_unconfirmed_minutes_missing_and_can_be_redone(sealed):
    """부분 회수 → 같은 창을 다시 연다(ALPHA-1153).

    첫 회수에서 저유동 종목이 체결하지 않은 대상 분은 결손이다(벤더가 행을 안 줬다 — 무거래인지
    누락인지 모른다). 그 창들(INCOMPLETE)만 `--windows` 로 다시 열어, 이번엔 그 분의 근거가 있는
    응답(거래량 0 행 — 여기선 FakeKis.explicit_no_trade 로 표현)으로 받으면 다음 generation 으로
    덮여 결손이 풀리고, 첫 회수에서 이미 온전했던 창과 회수 대상 밖 창은 그대로다.
    """
    h = sealed
    before_windows = _windows(h)
    _reopen_targets(h)
    h.collect_then_drain()
    assert h.run("qc-minute-session", "--session-id", h.sid)[0] == 0
    first = _windows(h)
    incomplete = [hhmm for hhmm, start in zip(TARGETS, h.targets)
                  if first[start]["data_status"] == "INCOMPLETE"]
    assert incomplete == [hhmm for hhmm in TARGETS if hhmm not in LOW_LIQUIDITY_TRADED]
    outbox, judgments = _counts(h, "dataset_commit_outbox"), _judgments(h)

    h.kis.explicit_no_trade = True
    _reopen_targets(h, incomplete)
    h.collect_then_drain()
    code, qc = h.run("qc-minute-session", "--session-id", h.sid)
    assert (code, qc["phase"], qc["missing_confirmed"]) == (0, "FINALIZED", 0)
    second = _windows(h)
    for hhmm, start in zip(TARGETS, h.targets):
        row = second[start]
        if hhmm in incomplete:
            # 다음 세대로 덮였다 — 결손이 풀리고 저유동 종목은 근거 있는 무거래(거래량 0)로 실린다
            assert (row["data_status"], row["generation"], row["succeeded_unit_count"],
                    row["failed_unit_count"], row["missing_units"]) == ("VALID", 2, 3, 0, None), hhmm
            records, units = _artifact(h, start, row)
            assert records[LOW_LIQUIDITY]["volume"] == "0" and units["no_trade"] == [LOW_LIQUIDITY]
        else:
            assert row == first[start], hhmm              # 첫 회수에서 온전했던 창은 그대로
    untouched = {start: row for start, row in before_windows.items() if start not in h.targets}
    assert {start: second[start] for start in untouched} == untouched
    assert _counts(h, "dataset_commit_outbox") == outbox and _judgments(h) == judgments


def test_recovered_closing_call_minutes_stay_missing_and_close_on_the_auction(sealed):
    """회수 경로에서 소급 응답에 없는 분은 결손 그대로다 — 종가 단일가 접수 구간도(ALPHA-1153).

    ⚠️ 의도가 바뀐 자리다(사용자 결정). 예전엔 접수 구간(15:20~15:29)을 거래소 규칙으로 15:19 종가
    flat 으로 채워 회수해도 VALID_EMPTY 였다. 이제 소급 경로는 벤더가 준 행만 싣는다: 봉인 단계(당일
    경로 흉내, 벤더 flat 행)에서 VALID_EMPTY 였던 그 창들을 다시 열어 실제 소급 TR 처럼 받으면
    15:20~15:28 은 전 종목 결손(INCOMPLETE)이고, 15:29 창은 15:30 단일가 봉만이다(시가=고가=저가=
    종가=단일가, 거래량=단일가 거래량). 5분 15:25 버킷도 그 봉 하나로 만들어진다 — 수동 5분 백필과
    회수 rollup 이 소비자에게 내는 모양이 이것이다.
    """
    h = sealed
    before_windows = _windows(h)
    starts = [_kst(hhmm) for hhmm in ORDER_ONLY]
    assert {before_windows[start]["data_status"] for start in starts} == {"VALID_EMPTY"}
    assert _five_minute_volume(h, "100000", "1525") == 0

    _reopen_targets(h, ORDER_ONLY)
    h.collect_then_drain()
    code, qc = h.run("qc-minute-session", "--session-id", h.sid)
    # 22창은 이미 MISSING 으로 확정돼 있어 이번 QC 가 새로 확정하는 것은 0 이다(아래 untouched 가 본다)
    assert (code, qc["phase"], qc["missing_confirmed"]) == (0, "FINALIZED", 0)
    assert h.run("rollup-minute-session", "--dataset", "price_minute", "--source-group", "kis",
                 "--session-date", DAY)[0] == 0

    after = _windows(h)
    all_units = sorted(UNITS["etf_ids"] + UNITS["constituent_ids"])
    generation = before_windows[starts[0]]["generation"] + 1
    for hhmm, start in zip(ORDER_ONLY[:-1], starts):
        row = after[start]
        # 15:19 봉이 있어도 그 뒤를 채우지 않는다 — 전 종목 결손, 실린 봉 0
        assert (row["data_status"], row["generation"], row["succeeded_unit_count"],
                row["failed_unit_count"], row["record_count"], sorted(row["missing_units"])) == (
            "INCOMPLETE", generation, 0, 3, 0, all_units), hhmm
    last = after[starts[-1]]
    assert (last["data_status"], last["generation"], last["record_count"], last["missing_units"]) == (
        "VALID", generation, 3, None)
    records, units = _artifact(h, starts[-1], last)
    assert sorted(records) == all_units and units["missing"] == []
    for unit, record in records.items():
        assert (record["open"], record["high"], record["low"], record["close"], record["volume"]) == (
            AUCTION_PRICE, AUCTION_PRICE, AUCTION_PRICE, AUCTION_PRICE, "1530"), unit
    bucket = _five_minute_bar(h, "100000", "1525")
    assert (bucket["open"], bucket["close"], int(bucket["volume"])) == (
        float(AUCTION_PRICE), float(AUCTION_PRICE), 1530)
    # 회수 대상 밖 창(368창 + 아직 MISSING 인 22창)은 그대로다
    untouched = {start: row for start, row in before_windows.items() if start not in starts}
    assert {start: after[start] for start in untouched} == untouched
