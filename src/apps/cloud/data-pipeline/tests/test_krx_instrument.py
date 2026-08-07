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

def _ingest(tmp_path, by_board, *, ingest_date=None, **kw):
    storage = LocalStorage(tmp_path / "lake")
    source = _source(by_board, **kw)
    return (ingest_raw_instrument.run(storage, source, _RUN_ID, ingest_date=ingest_date),
            storage)


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


def test_gate_blocks_a_plausible_looking_but_truncated_master(tmp_path):
    """세 시장이 다 있고 형태도 멀쩡한데 **행수만 적은** 마스터를 막는다(ALPHA-829).

    WHY: 이건 행수 하한이 **유일하게** 잡는 실패다. KRX 가 페이징을 도입해 보드마다 100행만
    돌려주기 시작하면 — 시장 집합 통과, 티커 비율 1.0, 한글명 비율 1.0 — 다른 검사는 전부
    초록이다. 그렇게 착지한 300종짜리 마스터는 그럴듯해 보이고, 빠진 2,500여 종은 영구
    미해소가 된다. 이 테스트가 없으면 하한을 0 으로 낮춰도 스위트가 초록이다.
    """
    boards = _full_boards(n_kospi=100, n_kosdaq=100, n_konex=100)   # 300 < 하한 2,000
    code, storage = _ingest(tmp_path, boards)
    assert code != 0
    assert [k for k in storage.list_keys("raw/")] == []
    error = _log(storage)["error"]
    assert "행수" in error
    # 다른 검사는 통과했음을 함께 고정한다 — 안 그러면 이 테스트가 무엇을 재는지 모호해진다
    assert "시장 결손" not in error and "티커" not in error and "한글" not in error


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
    """플래그로 끈 소스는 실패가 아니라 명시적 skip 이다."""
    code, storage = _ingest(tmp_path, _full_boards(), enabled=False)
    assert code == 0
    assert [k for k in storage.list_keys("raw/")] == []
    assert _log(storage)["status"] == "skipped"


def test_missing_auth_key_skips_instead_of_firing_empty_requests(tmp_path):
    """인증키가 없으면 **요청을 보내지 않고** skip 한다(ALPHA-829).

    WHY: 시크릿 주입이 누락된 배포에서 `enabled` 가 설정 플래그만 보면 빈 `AUTH_KEY` 로
    3콜이 나가 4xx→중단(exit 1)이 된다. 그러면 로그에 남는 건 "수집 실패"인데 실제 원인은
    설정 결손이라, 고쳐야 할 곳이 가려진다. 스텝의 skip 사유 문구가 "disabled or missing
    credentials" 인데 뒤쪽이 영영 성립하지 않게 되는 것도 같은 문제다(krx_etf 와 같은 계약).
    """
    storage = LocalStorage(tmp_path / "lake")
    source = KrxInstrumentSource(
        KrxInstrumentSourceConfig(enabled=True, auth_key=None),
        client=FakeClient(_full_boards()), today=date(2026, 8, 7),
    )
    assert ingest_raw_instrument.run(storage, source, _RUN_ID) == 0
    assert source.client.calls == []          # ← 크리덴셜을 안 보면 여기서 3콜이 나간다
    assert [k for k in storage.list_keys("raw/")] == []
    assert _log(storage)["status"] == "skipped"


