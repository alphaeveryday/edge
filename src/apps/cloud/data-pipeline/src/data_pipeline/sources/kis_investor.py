"""KIS(한국투자) 종목별 투자자 수급 소스 어댑터 (ALPHA-482 — 구성종목 투자자 Step1 원본저장).

API: 국내주식 종목별 투자자매매동향(일별) investor-trade-by-stock-daily, tr_id FHPTJ04160001.
`FID_INPUT_DATE_1`(기준일)을 창 끝으로 주면 그 날부터 과거로 최대 30거래일의 일별 투자자
순매수를 `output2` 로 준다(2026-07-21 라이브 실측: 069500·005930 각 30행). 값은 zero-pad
문자열(개인/외국인/기관계 순매수 수량·대금 + 기관세분 증권·투신·사모·은행·보험·종금·기금(연기금)·
기타법인·기타단체) — 필드 선별·수치 캐스팅은 후속 canonical(normalize_investor) 소관이다.

가격 어댑터(kis_price.py)와 같은 관례 인터페이스(source_name·enabled·plan·fetch·
fetch_failures·planned_symbols·universe_from_holdings)를 지켜 `ingest_raw_investor` 스텝을
그대로 재사용한다. 대상이 ETF 자체가 아니라 그 **구성종목(개별주식)** 이므로 수집 유니버스는
가격과 같은 축 — canonical KR holdings 최신 스냅샷에서 파생한다(스텝이 symbols 를 주입).

KIS 일봉(kis_price)과의 차이는 (1) 창 파라미터가 FID_INPUT_DATE_1 하나뿐이라(기준일=창 끝)
과거 페이지네이션을 기준일을 뒤로 물려 돈다 (2) 응답 배열이 output2 인데 한 콜이 ≤30행이라
장기 백필은 콜 수가 일봉(100행/콜)보다 많다. 초당한도(EGW00201)·격리·bronze 무변형은 동형이다.

raw 존에는 output2 행 원본에 수집 provenance(our_ticker·market·kis_symbol·fetched_at)만
붙여 그대로 낸다(bronze 무변형 — 가격 어댑터와 동일).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from ..config import KisInvestorSource as KisInvestorSourceConfig
from ..parse import krx_short_code
from .http import PoliteClient, StopFetch
from .kis_auth import KisAuth, domain_for

logger = logging.getLogger(__name__)

TR_ID_INVESTOR = "FHPTJ04160001"
PATH_INVESTOR = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
MARKET_DIV = "J"  # J: KRX
# 무한 페이지 방어용 백스톱. 한 콜이 ≤30거래일이라 200콜 = ~6000영업일(~24년). 종목 이력이
# 끝나면(new==0) 또는 창 하한(earliest<=d1)에 먼저 멈춘다 — 이 값은 그 자연 종료가 안 될
# 때만 걸린다. 다년 백필이 이 상한에 절단되지 않게 넉넉히 둔다(일봉 MAX_PAGES=200과 동형).
MAX_PAGES = 200
RATE_MSG_CD = "EGW00201"  # "초당 거래건수 초과" — HTTP 429 아님(200 본문). 어댑터가 재시도.
MAX_RATE_RETRY = 5

KST = timezone(timedelta(hours=9))


def _yyyymmdd(date_str: str | None) -> str | None:
    """수집 창 날짜(YYYY-MM-DD) → KIS 파라미터 형식(YYYYMMDD). None 은 그대로 None."""
    return date_str.replace("-", "") if date_str else None


class KisInvestorSource:
    source_name = "kis"

    def __init__(self, config: KisInvestorSourceConfig, client: PoliteClient):
        self.env = config.env
        self.base = domain_for(config.env)
        self.app_key = config.app_key
        self.app_secret = config.app_secret
        self.config_enabled = config.enabled
        # our_ticker → KIS 6자리 코드. KR 은 대개 항등. 맵에 없는 종목(US 등)은 이 소스가
        # 건너뛴다 — KIS 는 국내(KRX) 전용이라 US 티커를 질의하면 안 된다(가격과 동일 정책).
        self.symbol_map = config.symbol_map
        self.client = client
        self.auth = KisAuth(config.app_key or "", config.app_secret or "", client, config.env)
        # 심볼 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다(격리≠은폐).
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한(매핑된) 대상 수. 활성인데 0이면 스텝이 skip 으로 드러낸다.
        self.planned_symbols: int | None = None
        # 수집 유니버스를 canonical KR holdings 에서 파생하라는 옵트인(ALPHA-419·482) —
        # ingest_raw_investor 가 이 플래그를 보고 구성종목·ETF 티커를 대상에 union 한다.
        # 대상이 ETF 가 아니라 그 편입 종목의 수급이라 가격과 같은 유니버스 축을 쓴다.
        self.universe_from_holdings = True

    @property
    def enabled(self) -> bool:
        # 앱키·시크릿은 env 로만 주입(커밋 금지) — 둘 중 하나라도 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.app_key) and bool(self.app_secret)

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, kis_symbol)]. KR 6자리 코드는 **항등 매핑**이 기본이고
        (KRX 코드가 곧 KIS 코드), symbol_map 은 항등이 아닌 예외의 오버라이드 축으로만 남는다.
        KRX 코드 형태가 아닌 미매핑 심볼(US 등)은 제외 — KIS 는 국내 전용(가격 plan 과 동형).

        형태 판정은 `parse.krx_short_code` 가 한다(ALPHA-463) — 문자 섞인 신형 단축코드
        (0093A0 등)도 KIS 가 그대로 받으므로 항등 매핑 대상이고, `ABCDEF` 같은 6자 US 심볼은
        국내 API 로 새지 않는다.
        """
        out: list[tuple[str, str]] = []
        for our_ticker in symbols:
            kis_symbol = self.symbol_map.get(our_ticker) or krx_short_code(our_ticker)
            if not kis_symbol:
                logger.info("kis 투자자 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, kis_symbol))
        return out

    def _note_failure(
        self, kis_symbol: str, our_ticker: str, reason: str, *, kind: str = "failure"
    ) -> None:
        """심볼 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("kis 투자자 심볼 건너뜀: %s (%s)", kis_symbol, reason)
        self.fetch_failures.append(
            {"symbol": kis_symbol, "our_ticker": our_ticker, "error": reason, "kind": kind}
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """심볼별로 [from_date, to_date] 창의 일별 투자자 순매수를 낸다(30행 윈도우 페이지네이션).

        토큰은 run 당 1회 발급한다 — 발급 실패는 소스 전체 문제(키)라 격리하지 않고 예외로
        올린다(스텝이 error/stopped 로 드러냄). 심볼 단위 실패(요청 실패·깨진 JSON·KIS
        오류코드)는 격리·기록하고 남은 심볼을 계속 수집한다. StopFetch(4xx/429)만 소스 전체를
        중단한다(키·쿼터라 재시도·격리 대상이 아니다).
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
        d2 = _yyyymmdd(to_date) or end_default
        for our_ticker, kis_symbol in plan:
            try:
                yield from self._fetch_symbol(our_ticker, kis_symbol, d1, d2, token, fetched_at)
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
        d2: str,
        token: str,
        fetched_at: str,
    ) -> Iterator[dict]:
        """한 심볼의 창을 기준일(FID_INPUT_DATE_1)을 뒤로 물려 최신→과거 순 페이지네이션한다.

        실패는 예외로 올려 호출부가 심볼 단위로 격리한다. 같은 거래일이 페이지 경계에서 겹쳐
        와도 raw 는 거래일 기준으로 dedup 해 보존한다(같은 행 중복 저장 방지). 날짜
        (stck_bsop_date) 없는 행(스키마 드리프트)은 페이지네이션 산식에서만 제외하고 raw 로는
        보존한다(bronze 무변형 — 가격 어댑터와 동형). 페이지 경계 중복만 제거한다.
        """
        def _emit(row: dict) -> dict:
            # bronze 무변형: output2 행 원본 보존 + 수집 provenance 만 부착(가격과 동형).
            record = dict(row)
            record["our_ticker"] = our_ticker
            record["market"] = "KR"  # KIS 는 KRX 로컬 전용
            record["kis_symbol"] = kis_symbol
            record["fetched_at"] = fetched_at
            return record

        rows: dict[str, dict] = {}  # 거래일 → 원본 행(날짜 기준 dedup)
        extras: list[dict] = []  # 날짜 없는 이상치 행(보존 대상)
        seen_extra: set[str] = set()  # 이상치의 페이지 경계 중복만 제거
        end = d2
        truncated = True
        for _ in range(MAX_PAGES):
            chunk = self._chunk(kis_symbol, end, token)
            dated = []
            for row in chunk:
                if not isinstance(row, dict):
                    # 배열 안 dict 아닌 행(스키마 드리프트)은 한 행이 심볼 전체를 끊지 않게 —
                    # 기록 후 스킵한다(조용히 버리지 않는다, Rule 12).
                    self._note_failure(kis_symbol, our_ticker, f"malformed row: {type(row).__name__}")
                    continue
                if row.get("stck_bsop_date"):
                    dated.append(row)
                    continue
                key = json.dumps(row, sort_keys=True, ensure_ascii=False)
                if key not in seen_extra:
                    seen_extra.add(key)
                    extras.append(row)
            if not dated:
                # 날짜 있는 행이 없으면 페이지네이션을 더 진전시킬 수 없다. 빈 응답(chunk==[])은
                # 정상 종료지만, 행은 있는데 전부 날짜 없는 페이지는 이상(스키마 드리프트)이라
                # 창이 절단될 수 있다 — 이상치는 위에서 보존했으되 조용한 success 대신 실패로
                # surface 한다(빈 페이지=정상, 비어있지 않은데 날짜 0=이상, 가격 어댑터와 동형).
                if chunk:
                    self._note_failure(
                        kis_symbol, our_ticker,
                        "날짜 있는 행이 없는 비어있지 않은 페이지 — 창 절단 가능(스키마 드리프트?)",
                    )
                truncated = False
                break
            new = 0
            for row in dated:
                day = row["stck_bsop_date"]
                # 창 하한 필터 — 이 엔드포인트는 FID_INPUT_DATE_1(창 끝) 하나만 받아 그 날부터
                # 과거 ~30거래일을 통째로 준다(kis_price 는 FID_INPUT_DATE_1=시작일로 서버가 하한을
                # 거르지만 여긴 못 건다). d1 아래 행까지 저장하면 증분 run 마다 ~30일치가 raw·
                # canonical 에 얹혀 창 밖 파티션을 매일 재작성한다 — 요청 창 [d1,d2] 로 좁힌다.
                # (페이지네이션 stop 은 아래 earliest 로 별도 판정하므로 필터가 종료를 깨지 않는다.)
                if d1 and day < d1:
                    continue
                if day not in rows:
                    rows[day] = row
                    new += 1
            earliest = min(r["stck_bsop_date"] for r in dated)
            if new == 0 or (d1 and earliest <= d1):
                truncated = False
                break
            # 다음 페이지: 이번 배치 최소일 하루 전을 새 기준일(창 끝)로 과거로.
            end = (datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        if truncated:
            # MAX_PAGES 를 다 돌았는데도 창 하한에 못 닿음 → 절단 가능. 조용히 버리지 않고
            # 실패로 기록해 런을 partial 로 드러낸다(창을 좁혀 재실행하라는 신호).
            self._note_failure(
                kis_symbol, our_ticker, f"MAX_PAGES({MAX_PAGES}) 도달 — 창 절단 가능(구간 좁혀 재실행)",
                kind="truncation",
            )
        for day in sorted(rows):
            yield _emit(rows[day])
        for row in extras:  # 날짜 없는 원본도 보존(수집 provenance 부착)
            yield _emit(row)

    def _chunk(self, kis_symbol: str, end: str, token: str) -> list[dict]:
        """투자자 수급 1콜(≤30거래일, 기준일=end 부터 과거). rt_cd!=0 은 오류,
        EGW00201(초당한도)만 본문 기반 재시도한다."""
        params = {
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV,
            "FID_INPUT_ISCD": kis_symbol,
            "FID_INPUT_DATE_1": end,
            "FID_ORG_ADJ_PRC": "",
            "FID_ETC_CLS_CODE": "",
        }
        url = self.base + PATH_INVESTOR + "?" + urllib.parse.urlencode(params)
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
            "tr_id": TR_ID_INVESTOR,
            "custtype": "P",
        }
        for attempt in range(MAX_RATE_RETRY):
            # 4xx/429 는 client 가 StopFetch 로 올린다(전체 중단). 5xx·네트워크는 client 가 재시도.
            body = self.client.request("GET", url, headers=headers, decode=True)
            data = json.loads(body)  # 깨진 JSON → 심볼 단위 실패로 전파
            # KIS 는 항상 {rt_cd, output1, output2, ...} 객체로 답한다 — 배열·스칼라(스키마
            # 드리프트)면 .get 이 AttributeError 를 내기 전에 명확한 메시지로 fail-loud.
            if not isinstance(data, dict):
                raise ValueError(f"KIS 응답이 객체가 아님: {type(data).__name__}")
            if data.get("rt_cd") == "0":
                output2 = data.get("output2")
                # 단일 거래일(예: 1일 창·신규상장)은 output2 를 dict 하나로 줄 수 있다(KIS 공식
                # 샘플도 list 아니면 단일 객체로 감싼다) — 유효한 1행을 실패로 처리하지 않게 list 로 감싼다.
                if isinstance(output2, dict):
                    output2 = [output2]
                # 빈 list([])는 정상 종료(더 이상 데이터 없음)지만, 키 누락(None)·비-list 는
                # rt_cd=0 인데도 이상(malformed success·스키마 드리프트)이다 — 정상 빈 페이지로
                # 위장(success 0건)하지 않고 fail-loud(심볼 단위 실패로 격리·기록)한다.
                if not isinstance(output2, list):
                    raise ValueError(f"KIS rt_cd=0 인데 output2 이상: {type(output2).__name__}")
                # 비-dict 행(스키마 드리프트)의 격리는 호출부(_fetch_symbol)가 our_ticker
                # 컨텍스트와 함께 한다 — 여기선 배열 그대로 돌려준다(가격 _chunk 와 동형).
                return output2
            # 초당한도는 HTTP 429 가 아니라 본문 코드로 온다 — 운반 계층이 모르니 여기서 재시도.
            if data.get("msg_cd") == RATE_MSG_CD and attempt < MAX_RATE_RETRY - 1:
                self.client._sleep(0.7 * (attempt + 1))
                continue
            raise ValueError(
                f"KIS rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
            )
        raise ValueError(f"KIS {RATE_MSG_CD} 재시도 소진")
