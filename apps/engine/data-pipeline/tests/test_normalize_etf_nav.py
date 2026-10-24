"""normalize_etf_nav 테스트 — 정규화·게이트·멱등 병합·격리.

게이트가 하는 일이 '나쁜 NAV 를 canonical 에 못 들어가게 막는 것'이라, 통과 케이스보다
**나쁜 값이 passed 로 인증되지 않는지**를 더 촘촘히 본다(각도 H coerce-to-passing).
"""

import hashlib
import json

import pytest

from data_pipeline.lake import (
    LocalStorage,
    canonical_etf_nav_partition,
    canonical_run_manifest_key,
    canonical_run_partition_key,
)
from data_pipeline.steps import normalize_etf_nav

# 라이브 실측 KIS 응답 행 + 수집 provenance(sources/kis_nav.py 가 붙이는 4개). 전 필드 문자열.
def _kis_nav_row(**over):
    row = {
        "stck_bsop_date": "20260716", "stck_clpr": "109000", "prdy_vrss": "-7735",
        "prdy_ctrt": "-6.63", "acml_vol": "20103895", "dprt": "0.23",
        "nav_vrss_prpr": "253.67", "nav": "108746.33", "nav_prdy_ctrt": "-7.17",
        "our_etf_id": "069500", "market": "KR", "kis_symbol": "069500",
        "fetched_at": "2026-07-20T06:00:00+00:00",
    }
    row.update(over)
    return row


def _raw_key(run_id="R1", date="2026-07-20", source="kis", market="KR"):
    # 경로를 빌더가 아니라 f-string 으로 조립한다 — 경로 규약이 깨지면 이 테스트가 잡는다.
    return (f"raw/source={source}/dataset=etf_nav/market={market}"
            f"/ingest_date={date}/run_id={run_id}/part-00000.ndjson")


def _write_raw(storage, key, rows):
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    storage.put_bytes(key, body.encode("utf-8"))


def _quality_log(storage, run_id=None):
    keys = list(storage.list_keys("operations_archive/data_quality_logs/"))
    if run_id is not None:
        keys = [key for key in keys if f"/run_id={run_id}/" in key]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _canonical_rows(storage, market="KR", trade_date="2026-07-16"):
    prefix = canonical_etf_nav_partition(market, trade_date)
    rows = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(normalize_etf_nav._read_parquet_rows(storage.get_bytes(key)))
    return rows


def _manifest(storage, run_id="N1"):
    return json.loads(storage.get_bytes(canonical_run_manifest_key("etf_nav", run_id)))


def test_문자열_nav_가_수치로_정규화돼_canonical_에_들어간다(tmp_path):
    # WHY: KIS 는 전 필드를 문자열로 준다. 여기서 수치로 못 바꾸면 마트(NUMERIC(24,8)) 적재가
    #      ALPHA-383 에서 터진다 — 캐스팅 책임이 이 스텝에 있다는 계약을 값으로 고정한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_nav_row()])

    assert normalize_etf_nav.run(storage, "N1") == 0

    rows = _canonical_rows(storage)
    assert len(rows) == 1
    assert rows[0]["nav"] == pytest.approx(108746.33)
    assert rows[0]["etf_id"] == "069500"
    assert rows[0]["trade_date"] == "2026-07-16"  # YYYYMMDD → ISO
    assert rows[0]["currency"] == "KRW"
    assert rows[0]["source_vendor"] == "kis"
    # 참고 필드는 canonical 로 넘기지 않는다(파생 지표는 다운스트림 소관).
    assert "dprt" not in rows[0] and "stck_clpr" not in rows[0]


