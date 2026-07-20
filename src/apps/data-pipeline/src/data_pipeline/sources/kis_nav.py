"""KIS(한국투자) 국내 ETF NAV 소스 어댑터 (ALPHA-380 — KR ETF NAV Step1 원본저장).

API: ETF NAV비교추이(일) nav-comparison-daily-trend, tr_id FHPST02440200.
`FID_INPUT_DATE_1~2` 기간지정으로 창 안의 거래일 NAV 를 한 번에 준다(라이브 실측:
069500·091160 × 20260601~20260717 → 33행). 응답은 `output2` 가 아니라 **단일 `output`
배열**이고 값은 전부 문자열이다 — 수치 캐스팅은 후속 canonical(ALPHA-382) 소관.

KRX(krx_etf.py)를 쓰지 않는 이유: `data.krx.co.kr` getJsonData 는 무로그인·세션쿠키
모두 `LOGOUT` 만 반환하고(2026-07-20 실측), 로그인을 붙이면 구성종목 수집이 지고 있는
계정 동시세션 1개(CD011) 제약을 NAV 스텝까지 물려받는다. KIS 는 가격 수집(kis_price)에서
이미 검증된 경로다.

가격·ETF 어댑터와 같은 관례 인터페이스(source_name·enabled·plan·fetch·fetch_failures·
planned_etfs)를 지켜 기존 `ingest_raw_etf` 스텝을 그대로 재사용한다. 날짜창은 생성자로
받는다 — 스냅샷 소스(FmpEtfSource·KrxEtfSource)와 `fetch()` 시그니처를 맞춰 스텝이
소스별 분기 없이 돌게 하기 위함이다.

raw 존에는 output 행 원본에 수집 provenance(our_etf_id·market·kis_symbol·fetched_at)만
붙여 그대로 낸다 — `nav` 외에 `stck_clpr`(종가)·`dprt`(괴리율)가 같은 응답에 오는데
그것도 무변형 보존한다(필드 선별은 canonical 소관, bronze 무변형).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from ..config import KisNavSource as KisNavSourceConfig
from .http import PoliteClient, StopFetch
from .kis_auth import KisAuth, domain_for
from .krx_etf import _short_code

logger = logging.getLogger(__name__)

TR_ID_NAV_DAILY = "FHPST02440200"
PATH_NAV_DAILY = "/uapi/etfetn/v1/quotations/nav-comparison-daily-trend"
MARKET_DIV = "J"  # J: KRX
RATE_MSG_CD = "EGW00201"  # "초당 거래건수 초과" — HTTP 429 아님(200 본문). 어댑터가 재시도.
MAX_RATE_RETRY = 5

KST = timezone(timedelta(hours=9))


def _yyyymmdd(date_str: str | None) -> str | None:
    """수집 창 날짜(YYYY-MM-DD) → KIS 파라미터 형식(YYYYMMDD). None 은 그대로 None."""
    return date_str.replace("-", "") if date_str else None


class KisNavSource:
    source_name = "kis"

    def __init__(
        self,
        config: KisNavSourceConfig,
        etf_map: dict[str, str],
        client: PoliteClient,
        from_date: str | None = None,
        to_date: str | None = None,
    ):
        self.config_enabled = config.enabled
        self.app_key = config.app_key
        self.app_secret = config.app_secret
        self.base = domain_for(config.env)
        # our_etf_id → KRX ISIN(표준코드). krx_etf.source.etf_map 을 그대로 받는다 —
        # 구성종목과 NAV 의 수집 유니버스는 하나여야 한다(맵 복제 = 드리프트).
        self.etf_map = etf_map
        self.client = client
        self.auth = KisAuth(config.app_key or "", config.app_secret or "", client, config.env)
        self.from_date = from_date
        self.to_date = to_date
        # ETF 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다(격리≠은폐).
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한(매핑된) 대상 수. 활성인데 0이면 스텝이 skip 으로 드러낸다.
        self.planned_etfs: int | None = None

    @property
    def enabled(self) -> bool:
        # 앱키·시크릿은 env 로만 주입(커밋 금지) — 둘 중 하나라도 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.app_key) and bool(self.app_secret)

    def plan(self) -> list[tuple[str, str]]:
        """수집 대상 → [(our_etf_id, kis_symbol)]. etf_map 이 곧 유니버스다.

        KIS 는 ISIN 이 아니라 6자리 단축코드로 질의하므로 표준코드에서 파생한다.

        단축코드는 **숫자 전용이 아니다** — KRX 가 번호를 소진해 신규 상장분에는 문자가 섞인
        코드(0093A0·0005G0 등)를 발급하고, 우리 유니버스 31종 중 7종이 그렇다. KIS 는 이
        코드도 그대로 받는다(2026-07-20 실측: 0093A0·0005G0 각 33행). 그래서 숫자 검사가
        아니라 '6자리 영숫자'로 본다 — `isdigit()` 로 거르면 7종이 조용히 빠진다.
        파생 결과가 6자리 영숫자가 아니면 질의하지 않고 실패로 드러낸다 — 엉뚱한 코드로
        KIS 를 두드려 무의미한 오류를 쌓지 않는다.
        """
        out: list[tuple[str, str]] = []
        for our_etf_id, isin in sorted(self.etf_map.items()):
            symbol = _short_code(isin)
            if not (symbol.isalnum() and len(symbol) == 6):
                self._note_failure(symbol, our_etf_id, f"단축코드 파생 실패: isin={isin}")
                continue
            out.append((our_etf_id, symbol))
        return out

    def _note_failure(self, kis_symbol: str, our_etf_id: str, reason: str) -> None:
        """ETF 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("kis NAV 건너뜀: %s (%s)", kis_symbol, reason)
        self.fetch_failures.append(
            {"symbol": kis_symbol, "our_etf_id": our_etf_id, "error": reason}
        )

    def fetch(self) -> Iterator[dict]:
        """ETF 별로 [from_date, to_date] 창의 일별 NAV 행을 낸다(ETF 당 1콜).

        토큰은 run 당 1회 발급한다 — 발급 실패는 소스 전체 문제(키)라 격리하지 않고 예외로
        올린다(스텝이 error/stopped 로 드러냄). ETF 단위 실패(요청 실패·깨진 JSON·KIS
        오류코드·빈 output)는 격리·기록하고 남은 ETF 를 계속 수집한다. StopFetch(4xx/429)만
        소스 전체를 중단한다(키·쿼터라 재시도·격리 대상이 아니다).
        """
        self.fetch_failures = []
        plan = self.plan()
        self.planned_etfs = len(plan)  # 빈 plan(매핑 대상 0)을 스텝이 감지하게
        if not plan:
            return
        fetched_at = datetime.now(timezone.utc).isoformat()
        # 토큰 1회 발급(ETF마다 발급 금지). 키 오류(4xx)는 client 가 StopFetch 로,
        # 200-무토큰은 kis_auth 가 RuntimeError 로 올린다 — 둘 다 fetch 밖으로 전파(전체 중단).
        token = self.auth.token()
        d1 = _yyyymmdd(self.from_date) or ""
        d2 = _yyyymmdd(self.to_date) or datetime.now(KST).strftime("%Y%m%d")
        for our_etf_id, kis_symbol in plan:
            try:
                for row in self._fetch_etf(kis_symbol, d1, d2, token):
                    # bronze 무변형: output 행 원본 보존 + 수집 provenance 만 부착.
                    record = dict(row)
                    record["our_etf_id"] = our_etf_id
                    record["market"] = "KR"  # KIS ETF NAV 는 KRX 로컬 전용
                    record["kis_symbol"] = kis_symbol
                    record["fetched_at"] = fetched_at
                    yield record
            except StopFetch:
                raise  # 4xx/429 는 소스 전체 문제(키·쿼터) — 중단이 맞다
            except Exception as exc:
                # 요청 실패·깨진 JSON·KIS 오류코드는 ETF 단위로 격리 — 남은 ETF 계속.
                self._note_failure(kis_symbol, our_etf_id, str(exc))
                continue

    def _fetch_etf(self, kis_symbol: str, d1: str, d2: str, token: str) -> list[dict]:
        """한 ETF 의 창 NAV 를 1콜로 받는다. rt_cd!=0 은 오류, EGW00201 만 본문 기반 재시도.

        실패는 예외로 올려 호출부가 ETF 단위로 격리한다. 빈 output 은 정상 ETF·정상 창으로는
        나올 수 없어(잘못된 코드·비영업일만 걸린 창) fail-loud 한다 — 조용한 success 0건 금지.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV,
            "FID_INPUT_ISCD": kis_symbol,
            "FID_INPUT_DATE_1": d1,
            "FID_INPUT_DATE_2": d2,
        }
        url = self.base + PATH_NAV_DAILY + "?" + urllib.parse.urlencode(params)
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
            "tr_id": TR_ID_NAV_DAILY,
            "custtype": "P",
        }
        for attempt in range(MAX_RATE_RETRY):
            # 4xx/429 는 client 가 StopFetch 로 올린다(전체 중단). 5xx·네트워크는 client 가 재시도.
            body = self.client.request("GET", url, headers=headers, decode=True)
            data = json.loads(body)  # 깨진 JSON → ETF 단위 실패로 전파
            if not isinstance(data, dict):
                raise ValueError(f"KIS 응답이 객체가 아님: {type(data).__name__}")
            if data.get("rt_cd") == "0":
                # 이 엔드포인트는 output2 가 아니라 단일 output 배열이다(라이브 실측).
                # 키 누락·비-list 는 rt_cd=0 인데도 이상(스키마 드리프트)이라 fail-loud.
                output = data.get("output")
                if not isinstance(output, list):
                    raise ValueError(f"KIS rt_cd=0 인데 output 이상: {type(output).__name__}")
                if not output:
                    raise ValueError("empty output — 창에 거래일이 없거나 잘못된 종목코드")
                # 배열 안에 dict 아닌 행이 섞여도 한 행이 ETF 전체를 끊지 않게 — 기록 후 스킵.
                rows = []
                for row in output:
                    if isinstance(row, dict):
                        rows.append(row)
                    else:
                        self._note_failure(
                            kis_symbol, "", f"malformed row: {type(row).__name__}"
                        )
                return rows
            # 초당한도는 HTTP 429 가 아니라 본문 코드로 온다 — 운반 계층이 모르니 여기서 재시도.
            if data.get("msg_cd") == RATE_MSG_CD and attempt < MAX_RATE_RETRY - 1:
                self.client._sleep(0.7 * (attempt + 1))
                continue
            raise ValueError(
                f"KIS rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
            )
        raise ValueError(f"KIS {RATE_MSG_CD} 재시도 소진")
