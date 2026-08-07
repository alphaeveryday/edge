"""KRX 종목기본정보 수집·정제 테스트 (ALPHA-829).

검사하는 WHY: 이 데이터셋은 **엔티티 해소가 붙는 이름의 유일한 출처**다(뉴스 표기에 가까운
KRX 종목약명). 이름이 조용히 깨지거나 시장 하나가 빠지면 그 종목들이 영구 미해소가 되는데,
그건 몇 주 뒤 "해소율이 왜 떨어졌지"로만 드러난다 — 그래서 게이트가 저장 **전**에 있고,
기준일 축이 수집일과 갈리지 않는지까지 고정한다.
"""

import json
from datetime import date

import pytest

from data_pipeline.config import KrxInstrumentSource as KrxInstrumentSourceConfig
from data_pipeline.lake import LocalStorage
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.krx_instrument import KrxInstrumentSource
from data_pipeline.steps import ingest_raw_instrument, normalize_instrument_profile

_RUN_ID = "20260807T000000Z"


def _row(code, abbrv, nm=None, **over):
    row = {"ISU_CD": f"KR7{code}00{code[:1]}", "ISU_SRT_CD": code, "ISU_NM": nm or f"{abbrv}보통주",
           "ISU_ABBRV": abbrv, "ISU_ENG_NM": "X", "LIST_DD": "2000/01/01",
           "MKT_TP_NM": "KOSPI", "SECUGRP_NM": "주권", "SECT_TP_NM": "-",
           "KIND_STKCERT_TP_NM": "보통주", "PARVAL": "5000", "LIST_SHRS": "1000000"}
    row.update(over)
    return row


class FakeClient:
    """보드별 응답을 돌려주는 가짜 운반 계층. 예외를 심어 실패 경로도 만든다."""

    def __init__(self, by_board, *, raise_for=None):
        self.by_board = by_board          # {"stk": [row, ...], ...}
        self.raise_for = raise_for or {}  # {"knx": Exception(...)}
        self.calls = []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        board = url.split("/sto/")[1].split("_")[0]
        self.calls.append((url, dict(headers or {})))
        if board in self.raise_for:
            raise self.raise_for[board]
        return json.dumps({"OutBlock_1": self.by_board.get(board, [])})


def _source(by_board, *, raise_for=None, enabled=True, today=date(2026, 8, 7)):
    return KrxInstrumentSource(
        KrxInstrumentSourceConfig(enabled=enabled, auth_key="secret-key"),
        client=FakeClient(by_board, raise_for=raise_for),
        today=today,
    )


def _full_boards(n_kospi=2, n_kosdaq=2, n_konex=1):
    """게이트 하한을 넘기는 최소 구성 — 하한 자체는 별도 테스트가 본다."""
    return {
        "stk": [_row(f"0{i:05d}", f"코스피{i}") for i in range(n_kospi)],
        "ksq": [_row(f"1{i:05d}", f"코스닥{i}") for i in range(n_kosdaq)],
        "knx": [_row(f"2{i:05d}", f"코넥스{i}") for i in range(n_konex)],
    }


# ---------------------------------------------------------------- 소스 어댑터

def test_base_date_is_the_previous_trading_day_not_today():
    """기준일은 **직전 거래일**이지 오늘이 아니다(ALPHA-829).

    WHY: KRX 서비스 SQL 이 `basDd < 오늘` 이라 오늘을 보내면 0행이 온다. 그 0행은 "상장
    종목이 없다"가 아니라 "물어본 날이 틀렸다"인데, 날짜를 오늘로 잡으면 매 런이 그 0행을
    받게 되고 게이트가 없으면 빈 마스터가 착지한다. 금요일(08-07) 실행이면 목요일이다.
    """
    assert _source({}, today=date(2026, 8, 7)).base_date() == "20260806"
    # 월요일 실행은 주말을 건너뛰어 금요일로 — 달력을 안 쓰면 토요일을 물어 0행이 온다
    assert _source({}, today=date(2026, 8, 10)).base_date() == "20260807"


