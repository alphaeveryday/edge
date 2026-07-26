"""KIS(한국투자) 가격 일봉 소스 어댑터 (S004 국내 — 가격 Step1 원본저장).

API: 국내주식기간별시세(일/주/월/년) inquire-daily-itemchartprice, tr_id FHKST03010100.
콜당 최대 100건, `FID_INPUT_DATE_1~2` 기간지정, `FID_PERIOD_DIV_CODE=D`(일봉).
과거 장기 일봉을 100건 윈도우로 최신→과거 순 페이지네이션한다.

FMP 가격 어댑터(fmp_price.py)와 같은 관례 인터페이스(source_name·enabled·plan·fetch·
fetch_failures·planned_symbols)를 지켜 기존 `ingest_price_raw` 스텝을 그대로 재사용한다.
차이는 (1) 인증이 OAuth 토큰(run 당 1회, kis_auth) (2) KRX 로컬 시장이라 market 은 항상 KR
(3) 초당한도(EGW00201)가 HTTP 429 가 아니라 응답 본문으로 와 어댑터가 재시도한다는 점.

raw 존에는 output2 행 원본에 수집 provenance(our_ticker·market·kis_symbol·fetched_at)만
붙여 그대로 낸다 — 필드 선별·OHLCV 정합성(ALPHA-133)·정규화는 후속 canonical 소관이다
(bronze 무변형: FMP 가격 어댑터와 동일하게 raw 는 받은 행을 손대지 않는다).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from ..config import KisPriceSource
from ..ops.trading_calendar import is_trading_day
from ..parse import krx_short_code
from .http import PoliteClient, StopFetch
from .kis_auth import KisAuth, domain_for

logger = logging.getLogger(__name__)

TR_ID_DAILY = "FHKST03010100"
PATH_DAILY = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
MARKET_DIV = "J"  # J: KRX 주식
# 무한 페이지 방어용 백스톱. 종목 이력이 끝나면 new==0 으로, 창 하한에 닿으면
# earliest<=d1 로 먼저 멈춘다 — 이 값은 그 자연 종료가 안 될 때만 걸린다. 100건/콜 ×
# 200 = 2만 영업일(~80년)이라 가장 오래된 KRX 종목의 일봉 백필도 하한(d1)에 먼저 닿아
# 사실상 안 걸린다(다년 백필이 이 상한에 절단되지 않게 넉넉히 둔다).
MAX_PAGES = 200
# 콜당 응답 상한(라이브 실측 2026-07-26: 1년 7개월 창 요청에 output2 가 정확히 100행).
# 비종단 부분 응답(꽉 안 찬 페이지인데 아래에 더 있음)은 **관측된 적이 없다** — 벤더 문서에도
# 없고 실측에서도 안 나왔다. 그래서 이 값을 창 소진의 **단독 근거로 쓰지 않되**, 반대로 그
# 부재를 확정 사실로 삼지도 않는다: 아래 종료조건에서 달력 판정의 **교차 검증**으로만 쓴다
# (꽉 찬 페이지면 달력을 안 믿는다 — _stop_bound 주석).
PAGE_SIZE = 100
RATE_MSG_CD = "EGW00201"  # "초당 거래건수 초과" — HTTP 429 아님(200 본문). 어댑터가 재시도.
MAX_RATE_RETRY = 5

KST = timezone(timedelta(hours=9))


# 창 하한을 거래일로 스냅할 때 앞으로 훑는 상한. 최장 연휴(설·추석 + 앞뒤 주말)보다 넉넉하다.
MAX_SNAP_DAYS = 10


def _yyyymmdd(date_str: str | None) -> str | None:
    """수집 창 날짜(YYYY-MM-DD) → KIS 파라미터 형식(YYYYMMDD). None 은 그대로 None."""
    return date_str.replace("-", "") if date_str else None


def _stop_bound(d1: str) -> str:
    """창 하한 d1 → **종료 판정용** 하한(그 날 이후 첫 거래일). 요청에는 쓰지 않는다.

    `_fetch_symbol` 의 종료조건이 `earliest <= d1` 인데 d1 이 주말·공휴일이면 종료를 놓친다 —
    이 엔드포인트는 `FID_INPUT_DATE_1` 을 **시작일**로 받아 서버가 하한을 이미 걸므로, 창 안
    첫 거래일(earliest)이 비거래일 d1 보다 **항상** 커서 조건이 성립하지 않는다. 그래서 빈
    페이지를 한 번 더 받아야 `new == 0` 으로 멈췄다. 가격 소급이 5일 고정(`run.py`
    DEFAULT_PRICE_LOOKBACK_DAYS)이라 목·금 런은 d1 이 주말에 떨어져 **매번** 이 경로였다.

    ⚠️ **요청 하한(FID_INPUT_DATE_1)은 원본 d1 을 그대로 쓴다.** 스냅한 값을 서버로 보내면
    휴장일 집합이 **과잉**일 때(수동 갱신이라 실제 거래일이 잘못 등재될 수 있다) 그 날 봉이
    창 밖으로 밀려 조용히 유실되고 collection_log 는 success 로 남는다(Rule 12 위반).

    요청을 원본으로 둬도 **판정 하나만으로는 부족하다**: 잘못 등재된 날이 페이지 경계에
    정확히 걸리면(첫 페이지 earliest 가 곧 d1_stop) 그 날 봉은 다음 페이지에 있는데 첫
    페이지에서 종료해버린다. 그래서 종료조건이 **꽉 찬 페이지에서는 달력을 믿지 않는다** —
    꽉 찼다는 건 아래에 더 있을 수 있다는 뜻이라 한 페이지 더 간다(아래 `page_full`).
    달력 기반 종료는 달력 오류를 물려받을 수밖에 없으므로, 공식이 아니라 이 교차 검증으로 막는다.

    집합이 **결손**이면 종료를 못 잡아 옛 동작(빈 콜 1회)으로 퇴화한다 — 낭비 쪽이다.

    ⚠️ **남는 구멍(의도적)**: 휴장일 집합이 과잉이고 *동시에* 그 경계에서 벤더가 비종단 short
    page 를 주면 그 날 봉을 놓친다. 완전히 닫으려면 달력을 전혀 못 믿게 되어 이 최적화 자체가
    사라지므로 닫지 않았다. 일일 경로는 자가 치유된다 — 소급이 5일이고 매일 도는데 절단은 창
    **최하단**에서만 생기므로 다음 날 런에서 그 날은 하단이 아니게 되어 정상 수집된다(raw 는
    겹치는 거래일을 append 하고 canonical 이 (market,ticker,trade_date) 로 병합). 자가 치유가
    없는 건 **일회성 백필**(--from/--to)이다 — 그때 휴장일 집합 정확성이 전제 조건이다.

    휴장일 집합은 `OPS_KR_HOLIDAYS`(env)로 KRX·iNAV 와 공유한다(tasks.tf 의 kis 환경).
    상한 안에 거래일이 없으면(달력 주입 이상) 원본 d1 을 돌려줘 옛 동작으로 퇴화한다.
    """
    day = datetime.strptime(d1, "%Y%m%d").date()
    for _ in range(MAX_SNAP_DAYS):
        if is_trading_day(day):
            return day.strftime("%Y%m%d")
        day += timedelta(days=1)
    logger.warning("창 하한 %s 이후 %d일 안에 거래일이 없다 — 스냅 생략(OPS_KR_HOLIDAYS 확인)",
                   d1, MAX_SNAP_DAYS)
    return d1


class KisDailyPriceSource:
    source_name = "kis"

    def __init__(self, config: KisPriceSource, client: PoliteClient):
        self.env = config.env
        self.base = domain_for(config.env)
        self.app_key = config.app_key
        self.app_secret = config.app_secret
        self.config_enabled = config.enabled
        # our_ticker → KIS 6자리 코드. KR 은 대개 항등이지만, 맵에 없는 종목(US 등)은
        # 이 소스가 건너뛴다 — KIS 는 국내 전용이라 US 티커를 질의하면 안 된다.
        self.symbol_map = config.symbol_map
        self.client = client
        self.auth = KisAuth(config.app_key or "", config.app_secret or "", client, config.env)
        # 심볼 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다(격리≠은폐).
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한(매핑된) 대상 수. 활성인데 0이면 스텝이 skip 으로 드러낸다.
        self.planned_symbols: int | None = None
        # 수집 유니버스를 canonical KR holdings 에서 파생하라는 옵트인(ALPHA-419) —
        # ingest_price_raw 가 이 플래그를 보고 구성종목·ETF 티커를 대상에 union 한다.
        # 정적 targets/symbol_map 만으론 구성종목 36개 중 2개만 수집돼 proxy 커버리지가
        # 60% 에 머문다(뉴스 전환 ALPHA-416·417 과 같은 유니버스 정합 축).
        self.universe_from_holdings = True

    @property
    def enabled(self) -> bool:
        # 앱키·시크릿은 env 로만 주입(커밋 금지) — 둘 중 하나라도 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.app_key) and bool(self.app_secret)

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, kis_symbol)]. KR 6자리 코드는 **항등 매핑**이 기본이고
        (ALPHA-419 — KRX 코드가 곧 KIS 코드), symbol_map 은 항등이 아닌 예외의 오버라이드
        축으로만 남는다. KRX 코드 형태가 아닌 미매핑 심볼(US 등)은 제외 — KIS 는 국내 전용.

        형태 판정은 `parse.krx_short_code` 가 한다(ALPHA-463) — 문자 섞인 신형 단축코드
        (0093A0 등)도 KIS 가 그대로 받으므로 항등 매핑 대상이고, 반대로 `ABCDEF` 같은
        6자 US 심볼은 국내 API 로 새지 않는다.
        """
        out: list[tuple[str, str]] = []
        for our_ticker in symbols:
            kis_symbol = self.symbol_map.get(our_ticker) or krx_short_code(our_ticker)
            if not kis_symbol:
                logger.info("kis 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, kis_symbol))
        return out

    def _note_failure(
        self, kis_symbol: str, our_ticker: str, reason: str, *, kind: str = "failure"
    ) -> None:
        """심볼 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("kis 심볼 건너뜀: %s (%s)", kis_symbol, reason)
        self.fetch_failures.append(
            {"symbol": kis_symbol, "our_ticker": our_ticker, "error": reason, "kind": kind}
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """심볼별로 [from_date, to_date] 창의 일봉을 낸다(100건 윈도우 페이지네이션).

        토큰은 run 당 1회 발급한다 — 발급 실패는 소스 전체 문제(키)라 격리하지 않고
        예외로 올린다(스텝이 error/stopped 로 드러냄). 심볼 단위 실패(요청 실패·깨진
        JSON·KIS 오류코드)는 격리·기록하고 남은 심볼을 계속 수집한다. StopFetch(4xx/429)만
        소스 전체를 중단한다(키·쿼터라 재시도·격리 대상이 아니다).
        """
        self.fetch_failures = []
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)  # 빈 plan(매핑 대상 0)을 스텝이 감지하게
        if not plan:
            return
        fetched_at = datetime.now(timezone.utc).isoformat()
        # 토큰 1회 발급(종목마다 발급 금지). 키 오류(4xx)는 client 가 StopFetch 로,
        # 200-무토큰은 kis_auth 가 RuntimeError 로 올린다 — 둘 다 fetch 밖으로 전파(전체 중단).
        token = self.auth.token()
        end_default = datetime.now(KST).strftime("%Y%m%d")
        d1 = _yyyymmdd(from_date)
        # 요청 하한은 d1 원본, 종료 판정 하한만 거래일로 스냅한다(_stop_bound 주석 — 유실 방지).
        d1_stop = _stop_bound(d1) if d1 else None
        d2 = _yyyymmdd(to_date) or end_default
        for our_ticker, kis_symbol in plan:
            try:
                yield from self._fetch_symbol(
                    our_ticker, kis_symbol, d1, d1_stop, d2, token, fetched_at
                )
            except StopFetch:
                raise  # 4xx/429 는 소스 전체 문제(키·쿼터) — 중단이 맞다
            except Exception as exc:
                # 요청 실패·깨진 JSON·KIS 오류코드는 심볼 단위로 격리 — 남은 심볼 계속.
                self._note_failure(kis_symbol, our_ticker, str(exc))
                continue

    def _fetch_symbol(
        self,
        our_ticker: str,
        kis_symbol: str,
        d1: str | None,
        d1_stop: str | None,
        d2: str,
        token: str,
        fetched_at: str,
    ) -> Iterator[dict]:
        """한 심볼의 창을 100건 윈도우로 최신→과거 순 페이지네이션해 일봉 행을 낸다.

        실패는 예외로 올려 호출부가 심볼 단위로 격리한다. 같은 거래일이 페이지 경계에서
        겹쳐 와도 raw 는 거래일 기준으로 dedup 해 보존한다(같은 봉 중복 저장 방지 —
        정체성 upsert 아님, 페이지 경계 중복만 제거).

        날짜(stck_bsop_date) 없는 행(스키마 드리프트/이상치)은 페이지네이션 산식에서만
        제외하고 raw 로는 보존한다(bronze 무변형 — FMP 가격이 date 없는 dict 행도 버리지
        않는 것과 동형; 조용히 드롭하면 드리프트가 묻힌다). 페이지 경계 중복만 제거한다.
        """
        def _emit(bar: dict) -> dict:
            # bronze 무변형: output2 행 원본 보존 + 수집 provenance 만 부착(FMP 가격과 동형).
            record = dict(bar)
            record["our_ticker"] = our_ticker
            record["market"] = "KR"  # KIS 는 KRX 로컬 전용
            record["kis_symbol"] = kis_symbol
            record["fetched_at"] = fetched_at
            return record

        bars: dict[str, dict] = {}  # 거래일 → 원본 봉(날짜 기준 dedup)
        extras: list[dict] = []  # 날짜 없는 이상치 행(보존 대상)
        seen_extra: set[str] = set()  # 이상치의 페이지 경계 중복만 제거
        end = d2
        truncated = True
        for _ in range(MAX_PAGES):
            raw_chunk = self._chunk(kis_symbol, d1, end, token)
            dated = []
            for bar in raw_chunk:
                if bar.get("stck_bsop_date"):
                    dated.append(bar)
                    continue
                # 날짜 없는 행은 페이지네이션엔 못 쓰지만 raw 로는 보존(내용 기준 중복 제거).
                key = json.dumps(bar, sort_keys=True, ensure_ascii=False)
                if key not in seen_extra:
                    seen_extra.add(key)
                    extras.append(bar)
            if not dated:
                # 날짜 있는 행이 없으면 페이지네이션을 더 진전시킬 수 없다. 빈 응답(raw_chunk==[])은
                # 정상 종료지만, 행은 있는데 전부 날짜 없는 페이지는 이상(스키마 드리프트/malformed)
                # 이라 창이 절단될 수 있다 — 이상치는 위에서 보존했으되 조용한 success 대신 실패로
                # surface 한다(둘을 구분: 빈 페이지=정상, 비어있지 않은데 날짜 0=이상).
                if raw_chunk:
                    self._note_failure(
                        kis_symbol, our_ticker,
                        "날짜 있는 행이 없는 비어있지 않은 페이지 — 창 절단 가능(스키마 드리프트?)",
                    )
                truncated = False
                break
            new = 0
            for bar in dated:
                day = bar["stck_bsop_date"]
                if day not in bars:
                    bars[day] = bar
                    new += 1
            earliest = min(b["stck_bsop_date"] for b in dated)
            # 요청은 d1(원본), 종료 판정은 d1_stop(거래일 스냅) — 둘을 섞으면 창이 좁아진다.
            # `earliest <= d1` 은 달력과 무관한 정확한 종료다. 달력 기반(d1_stop)은 페이지가
            # 꽉 차지 **않았을 때만** 믿는다 — 꽉 찬 페이지는 아래에 더 있을 수 있고, 휴장일
            # 집합이 과잉이면 그 경계에서 실제 거래일 봉을 잘라먹는다(_stop_bound 주석).
            page_full = len(raw_chunk) >= PAGE_SIZE
            if (
                new == 0
                or (d1 and earliest <= d1)
                or (d1_stop and earliest <= d1_stop and not page_full)
            ):
                truncated = False
                break
            # 다음 페이지: 이번 배치 최소일 하루 전까지 과거로.
            end = (datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        if truncated:
            # MAX_PAGES 를 다 돌았는데도 창 하한에 못 닿음 → 절단 가능. 조용히 버리지 않고
            # 실패로 기록해 런을 partial 로 드러낸다(창을 좁혀 재실행하라는 신호).
            self._note_failure(
                kis_symbol, our_ticker, f"MAX_PAGES({MAX_PAGES}) 도달 — 창 절단 가능(구간 좁혀 재실행)",
                kind="truncation",
            )
        for day in sorted(bars):
            yield _emit(bars[day])
        for bar in extras:  # 날짜 없는 원본도 보존(수집 provenance 부착)
            yield _emit(bar)

    def _chunk(self, kis_symbol: str, d1: str | None, d2: str, token: str) -> list[dict]:
        """일봉 1콜(≤100건). rt_cd!=0 은 오류, EGW00201(초당한도)만 본문 기반 재시도한다."""
        params = {
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV,
            "FID_INPUT_ISCD": kis_symbol,
            "FID_INPUT_DATE_1": d1 or "",
            "FID_INPUT_DATE_2": d2,
            "FID_PERIOD_DIV_CODE": "D",
            # KIS FID_ORG_ADJ_PRC: 0=수정주가(반영), 1=원주가(미반영). bronze 는 원본
            # (미조정) 봉을 보존해야 후속 canonical 이 조정을 재현할 수 있으므로 1(원주가).
            # 0 을 쓰면 조정 시점마다 값이 바뀌어 원본 복원이 불가능하다(무변형 원칙 위반).
            "FID_ORG_ADJ_PRC": "1",
        }
        url = self.base + PATH_DAILY + "?" + urllib.parse.urlencode(params)
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
            "tr_id": TR_ID_DAILY,
            "custtype": "P",
        }
        for attempt in range(MAX_RATE_RETRY):
            # 4xx/429 는 client 가 StopFetch 로 올린다(전체 중단). 5xx·네트워크는 client 가 재시도.
            body = self.client.request("GET", url, headers=headers, decode=True)
            data = json.loads(body)  # 깨진 JSON → 심볼 단위 실패로 전파
            # KIS 는 항상 {rt_cd, output1, output2, ...} 객체로 답한다 — 배열·스칼라(스키마
            # 드리프트)면 .get 이 AttributeError 를 내기 전에 명확한 메시지로 fail-loud(FMP
            # 어댑터가 응답 형태를 명시 검사하는 것과 동형).
            if not isinstance(data, dict):
                raise ValueError(f"KIS 응답이 객체가 아님: {type(data).__name__}")
            if data.get("rt_cd") == "0":
                output2 = data.get("output2")
                # 빈 list([])는 정상 종료(더 이상 데이터 없음)지만, 키 누락(None)·비-list 는
                # rt_cd=0 인데도 이상(malformed success·스키마 드리프트)이다 — 정상 빈 페이지로
                # 위장(success 0건)하지 않고 fail-loud(심볼 단위 실패로 격리·기록)한다.
                if not isinstance(output2, list):
                    raise ValueError(f"KIS rt_cd=0 인데 output2 이상: {type(output2).__name__}")
                return output2
            # 초당한도는 HTTP 429 가 아니라 본문 코드로 온다 — 운반 계층이 모르니 여기서 재시도.
            if data.get("msg_cd") == RATE_MSG_CD and attempt < MAX_RATE_RETRY - 1:
                self.client._sleep(0.7 * (attempt + 1))
                continue
            raise ValueError(
                f"KIS rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
            )
        raise ValueError(f"KIS {RATE_MSG_CD} 재시도 소진")