@pytest.mark.parametrize(
    "bad_nav,reason",
    [
        ("0", "non_positive_nav"),        # 마트 CHECK(nav > 0) 위반
        ("-1.5", "non_positive_nav"),
        ("nan", "missing_nav"),           # float('nan') 은 모든 비교가 False — 게이트를 조용히 통과한다
        ("inf", "missing_nav"),
        ("", "missing_nav"),
        ("N/A", "missing_nav"),
        (True, "missing_nav"),            # float(True)=1.0 이 양수라 통과해버린다
        (None, "missing_nav"),
        # finite 지만 마트 NUMERIC(24,8) 정수부(16자리)를 넘는 값 — CHECK 는 통과하고
        # INSERT 만 터지는 구간이라 게이트가 안 보면 적재에서야 발견된다.
        ("1e308", "nav_out_of_range"),
        ("10000000000000000", "nav_out_of_range"),
    ],
)
def test_나쁜_nav_는_통과로_인증되지_않는다(tmp_path, bad_nav, reason):
    # WHY: 이 게이트의 존재 이유가 '나쁜 값을 records_passed 로 세지 않는 것'이다. NaN/Inf/bool 은
    #      수치 비교를 조용히 통과하는 대표적 coerce-to-passing 경로라 값으로 못박는다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_nav_row(nav=bad_nav)])

    assert normalize_etf_nav.run(storage, "N1") == 2

    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert log["records_failed"] == 1
    assert reason in log["failures"][0]["reasons"]
    assert _canonical_rows(storage) == []


@pytest.mark.parametrize("bad_date", ["20260231", "202671", "", "2026-07-16", "20261301"])
def test_비달력일_미패딩_거래일은_막힌다(tmp_path, bad_date):
    # WHY: strptime 은 미패딩('202671')을 관대하게 받아 엉뚱한 파티션을 만든다. 왕복 검증이
    #      빠지면 존재하지 않는 거래일의 NAV 가 canonical 에 생긴다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_nav_row(stck_bsop_date=bad_date)])

    assert normalize_etf_nav.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert "missing_trade_date" in log["failures"][0]["reasons"]


def test_미래_거래일은_bad_trade_date_로_막힌다(tmp_path):
    # WHY: 파싱은 되지만 범위 밖인 날짜('20991231')가 passed 로 인증되면 엉뚱한 미래 파티션이 생긴다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_nav_row(stck_bsop_date="20991231")])

    assert normalize_etf_nav.run(storage, "N1") == 2
    assert "bad_trade_date" in _quality_log(storage)["failures"][0]["reasons"]


def test_날짜창_백필로_같은_거래일이_두_run_에_와도_멱등이다(tmp_path):
    # WHY: 수집이 --from/--to 로 구간 전체를 받으므로 같은 (etf_id,trade_date)가 여러 run 의 raw 에
    #      반드시 중복 유입된다(이 파이프라인의 정상 동작). 병합이 없으면 canonical 이 중복으로
    #      부풀고 마트 PK(etf_instrument_id,trade_date) 적재가 깨진다. 최신 fetched_at 이 이긴다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R1"),
               [_kis_nav_row(nav="100.0", fetched_at="2026-07-20T06:00:00+00:00")])
    _write_raw(storage, _raw_key(run_id="R2"),
               [_kis_nav_row(nav="200.0", fetched_at="2026-07-21T06:00:00+00:00")])

    assert normalize_etf_nav.run(storage, "N1") == 0
    first = _canonical_rows(storage)
    assert normalize_etf_nav.run(storage, "N2") == 0
    second = _canonical_rows(storage)

    assert len(first) == 1 and first == second      # 중복 0, 2런 수렴
    assert first[0]["nav"] == pytest.approx(200.0)  # 최신 fetched_at 이 이긴다
    parts = [k for k in storage.list_keys("canonical/") if k.endswith(".parquet")]
    assert len(parts) == 1, "part 가 누적되면 병합이 아니라 중복이 쌓인다"


def test_input_run_id_는_그_수집런의_raw_만_읽는다(tmp_path):
    # WHY: SFN 상시 경로가 이 인자로 돈다(ALPHA-389). 필터가 끊기면 매 런이 전체 raw 를 재정제해
    #      런타임이 유니버스에 비례해 늘고, 스코프 런이라는 계약이 무너진다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R1"), [_kis_nav_row(our_etf_id="069500")])
    _write_raw(storage, _raw_key(run_id="R2"), [_kis_nav_row(our_etf_id="091160")])

    assert normalize_etf_nav.run(storage, "N1", "R2") == 0

    log = _quality_log(storage)
    assert log["raw_files"] == 1
    assert log["input_run_id"] == "R2"
    assert [r["etf_id"] for r in _canonical_rows(storage)] == ["091160"]


