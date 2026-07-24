"""KIS 국내 ETF 프로필(종목정보) 소스 어댑터 (ALPHA-462 — ETF 마스터 Step1 원본저장).

API: 상품기본조회 search-stock-info, tr_id `CTPF1604R`, `PRDT_TYPE_CD=300`(주식·ETF).
ETF 당 1콜로 상품 식별·명칭을 준다(스냅샷, 날짜창 없음).

**왜 이 소스가 필요한가**: ETF `instrument` 마스터를 만들려면 `entity.display_name`(NOT NULL)이
필요한데 우리 레이크 어디에도 ETF 자기 이름이 없었다 — 구성종목 canonical 은 `COMPST_ISU_NM`
(구성종목 이름)만 갖고, NAV canonical 에도 없으며, KIS `inquire-price`(FHPST02400000)조차
운용사·추종지수만 주고 상품명을 안 준다(2026-07-20 실측). 그래서 31종 중 30종이 마스터에 없어
NAV 마트 적재가 1/31 에 머물렀다(ALPHA-383).

실측 응답(069500 / 0093A0):
  prdt_abrv_name       "KODEX 200"            / "RISE AI반도체TOP10"     ← 표시명
  prdt_name            "삼성 KODEX200 증권…"  / "KB RISE AI반도체TOP10…" ← 법적 명칭
  std_pdno             "KR7069500007"         / "KR70093A0000"           ← ISIN(우리 etf_map 값)
  prdt_clsf_name       "ETF"
  pdno                 "00000A069500"  ← 패딩된 내부 코드다. **티커로 쓰지 마라** — 우리는
                       provenance 의 our_etf_id 를 티커로 쓴다(수집 유니버스가 곧 진실).

가격·NAV 어댑터와 같은 관례 인터페이스(source_name·enabled·plan·fetch·fetch_failures·
planned_etfs)를 지켜 기존 `ingest_raw_etf` 스텝을 그대로 재사용한다(ALPHA-380 선례).

raw 존에는 output 원본에 수집 provenance(our_etf_id·market·kis_symbol·fetched_at)만 붙여
그대로 낸다 — 필드 선별·명칭 선택은 후속 canonical 소관(bronze 무변형).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import KisNavSource as KisEtfProfileSourceConfig
from ..parse import krx_short_code
from .http import PoliteClient, StopFetch
from .kis_auth import KisAuth, domain_for
from .krx_etf import _short_code

logger = logging.getLogger(__name__)

TR_ID_STOCK_INFO = "CTPF1604R"
PATH_STOCK_INFO = "/uapi/domestic-stock/v1/quotations/search-stock-info"
PRDT_TYPE_STOCK = "300"  # 주식·ETF·ETN
RATE_MSG_CD = "EGW00201"  # 초당한도 — HTTP 429 가 아니라 200 본문으로 온다.
MAX_RATE_RETRY = 5


class KisEtfProfileSource:
    source_name = "kis"

    def __init__(
        self,
        config: KisEtfProfileSourceConfig,
        etf_map: dict[str, str],
        client: PoliteClient,
    ):
        self.config_enabled = config.enabled
        self.app_key = config.app_key
        self.app_secret = config.app_secret
        self.base = domain_for(config.env)
        # our_etf_id → KRX ISIN. NAV 와 같은 맵(krx_etf.source.etf_map)을 공유한다 —
        # 마스터·NAV·구성종목이 서로 다른 ETF 목록을 보면 안 된다.
        self.etf_map = etf_map
        self.client = client
        self.auth = KisAuth(config.app_key or "", config.app_secret or "", client, config.env)
        self.fetch_failures: list[dict] = []
        self.planned_etfs: int | None = None

    @property
    def enabled(self) -> bool:
        return self.config_enabled and bool(self.app_key) and bool(self.app_secret)

    def plan(self) -> list[tuple[str, str]]:
        """수집 대상 → [(our_etf_id, kis_symbol)]. ISIN → 6자리 단축코드(NAV 와 동일 규칙).

        단축코드는 숫자 전용이 아니다 — 신규 상장분은 문자가 섞인다(0093A0 등). 형태 판정은
        `parse.krx_short_code`(선두 숫자 + 영숫자 6자) 하나로 간다(ALPHA-380·463).
        """
        out: list[tuple[str, str]] = []
        for our_etf_id, isin in sorted(self.etf_map.items()):
            raw = _short_code(isin)
            symbol = krx_short_code(raw)
            if symbol is None:
                self._note_failure(raw, our_etf_id, f"단축코드 파생 실패: isin={isin}")
                continue
            out.append((our_etf_id, symbol))
        return out

    def _note_failure(self, kis_symbol: str, our_etf_id: str, reason: str) -> None:
        logger.warning("kis ETF 프로필 건너뜀: %s (%s)", kis_symbol, reason)
        self.fetch_failures.append(
            {"symbol": kis_symbol, "our_etf_id": our_etf_id, "error": reason}
        )

    def fetch(self) -> Iterator[dict]:
        """ETF 별 프로필 1행을 낸다(ETF 당 1콜, 스냅샷).

        토큰은 run 당 1회 발급한다(발급 실패는 소스 전체 문제라 전파). ETF 단위 실패는
        격리·기록하고 남은 ETF 를 계속 수집한다. StopFetch(4xx/429)만 소스 전체를 중단한다.
        """
        self.fetch_failures = []
        plan = self.plan()
        self.planned_etfs = len(plan)
        if not plan:
            return
        fetched_at = datetime.now(timezone.utc).isoformat()
        token = self.auth.token()
        for our_etf_id, kis_symbol in plan:
            try:
                record = dict(self._fetch_etf(kis_symbol, token))
            except StopFetch:
                raise
            except Exception as exc:
                self._note_failure(kis_symbol, our_etf_id, str(exc))
                continue
            record["our_etf_id"] = our_etf_id
            record["market"] = "KR"  # KIS 국내 종목정보라 KR 고정
            record["kis_symbol"] = kis_symbol
            record["fetched_at"] = fetched_at
            yield record

    def _fetch_etf(self, kis_symbol: str, token: str) -> dict:
        """한 ETF 의 상품기본정보. rt_cd!=0 은 오류, EGW00201 만 본문 기반 재시도한다.

        `output` 이 **객체 하나**다(NAV 의 배열과 다르다). 빈 객체는 정상 ETF 로는 나올 수 없어
        (잘못된 코드·상장폐지) fail-loud 한다 — 조용한 0건 금지.
        """
        params = {"PRDT_TYPE_CD": PRDT_TYPE_STOCK, "PDNO": kis_symbol}
        url = self.base + PATH_STOCK_INFO + "?" + urllib.parse.urlencode(params)
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
            "tr_id": TR_ID_STOCK_INFO,
            "custtype": "P",
        }
        for attempt in range(MAX_RATE_RETRY):
            body = self.client.request("GET", url, headers=headers, decode=True)
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError(f"KIS 응답이 객체가 아님: {type(data).__name__}")
            if data.get("rt_cd") == "0":
                output = data.get("output")
                if not isinstance(output, dict):
                    raise ValueError(f"KIS rt_cd=0 인데 output 이상: {type(output).__name__}")
                if not output:
                    raise ValueError("empty output — 잘못된 종목코드이거나 상장폐지")
                return output
            if data.get("msg_cd") == RATE_MSG_CD and attempt < MAX_RATE_RETRY - 1:
                self.client._sleep(0.7 * (attempt + 1))
                continue
            raise ValueError(
                f"KIS rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
            )
        raise ValueError(f"KIS {RATE_MSG_CD} 재시도 소진")