def test_auth_key_goes_in_the_header_and_no_cookie_is_sent():
    """인증은 `AUTH_KEY` 헤더 하나이고 **쿠키를 보내지 않는다**(ALPHA-829).

    WHY: 같은 저장소의 `krx_etf` 는 계정 로그인 세션(JSESSIONID)을 유지한다. KRX 로그인
    쿠키는 `Domain=.krx.co.kr` 라 이 호스트로도 전송되는 범위이고, 이 API 응답도 자기
    JSESSIONID 를 준다(라이브 실측). 여기에 세션 기반 클라이언트를 끌어오면 두 레인이 한
    쿠키 자를 공유하게 된다 — 그 결합을 애초에 못 만들도록 헤더만 쓰는 것을 고정한다.
    """
    source = _source(_full_boards())
    list(source.fetch())
    assert len(source.client.calls) == 3
    for url, headers in source.client.calls:
        assert headers == {"AUTH_KEY": "secret-key"}   # 쿠키 헤더가 붙으면 여기서 깨진다
        assert url.startswith("https://data-dbg.krx.co.kr/svc/apis/sto/")
        assert url.endswith("_isu_base_info.json?basDd=20260806")


def test_all_three_markets_are_queried():
    """시장 3개를 모두 부른다 — 한 엔드포인트가 전종목을 주지 않는다.

    WHY: 하나를 빠뜨리면 그 시장 종목이 마스터에 없어 영구 미해소가 되는데, 행수만 보면
    코스피·코스닥이 채워 줘서 정상처럼 보인다.
    """
    source = _source(_full_boards())
    boards = {r["board"] for r in source.fetch()}
    assert boards == {"KOSPI", "KOSDAQ", "KONEX"}


def test_empty_board_is_a_failure_not_a_quiet_zero():
    """정상 시장이 0종일 수는 없다 — 빈 배열은 실패로 격리한다(Rule 12)."""
    source = _source({**_full_boards(), "knx": []})
    rows = list(source.fetch())
    assert {r["board"] for r in rows} == {"KOSPI", "KOSDAQ"}
    assert [f["board"] for f in source.fetch_failures] == ["KONEX"]
    assert "basDd" in source.fetch_failures[0]["error"]


def test_stop_fetch_propagates_because_it_is_a_source_wide_problem():
    """4xx/429 는 소스 전체 문제라 중단이다 — 인증키 오류·활용신청 미승인이 여기 온다."""
    source = _source(_full_boards(), raise_for={"stk": StopFetch("401")})
    with pytest.raises(StopFetch):
        list(source.fetch())


# ------------------------------------------------------------------ raw 스텝

def _ingest(tmp_path, by_board, **kw):
    storage = LocalStorage(tmp_path / "lake")
    source = _source(by_board, **kw)
    return ingest_raw_instrument.run(storage, source, _RUN_ID), storage


def _log(storage, prefix="operations_archive/"):
    keys = [k for k in storage.list_keys(prefix) if k.endswith(".json")]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_sanity_gate_blocks_the_write_not_just_the_log(tmp_path):
    """게이트 위반이면 **raw 를 쓰지 않는다**(ALPHA-829 완료 조건).

    WHY: 저장 후에 검사하면 깨진 마스터가 이미 레이크에 있고, 다음 런이 성공하기 전까지
    canonical 이 그걸 읽는다. 조용한 부분 적재를 만들지 않으려면 게이트가 쓰기 앞에 있어야
    한다 — 로그만 빨갛고 데이터는 착지하는 형태를 금지한다.
    """
    boards = {"stk": [_row("005930", "삼성전자")], "ksq": [], "knx": []}
    code, storage = _ingest(tmp_path, boards)
    assert code != 0
    assert [k for k in storage.list_keys("raw/")] == []      # ← 한 바이트도 안 썼다
    log = _log(storage)
    assert log["status"] == "error"
    assert log["ops"]["records_out"] == 0


