"""KisSectorIndexDailySource 테스트 — 코드 번역·50행 페이지 넘김·빈 응답 경계·raw 식별자.

일별 NAV 어댑터를 상속하므로 토큰 1회·EGW00201 재시도·격리는 test_kis_nav 가 덮는다.
여기선 업종 일봉 고유(KIS 응답 상한 50행·`output2`·KRX↔KIS 코드·`index_code`)만 본다.
"""

import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import pytest

from data_pipeline.config import KisNavSource as KisNavSourceConfig
from data_pipeline.sources.http import StopFetch
from data_pipeline.lake import LocalStorage, collection_log_key, raw_sector_index_daily_partition
from data_pipeline.sources.kis_sector_index_daily import KisSectorIndexDailySource
from data_pipeline.steps import ingest_raw_etf


class FakeAuth:
    def token(self):
        return "TOKEN"


def _bar(day: str, close: str = "100.0") -> dict:
    return {"stck_bsop_date": day, "bstp_nmix_prpr": close, "bstp_nmix_oprc": "99.0",
            "bstp_nmix_hgpr": "101.0", "bstp_nmix_lwpr": "98.0", "acml_vol": "1",
            "acml_tr_pbmn": "1", "mod_yn": "N"}


def _weekdays(start: str, end: str) -> list[str]:
    day, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    while day <= last:
        if day.weekday() < 5:
            out.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    return out


class FakeVendor:
    """KIS 업종 일봉 TR 흉내 — 창 [DATE_1, DATE_2] 안의 거래일을 **최신순 최대 50행**만 준다.

    `tr_cont` 같은 다음 페이지 신호는 없다(2026-10-08 실측과 같은 형상)."""

    def __init__(self, days_by_kis_code: dict[str, list[str]], *, defect_day=None,
                 ignore_date2=False, extra_rows=(), fail_from=None,
                 fail_with=ValueError("재시도 소진")):
        self.days = days_by_kis_code
        self.queries: list[dict] = []
        self.fail_from = fail_from        # 이 번째(1부터) 요청부터 fail_with 를 올린다
        self.fail_with = fail_with
        self.defect_day = defect_day      # 이 날짜 행은 거래일 필드가 빠진 결함 행으로 준다
        self.ignore_date2 = ignore_date2  # 창 끝 이동을 무시하고 늘 첫 창을 준다
        self.extra_rows = list(extra_rows)  # 응답 끝에 그대로 덧붙일 행

    def request(self, method, url, *, headers=None, data=None, decode=True):
        params = {k: v[0] for k, v in
                  urllib.parse.parse_qs(urllib.parse.urlparse(url).query,
                                        keep_blank_values=True).items()}
        self.queries.append(params)
        if self.fail_from is not None and len(self.queries) >= self.fail_from:
            raise self.fail_with
        d1, d2 = params["FID_INPUT_DATE_1"], params["FID_INPUT_DATE_2"]
        if self.ignore_date2:
            d2 = self.queries[0]["FID_INPUT_DATE_2"]
        days = [d for d in self.days.get(params["FID_INPUT_ISCD"], [])
                if (not d1 or d >= d1) and d <= d2]
        rows = [_bar(d) for d in sorted(days, reverse=True)[:50]]
        for row in rows:
            if row["stck_bsop_date"] == self.defect_day:
                del row["stck_bsop_date"]
        if len(self.queries) == 1:
            rows += self.extra_rows
        return json.dumps({"rt_cd": "0", "output1": {}, "output2": rows})

    def _sleep(self, seconds):
        pass


def _source(vendor, index_map, from_date, to_date):
    src = KisSectorIndexDailySource(
        KisNavSourceConfig(app_key="k", app_secret="s"), index_map, vendor, from_date, to_date)
    src.auth = FakeAuth()
    return src


def test_50행_상한을_넘는_창은_날짜를_옮겨_끝까지_받는다():
    # WHY: KIS 는 한 응답에 50행까지만 주고 다음 페이지 신호도 없다. 페이지를 안 넘기면
    # 07-01 부터의 소급이 조용히 최근 50거래일(07-27~)에서 끊긴다 — 실측 그대로의 함정이다.
    days = _weekdays("2026-07-01", "2026-10-08")
    vendor = FakeVendor({"0005": days})
    src = _source(vendor, {"1005": "0005"}, "2026-07-01", "2026-10-08")

    records = list(src.fetch())

    assert [r["stck_bsop_date"] for r in records] == days
    assert len(days) > 50
    # 두 번째 질의의 창 끝은 첫 페이지의 가장 이른 거래일 전날이다.
    first_page_earliest = sorted(days, reverse=True)[49]
    assert vendor.queries[1]["FID_INPUT_DATE_2"] < first_page_earliest
    assert src.fetch_failures == []


