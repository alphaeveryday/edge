"""normalize_investor_estimate 스텝 테스트 — 슬롯 축 정체성·수치 게이트·최신값 덮어쓰기.

EOD 정제(test_normalize_investor)와 겹치는 것을 다시 검사하지 않는다. 이 데이터셋에서만
틀릴 수 있는 것을 본다: **슬롯이 정체성 키의 일부**라는 사실(빠지면 장중 추이가 사라진다),
거래일 라벨이 응답이 아니라 수집 provenance(`asof_date`)라는 사실, 그리고 EOD raw 와
데이터셋이 갈린다는 사실.
"""

import json

from data_pipeline.lake import (
    LocalStorage,
    canonical_investor_flow_intraday_partition,
    canonical_run_manifest_key,
)
from data_pipeline.steps import normalize_investor_estimate as step


def _raw_key(dataset: str = "investor_flow_intraday", source: str = "kis",
             market: str = "KR", run_id: str = "R1", date: str = "2026-08-05") -> str:
    return (
        f"raw/source={source}/dataset={dataset}/market={market}"
        f"/ingest_date={date}/run_id={run_id}/part-00000.ndjson"
    )


def _write_raw(storage, key: str, rows: list[dict]) -> None:
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    storage.put_bytes(key, body.encode("utf-8"))


def _kis_row(slot: str = "0930", **over) -> dict:
    # KIS 장중 추정 원본은 zero-pad 문자열(음수 가능). asof_date·market·our_ticker·fetched_at 은
    # 수집(ALPHA-767)이 붙이는 provenance — 응답에 날짜 필드가 없어 거래일은 이것뿐이다.
    row = {
        "bsop_hour_gb": slot,
        "frgn_fake_ntby_qty": "-00000000000012000",
        "orgn_fake_ntby_qty": "000000000000003400",
        "sum_fake_ntby_qty": "-00000000000008600",
        "our_ticker": "005930", "market": "KR", "kis_symbol": "005930",
        "asof_date": "2026-08-05",
        "fetched_at": "2026-08-05T00:30:00+00:00",
    }
    row.update(over)
    return row


def _canonical_rows(storage, market: str = "KR", trade_date: str = "2026-08-05") -> list[dict]:
    prefix = canonical_investor_flow_intraday_partition(market, trade_date)
    rows: list[dict] = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(step._read_parquet_rows(storage.get_bytes(key)))
    return rows


def _quality_log(storage) -> dict:
    keys = storage.list_keys("operations_archive/data_quality_logs/")
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _manifest(storage, run_id: str) -> dict:
    return json.loads(storage.get_bytes(canonical_run_manifest_key("investor_flow_intraday", run_id)))


class _FailingStorage:
    def __init__(self, inner, *, fail_put: str | None = None, fail_get: str | None = None,
                 fail_list: str | None = None):
        self.inner = inner
        self.fail_put = fail_put
        self.fail_get = fail_get
        self.fail_list = fail_list

    def list_keys(self, prefix):
        if self.fail_list and self.fail_list in prefix:
            raise OSError("의도된 목록 조회 실패")
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        if self.fail_get and self.fail_get in key:
            raise OSError("의도된 읽기 실패")
        return self.inner.get_bytes(key)

    def put_bytes(self, key, data):
        if self.fail_put and self.fail_put in key:
            raise OSError("의도된 쓰기 실패")
        return self.inner.put_bytes(key, data)


def test_가집계_문자열이_추정_수량_행이_된다(tmp_path):
    # WHY: 정제의 존재 이유는 KIS zero-pad 가집계 문자열을 표준 정수 행으로 수렴시키는 것.
    #      거래일은 응답이 아니라 수집이 붙인 asof_date 라, 이 매핑이 어긋나면 canonical 이
    #      어느 거래일 스냅샷인지 복원하지 못한다(소급 재조회가 없어 사후 정정 불가).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row()])

    assert step.run(storage, "N1") == 0
    [row] = _canonical_rows(storage)
    assert (row["ticker"], row["trade_date"], row["asof_slot"]) == ("005930", "2026-08-05", "0930")
    assert row["net_qty_foreign_est"] == -12000       # 순매도(음수)가 정상이다
    assert row["net_qty_institution_est"] == 3400
    assert row["net_qty_total_est"] == -8600
    assert row["source_vendor"] == "kis"
    log = _quality_log(storage)
    assert (log["records_read"], log["records_passed"], log["records_failed"]) == (1, 1, 0)


