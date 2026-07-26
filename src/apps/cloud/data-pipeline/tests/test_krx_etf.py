"""KrxEtfSource 어댑터 테스트 — 메타 부착·원본 보존(대시 포함)·plan·StopFetch 전파·격리.

FmpEtfSource(US)와 같은 관례 인터페이스를 지켜 ingest_raw_etf 스텝을 그대로 재사용하므로,
스텝의 fail-loud 상태 로직은 test_ingest_raw_etf(벤더 무관)가 이미 덮는다 — 여기선 KRX
고유(로그인 1회·POST getJsonData·JSESSIONID 쿠키·ISIN 질의·해외기초 대시 보존)만 검증한다.
"""

import json
import urllib.parse
from datetime import date, datetime

import pytest

from data_pipeline.config import KrxEtfSource as KrxEtfSourceConfig
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.krx_etf import KST, KrxEtfSource, _as_of, _short_code


class FakeAuth:
    """로그인을 건너뛰고 고정 JSESSIONID 를 준다(어댑터를 실제 KRX 로그인과 분리)."""

    def __init__(self, jsessionid="SESS123"):
        self.jsessionid = jsessionid
        self.calls = 0

    def session(self):
        self.calls += 1  # run 당 1회 규약 검증용
        return self.jsessionid


class FakeClient:
    def __init__(self, responses):
        self.responses = responses  # {isin: <payload dict | Exception>}
        self.cookies = []  # 요청마다 보낸 Cookie 헤더(쿠키 부착 검증용)

    def request(self, method, url, *, headers=None, data=None, decode=True):
        assert method == "POST"
        self.cookies.append((headers or {}).get("Cookie"))
        params = urllib.parse.parse_qs(data.decode("utf-8"))
        isin = params["isuCd"][0]
        payload = self.responses.get(isin, {"output": []})
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)


def _source(responses, etf_map=None, auth=None, concurrency=1):
    config = KrxEtfSourceConfig(
        mbr_id="id", pw="pw",
        etf_map=etf_map if etf_map is not None else {"069500": "KR7069500007"},
    )
    src = KrxEtfSource(config, FakeClient(responses), concurrency=concurrency)
    src.auth = auth or FakeAuth()
    return src


def test_short_code_derives_6digit_from_isin():
    # WHY: getJsonData 는 isuCd2(6자리 단축코드)도 받는다 — 표준코드 12자리 [3:9]가 단축코드.
    #      라이브 실측 요청 본문과 일치시켜야 응답이 재현된다.
    assert _short_code("KR7069500007") == "069500"
    assert _short_code("KR7360750004") == "360750"


def test_as_of_labels_non_trading_day_with_previous_trading_day(monkeypatch):
    # WHY: 비거래일 런에도 KRX 는 빈 응답이 아니라 **직전 거래일 PDF** 를 그대로 준다(dev 실측:
    #      토 07-18 응답이 금 07-17 과 바이트 동일). 그걸 오늘로 라벨하면 존재하지 않는 거래일의
    #      스냅샷이 canonical 에 as-of 로 남는다 — 라벨은 데이터의 실제 기준일이어야 한다.
    monkeypatch.delenv("OPS_KR_HOLIDAYS", raising=False)
    assert _as_of(date(2026, 7, 23)) == date(2026, 7, 23)  # 목요일(거래일) — 오늘 그대로
    assert _as_of(date(2026, 7, 18)) == date(2026, 7, 17)  # 토요일 → 직전 금요일
    # 평일 공휴일도 건너뛴다 — 달력은 Planner 와 같은 OPS_KR_HOLIDAYS 를 본다(판정 분기 금지).
    monkeypatch.setenv("OPS_KR_HOLIDAYS", "2026-07-17")
    assert _as_of(date(2026, 7, 18)) == date(2026, 7, 16)


def test_as_of_fails_loud_when_no_trading_day_in_range(monkeypatch):
    # WHY: 탐색 상한(10일)을 넘기는 건 달력이 아니라 휴장일 주입이 잘못된 상황이다 —
    #      조용히 아무 날짜나 찍으면 그 오라벨이 canonical 까지 그대로 흘러간다.
    monkeypatch.setenv(
        "OPS_KR_HOLIDAYS", ",".join(f"2026-07-{d:02d}" for d in range(9, 24))
    )
    with pytest.raises(ValueError, match="OPS_KR_HOLIDAYS"):
        _as_of(date(2026, 7, 23))