def test_창_시작이_휴장일이라_다음_페이지가_비면_정상_종료한다():
    # WHY: 첫 페이지가 꽉 찼는데 창 시작일이 주말이면 남은 창에 거래일이 없다. 그 빈 응답을
    # 부모 규칙대로 실패로 접으면 받은 50행이 통째로 버려진다(지수 단위 격리).
    days = _weekdays("2026-07-06", "2026-10-08")[-50:]  # 정확히 50거래일
    vendor = FakeVendor({"0005": days})
    start = (date.fromisoformat(f"{days[0][:4]}-{days[0][4:6]}-{days[0][6:]}")
             - timedelta(days=2)).isoformat()  # 첫 거래일 이틀 전(주말 포함)
    src = _source(vendor, {"1005": "0005"}, start, "2026-10-08")

    records = list(src.fetch())

    assert len(records) == 50
    assert src.fetch_failures == []


def test_결함_행이_섞인_꽉_찬_페이지도_과거로_계속_넘긴다():
    # WHY: 종료를 걸러진 행 수로 판정하면 결함 행 하나가 50행을 49행으로 만들어 "마지막
    # 페이지"로 오인되고, 그보다 과거의 정상 행 수십 일치를 조용히 안 받는다.
    days = _weekdays("2026-07-01", "2026-10-08")
    vendor = FakeVendor({"0005": days}, defect_day=days[-1])
    src = _source(vendor, {"1005": "0005"}, "2026-07-01", "2026-10-08")

    records = list(src.fetch())

    assert [r["stck_bsop_date"] for r in records] == days[:-1]
    assert len(src.fetch_failures) == 1  # 결함 행은 드러난다


def test_창_이동이_먹지_않으면_받은_행은_남기고_절단을_드러낸다():
    # WHY: 벤더가 바뀐 DATE_2 를 무시하고 같은 50행을 주면 창 앞쪽이 영영 안 온다. 이걸 정상
    # 종료로 접으면 절단된 소급이 success 로 기록되고 정제가 그대로 확정한다.
    days = _weekdays("2026-07-01", "2026-10-08")
    vendor = FakeVendor({"0005": days}, ignore_date2=True)
    src = _source(vendor, {"1005": "0005"}, "2026-07-01", "2026-10-08")

    records = list(src.fetch())

    assert len(records) == 50  # 받은 것은 버리지 않는다
    assert any("진전하지 않는다" in f["error"] for f in src.fetch_failures)


def test_페이지_상한에_닿으면_받은_행은_남기고_절단을_드러낸다(monkeypatch):
    # WHY: 상한을 예외로 올리면 그 지수의 받은 행까지 버려진다 — 남기고 partial 로 드러낸다.
    from data_pipeline.sources import kis_sector_index_daily as mod
    monkeypatch.setattr(mod, "MAX_PAGES", 1)
    vendor = FakeVendor({"0005": _weekdays("2026-07-01", "2026-10-08")})
    src = _source(vendor, {"1005": "0005"}, "2026-07-01", "2026-10-08")

    assert len(list(src.fetch())) == 50
    assert any("MAX_PAGES" in f["error"] for f in src.fetch_failures)


def test_같은_거래일에_값이_다른_행은_둘_다_raw_에_남긴다():
    # WHY: 거래일로만 접으면 두 번째 값이 저장 전에 사라져, 정제의 값 충돌 검사(같은 fetched_at
    # 에 값이 다르면 격리)까지 못 가고 응답 순서로 고른 값이 정본이 된다.
    days = _weekdays("2026-10-05", "2026-10-08")
    vendor = FakeVendor({"0005": days}, extra_rows=[_bar(days[-1], "999.0"), _bar(days[0])])
    src = _source(vendor, {"1005": "0005"}, "2026-10-05", "2026-10-08")

    records = list(src.fetch())

    closes = [r["bstp_nmix_prpr"] for r in records if r["stck_bsop_date"] == days[-1]]
    assert sorted(closes) == ["100.0", "999.0"]
    assert len(records) == len(days) + 1  # 완전히 같은 원본(days[0])만 접힌다


def test_뒤_페이지_요청이_실패해도_앞서_받은_행은_남긴다():
    # WHY: 뒤 페이지의 전송 실패가 지수 단위 격리로 올라가면 이미 받은 50행까지 raw 에서
    # 사라진다. 받은 행은 내고 절단은 실패로 기록해야 한다(런은 partial).
    vendor = FakeVendor({"0005": _weekdays("2026-07-01", "2026-10-08")}, fail_from=2)
    src = _source(vendor, {"1005": "0005"}, "2026-07-01", "2026-10-08")

    assert len(list(src.fetch())) == 50
    assert any("뒤 페이지 실패" in f["error"] for f in src.fetch_failures)