def test_같은_종목_같은_날의_슬롯들이_모두_남는다(tmp_path):
    # WHY: 이 데이터셋의 존재 이유가 장중 추이다. 병합 키에서 슬롯이 빠지면(EOD 처럼 ticker
    #      단독) 하루 4~5 스냅샷 중 마지막 하나만 남아 추이가 통째로 사라진다 — 그래도 런은
    #      성공하고 로그도 초록이라 아무도 못 잡는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [
        _kis_row(slot="0930", sum_fake_ntby_qty="100"),
        _kis_row(slot="1120", sum_fake_ntby_qty="200"),
        _kis_row(slot="1320", sum_fake_ntby_qty="300"),
    ])

    assert step.run(storage, "N1") == 0
    rows = _canonical_rows(storage)
    assert [(r["asof_slot"], r["net_qty_total_est"]) for r in rows] == [
        ("0930", 100), ("1120", 200), ("1320", 300),
    ]


def test_같은_슬롯_재관측은_최신값이_이긴다(tmp_path):
    # WHY: 정정 정책이 '최신값 덮어쓰기'다 — 벤더가 가집계를 고치면 canonical 이 따라가야
    #      한다. 앞선 관측이 이기면 정정이 영영 반영되지 않고, 둘 다 남으면 PK 가 깨진다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(run_id="R1"), [
        _kis_row(slot="0930", sum_fake_ntby_qty="100", fetched_at="2026-08-05T00:30:00+00:00")])
    assert step.run(storage, "N1") == 0
    _write_raw(storage, _raw_key(run_id="R2"), [
        _kis_row(slot="0930", sum_fake_ntby_qty="180", fetched_at="2026-08-05T02:20:00+00:00")])
    assert step.run(storage, "N2") == 0

    [row] = _canonical_rows(storage)
    assert row["net_qty_total_est"] == 180  # 정정분이 이긴다


def test_슬롯_없는_행은_canonical_로_가지_않는다(tmp_path):
    # WHY: 슬롯은 정체성 키의 일부라 없으면 그 행이 **어느 시점 값인지 영영 모른다**(소급
    #      재조회 없음). 수집은 bronze 보존을 위해 그런 행도 저장하므로, 막을 곳은 여기다.
    #      조용히 통과시키면 시점 없는 행이 마트까지 흘러 추이 질의를 오염시킨다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(slot="0930"), _kis_row(bsop_hour_gb="  ")])

    assert step.run(storage, "N1") == 2
    assert [r["asof_slot"] for r in _canonical_rows(storage)] == ["0930"]
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["missing_field"]


def test_비문자열_슬롯은_새_정체성으로_인증되지_않는다(tmp_path):
    # WHY: asof_slot 은 어떤 문자열이든 받는 유일한 정체성 축이라, 타입 드리프트를 str() 로
    #      받으면 JSON 숫자 930 이 "0930" 과 **별도 PK** 로 조용히 인증된다 — 같은 관측이 둘로
    #      갈리고 정정이 기존 행을 못 덮는데, 소급 재조회가 없어 사후 병합도 불가하다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(slot="0930"), _kis_row(bsop_hour_gb=930),
                                     _kis_row(bsop_hour_gb=[])])

    assert step.run(storage, "N1") == 2
    assert [r["asof_slot"] for r in _canonical_rows(storage)] == ["0930"]
    log = _quality_log(storage)
    assert [f["reasons"] for f in log["failures"]] == [["bad_asof_slot"], ["bad_asof_slot"]]


def test_NUL_이_든_슬롯은_적재_전에_막힌다(tmp_path):
    # WHY: NUL 은 PostgreSQL TEXT 가 저장 자체를 거부하는 유일한 문자다. 슬롯은 자유 텍스트
    #      축이라 로더가 뒤에서 걸러낼 방법이 없어(비공백이면 유효한 슬롯이다) 여기가 마지막
    #      방어선이고, 새면 INSERT 예외로 정상 슬롯까지 든 트랜잭션이 통째로 롤백된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(slot="0930"), _kis_row(slot="1120\x00")])

    assert step.run(storage, "N1") == 2
    assert [r["asof_slot"] for r in _canonical_rows(storage)] == ["0930"]
    assert _quality_log(storage)["failures"][0]["reasons"] == ["bad_asof_slot"]