def test_깨진_행은_격리되고_남은_행은_수집된다(tmp_path):
    # WHY: 비객체 행(null·배열)은 _normalize 의 record.get 에서 AttributeError 로 배치를 통째로
    #      죽인다. 행 단위 격리가 없으면 raw 한 줄이 그날 정제 전체를 날린다.
    storage = LocalStorage(tmp_path / "lake")
    key = _raw_key()
    body = "\n".join([
        json.dumps(_kis_nav_row(our_etf_id="069500")),
        "{not json",
        "null",
        "[1, 2]",
        json.dumps(_kis_nav_row(our_etf_id="091160")),
    ]) + "\n"
    storage.put_bytes(key, body.encode("utf-8"))

    assert normalize_etf_nav.run(storage, "N1") == 2

    log = _quality_log(storage)
    assert log["records_read"] == 5
    assert log["records_passed"] == 2
    reasons = [r for f in log["failures"] for r in f["reasons"]]
    assert reasons.count("unparseable_json") == 1
    assert reasons.count("non_object_row") == 2
    assert sorted(r["etf_id"] for r in _canonical_rows(storage)) == ["069500", "091160"]
    assert _manifest(storage)["canonical_written"] is True
    assert _manifest(storage)["canonical_partitions"][0]["winner_ids"] == [
        {"etf_id": "069500"}, {"etf_id": "091160"},
    ]


def test_알수없는_벤더는_사유로_드러난다(tmp_path):
    # WHY: 벤더 판별은 raw 키의 source= 로만 한다. 새 벤더가 붙었는데 정규화 매핑이 없으면
    #      조용히 통과시키지 않고 unsupported_vendor 로 표면화해야 한다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(source="krx"), [_kis_nav_row()])

    assert normalize_etf_nav.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert log["failures"][0]["reasons"] == ["unsupported_vendor"]


def test_KR_아닌_시장은_막힌다(tmp_path):
    # WHY: NAV 수집은 KIS 단일 벤더·KR 고정이다(ADR-0024). 다른 시장 행이 흘러들면 잘못 라우팅된
    #      데이터이므로 통화 계약도 못 만든다 — 조용히 통과시키면 마트에 시장 미상 NAV 가 쌓인다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(market="US"), [_kis_nav_row(market="US")])

    assert normalize_etf_nav.run(storage, "N1") == 2
    assert "unsupported_market" in _quality_log(storage)["failures"][0]["reasons"]


class _TrackingStorage:
    def __init__(self, inner, *, fail_put=None, corrupt_completed=False):
        self.inner = inner
        self.fail_put = fail_put
        self.corrupt_completed = corrupt_completed
        self.events = []

    def list_keys(self, prefix):
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        data = self.inner.get_bytes(key)
        completed = None
        if "canonical_run_manifests" in key:
            completed = json.loads(data).get("canonical_written")
            if self.corrupt_completed and completed is True:
                data = b"{}"
        self.events.append(("get", key, completed))
        return data

    def put_bytes(self, key, data):
        completed = None
        if "canonical_run_manifests" in key:
            completed = json.loads(data).get("canonical_written")
        self.events.append(("put", key, completed))
        if self.fail_put and self.fail_put in key:
            raise OSError("의도된 저장 실패")
        return self.inner.put_bytes(key, data)

    def get_bytes_with_version(self, key):
        data, version = self.inner.get_bytes_with_version(key)
        if data is not None and "canonical_run_manifests" in key:
            completed = json.loads(data).get("canonical_written")
            if self.corrupt_completed and completed is True:
                data = b"{}"
        return data, version

    def put_bytes_if_version(self, key, data, version):
        completed = None
        if "canonical_run_manifests" in key:
            completed = json.loads(data).get("canonical_written")
        self.events.append(("cas", key, completed, version))
        if self.fail_put and self.fail_put in key:
            raise OSError("의도된 저장 실패")
        return self.inner.put_bytes_if_version(key, data, version)