def test_뒤_페이지에서_4xx_를_맞으면_받은_행을_내보낸_뒤_소스를_멈춘다():
    # WHY: 4xx/429 는 키·쿼터 문제라 소스 전체를 멈추는 게 맞다. 그런데 바로 올리면 부모
    # fetch 가 그 지수의 행을 yield 하기 전이라 이미 받은 원본이 raw 에서 사라진다.
    # 다음 지수는 묻지 않아야 하고, 그 지수가 마지막이어도 중단 신호가 묻히면 안 된다.
    days = _weekdays("2026-07-01", "2026-10-08")
    vendor = FakeVendor({"0005": days, "0006": days}, fail_from=2,
                        fail_with=StopFetch("429", status=429))
    src = _source(vendor, {"1005": "0005", "1006": "0006"}, "2026-07-01", "2026-10-08")

    records = []
    with pytest.raises(StopFetch):
        for record in src.fetch():
            records.append(record)

    assert len(records) == 50 and {r["index_code"] for r in records} == {"1005"}
    assert {q["FID_INPUT_ISCD"] for q in vendor.queries} == {"0005"}  # 다음 지수는 안 묻는다

    last = FakeVendor({"0005": days}, fail_from=2, fail_with=StopFetch("429", status=429))
    only = _source(last, {"1005": "0005"}, "2026-07-01", "2026-10-08")
    with pytest.raises(StopFetch):
        list(only.fetch())


def test_비달력_거래일_행은_결함으로_격리하고_정상_행은_받는다():
    # WHY: '20260631' 처럼 8자리 숫자인 비달력일이 결함 검사를 통과하면 페이지 커서 계산에서
    # 터져 그 지수의 정상 행이 전부 버려진다.
    days = _weekdays("2026-06-01", "2026-10-08")
    vendor = FakeVendor({"0005": days}, extra_rows=[_bar("20260631")])
    src = _source(vendor, {"1005": "0005"}, "2026-06-01", "2026-10-08")

    records = list(src.fetch())

    assert [r["stck_bsop_date"] for r in records] == days
    assert [f["error"] for f in src.fetch_failures] == [
        "거래일 없는 행: stck_bsop_date='20260631'"]


def test_첫_페이지가_비면_실패로_드러난다():
    # WHY: 첫 응답부터 비었다면 코드가 틀렸거나 거래일 없는 창이다 — 성공 0건으로 위장 금지.
    vendor = FakeVendor({})
    src = _source(vendor, {"1005": "0005"}, "2026-10-01", "2026-10-08")

    assert list(src.fetch()) == []
    assert [f["symbol"] for f in src.fetch_failures] == ["0005"]


def test_KRX_코드로_받고_KIS_코드로_묻는다():
    # WHY: KIS `U` 코드는 KRX 업종코드가 아니다. KRX 코드로 물으면 rt_cd=0 에 남의 지수가 오고,
    # raw 에 KIS 코드가 남으면 canonical 이 일봉 `sector_index` 와 조인되지 않는다.
    vendor = FakeVendor({"1006": _weekdays("2026-10-05", "2026-10-08")})
    src = _source(vendor, {"2012": "1006"}, "2026-10-05", "2026-10-08")

    records = list(src.fetch())

    assert {q["FID_INPUT_ISCD"] for q in vendor.queries} == {"1006"}
    assert vendor.queries[0]["FID_COND_MRKT_DIV_CODE"] == "U"
    assert {r["index_code"] for r in records} == {"2012"}
    assert all("our_etf_id" not in r for r in records)


def test_raw_스텝이_지수_수를_받은_단위로_센다(tmp_path):
    # WHY: 범용 raw 스텝은 받은 단위를 `our_etf_id` 로 세 왔다. 업종 일봉은 그 필드가 없어
    # `unit_field` 를 따라 세지 않으면 KeyError 로 수집 전체가 error 가 된다.
    days = _weekdays("2026-10-05", "2026-10-08")
    vendor = FakeVendor({"0005": days, "1006": days})
    src = _source(vendor, {"1005": "0005", "2012": "1006"}, "2026-10-05", "2026-10-08")
    storage = LocalStorage(tmp_path)
    before = datetime.now(timezone.utc).date().isoformat()

    exit_code = ingest_raw_etf.run(
        None, storage, src, "R1", dataset="sector_index_daily",
        partition=raw_sector_index_daily_partition, job_name="ingest_raw_sector_index_daily")

    after = datetime.now(timezone.utc).date().isoformat()
    assert exit_code == 0
    keys = [collection_log_key("kis", "sector_index_daily", d, "R1") for d in {before, after}]
    found = [data for data, _ in (storage.get_bytes_with_version(k) for k in keys)
             if data is not None]
    log = json.loads(found[0])
    assert log["status"] == "success"
    assert log["ops"]["received_count"] == 2
    assert log["ops"]["records_out"] == 2 * len(days)