def test_fetch_attaches_meta_and_preserves_original():
    # WHY: raw 는 원본 필드를 무변형 보존하고 수집 메타(our_etf_id/market/isin/trd_dd/
    #      fetched_at)만 덧붙인다 — 특히 우리가 지정한 기준일(trd_dd)이 as-of 로 남아야 한다.
    src = _source({"KR7069500007": {"output": [
        {"COMPST_ISU_CD": "005930", "COMPST_ISU_NM": "삼성전자", "COMPST_RTO": "30.5"}]}})
    # 자정을 넘겨도 안 깨지게 fetch 앞뒤로 기대값을 잡는다(둘 다 정답인 유일한 순간이다).
    before = _as_of(datetime.now(KST).date()).strftime("%Y%m%d")
    rows = list(src.fetch())
    after = _as_of(datetime.now(KST).date()).strftime("%Y%m%d")

    assert len(rows) == 1
    row = rows[0]
    assert row["our_etf_id"] == "069500" and row["market"] == "KR"
    assert row["isin"] == "KR7069500007" and "fetched_at" in row
    # 라벨은 실제 기준일(_as_of) — 오늘 날짜를 그대로 찍지 않는다(ALPHA-387).
    assert row["trd_dd"] in {before, after}
    # 원본 필드 무변형 보존.
    assert row["COMPST_ISU_CD"] == "005930" and row["COMPST_RTO"] == "30.5"


def test_foreign_underlying_dash_preserved():
    # WHY: 해외기초 ETF(TIGER美S&P500)는 비중·금액이 `-`(대시)로 온다 — bronze 무변형이라
    #      조용히 버리거나 0 으로 강제하지 않고 그대로 보존한다(정규화는 후속 canonical 소관).
    src = _source({"KR7360750004": {"output": [
        {"COMPST_ISU_CD": "AAPL", "COMPST_RTO": "-", "VALU_AMT": "-", "COMPST_ISU_CU1_SHRS": "12"}]}},
        etf_map={"360750": "KR7360750004"})
    [row] = list(src.fetch())
    assert row["COMPST_RTO"] == "-" and row["VALU_AMT"] == "-"
    assert row["COMPST_ISU_CU1_SHRS"] == "12"


def test_login_once_and_cookie_attached():
    # WHY: 로그인은 run 당 1회(ETF마다 로그인 금지, KIS 토큰 규약과 동형)이고, 그 세션
    #      JSESSIONID 가 매 getJsonData 요청의 Cookie 헤더로 붙어야 게이트를 통과한다.
    auth = FakeAuth("SESS999")
    src = _source({"KR7069500007": {"output": [{"COMPST_ISU_CD": "A"}]},
                   "KR7360750004": {"output": [{"COMPST_ISU_CD": "B"}]}},
                  etf_map={"069500": "KR7069500007", "360750": "KR7360750004"}, auth=auth)
    list(src.fetch())
    assert auth.calls == 1  # 2개 ETF 인데 로그인은 1회
    assert src.client.cookies == ["JSESSIONID=SESS999", "JSESSIONID=SESS999"]


def test_plan_maps_and_sets_planned_count():
    # WHY: etf_map 이 곧 수집 유니버스 — plan 은 (our_etf_id, isin) 로 정렬 매핑하고,
    #      planned_etfs 를 세워 스텝이 '매핑 0개'를 skip 으로 드러내게 한다.
    src = _source({"KR7069500007": {"output": [{"x": 1}]}, "KR7360750004": {"output": [{"x": 1}]}},
                  etf_map={"069500": "KR7069500007", "360750": "KR7360750004"})
    list(src.fetch())
    assert src.planned_etfs == 2
    assert src.plan() == [("069500", "KR7069500007"), ("360750", "KR7360750004")]


def test_empty_output_isolated_as_failure():
    # WHY: ETF 는 정의상 구성종목이 있으므로 빈 output(비영업일·미게시·잘못된 ISIN)은
    #      정상이 아니다 — fail-loud 하게 ETF 단위 실패로 격리한다(런은 partial/error).
    src = _source({"KR7069500007": {"output": [{"x": 1}]}, "KR7360750004": {"output": []}},
                  etf_map={"069500": "KR7069500007", "360750": "KR7360750004"})
    rows = list(src.fetch())
    assert len(rows) == 1  # KODEX200 만
    assert len(src.fetch_failures) == 1
    assert src.fetch_failures[0]["our_etf_id"] == "360750"
    assert "empty output" in src.fetch_failures[0]["error"]


def test_non_object_output_is_failure():
    # WHY: 200 인데 output 이 없거나 비-list(오류 응답·스키마 드리프트)면 조용한 0행 처리
    #      금지 — ETF 실패로 올려 fail loud(US 어댑터의 '비배열 응답' 처리와 동형).
    src = _source({"KR7069500007": {"output": "nope"}})
    list(src.fetch())
    assert len(src.fetch_failures) == 1
    assert "output 이상" in src.fetch_failures[0]["error"]


def test_malformed_row_skipped_others_preserved():
    # WHY: output 배열에 dict 아닌 행(null·문자열)이 섞여도 한 행이 남은 수집을 끊지
    #      않는다 — 불량 행은 기록 후 스킵하고 정상 행은 보존한다.
    src = _source({"KR7069500007": {"output": [{"COMPST_ISU_CD": "A"}, None, "junk"]}})
    rows = list(src.fetch())
    assert len(rows) == 1 and len(src.fetch_failures) == 2