def test_manifest는_재확정과_최신_winner를_정렬해_직접키_sha로_기록한다(tmp_path):
    # WHY(ALPHA-1042): consumer 범위는 값 변경분이 아니라 이번 실행의 성공 winner이며,
    # 파티션·ID 순서와 direct object digest가 결정적이어야 재시도와 무결성 검증이 가능하다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R0"), [_kis_nav_row()])
    assert normalize_etf_nav.run(storage, "N0", "R0") == 0
    _write_raw(storage, _raw_key(run_id="R1"), [
        _kis_nav_row(our_etf_id="091160"),
        _kis_nav_row(),
        _kis_nav_row(),
        _kis_nav_row(our_etf_id="091160", nav="200", fetched_at="2026-07-20T07:00:00+00:00"),
        _kis_nav_row(our_etf_id="000660", stck_bsop_date="20260715"),
    ])

    assert normalize_etf_nav.run(storage, "N1", "R1") == 0
    parts = _manifest(storage)["canonical_partitions"]
    assert [(p["trade_date"], p["winner_ids"]) for p in parts] == [
        ("2026-07-15", [{"etf_id": "000660"}]),
        ("2026-07-16", [{"etf_id": "069500"}, {"etf_id": "091160"}]),
    ]
    for part in parts:
        assert part["key"] == canonical_run_partition_key(
            "etf_nav", "N1", part["trade_date"],
        )
        assert part["sha256"] == hashlib.sha256(storage.get_bytes(part["key"])).hexdigest()
    rows = {row["etf_id"]: row for row in _canonical_rows(storage)}
    assert rows["091160"]["nav"] == pytest.approx(200)


def test_오래된_현재행은_canonical_winner_계보를_주장하지_않는다(tmp_path):
    # WHY(ALPHA-1042): 앞 런의 더 최신 NAV가 canonical에 있으면 늦게 도착한 과거 관측은
    # 그 값을 재확인하지 않았다. manifest에 ID를 넣으면 consumer가 최신값에 현재 run_id를
    # stamp해 거짓 lineage를 만든다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R-new"), [
        _kis_nav_row(nav="200", fetched_at="2026-07-21T06:00:00+00:00"),
    ])
    assert normalize_etf_nav.run(storage, "N-new", "R-new") == 0
    _write_raw(storage, _raw_key(run_id="R-old"), [
        _kis_nav_row(nav="100", fetched_at="2026-07-20T06:00:00+00:00"),
    ])

    assert normalize_etf_nav.run(storage, "N-old", "R-old") == 0
    assert _manifest(storage, "N-old")["canonical_partitions"] == []
    assert _canonical_rows(storage)[0]["nav"] == pytest.approx(200)
    assert _quality_log(storage, "N-old")["superseded_current_rows"] == [{
        "market": "KR", "etf_id": "069500", "trade_date": "2026-07-16",
        "current_fetched_at": "2026-07-20T06:00:00+00:00",
        "canonical_fetched_at": "2026-07-21T06:00:00+00:00",
        "reason": "superseded_by_canonical",
    }]


def test_run_scoped_manifest는_shared_canonical_덮어쓰기후에도_불변이다(tmp_path):
    # WHY(ALPHA-1042): 다음 normalize가 날짜 canonical을 덮어써도 먼저 완료된 manifest의
    # direct key와 SHA는 유지돼야 앞 consumer의 재시도가 경합 없이 같은 winner를 읽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R1"), [
        _kis_nav_row(nav="100", fetched_at="2026-07-20T06:00:00+00:00"),
    ])
    assert normalize_etf_nav.run(storage, "N1", "R1") == 0
    old_part = _manifest(storage, "N1")["canonical_partitions"][0]
    old_bytes = storage.get_bytes(old_part["key"])

    _write_raw(storage, _raw_key(run_id="R2"), [
        _kis_nav_row(nav="200", fetched_at="2026-07-21T06:00:00+00:00"),
    ])
    assert normalize_etf_nav.run(storage, "N2", "R2") == 0

    assert storage.get_bytes(old_part["key"]) == old_bytes
    assert normalize_etf_nav._read_parquet_rows(old_bytes)[0]["nav"] == pytest.approx(100)
    assert _canonical_rows(storage)[0]["nav"] == pytest.approx(200)