def test_a_whole_missing_board_is_blocked_not_saved_as_partial(tmp_path):
    """시장이 통째로 실패하면 **저장하지 않는다** — partial 로 반쪽 마스터를 남기지 않는다.

    WHY: 격리와 은폐 사이에서 이 데이터셋은 격리 쪽이 아니다. 소비자가 읽는 것은 "그
    기준일의 전종목"이라, 코넥스가 빠진 마스터는 그 시장 종목을 영구 미해소로 만든다.
    시장 결손 게이트가 실패 격리보다 **먼저** 판정하는 것이 그 뜻이다.
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    code, storage = _ingest(tmp_path, boards, raise_for={"knx": ValueError("boom")})
    assert code != 0
    log = _log(storage)
    assert log["status"] == "error"
    assert [k for k in storage.list_keys("raw/")] == []
    assert "시장 결손" in log["error"]
    assert [f["board"] for f in log["failed_boards"]] == ["KONEX"]   # 사유는 남는다


def test_every_board_failing_is_an_error_that_names_the_cause(tmp_path):
    """세 시장이 모두 실패하면 error 로 끝나고 사유가 원인을 가리킨다.

    WHY: 도달 경로가 실재한다 — `OPS_KR_HOLIDAYS` 가 주입되지 않으면 달력이 공휴일을
    거래일로 보고, 그 기준일로 물으면 세 보드가 전부 0행을 준다(README 가 경고하는 바로
    그 경우). 이때 로그가 "행수 0 < 하한" 이라고만 말하면 **증상**을 가리키게 되고, 고칠
    곳이 달력 설정이라는 걸 알 수 없다. 수집 자체가 전멸했다는 사실이 먼저 나와야 한다.
    """
    boom = ValueError("0행 — basDd 확인")
    code, storage = _ingest(tmp_path, _full_boards(),
                            raise_for={"stk": boom, "ksq": boom, "knx": boom})
    assert code != 0
    log = _log(storage)
    assert log["status"] == "error"
    assert "모든 시장 수집 실패" in log["error"]      # 증상(행수 0)이 아니라 원인
    assert log["ops"]["records_out"] == 0
    assert len(log["failed_boards"]) == 3


def test_a_board_that_breaks_midway_still_lands_and_is_marked_partial(tmp_path):
    """보드가 **행을 내다가** 깨지면 받은 만큼 저장하고 partial 로 드러낸다.

    WHY: 위 테스트와 짝이다. 시장이 아예 없는 것과, 그 시장이 일부만 온 것은 다르다 —
    후자는 세 시장이 다 있어 게이트를 지나므로 저장되고, 그 불완전함은 **상태로만** 드러난다.
    조용히 success 로 끝나면 빠진 종목을 아무도 모른다(Rule 12).
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    boards["knx"] = [*boards["knx"], "이건 객체가 아니다"]   # 100행 낸 뒤 깨진다
    code, storage = _ingest(tmp_path, boards)
    assert code != 0
    log = _log(storage)
    assert log["status"] == "partial"
    assert log["ops"]["records_out"] == 2100          # 받은 것은 남겼다
    assert [f["board"] for f in log["failed_boards"]] == ["KONEX"]


# ------------------------------------------------------- canonical 정제 스텝