def test_불량_fetched_at_은_통과하지_않는다(tmp_path):
    # WHY: fetched_at 은 병합의 '최신 우선' 키이자 적재 available_at 의 원본이다. 불량값이
    #      통과하면 그 행은 병합에서 영원히 지고(파싱 실패=가장 오래된 것), 적재에선
    #      TIMESTAMPTZ 변환 오류로 **정상 행까지 든 트랜잭션 전체가 롤백**된다. 결측도
    #      마찬가지 — 로더가 실행 시각으로 대체해 관측 가능 시각을 지어낸다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(slot="0930"),
                                     _kis_row(slot="1120", fetched_at="garbage"),
                                     _kis_row(slot="1320", fetched_at=None)])

    assert step.run(storage, "N1") == 2
    assert [r["asof_slot"] for r in _canonical_rows(storage)] == ["0930"]
    assert [f["reasons"] for f in _quality_log(storage)["failures"]] == [
        ["bad_fetched_at"], ["missing_field"]]


def test_오프셋_없는_수집시각은_통과하지_않는다(tmp_path):
    # WHY: 날짜만("2026-08-05")이면 자정으로 굳어 **실제 수집보다 이른** available_at 이 되고,
    #      PIT 조회가 그 시점엔 없던 관측을 노출한다. naive("…T09:30:00")는 병합(UTC 가정)과
    #      PostgreSQL TIMESTAMPTZ 변환(세션 시간대)이 서로 다른 순간으로 읽어 두 계층이 조용히
    #      어긋난다. 둘 다 fromisoformat 은 통과하므로 오프셋을 따로 요구해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(slot="0930"),
                                     _kis_row(slot="1120", fetched_at="2026-08-05"),
                                     _kis_row(slot="1320", fetched_at="2026-08-05T09:30:00")])

    assert step.run(storage, "N1") == 2
    assert [r["asof_slot"] for r in _canonical_rows(storage)] == ["0930"]
    assert [f["reasons"] for f in _quality_log(storage)["failures"]] == [
        ["bad_fetched_at"], ["bad_fetched_at"]]


def test_int64_를_넘는_수량은_격리한다(tmp_path):
    # WHY: 파이썬 int 는 무한정이라 캐스팅 게이트를 통과하지만 canonical 은 int64 다 —
    #      한 행의 과대값이 parquet 직렬화에서 터지면 **그 파티션의 통과 행이 통째로 안 써진다**
    #      (게이트가 스스로 죽는 형태). 정상 행은 살아야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(slot="0930"),
                                     _kis_row(slot="1120", sum_fake_ntby_qty=str(2**80))])

    assert step.run(storage, "N1") == 2
    assert [r["asof_slot"] for r in _canonical_rows(storage)] == ["0930"]
    assert _quality_log(storage)["failures"][0]["reasons"] == ["out_of_range"]


def test_추정_수량이_하나라도_없으면_행이_탈락한다(tmp_path):
    # WHY: 벤더가 주는 값은 이 셋뿐이다 — 하나가 비면 그 행에 남는 관측이 부분적인데,
    #      테이블은 셋 다 NOT NULL 이라 넣으면 배치가 죽는다. 정제가 먼저 걸러야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(orgn_fake_ntby_qty="")])

    assert step.run(storage, "N1") == 2
    assert _canonical_rows(storage) == []
    assert _quality_log(storage)["failures"][0]["reasons"] == ["missing_field"]


def test_거래일_라벨이_실재하지_않는_날짜면_탈락한다(tmp_path):
    # WHY: asof_date 는 우리가 붙인 라벨이라 손상되면 canonical 파티션이 존재하지 않는
    #      거래일로 열린다 — 소급 재조회가 없어 그 파티션은 영영 대조 불가다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(asof_date="2026-02-31")])

    assert step.run(storage, "N1") == 2
    assert _quality_log(storage)["failures"][0]["reasons"] == ["bad_trade_date"]


def test_EOD_raw_는_읽지_않는다(tmp_path):
    # WHY: 두 데이터셋이 같은 raw/ 아래 산다. 마커가 느슨해 EOD(investor_flow_daily) 행까지
    #      집어오면 확정치가 슬롯 없는 추정 행으로 둔갑해 잠정↔확정 구분이 무너진다 —
    #      이 티켓이 별도 데이터셋으로 간 이유가 바로 그 구분이다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(dataset="investor_flow_daily"), [_kis_row()])
    _write_raw(storage, _raw_key(), [_kis_row(slot="1120")])

    assert step.run(storage, "N1") == 0
    assert [r["asof_slot"] for r in _canonical_rows(storage)] == ["1120"]
    assert _quality_log(storage)["raw_files"] == 1


