"""KRX 종목기본정보 소스 어댑터 — 상장 전종목의 단축코드·한글명 (ALPHA-829).

엔드포인트: GET `data-dbg.krx.co.kr/svc/apis/sto/{stk|ksq|knx}_isu_base_info.json?basDd=`
(헤더 `AUTH_KEY`). KRX Data Marketplace OPEN API 이고, **같은 저장소의 `krx_etf` 가 쓰는
비공식 getJsonData 와는 다른 서비스다**:

  - `krx_etf` → `data.krx.co.kr` 비공식 경로. 계정 로그인 게이트(JSESSIONID 세션 유지)
  - 여기  → `data-dbg.krx.co.kr` 공식 OpenAPI. **무상태 헤더 인증**, 로그인 자체가 없다

🔴 **이 어댑터는 쿠키를 쓰지 않는다.** 두 레인이 같은 계정을 쓰더라도 OpenAPI 는 로그인
POST 를 하지 않으므로 KRX 의 중복 로그인 축출(`CD011`)을 유발하지 않는다 — holdings 세션은
안전하다. 다만 **응답은 자기 `Set-Cookie: JSESSIONID` 를 준다**(라이브 실측)이고 holdings
로그인 쿠키는 `Domain=.krx.co.kr` 라 이 호스트로도 전송되는 범위다. 지금 무해한 이유는
양쪽 다 쿠키 자를 쓰지 않기 때문이다 — **여기에 세션 기반 클라이언트를 끌어오지 마라.**

**시장마다 엔드포인트가 다르다.** 한 번에 전종목을 주지 않아 3콜(유가·코스닥·코넥스)이다.
2026-08-06 실측: 943 + 1,820 + 109 = 2,872종.

⚠️ **당일 조회가 막혀 있다.** 서비스 SQL 에 `:basDd < TO_CHAR(SYSDATE,'YYYYMMDD')` 가 박혀
있어 **전 영업일까지만** 응답한다(최근 거래일분은 08시 이후). 그래서 기준일은 "오늘"이
아니라 `latest_kr_trading_day(어제)` 다 — Planner·krx_etf 와 같은 달력 규칙을 쓴다.

유량은 제약이 아니다: 약관상 키당 1일 10,000회인데 이 어댑터는 run 당 3콜이다.

raw 존에는 응답 행 원본에 수집 provenance(market·bas_dd·fetched_at)만 붙여 그대로 낸다.
필드 선별·정규화는 후속 canonical(`normalize_instrument_profile`) 소관이다(레이크 규약).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

from ..config import KrxInstrumentSource as KrxInstrumentSourceConfig
from ..ops.trading_calendar import latest_kr_trading_day
from .http import PoliteClient, StopFetch

logger = logging.getLogger(__name__)

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/sto"

# 시장 코드 → 엔드포인트 접두어. KRX 가 시장별로 서비스를 나눠 둬서 합집합이 곧 전종목이다.
_ENDPOINT_BY_BOARD = {"KOSPI": "stk", "KOSDAQ": "ksq", "KONEX": "knx"}

KST = timezone(timedelta(hours=9))


class KrxInstrumentSource:
    """KRX OpenAPI 종목기본정보 → raw 행 스트림.

    관례 인터페이스(`source_name`·`enabled`·`plan`·`fetch`·`fetch_failures`)를 지켜
    `ingest_raw_instrument` 스텝이 벤더를 몰라도 되게 한다(다른 소스 어댑터와 동형).
    """

    market = "KR"

    def __init__(
        self,
        config: KrxInstrumentSourceConfig,
        *,
        client: PoliteClient | None = None,
        today: date | None = None,
    ):
        self.config = config
        # min_interval 1.0 — 3콜뿐이라 유량 제약은 없지만, 공유 운반 계층의 기본 예의를 따른다.
        self.client = client or PoliteClient(min_interval=1.0, timeout=30.0)
        self._today = today
        self.fetch_failures: list[dict] = []

    @property
    def source_name(self) -> str:
        """소스 라벨 — raw provenance·로그의 축."""
        return "krx"

    @property
    def enabled(self) -> bool:
        """설정 플래그 **와** 크리덴셜 유무를 함께 본다(krx_etf 와 같은 계약).

        키가 없는데 True 를 돌려주면 빈 `AUTH_KEY` 로 3콜이 나가 4xx→중단(exit 1)이 된다.
        스텝의 skip 사유 문구가 "disabled or missing credentials" 인데 뒤쪽이 영영 성립하지
        않게 되는 것도 문제다 — 시크릿 주입 누락이 수집 장애로 위장된다.
        """
        return self.config.enabled and bool(self.config.auth_key)

    def base_date(self) -> str:
        """질의 기준일 `basDd`(YYYYMMDD).

        **오늘이 아니라 직전 거래일이다** — 모듈 독스트링의 당일 차단 때문이다. 오늘을
        보내면 조용히 0행이 오는데, 그건 "상장 종목이 없다"가 아니라 "물어본 날이 틀렸다"라서
        0행을 정상으로 처리하면 빈 마스터가 착지한다.
        """
        today = self._today or datetime.now(KST).date()
        return latest_kr_trading_day(today - timedelta(days=1)).strftime("%Y%m%d")

    def plan(self) -> list[str]:
        """수집 대상 시장 목록. 유니버스가 고정이라 계획도 고정 3건이다."""
        return list(_ENDPOINT_BY_BOARD)

    def fetch(self) -> Iterator[dict]:
        """시장 보드 3건을 순회해 상장종목 행(raw)을 낸다.

        4xx(StopFetch)는 시장 하나가 아니라 소스 전체 문제라 전파한다 —
        행 단위 이상은 `fetch_failures` 로 격리된다.
        """
        # 재호출이면 앞선 실패 목록을 비운다 — 안 비우면 두 번째 fetch 가 첫 번째의 실패까지
        # 세어 런 상태가 실제보다 나빠진다(krx_etf 와 같은 계약).
        self.fetch_failures = []
        bas_dd = self.base_date()
        fetched_at = datetime.now(timezone.utc).isoformat()
        for board in self.plan():
            try:
                yield from self._fetch_board(board, bas_dd, fetched_at)
            except StopFetch:
                # 4xx/429 — 인증키 오류·활용신청 미승인이 여기 온다. 시장 하나만의 문제가
                # 아니라 소스 전체 문제이므로 중단이 맞다.
                raise
            except Exception as exc:
                # 한 시장의 실패로 나머지를 버리지 않는다. 다만 조용히 넘기지 않고 사유를
                # 남겨 스텝이 partial 로 드러낸다(Rule 12).
                self.fetch_failures.append({"board": board, "bas_dd": bas_dd, "error": str(exc)})
                logger.warning("KRX 종목기본정보 %s 실패: %s", board, exc)

    def _fetch_board(self, board: str, bas_dd: str, fetched_at: str) -> Iterator[dict]:
        url = f"{BASE_URL}/{_ENDPOINT_BY_BOARD[board]}_isu_base_info.json?basDd={bas_dd}"
        raw = self.client.request(
            "GET", url, headers={"AUTH_KEY": self.config.auth_key or ""}, decode=True
        )
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"json: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"KRX 응답이 객체가 아님: {type(data).__name__}")
        rows = data.get("OutBlock_1")
        if not isinstance(rows, list):
            # 200 인데 블록이 없거나 비-list — 스키마 드리프트나 오류 응답이다. 조용한 0행
            # 처리 금지(krx_etf 의 빈 output 처리와 동형).
            raise ValueError(f"OutBlock_1 이 배열이 아님: {type(rows).__name__}")
        if not rows:
            # 빈 배열은 대개 **기준일이 틀린 것**이다(당일·비거래일·2010-01-04 이전).
            # 정상 시장이 0종일 수는 없으니 실패로 올린다.
            raise ValueError(f"0행 — basDd={bas_dd} 가 조회 가능한 거래일인지 확인")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"행이 객체가 아님: {type(row).__name__}")
            yield {**row, "board": board, "market": self.market,
                   "bas_dd": bas_dd, "fetched_at": fetched_at}
