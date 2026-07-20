"""normalize_etf_nav 테스트 — 정규화·게이트·멱등 병합·격리.

게이트가 하는 일이 '나쁜 NAV 를 canonical 에 못 들어가게 막는 것'이라, 통과 케이스보다
**나쁜 값이 passed 로 인증되지 않는지**를 더 촘촘히 본다(각도 H coerce-to-passing).
"""

import json

import pytest

from data_pipeline.lake import LocalStorage, canonical_etf_nav_partition
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


def _quality_log(storage):
    keys = list(storage.list_keys("operations_archive/data_quality_logs/"))
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _canonical_rows(storage, market="KR", trade_date="2026-07-16"):
    prefix = canonical_etf_nav_partition(market, trade_date)
    rows = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(normalize_etf_nav._read_parquet_rows(storage.get_bytes(key)))
    return rows


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
    ],
)
def test_나쁜_nav_는_통과로_인증되지_않는다(tmp_path, bad_nav, reason):
    # WHY: 이 게이트의 존재 이유가 '나쁜 값을 records_passed 로 세지 않는 것'이다. NaN/Inf/bool 은
    #      수치 비교를 조용히 통과하는 대표적 coerce-to-passing 경로라 값으로 못박는다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_nav_row(nav=bad_nav)])

    assert normalize_etf_nav.run(storage, "N1") == 0  # 행 탈락은 인프라 실패가 아니다

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

    assert normalize_etf_nav.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert "missing_trade_date" in log["failures"][0]["reasons"]


def test_미래_거래일은_bad_trade_date_로_막힌다(tmp_path):
    # WHY: 파싱은 되지만 범위 밖인 날짜('20991231')가 passed 로 인증되면 엉뚱한 미래 파티션이 생긴다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_nav_row(stck_bsop_date="20991231")])

    assert normalize_etf_nav.run(storage, "N1") == 0
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

    assert normalize_etf_nav.run(storage, "N1") == 0

    log = _quality_log(storage)
    assert log["records_read"] == 5
    assert log["records_passed"] == 2
    reasons = [r for f in log["failures"] for r in f["reasons"]]
    assert reasons.count("unparseable_json") == 1
    assert reasons.count("non_object_row") == 2
    assert sorted(r["etf_id"] for r in _canonical_rows(storage)) == ["069500", "091160"]


def test_알수없는_벤더는_사유로_드러난다(tmp_path):
    # WHY: 벤더 판별은 raw 키의 source= 로만 한다. 새 벤더가 붙었는데 정규화 매핑이 없으면
    #      조용히 통과시키지 않고 unsupported_vendor 로 표면화해야 한다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(source="krx"), [_kis_nav_row()])

    assert normalize_etf_nav.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert log["failures"][0]["reasons"] == ["unsupported_vendor"]


def test_KR_아닌_시장은_막힌다(tmp_path):
    # WHY: NAV 수집은 KIS 단일 벤더·KR 고정이다(ADR-0024). 다른 시장 행이 흘러들면 잘못 라우팅된
    #      데이터이므로 통화 계약도 못 만든다 — 조용히 통과시키면 마트에 시장 미상 NAV 가 쌓인다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(market="US"), [_kis_nav_row(market="US")])

    assert normalize_etf_nav.run(storage, "N1") == 0
    assert "unsupported_market" in _quality_log(storage)["failures"][0]["reasons"]