def test_재실행이_멱등이다(tmp_path):
    # WHY: 창 미지정이 raw 전체 스캔이라 매 슬롯 런이 앞 슬롯 raw 를 다시 훑는다. 멱등이
    #      아니면 같은 슬롯이 파티션에 누적돼 적재가 PK 위반으로 죽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [_kis_row(slot="0930"), _kis_row(slot="1120")])

    assert step.run(storage, "N1") == 0
    first = _canonical_rows(storage)
    assert step.run(storage, "N2") == 0
    assert _canonical_rows(storage) == first


def test_manifest는_변경분이_아닌_이번_실행의_모든_winner를_기록한다(tmp_path):
    # WHY(ALPHA-1035): 후속 로더 범위는 canonical 값의 변경 여부가 아니라 이번 normalize가
    #      성공적으로 확정한 논리 winner다. 동일 값 재확정을 빼면 정상 슬롯 재실행이 0건으로
    #      축소되고, 중복 raw를 그대로 싣으면 같은 PK를 두 번 처리한다.
    storage = LocalStorage(tmp_path / "lake")
    same = _kis_row(slot="0930", our_ticker="005930")
    _write_raw(storage, _raw_key(run_id="R0"), [same])
    assert step.run(storage, "N0", input_run_id="R0") == 0

    _write_raw(storage, _raw_key(run_id="R1"), [
        same,
        _kis_row(slot="1120", our_ticker="000660", fetched_at="2026-08-05T01:00:00+00:00"),
        _kis_row(slot="1120", our_ticker="000660", fetched_at="2026-08-05T02:00:00+00:00"),
    ])
    assert step.run(storage, "N1", input_run_id="R1") == 0

    prefix = canonical_investor_flow_intraday_partition("KR", "2026-08-05")
    assert _manifest(storage, "N1") == {
        "run_id": "N1",
        "producer": "normalize_investor_estimate",
        "canonical_written": True,
        "canonical_partitions": [{
            "market": "KR",
            "trade_date": "2026-08-05",
            "key": f"{prefix}/part-00000.parquet",
            "winner_ids": [
                {"ticker": "000660", "asof_slot": "1120"},
                {"ticker": "005930", "asof_slot": "0930"},
            ],
        }],
    }


def test_부분_행_실패는_성공_manifest를_보존하고_실행은_실패한다(tmp_path):
    # WHY(ALPHA-1035): 한 종목 불량 때문에 다른 성공 winner의 계보를 버리면 후속 처리가 막히고,
    #      반대로 exit 0이면 부분 유실이 SFN에서 숨는다. 성공 범위와 전체 상태를 분리한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [
        _kis_row(slot="0930", our_ticker="005930"),
        _kis_row(slot="1120", our_ticker="000660", orgn_fake_ntby_qty=""),
    ])

    assert step.run(storage, "N1", input_run_id="R1") == 2
    assert [(row["ticker"], row["asof_slot"]) for row in _canonical_rows(storage)] == [
        ("005930", "0930")
    ]
    assert _manifest(storage, "N1")["canonical_partitions"][0]["winner_ids"] == [
        {"ticker": "005930", "asof_slot": "0930"}
    ]
    log = _quality_log(storage)
    assert log["ops"] == {"records_out": 1, "failed_records": 1}
    assert (log["failures"][0]["ticker"], log["failures"][0]["asof_slot"]) == (
        "000660", "1120"
    )


def test_빈_입력은_유효한_빈_완료_manifest다(tmp_path):
    # WHY(ALPHA-1035): 0건은 producer 실패가 아니다. 완료된 빈 범위가 있어야 consumer가
    #      결손과 구분하고 canonical 전체 스캔으로 넓히지 않는다.
    storage = LocalStorage(tmp_path / "lake")

    assert step.run(storage, "N1", input_run_id="empty") == 0
    assert _manifest(storage, "N1") == {
        "run_id": "N1",
        "producer": "normalize_investor_estimate",
        "canonical_written": True,
        "canonical_partitions": [],
    }


def test_동일_run_manifest는_결정적이고_멱등이다(tmp_path):
    # WHY(ALPHA-1035): 재시도마다 winner 순서나 바이트가 달라지면 같은 계보가 새 산출처럼 보여
    #      비교와 감사 결과가 흔들린다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key(), [
        _kis_row(slot="1120", our_ticker="005930"),
        _kis_row(slot="0930", our_ticker="000660"),
    ])

    assert step.run(storage, "N1", input_run_id="R1") == 0
    key = canonical_run_manifest_key("investor_flow_intraday", "N1")
    first = storage.get_bytes(key)
    assert step.run(storage, "N1", input_run_id="R1") == 0
    assert storage.get_bytes(key) == first