def test_shared_canonical_CAS경합은_최신판을_다시_병합한다(tmp_path):
    # WHY(ALPHA-1042): 두 producer가 같은 이전판을 읽고 무조건 PUT하면 후착 writer가 선착
    # writer의 ETF를 잃는다. 조건부 write 실패 뒤 최신판을 다시 읽어 둘 다 보존해야 한다.
    inner = LocalStorage(tmp_path / "lake")
    competitor = normalize_etf_nav._write_parquet_rows([{
        "market": "KR", "etf_id": "091160", "trade_date": "2026-07-16", "nav": 200.0,
        "currency": "KRW", "source_vendor": "kis",
        "fetched_at": "2026-07-20T07:00:00+00:00",
    }])

    class InjectRaceStorage(_TrackingStorage):
        def __init__(self, storage):
            super().__init__(storage)
            self.injected = False

        def put_bytes_if_version(self, key, data, version):
            if not self.injected and key.startswith("canonical/"):
                self.injected = True
                self.inner.put_bytes(key, competitor)
            return super().put_bytes_if_version(key, data, version)

    _write_raw(inner, _raw_key(run_id="R1"), [_kis_nav_row(our_etf_id="069500")])
    storage = InjectRaceStorage(inner)

    assert normalize_etf_nav.run(storage, "N1", "R1") == 0
    assert [row["etf_id"] for row in _canonical_rows(inner)] == ["069500", "091160"]


def test_동일최신시각의_상이nav는_그_id만_제외하고_exit2다(tmp_path):
    # WHY(ALPHA-1042): 동일 vendor·시각이 서로 다른 fact를 주장하면 입력 순서로 고르는 것은
    # 비결정적 오염이다. 충돌 ID만 제외하고 같은 파티션의 성공 winner는 보존한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [
        _kis_nav_row(nav="100"), _kis_nav_row(nav="200"),
        _kis_nav_row(our_etf_id="091160"),
    ])

    assert normalize_etf_nav.run(storage, "N1", "R1") == 2
    [part] = _manifest(storage)["canonical_partitions"]
    assert part["winner_ids"] == [{"etf_id": "091160"}]
    log = _quality_log(storage)
    assert log["same_timestamp_conflicts"][0]["etf_id"] == "069500"
    assert log["ops"] == {"records_out": 1, "failed_records": 1}


def test_빈_입력은_유효한_빈_완료_manifest다(tmp_path):
    # WHY(ALPHA-1042): 정상 0건과 manifest 결손을 구분해야 consumer가 전체 scan으로 넓히지 않는다.
    storage = LocalStorage(tmp_path / "lake")
    assert normalize_etf_nav.run(storage, "N1", "EMPTY") == 0
    assert _manifest(storage) == {
        "run_id": "N1", "producer": "normalize_etf_nav", "canonical_written": True,
        "canonical_partitions": [], "producer_exit_code": 0,
    }


def test_manifest_commit순서와_동일run_멱등성을_고정한다(tmp_path):
    # WHY(ALPHA-1042): CAS claim 뒤 canonical→quality 순서로만 완료를 공개하고, 같은 run
    # 재시도는 완료 artifact/manifest를 그대로 재사용해야 consumer 입력이 흔들리지 않는다.
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key(), [_kis_nav_row()])
    storage = _TrackingStorage(inner)
    assert normalize_etf_nav.run(storage, "N1", "R1") == 0
    first = inner.get_bytes(canonical_run_manifest_key("etf_nav", "N1"))
    assert normalize_etf_nav.run(storage, "N1", "R1") == 0
    assert inner.get_bytes(canonical_run_manifest_key("etf_nav", "N1")) == first
    assert storage.events[0] == (
        "cas", canonical_run_manifest_key("etf_nav", "N1"), False, None,
    )
    canonical_put = next(
        i for i, e in enumerate(storage.events)
        if e[0] in ("put", "cas") and e[1].startswith("canonical/")
    )
    quality_put = next(i for i, e in enumerate(storage.events) if e[0] == "put" and "data_quality_logs" in e[1])
    completed_put = next(
        i for i, e in enumerate(storage.events)
        if e[:3] == ("cas", canonical_run_manifest_key("etf_nav", "N1"), True)
    )
    assert canonical_put < quality_put < completed_put


