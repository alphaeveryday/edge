"""KIS(한국투자) 국내 종목별 **장중** 투자자 추정 소스 어댑터 (ALPHA-767 — 원본저장).

API: 국내주식 투자자매매동향(장중 추정) investor-trend-estimate, tr_id `HHPTJ04160200`.
종목당 1콜(`MKSC_SHRN_ISCD`)로 그날의 장중 스냅샷 행들을 `output2` 로 준다.

**EOD 어댑터(`kis_investor.py`)와 무엇이 다른가** — 같은 "투자자 수급"이지만 축이 다르다:

| | EOD `FHPTJ04160001` | 장중 `HHPTJ04160200` |
|---|---|---|
| 값 | 거래일 **확정** 순매수 | 장중 **추정**(`*_fake_*` = 가집계) |
| 시간축 | `stck_bsop_date`(거래일) | `bsop_hour_gb`(그날의 슬롯) |
| 날짜창 | `FID_INPUT_DATE_1` 로 과거 30거래일 | **없다 — 오늘치만** |
| 서빙 | 15:41 이후(`OPSQ2001` 경계) | 장중 |
| 대상 | 개별주식·ETF | **개별주식만** — ETF 는 0행 |

⚠️ **소급 조회가 없다.** 날짜 파라미터 자체가 없어 지난 날의 장중 추이는 영영 받을 수 없다.
그래서 이 소스의 결손은 **슬롯을 놓치면 그날 안에서만 회복 가능**하다(iNAV 와 같은 성질,
[[kis-inav-minute-fields]]). 갭은 폴링 슬롯으로만 막는다.

⚠️ **ETF 자체의 장중 투자자 귀속은 거래소가 생산하지 않는다**(KIS 장중 투자자 4종 전수조사로
확정, 2026-07-21 — `.dev/etf-flow-collection-plan.md` §2.5). 우리 유니버스는 ETF **구성종목**
(개별주식)이라 적용되지만, holdings 유니버스에 섞여 오는 ETF 자신은 빈 응답이 정상이다.

EOD 어댑터와 같은 관례 인터페이스(`source_name`·`enabled`·`plan`·`fetch`·`fetch_failures`·
`planned_symbols`·`universe_from_holdings`)를 지켜 `ingest_raw_investor` 스텝을 그대로
재사용한다(dataset·파티션만 인자로 갈아끼운다 — ALPHA-380 의 `ingest_raw_etf` 선례와 같은 형태).

raw 존에는 `output2` 행 원본에 수집 provenance 만 붙여 그대로 낸다(bronze 무변형). 다만
`asof_date` 는 **응답에 날짜 필드가 없어서** 붙이는 필수 provenance 다 — 이게 없으면 canonical
이 어느 거래일의 스냅샷인지 복원할 수 없다(KRX holdings 가 `trd_dd` 를 우리가 라벨하는 것과
같은 형태, ALPHA-653).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, time, timedelta, timezone

from ..config import KisInvestorSource as KisInvestorSourceConfig
from ..ops.trading_calendar import is_trading_day
from ..parse import krx_short_code
from .http import PoliteClient, StopFetch
from .kis_auth import KisAuth, domain_for

logger = logging.getLogger(__name__)

TR_ID_INVESTOR_ESTIMATE = "HHPTJ04160200"
PATH_INVESTOR_ESTIMATE = "/uapi/domestic-stock/v1/quotations/investor-trend-estimate"
RATE_MSG_CD = "EGW00201"  # "초당 거래건수 초과" — HTTP 429 아님(200 본문). 어댑터가 재시도.
MAX_RATE_RETRY = 5

KST = timezone(timedelta(hours=9))
# 그날 첫 스냅샷 슬롯(외국인 09:30 — 기관은 10:00). 이 시각 **전에는 오늘의 추정이 존재하지
# 않는다**(.dev/etf-flow-collection-plan.md §2.5 실측).
SESSION_FIRST_SLOT = time(9, 30)


class KisInvestorEstimateSource:
    """KIS 장중 투자자 추정 어댑터 — 슬롯 기반 스냅샷. 수집 가능 시각 판정은
    `skip_reason` 이 진다(비거래일엔 라벨을 지어낼 수 없다)."""

    source_name = "kis"

    def __init__(self, config: KisInvestorSourceConfig, client: PoliteClient):
        self.env = config.env
        self.base = domain_for(config.env)
        self.app_key = config.app_key
        self.app_secret = config.app_secret
        self.config_enabled = config.enabled
        # our_ticker → KIS 6자리 코드. KR 은 대개 항등. 맵에 없는 종목(US 등)은 이 소스가
        # 건너뛴다 — KIS 는 국내(KRX) 전용이다(EOD 어댑터와 동일 정책).
        self.symbol_map = config.symbol_map
        self.client = client
        self.auth = KisAuth(config.app_key or "", config.app_secret or "", client, config.env)
        # 심볼 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다(격리≠은폐).
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한(매핑된) 대상 수. 활성인데 0이면 스텝이 skip 으로 드러낸다.
        self.planned_symbols: int | None = None
        # 수집 유니버스를 canonical KR holdings 에서 파생하라는 옵트인(ALPHA-419·482) —
        # EOD 와 **같은 축**이다(구성종목 개별주식). 두 데이터셋이 다른 유니버스를 보면
        # 장중↔확정 대조 자체가 성립하지 않는다.
        self.universe_from_holdings = True

    @property
    def enabled(self) -> bool:
        """수집 가능 여부 — 설정 플래그 AND 앱키·시크릿(env 주입) 존재."""
        # 앱키·시크릿은 env 로만 주입(커밋 금지) — 둘 중 하나라도 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.app_key) and bool(self.app_secret)

    @property
    def skip_reason(self) -> str | None:
        """지금 수집하면 안 되는 사유, 수집해도 되면 None (ALPHA-769).

        판정 내용은 종전과 같다 — **비거래일엔 라벨을 지어낼 수 없다.** 응답에 날짜가 없어
        거래일은 우리가 붙이는데(`fetch` 의 asof_date), 휴장일·개장 전에 KIS 가 직전 슬롯을
        그대로 돌려주면 **어제 데이터가 오늘 거래일로 저장된다**. 소급 재조회가 없는 소스라
        잘못 붙은 라벨은 응답으로 복원할 수 없다.

        KRX holdings 가 `_as_of` 로 "거래일이면 오늘, 아니면 직전 거래일"을 택한 것과 다른
        선택인 이유: 그건 **일별 스냅샷**이라 직전 거래일 값이 여전히 참이지만, 장중 추정은
        비거래일에 존재 자체를 하지 않아 어떤 라벨도 참이 아니다.

        ⚠️ **바뀐 것은 이 사실을 무엇으로 표현하느냐다(ALPHA-767 이 남긴 규약 충돌).**
        종전에는 `fetch` 안에서 `ValueError` 를 올렸다 — 그러면 스텝이 `status=error`·exit 1 로
        마감한다. 레인이 평일 cron 으로 도는 순간(ALPHA-769) 그 규약은 **공휴일마다 런을
        FAILED 로 만든다**: 예정대로 아무것도 없는 날인데 알람이 울리고, 진짜 고장과 구분되지
        않는다. 같은 성질의 소스인 iNAV(`kis_inav.skip_reason`)는 처음부터 skip 규약이었다 —
        레포가 이미 푼 문제라 그쪽에 맞춘다(Rule 7·11).

        거래일 판정은 `ops.trading_calendar.is_trading_day`(env `OPS_KR_HOLIDAYS`)를 그대로
        쓴다. Planner·KRX·iNAV 와 같은 휴장일 집합이어야 한다 — 복제하면 갈라진다.
        """
        now_kst = datetime.now(KST)
        if not is_trading_day(now_kst.date()):
            return (
                f"{now_kst.date()} 는 KR 거래일이 아니다 — 장중 투자자 추정은 비거래일에 "
                "존재하지 않아 거래일 라벨을 붙일 수 없다(소급 불가라 사후 정정도 안 된다)"
            )
        # 거래일 여부만으로는 부족하다 — **개장 전**에도 `is_trading_day` 는 참이다. 월요일
        # 08:00 실행에서 KIS 가 금요일 마지막 슬롯을 그대로 주면 그 행이 월요일 거래일로
        # 저장된다(위와 같은 유실, 같은 이유로 복원 불가). 첫 슬롯 전에는 오늘의 추정이
        # 존재하지 않으므로 라벨을 만들지 않는다.
        if now_kst.time() < SESSION_FIRST_SLOT:
            return (
                f"{now_kst.time().strftime('%H:%M')} 는 첫 슬롯"
                f"({SESSION_FIRST_SLOT.strftime('%H:%M')}) 전이다 — 오늘의 장중 추정이 아직 "
                "없어 거래일 라벨을 붙일 수 없다"
            )
        return None

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, kis_symbol)]. EOD 어댑터의 `plan` 과 같은 규칙이다 —
        KRX 6자리 코드는 항등 매핑이 기본이고 `symbol_map` 은 예외 오버라이드 축이다.
        형태 판정은 `parse.krx_short_code`(ALPHA-463) — 문자 섞인 신형 단축코드(0093A0)도
        국내 코드라 항등 대상이고, `ABCDEF` 같은 US 심볼은 국내 API 로 새지 않는다.
        """
        out: list[tuple[str, str]] = []
        for our_ticker in symbols:
            kis_symbol = self.symbol_map.get(our_ticker) or krx_short_code(our_ticker)
            if not kis_symbol:
                logger.info("kis 장중 투자자 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, kis_symbol))
        return out

    def _note_failure(self, kis_symbol: str, our_ticker: str, reason: str) -> None:
        """심볼 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("kis 장중 투자자 심볼 건너뜀: %s (%s)", kis_symbol, reason)
        self.fetch_failures.append(
            {"symbol": kis_symbol, "our_ticker": our_ticker, "error": reason, "kind": "failure"}
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """심볼별로 **오늘**의 장중 투자자 추정 행을 낸다(종목당 1콜, 페이지네이션 없음).

        `from_date`/`to_date` 는 스텝 인터페이스 호환을 위해 받되 **쓰지 않는다** — 이 API 는
        날짜 파라미터가 없다. 창을 준 실행 자체는 CLI 가 거부하므로(run.py) 여기 도달하지
        않지만, 도달하더라도 조용히 무시하지 않고 경고를 남긴다(Rule 12).

        토큰은 run 당 1회 발급한다 — 발급 실패는 소스 전체 문제(키)라 격리하지 않고 예외로
        올린다. 심볼 단위 실패(요청 실패·깨진 JSON·KIS 오류코드)는 격리·기록하고 남은 심볼을
        계속 수집한다. StopFetch(4xx/429)만 소스 전체를 중단한다(EOD 어댑터와 동형).
        """
        if from_date or to_date:
            logger.warning(
                "장중 투자자 추정은 날짜창을 받지 않는다(소급 불가) — from=%s to=%s 무시",
                from_date, to_date,
            )
        self.fetch_failures = []
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)  # 빈 plan(매핑 대상 0)을 스텝이 감지하게
        if not plan:
            return
        now_kst = datetime.now(KST)
        # ⚠️ **라벨 가드는 여기서도 유효하다** — 배선된 경로는 스텝이 `skip_reason` 을 먼저 보고
        # 돌아가므로(ingest_raw_investor) 이 raise 는 안 밟힌다. 남겨 두는 건 **직접 호출자**
        # 때문이다: 조건을 스텝으로만 옮기면 어댑터를 직접 쓰는 경로가 휴장일에 조용히 어제
        # 데이터를 오늘 거래일로 라벨하고, 이 소스는 소급 재조회가 없어 사후 정정이 불가하다.
        # 조건 자체는 복제하지 않는다 — `skip_reason` 하나가 정본이다(갈라지면 두 경로의 판정이
        # 어긋난다). 사유 문구는 그대로 쓴다.
        if (blocked := self.skip_reason) is not None:
            raise ValueError(blocked)
        fetched_at = datetime.now(timezone.utc).isoformat()
        # 응답에 날짜가 없다 — 수집 시점의 KST 날짜가 이 스냅샷의 거래일이다. 위 거래일 가드가
        # 통과한 뒤라야 이 라벨이 참이다.
        asof_date = now_kst.strftime("%Y-%m-%d")
        # 토큰 1회 발급(종목마다 발급 금지). 키 오류(4xx)는 client 가 StopFetch 로,
        # 200-무토큰은 kis_auth 가 RuntimeError 로 올린다 — 둘 다 fetch 밖으로 전파(전체 중단).
        token = self.auth.token()
        for our_ticker, kis_symbol in plan:
            # ⚠️ **행 단위 사유는 심볼당 1회만 기록한다.** `fetch_failures` 는 스텝이
            # `records_failed_symbols`·`ops.failed_records` 로 세는 **심볼 단위** 목록이라
            # (`ingest_raw_investor` 참조), 행마다 append 하면 심볼 1개가 행 수만큼 실패로
            # 부풀어 카운터의 단위가 어긋난다. 사유별로 한 번씩만 남긴다.
            noted: set[str] = set()
            try:
                for row in self._fetch_symbol(kis_symbol, token):
                    if not isinstance(row, dict):
                        # 배열 안 dict 아닌 행(스키마 드리프트)은 한 행이 심볼 전체를 끊지
                        # 않게 기록 후 스킵한다(조용히 버리지 않는다, Rule 12).
                        if "malformed" not in noted:
                            noted.add("malformed")
                            self._note_failure(
                                kis_symbol, our_ticker, f"malformed row: {type(row).__name__}"
                            )
                        continue
                    # 슬롯(`bsop_hour_gb`)은 이 데이터셋 정체성 키의 일부다 — canonical 이
                    # (market, ticker, trade_date, asof_slot) 로 병합하므로 없으면 그 행은
                    # **어느 시점 값인지 영영 모른다**. 행은 보존하되(bronze 무변형) 실패로
                    # 기록해 런을 partial 로 드러낸다 — 안 그러면 쓸 수 없는 행이 records_out
                    # 에 섞여 성공 0건과 구분되지 않는다(Rule 12).
                    if not str(row.get("bsop_hour_gb") or "").strip() and "slotless" not in noted:
                        noted.add("slotless")
                        self._note_failure(
                            kis_symbol, our_ticker,
                            "슬롯 필드(bsop_hour_gb) 없음 — canonical 정체성 키 조립 불가",
                        )
                    # bronze 무변형: output2 행 원본 보존 + 수집 provenance 만 부착.
                    record = dict(row)
                    record["our_ticker"] = our_ticker
                    record["market"] = "KR"  # KIS 는 KRX 로컬 전용
                    record["kis_symbol"] = kis_symbol
                    record["asof_date"] = asof_date  # 응답에 날짜가 없어 붙이는 필수 provenance
                    record["fetched_at"] = fetched_at
                    yield record
            except StopFetch:
                raise  # 4xx/429 는 소스 전체 문제(키·쿼터) — 중단이 맞다
            except Exception as exc:
                # 요청 실패·깨진 JSON·KIS 오류코드는 심볼 단위로 격리 — 남은 심볼 계속.
                self._note_failure(kis_symbol, our_ticker, str(exc))
                continue

    def _fetch_symbol(self, kis_symbol: str, token: str) -> list[dict]:
        """한 심볼의 장중 추정 1콜. rt_cd!=0 은 오류. 본문 오류코드 EGW00201(초당한도)만 재시도.

        **EOD 어댑터의 `OPSQ2001` 재시도를 옮겨오지 않는다.** 그건 "EOD 확정 데이터가 아직
        서빙 전"이라는 조건이고(15:41 해소), 이 엔드포인트는 장중에 서빙된다. 여기서 그 코드가
        오면 그건 자가해소되는 경계가 아니라 **우리가 모르는 상태**라 즉시 격리·기록이 맞다
        (ALPHA-562 의 교훈: 벤더 오류코드에 재시도를 붙이기 전에 "시간이 지나면 풀리는가,
        조건이 바뀌어야 풀리는가"를 먼저 정한다).
        """
        # ⚠️ 파라미터 이름은 KIS 공식 예제(open-trading-api `investor_trend_estimate`)를
        # 그대로 따른다 — 문서 표기는 `MKSC_SHRN_ISCD` 지만 공식 샘플이 이 형태로 보낸다.
        params = {"MKSC_SHRN_ISCD": kis_symbol}
        url = self.base + PATH_INVESTOR_ESTIMATE + "?" + urllib.parse.urlencode(params)
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
            "tr_id": TR_ID_INVESTOR_ESTIMATE,
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
                # 단일 슬롯(장 초반)은 output2 를 dict 하나로 줄 수 있다 — 유효한 1행을
                # 실패로 처리하지 않게 list 로 감싼다(EOD 어댑터와 동형).
                if isinstance(output2, dict):
                    return [output2]
                # 빈 list([])는 정상이다 — **ETF 는 구조적으로 0행**이고, 장 시작 직후엔
                # 개별주식도 아직 슬롯이 없다. 반면 키 누락(None)·비-list 는 rt_cd=0 인데도
                # 이상(malformed success)이라 정상 빈 응답으로 위장시키지 않는다(Rule 12).
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
        raise ValueError(f"KIS 초당한도 재시도 소진 ({MAX_RATE_RETRY}회)")