def test_canonical_실패는_완료_manifest를_남기지_않는다(tmp_path):
    # WHY(ALPHA-1035): 일부/없는 canonical을 완료 범위로 승인하면 consumer가 손상 계보를 읽는다.
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key(), [_kis_row()])
    storage = _FailingStorage(inner, fail_put="canonical/market_data/investor_flow_intraday")

    assert step.run(storage, "N1") == 1
    assert _manifest(inner, "N1")["canonical_written"] is False
    assert _quality_log(storage)["canonical_written"] is False


def test_raw_저장소_실패는_부분성공으로_승격하지_않는다(tmp_path):
    # WHY(ALPHA-1035): 손상 행은 종목 단위 부분 실패지만 raw GET 실패는 입력 자체를 확인하지
    # 못한 hard failure다. 이를 exit 2로 승인하면 빈/부분 manifest를 loader가 정상 범위로 믿는다.
    inner = LocalStorage(tmp_path / "lake")
    raw_key = _raw_key()
    _write_raw(inner, raw_key, [_kis_row()])
    storage = _FailingStorage(inner, fail_get=raw_key)

    assert step.run(storage, "N1") == 1
    assert _manifest(inner, "N1")["canonical_written"] is False
    log = _quality_log(inner)
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["raw_read_error"]
    assert log["canonical_written"] is True


def test_raw_목록_실패는_이전_완료_manifest를_무효화한다(tmp_path):
    # WHY(ALPHA-1035): LIST가 marker 초기화보다 먼저 실패하면 같은 run 재시도의 이전 승인 범위가
    # 살아남아 consumer가 실패한 재시도를 성공으로 오인한다. 목록 장애도 hard failure 증적이다.
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key(), [_kis_row()])
    assert step.run(inner, "N1") == 0
    assert _manifest(inner, "N1")["canonical_written"] is True

    storage = _FailingStorage(inner, fail_list="raw/")
    assert step.run(storage, "N1") == 1
    assert _manifest(inner, "N1")["canonical_written"] is False
    log = _quality_log(inner)
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["raw_list_error"]


def test_quality_실패는_완료_manifest를_남기지_않는다(tmp_path):
    # WHY(ALPHA-1035): 성공/실패 감사 로그가 유실된 실행을 완료 계보로 공개하면 부분 실패 여부를
    #      복원할 수 없다. canonical이 써졌어도 미완료 marker를 유지한다.
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key(), [_kis_row()])
    storage = _FailingStorage(inner, fail_put="data_quality_logs")

    assert step.run(storage, "N1") == 1
    assert _manifest(inner, "N1")["canonical_written"] is False


def test_manifest_초기화_실패는_canonical을_바꾸지_않는다(tmp_path):
    # WHY(ALPHA-1035): 이전 완료 manifest를 무효화할 수 없는데 canonical부터 바꾸면 stale
    #      winner_ids가 새 파일을 가리킨다. 초기 marker PUT이 막히면 쓰기를 시작하지 않는다.
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key(run_id="R1"), [_kis_row(sum_fake_ntby_qty="100")])
    assert step.run(inner, "N1", input_run_id="R1") == 0
    first_rows = _canonical_rows(inner)
    first_manifest = _manifest(inner, "N1")
    _write_raw(inner, _raw_key(run_id="R2"), [
        _kis_row(sum_fake_ntby_qty="200", fetched_at="2026-08-05T02:00:00+00:00")
    ])
    storage = _FailingStorage(inner, fail_put="canonical_run_manifests")

    assert step.run(storage, "N1", input_run_id="R2") == 1
    assert _canonical_rows(inner) == first_rows
    assert _manifest(inner, "N1") == first_manifest


def test_완료_manifest_쓰기_실패는_fail_loud다(tmp_path):
    # WHY(ALPHA-1035): 마지막 commit marker가 실패하면 canonical 성공만으로 완료할 수 없다.
    class _CompletedManifestFailingStorage(_FailingStorage):
        def put_bytes(self, key, data):
            if "canonical_run_manifests" in key and json.loads(data)["canonical_written"] is True:
                raise OSError("의도된 완료 manifest 쓰기 실패")
            return self.inner.put_bytes(key, data)

    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key(), [_kis_row()])
    storage = _CompletedManifestFailingStorage(inner)

    assert step.run(storage, "N1") == 1
    assert _canonical_rows(inner)
    assert _manifest(inner, "N1")["canonical_written"] is False