def test_완료된_동일run은_입력이_바뀌어도_artifact와_manifest를_덮어쓰지_않는다(tmp_path):
    # WHY(ALPHA-1042): run_id는 immutable snapshot의 정체성이다. 같은 수집 key의 내용이
    # 뒤늦게 바뀌어도 완료 run을 다시 계산하면 이미 공개된 SHA 계약이 깨진다.
    storage = LocalStorage(tmp_path / "lake")
    raw_key = _raw_key()
    _write_raw(storage, raw_key, [_kis_nav_row(nav="100")])
    assert normalize_etf_nav.run(storage, "N1", "R1") == 0
    manifest_key = canonical_run_manifest_key("etf_nav", "N1")
    manifest_before = storage.get_bytes(manifest_key)
    [part] = _manifest(storage)["canonical_partitions"]
    artifact_before = storage.get_bytes(part["key"])

    _write_raw(storage, raw_key, [_kis_nav_row(nav="999")])
    assert normalize_etf_nav.run(storage, "N1", "R1") == 0

    assert storage.get_bytes(manifest_key) == manifest_before
    assert storage.get_bytes(part["key"]) == artifact_before
    assert normalize_etf_nav._read_parquet_rows(artifact_before)[0]["nav"] == pytest.approx(100)


def test_storage와_manifest_무결성_실패는_exit1이고_완료를_남기지_않는다(tmp_path):
    # WHY(ALPHA-1042): canonical/quality/manifest 중 하나라도 신뢰할 수 없으면 부분 성공이
    # 아니라 fatal이며, 같은 run의 이전 completed marker도 공개돼서는 안 된다.
    for failing_prefix in (
        "canonical/market_data/etf_nav", "canonical_run_artifacts", "data_quality_logs",
    ):
        inner = LocalStorage(tmp_path / failing_prefix.replace("/", "_"))
        _write_raw(inner, _raw_key(), [_kis_nav_row()])
        assert normalize_etf_nav.run(_TrackingStorage(inner, fail_put=failing_prefix), "N1", "R1") == 1
        assert _manifest(inner)["canonical_written"] is False
        assert _manifest(inner)["retryable"] is True
        assert normalize_etf_nav.run(inner, "N1", "R1") == 0

    inner = LocalStorage(tmp_path / "manifest_corrupt")
    _write_raw(inner, _raw_key(), [_kis_nav_row()])
    assert normalize_etf_nav.run(_TrackingStorage(inner, corrupt_completed=True), "N1", "R1") == 1
    assert _manifest(inner)["canonical_written"] is False
    assert _manifest(inner)["retryable"] is True
    assert _quality_log(inner)["exit_code"] == 1
    assert normalize_etf_nav.run(inner, "N1", "R1") == 0


def test_fresh_manifest_claim_경쟁자는_run_scoped출력을_건드리지_않는다(tmp_path):
    # WHY(ALPHA-1042): 같은 run_id의 claim 패자가 계속 실행하면 승자의 단일 quality log를
    # 실패 로그로 덮을 수 있다. 소유권을 못 얻은 시도는 raw/canonical/log 쓰기 전에 끝나야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_nav_row()])
    manifest_key = canonical_run_manifest_key("etf_nav", "N1")
    attempt_id, claimed_at, version, completed_exit = normalize_etf_nav._claim_manifest(
        storage, manifest_key, "N1",
    )
    assert all(value is not None for value in (attempt_id, claimed_at, version))
    assert completed_exit is None

    assert normalize_etf_nav.run(storage, "N1", "R1") == 1

    assert storage.list_keys("canonical/") == []
    assert storage.list_keys("operations_archive/data_quality_logs/") == []
    assert _manifest(storage)["attempt_id"] == attempt_id