def test_canonical_as_of_is_the_vendor_base_date_not_the_ingest_date(tmp_path):
    """canonical 시간축은 **벤더 기준일(bas_dd)**이지 수집일이 아니다(ALPHA-829).

    WHY: 수집일을 as_of 로 쓰면 마스터가 하루 앞선 날짜를 주장하고, 최신 스냅샷을 읽는
    로더가 존재하지 않는 거래일의 마스터를 보게 된다.

    ⚠️ 수집일(UTC)을 **고정해서** 잰다. 실제 벽시계에 맡기면 08~09시 KST(= 전날 UTC) 실행
    에서 두 날짜가 우연히 같아져 이 단언이 어느 쪽 구현에서도 참이 된다 — CI 가 도는 시각에
    따라 붙었다 떨어졌다 하는 가드는 가드가 아니다.
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    code, storage = _ingest(tmp_path, boards, ingest_date="2026-09-30")
    assert code == 0
    assert normalize_instrument_profile.run(storage, _RUN_ID) == 0
    raw_keys = [k for k in storage.list_keys("raw/") if k.endswith(".ndjson")]
    assert "/ingest_date=2026-09-30/" in raw_keys[0]        # 수집일 축
    parts = [k for k in storage.list_keys("canonical/") if k.endswith(".parquet")]
    assert len(parts) == 1
    # 기준일 축 — 수집일과 **다른 값**이고, 그 값은 벤더가 준 bas_dd 다
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

    ⚠️ 이 테스트만으로는 병합을 못 박지 못한다 — 같은 raw 를 다시 읽어 같은 파일명에
    덮어쓰므로 병합을 통째로 지워도 수가 같다. 병합 자체는 아래 union 테스트가 본다.
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    _, storage = _ingest(tmp_path, boards)
    assert normalize_instrument_profile.run(storage, _RUN_ID) == 0
    first = len(_canonical_rows(storage))
    assert normalize_instrument_profile.run(storage, "20260807T010000Z") == 0
    assert len(_canonical_rows(storage)) == first


def test_second_run_merges_with_what_is_already_in_the_partition(tmp_path):
    """나중 런만 정제해도 **앞서 착지한 행이 살아남는다**(ALPHA-829).

    WHY: 한 기준일이 여러 런에 걸쳐 채워질 수 있다. 08:00 런이 일부만 남기고(partial),
    09:00 재시도가 나머지를 새 run_id 로 남긴 뒤 `--input-run-id <두번째>` 로 정제하는
    경로가 그것이다. 이때 병합이 없으면 파티션이 **두 번째 런의 행만으로 줄어드는데**,
    로그의 `rows_written` 은 그냥 작아진 수라 정상 런과 구분되지 않는다 — 조용히 마스터가
    깎인다. 위 재실행 테스트는 같은 raw 를 다시 읽어 이 경로를 못 밟는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    first_boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    assert ingest_raw_instrument.run(storage, _source(first_boards), "RUN1") == 0
    # 첫 런은 정제까지 마쳐 파티션에 착지시킨다(08:00 런이 남긴 것)
    assert normalize_instrument_profile.run(storage, "NORM0", input_run_id="RUN1") == 0

    # 같은 기준일, 겹치지 않는 종목을 다른 run_id 로 한 번 더 받는다(09:00 재시도)
    second_boards = {
        "stk": [_row(f"7{i:05d}", f"신규코스피{i}") for i in range(1000)],
        "ksq": [_row(f"8{i:05d}", f"신규코스닥{i}") for i in range(1000)],
        "knx": [_row(f"9{i:05d}", f"신규코넥스{i}") for i in range(100)],
    }
    assert ingest_raw_instrument.run(storage, _source(second_boards), "RUN2") == 0

    # 두 번째 런만 정제한다 — 파티션에 이미 있던 첫 런 행이 남아 있어야 한다
    assert normalize_instrument_profile.run(storage, "NORM1", input_run_id="RUN2") == 0
    tickers = {r["ticker"] for r in _canonical_rows(storage)}
    assert "700000" in tickers      # 이번에 정제한 것
    assert "000000" in tickers      # ← 병합이 없으면 사라진다
    assert len(tickers) == 4200


def test_cross_board_ticker_collision_is_named_before_the_merge_hides_it(tmp_path):
    """같은 단축코드가 두 시장에서 오면 **이름을 남긴다**(ALPHA-829).

    WHY: `market` 은 항상 "KR" 이라 board 가 파티션을 가르지 않는다. 그래서 충돌이 나면
    병합이 조용히 한쪽을 덮고(같은 런은 fetched_at 이 같아 나중 것이 이긴다), 종목 하나가
    잘못된 시장 이름을 달거나 사라진다. 실측 충돌은 0건이지만 그건 우리가 강제하는
    불변식이 아니라 KRX 의 성질이다 — 깨지는 날 로그에 수만 있으면 어느 종목인지 몰라
    고칠 수 없다. `top_unresolved` 를 20개로 자르던 것과 같은 실패 양식이다(Rule 12).
    """
    boards = _full_boards(n_kospi=1000, n_kosdaq=1000, n_konex=100)
    boards["knx"][0] = _row("000000", "충돌종목")     # 코스피 000000 과 같은 코드
    _, storage = _ingest(tmp_path, boards)
    assert normalize_instrument_profile.run(storage, _RUN_ID) == 0
    log = _quality_log(storage)
    assert log["cross_board_ticker_collisions"] == {"000000": ["KONEX", "KOSPI"]}


def _quality_log(storage):
    keys = [k for k in storage.list_keys("operations_archive/")
            if "instrument_profile" in k and "data_quality" in k]
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


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