def test_gate_catches_a_missing_market_that_row_count_alone_would_miss(tmp_path):
    """행수 하한만으로는 코넥스(109종) 결손을 못 잡는다 — 시장 집합도 본다.

    WHY: 코스피·코스닥이 2,700종을 채우면 행수 하한은 통과한다. 그런데 코넥스가 통째로
    빠진 마스터는 그 시장 종목을 영구 미해소로 만든다. 두 검사는 서로를 대신하지 못한다.
    """
    boards = _full_boards(n_kospi=1200, n_kosdaq=1200, n_konex=0)
    code, storage = _ingest(tmp_path, boards)
    assert code != 0
    assert [k for k in storage.list_keys("raw/")] == []
    assert "시장 결손" in _log(storage)["error"]
    assert "['KONEX']" in _log(storage)["error"]


def test_gate_lets_one_odd_ticker_through_but_blocks_a_systemic_break(tmp_path):
    """티커 게이트는 **비율**이다 — 한 건은 통과시키고 필드가 밀린 파손은 막는다.

    WHY: 두 실패는 성질이 다르다. 한 건은 KRX 가 새 코드 체계를 쓰기 시작한 것일 수 있고,
    그것 때문에 그날 마스터를 통째로 버리면 다운스트림이 낡은 스냅샷을 본다(개별 행은
    정제단이 사유와 함께 떨군다). 반면 필드가 통째로 밀리면 전 종목의 티커가 어긋나는데,
    그건 저장 전에 막아야 한다. 이 테스트가 없으면 게이트를 비율로 바꾼 것이 그냥
    '가드를 약하게 만든 것'과 구분되지 않는다.
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    boards["stk"][0]["ISU_SRT_CD"] = "NOPE!!"          # 1 / 2100 → 통과
    code, storage = _ingest(tmp_path, boards)
    assert code == 0
    assert len([k for k in storage.list_keys("raw/") if k.endswith(".ndjson")]) == 1

    broken = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    for row in broken["stk"]:                           # 1000 / 2100 → 막힌다
        row["ISU_SRT_CD"] = row["ISU_NM"]               # 필드가 한 칸 밀린 모양
    code, storage2 = _ingest(tmp_path / "b", broken)
    assert code != 0
    assert [k for k in storage2.list_keys("raw/")] == []
    assert "티커 형태 통과 비율" in _log(storage2)["error"]


def test_gate_catches_broken_names(tmp_path):
    """한글 종목약명 비율이 무너지면 막는다 — 인코딩·필드 이동의 첫 징후다."""
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    for row in boards["stk"]:
        row["ISU_ABBRV"] = "????"      # 깨진 이름
    code, storage = _ingest(tmp_path, boards)
    assert code != 0
    assert "한글 종목약명 비율" in _log(storage)["error"]


def test_healthy_run_writes_raw_and_success_log(tmp_path):
    """게이트를 통과하면 market 파티션에 ndjson 을 쓰고 성공으로 남긴다."""
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    code, storage = _ingest(tmp_path, boards)
    assert code == 0
    keys = [k for k in storage.list_keys("raw/") if k.endswith(".ndjson")]
    assert len(keys) == 1
    assert "/dataset=instrument_profile/market=KR/" in keys[0]
    assert f"/run_id={_RUN_ID}" in keys[0]
    log = _log(storage)
    assert log["status"] == "success"
    assert log["ops"]["records_out"] == 2100
    assert log["gate_violations"] == []


def test_disabled_source_skips_without_failing(tmp_path):
    """크리덴셜 미주입(로컬)은 실패가 아니라 명시적 skip 이다."""
    code, storage = _ingest(tmp_path, _full_boards(), enabled=False)
    assert code == 0
    assert [k for k in storage.list_keys("raw/")] == []
    assert _log(storage)["status"] == "skipped"


# ------------------------------------------------------- canonical 정제 스텝

def test_canonical_as_of_is_the_vendor_base_date_not_the_ingest_date(tmp_path):
    """canonical 시간축은 **벤더 기준일(bas_dd)**이지 수집일이 아니다(ALPHA-829).

    WHY: KRX 는 당일 조회를 막아 수집일과 기준일이 **항상 다르다**. 수집일을 as_of 로 쓰면
    마스터가 하루 앞선 날짜를 주장하게 되고, 최신 스냅샷을 읽는 로더가 존재하지 않는
    거래일의 마스터를 보게 된다. 이 테스트는 08-07 에 수집한 08-06 기준 데이터가
    `as_of_date=2026-08-06` 에 착지하는 것을 고정한다.
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    code, storage = _ingest(tmp_path, boards)
    assert code == 0
    assert normalize_instrument_profile.run(storage, _RUN_ID) == 0
    parts = [k for k in storage.list_keys("canonical/") if k.endswith(".parquet")]
    assert len(parts) == 1
    assert "canonical/reference/instrument_profile/market=KR/as_of_date=2026-08-06/" in parts[0]