def test_stopfetch_aborts_whole_source():
    # WHY: 4xx/429(미로그인 400 LOGOUT 포함)는 세션·쿼터 문제라 ETF 단위 격리 대상이
    #      아니다 — 소스 전체를 중단해야 한다.
    src = _source({"KR7069500007": StopFetch("400 LOGOUT")})
    with pytest.raises(StopFetch):
        list(src.fetch())


def test_disabled_without_credentials():
    # WHY: 자격증명은 env 로만 주입 — 없으면 이 소스는 비활성(스텝이 skip 으로 드러냄).
    config = KrxEtfSourceConfig(etf_map={"069500": "KR7069500007"})
    assert KrxEtfSource(config, FakeClient({})).enabled is False


def _multi_etf_fixture(n=8):
    """ETF n종 + 각 1행 응답. 병렬/직렬 동치 비교용."""
    etf_map = {f"{100 + i:06d}": f"KR7{100 + i:06d}0" for i in range(n)}
    responses = {
        isin: {"output": [{"COMPST_ISU_CD": f"{i:06d}", "COMPST_ISU_NM": f"종목{i}",
                           "COMPST_RTO": "1.0", "MKT_ID": "STK"}]}
        for i, isin in enumerate(etf_map.values())
    }
    return etf_map, responses


def test_concurrent_output_matches_serial():
    # WHY: 병렬화는 **산출물을 바꾸지 않아야** 한다. 순서까지 같아야 raw ndjson 을 회귀 비교할
    #      수 있고, 하류(normalize 의 파티션 병합)가 수집 타이밍에 흔들리지 않는다.
    etf_map, responses = _multi_etf_fixture()

    def collect(concurrency):
        rows = list(_source(responses, etf_map=etf_map, concurrency=concurrency).fetch())
        # fetched_at 은 수집 시각이라 런마다 다르다 — 비교 대상이 아니다.
        return [{k: v for k, v in r.items() if k != "fetched_at"} for r in rows]

    serial, parallel = collect(1), collect(4)
    assert serial == parallel
    assert len(serial) == 8


def test_login_still_once_under_concurrency():
    # WHY: 로그인은 run 당 1회 규약이다(계정당 동시세션 1개 — 사람이 겹치면 CD011). 팬아웃이
    #      워커마다 로그인하면 그 규약이 깨져 수집 전체가 죽는다. 세션 쿠키는 문자열이라
    #      공유해도 되고, 프로브에서도 6·8·12 동시에 로그아웃 0건이었다.
    etf_map, responses = _multi_etf_fixture()
    auth = FakeAuth()
    src = _source(responses, etf_map=etf_map, auth=auth, concurrency=4)
    list(src.fetch())
    assert auth.calls == 1
    assert set(src.client.cookies) == {"JSESSIONID=SESS123"}  # 전 워커가 같은 세션


def test_failure_isolated_under_concurrency():
    # WHY: 격리 규약이 동시성에서도 같아야 한다 — 한 ETF 의 이상 응답이 나머지를 죽이지 않고,
    #      기록은 남아야 한다(격리≠은폐).
    etf_map, responses = _multi_etf_fixture()
    broken = list(etf_map.values())[3]
    responses[broken] = {"output": []}  # 빈 output = fail-loud 대상
    src = _source(responses, etf_map=etf_map, concurrency=4)
    rows = list(src.fetch())
    assert len(rows) == 7
    assert [f["isin"] for f in src.fetch_failures] == [broken]


def test_worker_side_failures_sorted_on_stopfetch(monkeypatch):
    # WHY: malformed row 는 `_fetch_etf` **안**(워커 스레드)에서 기록돼 팬아웃의 입력순 기록을
    #      우회한다. StopFetch 로 빠져나가도 스텝은 그때까지의 fetch_failures 를
    #      status=stopped 로그에 쓰므로 그 목록도 결정적이어야 한다 — 정렬이 정상 종료 경로에만
    #      있으면 중단 런의 failed_etfs 순서가 실행마다 달라진다.
    #      빈 output 실패로는 이걸 못 잡는다(그건 이미 입력순 경로다) — 워커 경로를 직접 만든다.
    etf_map, responses = _multi_etf_fixture(4)
    src = _source(responses, etf_map=etf_map, concurrency=2)
    plan = src.plan()
    first, early, late = plan[0][0], plan[1][0], plan[3][0]

    real = src._fetch_etf

    def patched(our_etf_id, isin, *args, **kwargs):
        if our_etf_id == first:
            # 워커 스레드가 plan 역순으로 기록한 상황을 재현한 뒤 소스 전체를 중단시킨다.
            src._note_failure("isin-late", late, "malformed row: str")
            src._note_failure("isin-early", early, "malformed row: str")
            raise StopFetch("HTTP 429")
        return real(our_etf_id, isin, *args, **kwargs)

    monkeypatch.setattr(src, "_fetch_etf", patched)

    with pytest.raises(StopFetch):
        list(src.fetch())

    assert [f["our_etf_id"] for f in src.fetch_failures] == [early, late]  # plan 순