def test_비정상종료한_stale_claim은_CAS로_인수해_완료한다(tmp_path):
    # WHY(ALPHA-1042): 프로세스가 claim 직후 강제 종료되면 retryable 표시를 못 남긴다.
    # 실행 최대시간보다 오래된 lease는 같은 run_id 재시도가 인수해야 영구 고착되지 않는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_nav_row()])
    manifest_key = canonical_run_manifest_key("etf_nav", "N1")
    storage.put_bytes(manifest_key, normalize_etf_nav._manifest_bytes(
        "N1", False, [], attempt_id="dead-attempt",
        claimed_at="2000-01-01T00:00:00+00:00",
    ))

    assert normalize_etf_nav.run(storage, "N1", "R1") == 0
    assert _manifest(storage)["canonical_written"] is True


def test_raw_읽기_fatal은_부분artifact를_동결하지_않고_재시도한다(tmp_path):
    # WHY(ALPHA-1042): 일부 raw만 읽힌 상태에서 immutable artifact를 만들면 회복된 재시도가
    # 완전한 bytes를 쓸 수 없어 영구 충돌한다. fatal 시 snapshot 자체를 미뤄야 한다.
    inner = LocalStorage(tmp_path / "lake")
    good_key = _raw_key(run_id="R1", date="2026-07-20")
    failed_key = _raw_key(run_id="R1", date="2026-07-21")
    _write_raw(inner, good_key, [_kis_nav_row(our_etf_id="069500")])
    _write_raw(inner, failed_key, [_kis_nav_row(our_etf_id="091160")])

    class FailRawOnce(_TrackingStorage):
        def __init__(self, storage):
            super().__init__(storage)
            self.failed = False

        def get_bytes(self, key):
            if key == failed_key and not self.failed:
                self.failed = True
                raise OSError("일시 raw 읽기 실패")
            return super().get_bytes(key)

    storage = FailRawOnce(inner)
    assert normalize_etf_nav.run(storage, "N1", "R1") == 1
    assert inner.list_keys("operations_archive/canonical_run_artifacts/") == []

    assert normalize_etf_nav.run(storage, "N1", "R1") == 0
    assert _manifest(inner)["canonical_partitions"][0]["winner_ids"] == [
        {"etf_id": "069500"}, {"etf_id": "091160"},
    ]


def test_quality실패_뒤_shared가_전진해도_기존_run_snapshot으로_완료한다(tmp_path):
    # WHY(ALPHA-1042): artifact 확정 뒤 quality 저장만 실패할 수 있다. 그 사이 다음 run이
    # shared canonical을 갱신해도 재시도는 앞 run의 snapshot/SHA를 바꾸면 안 된다.
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key(run_id="R1"), [
        _kis_nav_row(nav="100", fetched_at="2026-07-20T06:00:00+00:00"),
    ])
    failing = _TrackingStorage(inner, fail_put="data_quality_logs")
    assert normalize_etf_nav.run(failing, "N1", "R1") == 1
    run_key = canonical_run_partition_key("etf_nav", "N1", "2026-07-16")
    old_bytes = inner.get_bytes(run_key)

    _write_raw(inner, _raw_key(run_id="R2"), [
        _kis_nav_row(nav="200", fetched_at="2026-07-21T06:00:00+00:00"),
    ])
    assert normalize_etf_nav.run(inner, "N2", "R2") == 0

    assert normalize_etf_nav.run(inner, "N1", "R1") == 0
    [part] = _manifest(inner, "N1")["canonical_partitions"]
    assert part["sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert inner.get_bytes(run_key) == old_bytes
    assert normalize_etf_nav._read_parquet_rows(old_bytes)[0]["nav"] == pytest.approx(100)
    assert _canonical_rows(inner)[0]["nav"] == pytest.approx(200)
