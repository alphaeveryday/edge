"""KIS 장중 투자자 추정 어댑터 테스트 (ALPHA-767) — 네트워크 없음.

각 테스트는 '왜'를 주석으로 남긴다(AGENTS Rule 9). EOD 어댑터(`test_kis_investor`)와 같은
관례(격리·fail-loud·bronze 무변형)를 지키는지 잠그되, **다른 축만** 검증한다:

* 날짜창이 없다 — 창을 넘겨도 요청에 안 실리고 경고만 남는다(소급 불가라 조용한 무시 금지)
* 페이지네이션이 없다 — 종목당 정확히 1콜
* `OPSQ2001` 재시도가 **없다** — 그건 EOD 서빙경계라 여기선 자가해소 대상이 아니다
* 응답에 날짜가 없으므로 `asof_date` provenance 를 우리가 붙인다(없으면 canonical 이 어느
  거래일인지 복원 불가)
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from data_pipeline.config import KisInvestorSource as Cfg
from data_pipeline.sources import kis_investor_estimate
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_investor_estimate import KisInvestorEstimateSource

KST = timezone(timedelta(hours=9))
# 장중 슬롯 한가운데로 못박는다 — 이 어댑터는 `asof_date` 를 '지금'에서 만들므로 실행 시각에
# 따라 단언이 갈리면 안 된다.
_NOW = datetime(2026, 8, 5, 11, 25, 0, tzinfo=KST)

_TOKEN = json.dumps({"access_token": "tok", "access_token_token_expired": "2026-07-07 00:00:00"})
_RATE = json.dumps({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
_BOUNDARY = json.dumps({"rt_cd": "2", "msg_cd": "OPSQ2001", "msg1": "TIME LIMIT 00:00 ~ 15:40"})


def _row(slot: str = "1120") -> dict:
    # 실측 필드명(.dev/etf-flow-collection-plan.md §2.5) — 가집계(`fake`)라 잠정이다.
    return {
        "bsop_hour_gb": slot,
        "frgn_fake_ntby_qty": "39367",
        "orgn_fake_ntby_qty": "11941",
        "sum_fake_ntby_qty": "51308",
    }


def _ok(rows) -> str:
    return json.dumps({"rt_cd": "0", "output2": rows})


def _qs(url: str, key: str) -> str:
    return url.split(f"{key}=")[1].split("&")[0] if f"{key}=" in url else ""


class FakeClient:
    """POST=토큰, GET=심볼별 응답(리스트를 순서대로 소비). 대기는 기록만 하고 no-op."""

    def __init__(self, responses, token_body: str = _TOKEN):
        self.responses = responses
        self.token_body = token_body
        self.urls: list[str] = []
        self.sleeps: list[float] = []
        self._idx: dict[str, int] = defaultdict(int)

    def _sleep(self, secs):
        self.sleeps.append(secs)

    def request(self, method, url, *, headers=None, data=None, decode=True):
        if method == "POST":
            return self.token_body
        self.urls.append(url)
        pages = self.responses.get(_qs(url, "MKSC_SHRN_ISCD"), [])
        idx = self._idx[_qs(url, "MKSC_SHRN_ISCD")]
        self._idx[_qs(url, "MKSC_SHRN_ISCD")] += 1
        return pages[idx] if idx < len(pages) else _ok([])


@pytest.fixture
def frozen(monkeypatch):
    """어댑터가 보는 '지금'을 못박고, **어떤 tz 로 물었는지 기록**한다.

    WHY 기록까지 하는가: `tz is KST` 같은 동일성 비교로 가짜 시계를 만들면 테스트 모듈과
    어댑터 모듈이 각자 만든 `timezone(+09:00)` 이 다른 객체라 항상 else 로 떨어진다. 그래도
    장중 시각은 KST 와 UTC 의 **날짜가 같아**(09:00~15:30 KST = 00:00~06:30 UTC) `asof_date`
    단언이 우연히 통과하고, 프로덕션이 `datetime.now(timezone.utc)` 로 회귀해도 안 깨진다.
    그래서 시계는 어떤 tz 든 정확히 환산해 주고(faithful), '거래일 라벨은 KST 축'이라는
    계약은 아래 offset 단언이 직접 잠근다(Rule 9).
    """
    asked: list = []

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            asked.append(tz)
            return _NOW.astimezone(tz) if tz is not None else _NOW.replace(tzinfo=None)

    monkeypatch.setattr(kis_investor_estimate, "datetime", _Clock)
    return asked


def _source(responses, **cfg):
    client = FakeClient(responses)
    base = {"app_key": "k", "app_secret": "s", "symbol_map": {}}
    return KisInvestorEstimateSource(Cfg(**{**base, **cfg}), client), client


def test_종목당_1콜이고_페이지네이션이_없다(frozen):
    """WHY: 이 API 는 날짜창이 없어 페이지를 물릴 축 자체가 없다. EOD 어댑터의 페이지네이션을
    실수로 이식하면 같은 오늘치를 반복 요청해 앱키 유량만 태운다."""
    src, client = _source({"005930": [_ok([_row("0930"), _row("1120")])]})

    rows = list(src.fetch(["005930"]))

    assert len(client.urls) == 1
    assert len(rows) == 2
    assert _qs(client.urls[0], "MKSC_SHRN_ISCD") == "005930"


def test_응답에_없는_거래일을_KST_축으로_붙인다(frozen):
    """WHY: 벤더 응답에 날짜 필드가 하나도 없다. 우리가 안 붙이면 canonical 이 어느 거래일의
    스냅샷인지 복원할 수 없어, 슬롯만 있고 날짜가 없는 행이 쌓인다(KRX holdings 의 trd_dd 를
    우리가 라벨하는 것과 같은 형태).

    라벨 **축이 KST 임**도 함께 잠근다 — UTC 로 회귀하면 장중 시각대에선 날짜가 우연히 같아
    값 단언만으로는 안 깨지고, 그 회귀는 09:00 이전 실행에서야 하루 밀린 라벨로 터진다."""
    src, _ = _source({"005930": [_ok([_row()])]})

    (row,) = list(src.fetch(["005930"]))

    assert row["asof_date"] == "2026-08-05"
    assert timedelta(hours=9) in {tz.utcoffset(None) for tz in frozen if tz is not None}
    assert row["our_ticker"] == "005930"
    assert row["market"] == "KR"
    # bronze 무변형 — 원본 필드는 손대지 않는다.
    assert row["frgn_fake_ntby_qty"] == "39367"
    assert row["bsop_hour_gb"] == "1120"


def test_날짜창은_요청에_안_실리고_경고만_남긴다(frozen, caplog):
    """WHY: 소급이 영구 불가라 창을 조용히 무시하면 갭을 메우려던 운영자가 오늘치를 받고
    복구된 줄 착각한다. CLI 가 1차로 막지만 어댑터도 흔적을 남긴다(Rule 12)."""
    src, client = _source({"005930": [_ok([_row()])]})

    with caplog.at_level("WARNING"):
        list(src.fetch(["005930"], "2026-08-01", "2026-08-04"))

    assert "FID_INPUT_DATE_1" not in client.urls[0]
    assert "2026-08-01" not in client.urls[0]
    assert any("소급 불가" in r.getMessage() for r in caplog.records)


def test_초당한도는_재시도하고_서빙경계는_즉시_격리한다(frozen):
    """WHY: 두 오류코드의 성질이 다르다. EGW00201 은 기다리면 풀리지만, OPSQ2001 은 EOD 확정
    데이터의 서빙경계라 **이 엔드포인트에선 자가해소되는 조건이 아니다** — 재시도를 이식하면
    풀릴 수 없는 것을 종목마다 기다려 유니버스 크기만큼 헛돈다(ALPHA-562 가 고친 결함)."""
    src, client = _source({
        "005930": [_RATE, _ok([_row()])],   # 초당한도 → 재시도 후 성공
        "000660": [_BOUNDARY],              # 서빙경계 → 대기 없이 격리
    })

    rows = list(src.fetch(["005930", "000660"]))

    assert len(rows) == 1                      # 005930 만 성공
    assert client.sleeps == [0.7]              # 초당한도 1회분만 기다렸다
    assert len(src.fetch_failures) == 1
    assert src.fetch_failures[0]["symbol"] == "000660"
    assert "OPSQ2001" in src.fetch_failures[0]["error"]


def test_빈_output2_는_정상이고_비list_는_실패다(frozen):
    """WHY: ETF 는 거래소가 장중 투자자 귀속을 생산하지 않아 **0행이 정상**이다. 반대로
    rt_cd=0 인데 output2 가 list 가 아니면 malformed success 라, 정상 빈 응답으로 위장시키면
    스키마 드리프트가 조용히 통과한다."""
    src, _ = _source({"069500": [_ok([])], "000660": [json.dumps({"rt_cd": "0"})]})

    rows = list(src.fetch(["069500", "000660"]))

    assert rows == []
    assert [f["symbol"] for f in src.fetch_failures] == ["000660"]


def test_심볼_실패는_격리되고_4xx_는_전체_중단이다(frozen):
    """WHY: 격리≠은폐 — 한 종목의 오류가 남은 유니버스를 끊으면 안 되지만, 4xx/429 는 키·쿼터
    문제라 계속 돌면 전량 실패를 반복할 뿐이다(EOD 어댑터와 같은 규약)."""

    class Boom(FakeClient):
        def request(self, method, url, *, headers=None, data=None, decode=True):
            if method == "GET" and "000660" in url:
                raise StopFetch("429")
            return super().request(method, url, headers=headers, data=data, decode=decode)

    src = KisInvestorEstimateSource(
        Cfg(app_key="k", app_secret="s"), Boom({"005930": [_ok([_row()])]})
    )

    with pytest.raises(StopFetch):
        list(src.fetch(["005930", "000660"]))


def test_국내코드가_아니면_계획에서_빠진다(frozen):
    """WHY: KIS 는 국내 전용이다. US 심볼이 새면 무의미한 전량 실패가 되고, planned_symbols=0
    을 스텝이 skip 으로 드러내는 경로도 막힌다."""
    src, client = _source({"005930": [_ok([_row()])]})

    list(src.fetch(["005930", "AAPL"]))

    assert src.planned_symbols == 1
    assert len(client.urls) == 1


def test_슬롯_없는_행은_보존하되_실패로_드러낸다(frozen):
    """WHY: `bsop_hour_gb` 는 canonical 정체성 키(market·ticker·trade_date·asof_slot)의 일부다.
    없으면 그 행이 어느 시점 값인지 영영 알 수 없는데, 조용히 저장하면 **쓸 수 없는 행이
    records_out 에 섞여** 정상 수집과 구분되지 않는다. 행은 보존(bronze)하고 실패로 드러낸다."""
    slotless = {"frgn_fake_ntby_qty": "1", "bsop_hour_gb": "  "}
    src, _ = _source({"005930": [_ok([slotless, slotless, _row()])]})

    rows = list(src.fetch(["005930"]))

    assert len(rows) == 3  # bronze 무변형 — 원본은 버리지 않는다
    # ⚠️ 심볼당 1건이다. fetch_failures 는 스텝이 `records_failed_symbols` 로 세는 **심볼 단위**
    #    목록이라, 행마다 append 하면 심볼 카운터가 행 수로 부풀어 단위가 어긋난다.
    assert len(src.fetch_failures) == 1
    assert "bsop_hour_gb" in src.fetch_failures[0]["error"]


def test_malformed_행도_심볼당_1회만_실패로_센다(frozen):
    """WHY: 슬롯 결측과 같은 이유다 — fetch_failures 는 심볼 단위 목록이라 행마다 세면
    실패 심볼 1개가 행 수만큼 부푼다. 두 사유(비-dict·슬롯결측) 어느 쪽도 예외가 아니다."""
    src, _ = _source({"005930": [_ok(["못난행", 42, _row()])]})

    rows = list(src.fetch(["005930"]))

    assert len(rows) == 1                    # 정상 행만 저장
    assert len(src.fetch_failures) == 1      # 비-dict 2건이지만 심볼 1건
    assert "malformed" in src.fetch_failures[0]["error"]


def test_비거래일에는_거래일_라벨을_지어내지_않는다(monkeypatch):
    """WHY: 거래일을 우리가 붙이는데(응답에 날짜가 없다) 휴장일에 KIS 가 직전 슬롯을 그대로
    주면 **어제 데이터가 오늘 거래일로 저장된다**. 이 소스는 소급 재조회가 없어 잘못 붙은
    라벨을 응답으로 되돌릴 수 없다 — 그러니 라벨하지 말고 죽어야 한다(Rule 12)."""
    saturday = datetime(2026, 8, 8, 11, 25, 0, tzinfo=KST)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return saturday.astimezone(tz) if tz is not None else saturday.replace(tzinfo=None)

    monkeypatch.setattr(kis_investor_estimate, "datetime", _Clock)
    src, client = _source({"005930": [_ok([_row()])]})

    with pytest.raises(ValueError, match="거래일이 아니다"):
        list(src.fetch(["005930"]))
    assert client.urls == []  # 토큰·질의를 태우기 전에 죽는다


def test_개장_전에는_오늘_라벨을_붙이지_않는다(monkeypatch):
    """WHY: 거래일 검사만으로는 부족하다 — 월요일 08:00 도 `is_trading_day` 는 참이다. 그때
    KIS 가 금요일 마지막 슬롯을 주면 그 행이 **월요일 거래일로** 저장되고, 소급 재조회가
    없어 되돌릴 수 없다. 첫 슬롯(09:30) 전에는 오늘의 추정이 존재하지 않는다."""
    premarket = datetime(2026, 8, 5, 8, 0, 0, tzinfo=KST)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return premarket.astimezone(tz) if tz is not None else premarket.replace(tzinfo=None)

    monkeypatch.setattr(kis_investor_estimate, "datetime", _Clock)
    src, client = _source({"005930": [_ok([_row()])]})

    with pytest.raises(ValueError, match="첫 슬롯"):
        list(src.fetch(["005930"]))
    assert client.urls == []


def test_크리덴셜이_없으면_비활성이다(frozen):
    """WHY: 로컬·미주입 환경에서 소스가 활성으로 보이면 스텝이 실패로 마감한다 — 크리덴셜
    부재는 실패가 아니라 명시적 skip 이어야 한다."""
    src = KisInvestorEstimateSource(Cfg(app_key=None, app_secret="s"), FakeClient({}))
    assert src.enabled is False