def test_canonical_keeps_both_names(tmp_path):
    """약명(표시)과 정식명(감사)을 **둘 다** 보존한다.

    WHY: 엔티티 해소가 붙는 건 뉴스 표기에 가까운 약명(`현대차`)이고, 정식명
    (`현대자동차보통주`)은 대조용이다. 약명만 남기면 감사가 불가능하고, 정식명만 남기면
    이 소스를 고른 이유(ALPHA-829 의 DART 대비 우위)가 사라진다.
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    boards["stk"][0] = _row("005380", "현대차", nm="현대자동차보통주")
    _, storage = _ingest(tmp_path, boards)
    assert normalize_instrument_profile.run(storage, _RUN_ID) == 0
    rows = _canonical_rows(storage)
    hyundai = next(r for r in rows if r["ticker"] == "005380")
    assert hyundai["display_name"] == "현대차"
    assert hyundai["legal_name"] == "현대자동차보통주"
    assert hyundai["security_group"] == "주권"


def _canonical_rows(storage):
    import io

    import pyarrow.parquet as pq

    rows = []
    for key in storage.list_keys("canonical/"):
        if key.endswith(".parquet"):
            rows.extend(pq.read_table(io.BytesIO(storage.get_bytes(key))).to_pylist())
    return rows


def test_normalize_is_idempotent_across_reruns(tmp_path):
    """같은 기준일을 다시 정제해도 행이 늘지 않는다 — ticker 키 멱등 병합.

    WHY: 마스터가 재실행마다 부풀면 `load_instruments` 가 같은 종목을 여러 번 보고,
    자연키 멱등에 기대는 ID 발번이 무의미해진다.
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    _, storage = _ingest(tmp_path, boards)
    assert normalize_instrument_profile.run(storage, _RUN_ID) == 0
    first = len(_canonical_rows(storage))
    assert normalize_instrument_profile.run(storage, "20260807T010000Z") == 0
    assert len(_canonical_rows(storage)) == first


def test_bad_ticker_row_is_dropped_with_a_reason(tmp_path):
    """형태를 어긴 티커는 사유와 함께 탈락한다 — 조용히 버리지 않는다(Rule 12).

    WHY: 티커가 마스터의 자연키다. 형태가 깨진 행이 통과하면 FK 가 붙을 수 없는 종목이
    생기고, 그게 몇 건인지 로그에 없으면 아무도 모른다.
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    boards["stk"][0]["ISU_SRT_CD"] = "NOPE!!"
    _, storage = _ingest(tmp_path, boards)
    assert normalize_instrument_profile.run(storage, _RUN_ID) == 0
    quality = [k for k in storage.list_keys("operations_archive/")
               if "instrument_profile" in k and "data_quality" in k]
    log = json.loads(storage.get_bytes(quality[0]).decode("utf-8"))
    assert log["dropped_by_reason"]["bad_ticker"] == 1
    assert log["rows_written"] == 2099
