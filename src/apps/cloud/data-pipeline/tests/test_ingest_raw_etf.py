"""ingest_raw_etf 스텝 테스트 — raw append(전부 보존, dedup 없음)·collection_log.

ETF holdings 는 스냅샷이라 날짜창이 없고, 수집 대상이 etf_map(ETF 목록)이다 —
가격(ingest_price_raw)과 fail-loud 상태 로직은 같되 그 두 점이 다르다.
"""

import json
import logging

from data_pipeline.config import EtfSource, load_settings
from data_pipeline.sources.etf import FmpEtfSource
from data_pipeline.lake import LocalStorage
from data_pipeline.steps import ingest_raw_etf

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

[price.source]
base_url = "https://fmp.example/stable/historical-price-eod/full"

[targets]
symbols = ["NVDA"]

[etf.source]
base_url = "https://fmp.example/stable/etf/holdings"

[etf.source.etf_map]
SPY = "SPY"
QQQ = "QQQ"
"""

_MAP = {"SPY": "SPY", "QQQ": "QQQ"}


class FakeClient:
    def __init__(self, responses):
        self.responses = responses  # {fmp_symbol: <payload: list|dict>}

    def get(self, url, *, accept="application/json"):
        symbol = url.split("symbol=")[1].split("&")[0]
        return json.dumps(self.responses.get(symbol, []))


def _holding(asset, weight=1.0, updated="2026-07-11 09:07:03"):
    # FMP holdings 실측 필드 일부 — asset(구성종목)·weightPercentage·updatedAt(기준일).
    return {"symbol": "SPY", "asset": asset, "weightPercentage": weight, "updatedAt": updated}


def _settings(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_settings(path)


def _run(tmp_path, responses, api_key="k", etf_map=None, run_id="20260703T000000Z"):
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = EtfSource(
        base_url=settings.etf.source.base_url,
        api_key=api_key,
        etf_map=_MAP if etf_map is None else etf_map,
    )
    source = FmpEtfSource(config, FakeClient(responses))
    code = ingest_raw_etf.run(settings, storage, source, run_id)
    return code, storage


def test_saves_ingest_date_partition_and_log(tmp_path):
    # WHY: ALPHA-337 AC — 구성종목 스냅샷이 ingest_date 파티션 규약(market 별 1파일)대로
    #      저장되고, 실행 결과가 collection_log 로 남아야 운영에서 수집 여부를 확인한다.
    #      ETF holdings 는 1 ETF→N 구성종목 fan-out 이라 여러 행이 한 파티션에 쌓인다.
    responses = {
        "SPY": [_holding("NVDA", 7.5), _holding("AAPL", 7.1)],
        "QQQ": [_holding("MSFT", 8.0)],
    }
    code, storage = _run(tmp_path, responses)

    assert code == 0
    keys = storage.list_keys("raw")
    # SPY·QQQ 모두 US → market=US 단일 파티션 1파일(ingest_date 는 실행일).
    assert len(keys) == 1
    [raw_key] = keys
    assert raw_key.startswith("raw/source=fmp/dataset=etf_holdings/market=US")
    assert "/ingest_date=" in raw_key and raw_key.endswith("/part-00000.ndjson")
    lines = storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()
    assert len(lines) == 3  # fan-out: 2 + 1 구성종목
    row = json.loads(lines[0])
    # 수집 메타가 붙되 원본 필드(asset·weightPercentage·updatedAt)는 무변형 보존.
    assert row["our_etf_id"] in {"SPY", "QQQ"} and row["market"] == "US" and "fetched_at" in row
    assert "weightPercentage" in row and "updatedAt" in row

    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "success"
    assert log["records_fetched"] == 3 and log["records_saved"] == 3
    # 행 수(holdings 3행)가 아니라 기대 snapshot과 같은 ETF entity grain(2종)이다.
    assert log["ops"]["records_out"] == 3
    assert log["ops"]["received_count"] == 2


def test_raw_preserves_duplicate_holding_rows(tmp_path):
    # WHY: raw 는 받은 행을 전부 보존한다 — FMP 가 같은 구성종목을 두 번 줘도(이상치)
    #      조용히 버리지 않는다. (etf,asset,as_of) 정체성 dedup 은 후속 canonical 소관.
    code, storage = _run(tmp_path, {"SPY": [_holding("NVDA", 7.5), _holding("NVDA", 9.9)], "QQQ": []},
                         etf_map={"SPY": "SPY"})

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    lines = storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()
    assert len(lines) == 2  # 둘 다 보존(버리지 않음)
    assert {json.loads(l)["weightPercentage"] for l in lines} == {7.5, 9.9}
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["ops"]["received_count"] == 1  # 같은 ETF의 중복 행은 entity 하나


def test_all_etfs_failing_marks_run_error(tmp_path):
    # WHY: ETF 는 정의상 구성종목이 있으므로 전 ETF 가 빈 holdings(0건 저장)면
    #      status=success 로 남기면 안 된다(조용한 성공 금지 — fail loud).
    code, storage = _run(tmp_path, {"SPY": [], "QQQ": []})

    assert code == 1
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "error"
    assert log["records_failed_etfs"] == 2


def test_partial_failure_marks_run_partial(tmp_path):
    # WHY: 일부 ETF 만 실패(빈 holdings)하면 저장분은 있으나 온전치 않다 — partial 로
    #      드러내고 비0 종료로 오케스트레이터에도 손실을 알린다.
    code, storage = _run(tmp_path, {"SPY": [_holding("NVDA")], "QQQ": []})

    assert code == 1
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "partial"
    assert log["records_saved"] == 1 and log["records_failed_etfs"] == 1
    assert log["ops"]["received_count"] == 1


def test_error_object_response_is_failure(tmp_path):
    # WHY: FMP 가 HTTP 200 으로 에러 객체({"Error Message": ...}·쿼터/플랜 게이팅)를 줘도
    #      조용히 0행 처리하면 런이 success 로 위장한다 — ETF 실패로 올려 fail loud.
    code, storage = _run(tmp_path, {"SPY": {"Error Message": "plan gated"}, "QQQ": [_holding("MSFT")]})

    assert code == 1
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "partial"  # QQQ 저장분 있음
    assert log["records_failed_etfs"] == 1
    assert "not a list" in log["failed_etfs"][0]["error"]


def test_malformed_row_skipped_others_preserved(tmp_path):
    # WHY: holdings 배열에 dict 아닌 행(null·문자열)이 섞여도 한 행이 남은 수집을 끊지
    #      않는다 — 불량 행은 기록 후 스킵하고 정상 행은 최대한 보존한다.
    code, storage = _run(tmp_path, {"SPY": [_holding("NVDA"), None, "junk"]}, etf_map={"SPY": "SPY"})

    # 정상 행 저장분 있고 불량 행 격리됨 → partial(온전치 않음).
    assert code == 1
    [raw_key] = storage.list_keys("raw")
    assert len(storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()) == 1
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "partial" and log["records_saved"] == 1


def test_raw_write_failure_still_writes_collection_log(tmp_path):
    # WHY: "결과는 항상 collection_log" 계약 — raw put_bytes 가 실패(IAM·네트워크)해도
    #      런 흔적이 사라지면 안 된다. status=error·exit 1 로 남고 로그는 남아야 한다.
    class RawFailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            if key.startswith("raw/"):
                raise OSError("S3 raw write denied")
            super().put_bytes(key, data)

    settings = _settings(tmp_path)
    storage = RawFailingStorage(tmp_path / "lake")
    config = EtfSource(base_url=settings.etf.source.base_url, api_key="k", etf_map={"SPY": "SPY"})
    source = FmpEtfSource(config, FakeClient({"SPY": [_holding("NVDA")]}))
    code = ingest_raw_etf.run(settings, storage, source, "20260703T000000Z")

    assert code == 1
    [log_key] = storage.list_keys("operations_archive")
    log = json.loads(storage.get_bytes(log_key))
    assert log["status"] == "error" and "denied" in log["error"]


def test_disabled_source_skips_with_log(tmp_path):
    # WHY: 키 미주입(로컬 등)은 실패가 아니라 '명시적 skip' — 조용히 성공처럼 보이면
    #      안 되고, skip 사실이 로그로 남아야 한다(Rule 12).
    code, storage = _run(tmp_path, {}, api_key=None)

    assert code == 0
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "skipped"
    assert log["ops"]["received_count"] == 0


def test_adapter_skip_reason_is_recorded_verbatim(tmp_path):
    # WHY: 어댑터가 "지금은 수집하면 안 된다"고 판단하는 사유(iNAV 의 비거래일·개장 전,
    #      ALPHA-557)는 크리덴셜 유무와 별개다. 하나로 합쳐 고정 문구를 남기면 감사
    #      레코드의 reason 이 거짓이 되고, 운영자가 왜 안 걷혔는지 로그로 못 가린다(Rule 12).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = EtfSource(
        base_url=settings.etf.source.base_url, api_key="k", etf_map=_MAP,
    )
    source = FmpEtfSource(config, FakeClient({}))
    source.skip_reason = "non-trading day (KST 2026-07-25)"  # 어댑터가 낸 사유

    code = ingest_raw_etf.run(settings, storage, source, "20260725T000000Z")

    assert code == 0  # skip 은 실패가 아니다 — 스케줄러가 휴장일마다 정상 통과해야 한다
    assert storage.list_keys("raw") == []  # 오염된 raw 를 쓰지 않는다
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "skipped"
    assert log["reason"] == "non-trading day (KST 2026-07-25)"


def test_adapter_skip_does_not_trip_the_skip_alarm(tmp_path, caplog):
    # WHY: tasks.tf 의 raw-ingest-skipped metric filter 가 "수집 건너뜀" 토큰으로 알람을
    #      울린다. 그 알람은 "skip 은 비정상"을 전제로 설계됐다(필터 주석: 정상 상태에서
    #      발화 없음). 어댑터가 낸 달력 skip 은 **예정된 정상 상태**라 같은 토큰을 쓰면
    #      휴장일마다 오경보가 나고, 그러면 아무도 그 알람을 안 보게 된다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = EtfSource(base_url=settings.etf.source.base_url, api_key="k", etf_map=_MAP)
    source = FmpEtfSource(config, FakeClient({}))
    source.skip_reason = "non-trading day (KST 2026-07-25)"

    with caplog.at_level(logging.INFO):
        ingest_raw_etf.run(settings, storage, source, "20260725T000000Z")

    assert "수집 건너뜀" not in caplog.text  # 알람 토큰
    assert "non-trading day" in caplog.text  # 그래도 로그로는 드러난다


def test_credential_skip_wins_over_calendar_skip(tmp_path, caplog):
    # WHY: 둘 다 해당할 때 달력 사유를 택하면 **설정 장애가 정상 skip 으로 위장**된다 —
    #      알람도 안 울리고 로그 사유도 거짓이라, 고쳐야 할 것이 조용해진다(Rule 12).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = EtfSource(base_url=settings.etf.source.base_url, api_key=None, etf_map=_MAP)
    source = FmpEtfSource(config, FakeClient({}))
    source.skip_reason = "non-trading day (KST 2026-07-25)"  # 달력도 동시에 해당

    with caplog.at_level(logging.INFO):
        ingest_raw_etf.run(settings, storage, source, "20260725T000000Z")

    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["reason"] == "fmp disabled or missing credentials"  # 달력 사유가 덮지 않는다
    assert "수집 건너뜀" in caplog.text  # 알람도 그대로 울린다


def test_credential_skip_still_trips_the_alarm(tmp_path, caplog):
    # WHY: 크리덴셜 미주입은 설정 장애라 알람이 울려야 한다 — 위 분리가 이쪽까지
    #      조용하게 만들면 기존 탐지가 사라진다(회귀).
    with caplog.at_level(logging.INFO):
        _run(tmp_path, {}, api_key=None)

    assert "수집 건너뜀" in caplog.text


def test_no_mapped_etfs_marks_skipped(tmp_path):
    # WHY: 활성 소스(키 주입됨)인데 etf_map 이 0개면 수집이 사실상 불가능하다 —
    #      success(0건)로 위장하지 않고 skip 으로 드러낸다.
    code, storage = _run(tmp_path, {}, etf_map={})

    assert code == 0  # 잘못된 설정이지만 크래시는 아님 — 로그로 드러냄
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "skipped" and log["reason"] == "no mapped etfs"
    assert log["ops"]["received_count"] == 0


def test_unexpected_failure_still_writes_log(tmp_path):
    # WHY: '결과는 항상 collection_log' 계약 — fetch 자체가 죽는 예기치 못한 실패도
    #      로그 없이 죽으면 운영에서 런이 있었는지조차 알 수 없다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = EtfSource(base_url=settings.etf.source.base_url, api_key="k", etf_map={"SPY": "SPY"})
    source = FmpEtfSource(config, FakeClient({}))
    source.fetch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))

    code = ingest_raw_etf.run(settings, storage, source, "20260703T000000Z")

    assert code == 1
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "error" and "boom" in log["error"]


def test_disabled_skip_survives_log_write_failure(tmp_path):
    # WHY: skip 도 collection_log 로 드러나는 것이 계약이다(Rule 12). 스토리지 장애로 그
    #      로그마저 못 남겼는데 exit 0 이면 스케줄러는 성공으로 보고, 감사 레코드가 사라진
    #      사실을 아무도 모른다. 5개 수집기가 이 처리를 3:2 로 달리해 통일한 자리다(ALPHA-451)
    #      — 되돌리면 그 분기가 되살아난다.
    class FailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            raise OSError("storage down")

    settings = _settings(tmp_path)
    storage = FailingStorage(tmp_path / "lake")
    config = EtfSource(base_url=settings.etf.source.base_url, api_key=None, etf_map=_MAP)
    source = FmpEtfSource(config, FakeClient({}))

    assert ingest_raw_etf.run(settings, storage, source, "20260703T000000Z") == 1
