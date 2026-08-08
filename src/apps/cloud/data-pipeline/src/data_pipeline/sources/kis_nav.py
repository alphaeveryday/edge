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
from ..parse import krx_short_code
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


class KisNavShapeError(ValueError):
    """`rt_cd=0` 인데 응답 형상이 계약 밖 — **재시도로 안 풀린다**.

    `ValueError` 를 상속하는 것이 핵심이다. 배치 경로(`fetch`)는 `except Exception` 으로
    ETF 단위 격리를 하므로 동작이 그대로고, **재시도 여부를 판정해야 하는 호출자만**
    이 타입으로 갈라 본다(1분 레인의 `minute/inav_collect.py`). 그쪽에서 형상 위반을
    재시도 축(missing)으로 접으면 벤더가 키 이름 하나만 바꿔도 전 unit 이 매 window
    "벤더가 안 줬다"로 기록되고, 원장에는 우리 파서가 깨진 사실이 남지 않는다.

    **`rt_cd=0` 인데 `output` 이 list 가 아닌 경우만** 이 타입이다. rt_cd 오류·빈 output
    (일시 거절·창에 데이터 없음)은 물론이고, **본문이 dict 조차 아닌 경우도 아니다** —
    그건 잘린 응답·프록시 오류 페이지 쪽이 압도적이라 재시도 축이 맞다.

    ⚠️ **`kis_minute` 과 여기서 갈린다.** 저쪽은 두 봉투 조건(`output2 이상`·`응답이
    객체가 아님`)을 **묶어서** 전송 사고로 본다(테스트로 고정 — `test_envelope_shape_
    error_is_transient_not_cached`). 여기서 하나만 떼는 근거는 `rt_cd`다: 본문이 유효한
    JSON 이고 `rt_cd="0"` 까지 들어 있으면 그 응답은 **KIS 가 준 것**이지 프록시가 끼운
    오류 페이지가 아니다(잘린 본문이 우연히 유효 JSON 경계에 떨어질 확률은 무시할 만하다).
    거기서 `output` 이 없거나 list 가 아니면 스키마 드리프트로 읽는 게 맞다.
    저쪽 규칙이 더 넓고 테스트도 있으므로, 이 갈림은 **의도된 것으로 여기 적어 둔다**
    (Rule 7 — 충돌은 평균 내지 말고 표면화). 저쪽을 이쪽에 맞출지는 별개 판단이다.
    """


def _yyyymmdd(date_str: str | None) -> str | None:
    """수집 창 날짜(YYYY-MM-DD) → KIS 파라미터 형식(YYYYMMDD). None 은 그대로 None."""
    return date_str.replace("-", "") if date_str else None


class KisNavSource:
    source_name = "kis"
    # 엔드포인트·질의 파라미터는 하위 어댑터가 갈아끼운다(kis_inav.KisInavSource) —
    # rt_cd 판정·EGW00201 재시도·malformed 행 격리는 한 곳(_fetch_etf)에 남긴다.
    tr_id = TR_ID_NAV_DAILY
    path = PATH_NAV_DAILY

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
        # 실제 EGW00201 재시도 수. 유량은 **앱키 전역**이라(초당 20) 이 소스가 1분 가격
        # 레인과 같은 한도를 나눠 쓴다 — 0 으로 고정하면 iNAV 폴링이 가격 레인을 굶기고
        # 있어도 관측에서 통째로 사라진다(`price_collect` 의 retry_count 와 같은 축).
        self.retry_count = 0

    @property
    def enabled(self) -> bool:
        # 앱키·시크릿은 env 로만 주입(커밋 금지) — 둘 중 하나라도 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.app_key) and bool(self.app_secret)

    def plan(self) -> list[tuple[str, str]]:
        """수집 대상 → [(our_etf_id, kis_symbol)]. etf_map 이 곧 유니버스다.

        KIS 는 ISIN 이 아니라 6자리 단축코드로 질의하므로 표준코드에서 파생한다.

        단축코드는 **숫자 전용이 아니다** — KRX 가 번호를 소진해 신규 상장분에는 문자가 섞인
        코드(0093A0·0005G0 등)를 발급하고, 우리 유니버스 31종 중 7종이 그렇다. KIS 는 이
        코드도 그대로 받는다(2026-07-20 실측: 0093A0·0005G0 각 33행). 형태 판정은
        `parse.krx_short_code`(선두 숫자 + 영숫자 6자) 하나로 간다 — `isdigit()` 로 거르면
        7종이 조용히 빠지고(ALPHA-380·463), 파생이 6자리 코드꼴이 아니면 질의하지 않고
        실패로 드러낸다(엉뚱한 코드로 KIS 를 두드려 무의미한 오류를 쌓지 않는다).
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
                for row in self._fetch_etf(our_etf_id, kis_symbol, d1, d2, token):
                    # bronze 무변형: output 행 원본 보존 + 수집 provenance 만 부착.
                    record = dict(row)
                    record.update(self._extra_provenance())
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

    def _row_defect(self, row: object) -> str | None:
        """이 행을 raw 로 낼 수 없으면 사유, 낼 수 있으면 None.

        기본은 형태만 본다 — bronze 는 무변형 보존이라 값 판정은 canonical 소관이다.
        다만 **행을 식별조차 못 하는 결손**은 격리해 드러낸다: 그런 행을 그대로 저장하면
        저장은 success 인데 다운스트림이 쓸 수 없어 수집 실패가 성공으로 위장된다(Rule 12).
        어느 필드가 그에 해당하는지는 엔드포인트마다 달라 하위 어댑터가 더한다.
        """
        if not isinstance(row, dict):
            return f"malformed row: {type(row).__name__}"
        return None

    def _query_params(self, kis_symbol: str, d1: str, d2: str) -> dict[str, str]:
        """이 엔드포인트의 질의 파라미터. 하위 어댑터가 오버라이드한다."""
        return {
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV,
            "FID_INPUT_ISCD": kis_symbol,
            "FID_INPUT_DATE_1": d1,
            "FID_INPUT_DATE_2": d2,
        }

    def _extra_provenance(self) -> dict[str, object]:
        """raw 행에 덧붙일 어댑터 고유 필드. 일별 NAV 는 없다."""
        return {}

    def _note_rows(
        self, our_etf_id: str, kis_symbol: str, rows: list[dict], received_count: int
    ) -> None:
        """한 ETF 의 응답 행이 확정된 뒤 부르는 관측 훅. 기본은 무동작.

        `_row_defect` 는 행 하나를 **버릴지** 정하고 이건 통과한 행 전체를 **본다** —
        응답 집합 수준에서만 보이는 성질(최신 행이 얼마나 낡았나 같은)이 있어서다.

        `rows` 는 걸러진 뒤고 `received_count` 는 **벤더가 준 행 수**다. 둘을 함께 주는
        이유: "벤더가 계약대로 줬는가"와 "그중 우리가 쓸 수 있는 게 몇인가"는 다른 질문이고,
        걸러진 수로 전자를 재면 우리가 버린 행이 벤더의 위반으로 보고된다.

        관측 전용이라 반환값이 없고, **호출부가 예외까지 삼킨다**(`_fetch_etf`). 반환값이
        없는 것과 성패를 못 뒤집는 것은 별개다 — 감싸지 않으면 오버라이드의 예외 하나가
        그 ETF 의 행을 통째로 날린다. 계약을 산문이 아니라 구조가 진다."""

    def _fetch_etf(
        self, our_etf_id: str, kis_symbol: str, d1: str, d2: str, token: str
    ) -> list[dict]:
        """한 ETF 의 창 NAV 를 1콜로 받는다. rt_cd!=0 은 오류, EGW00201 만 본문 기반 재시도.

        실패는 예외로 올려 호출부가 ETF 단위로 격리한다. 빈 output 은 정상 ETF·정상 창으로는
        나올 수 없어(잘못된 코드·비영업일만 걸린 창) fail-loud 한다 — 조용한 success 0건 금지.
        """
        url = (
            self.base + self.path + "?"
            + urllib.parse.urlencode(self._query_params(kis_symbol, d1, d2))
        )
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
            "tr_id": self.tr_id,
            "custtype": "P",
        }
        for attempt in range(MAX_RATE_RETRY):
            # 4xx/429 는 client 가 StopFetch 로 올린다(전체 중단). 5xx·네트워크는 client 가 재시도.
            body = self.client.request("GET", url, headers=headers, decode=True)
            data = json.loads(body)  # 깨진 JSON → ETF 단위 실패로 전파
            if not isinstance(data, dict):
                # ⚠️ 여기는 **재시도 축으로 남긴다**(`KisNavShapeError` 아님). 본문이 dict
                # 조차 아니면 잘린 응답·프록시 오류 페이지 같은 **전송 사고**가 압도적이다 —
                # `kis_minute` 이 같은 조건을 `KisUnitError`(전송 사고 축)로 돌리는 이유이고
                # 테스트로 고정돼 있다(`test_envelope_shape_error_is_transient_not_cached`).
                raise ValueError(f"KIS 응답이 객체가 아님: {type(data).__name__}")
            if data.get("rt_cd") == "0":
                # 이 엔드포인트는 output2 가 아니라 단일 output 배열이다(라이브 실측).
                # 키 누락·비-list 는 rt_cd=0 인데도 이상(스키마 드리프트)이라 fail-loud.
                output = data.get("output")
                if not isinstance(output, list):
                    raise KisNavShapeError(
                        f"KIS rt_cd=0 인데 output 이상: {type(output).__name__}"
                    )
                if not output:
                    raise ValueError("empty output — 응답 창에 데이터가 없거나 잘못된 종목코드")
                # 못 쓰는 행이 섞여도 한 행이 ETF 전체를 끊지 않게 — 기록 후 스킵.
                rows = []
                for row in output:
                    defect = self._row_defect(row)
                    if defect is None:
                        rows.append(row)
                    else:
                        # our_etf_id 를 함께 남긴다 — symbol 만으로는 로그 소비자가
                        # 어느 ETF 의 원본 행이 유실됐는지 내부 식별자로 잇지 못한다.
                        self._note_failure(kis_symbol, our_etf_id, defect)
                try:
                    self._note_rows(our_etf_id, kis_symbol, rows, len(output))
                except StopFetch:
                    # 소스 전역 신호는 삼키지 않는다. 아래 `fetch()` 가 4xx/429 를 격리
                    # 대상에서 명시적으로 빼는데(키·쿼터는 중단이 맞다), 그 계약 **아래층**
                    # 에서 `except Exception` 이 먼저 먹으면 규약이 두 곳으로 갈린다.
                    # 지금 이 훅은 HTTP 를 안 타 도달 불가지만, 아래 도크스트링이 삼킴을
                    # 계약으로 못박은 이상 `self.client` 를 쓰는 오버라이드가 붙는 순간
                    # 조용히 열린다.
                    raise
                except Exception:
                    # 훅 도크스트링의 "수집 성패를 뒤집지 않는다" 를 **코드로** 강제한다.
                    # 감싸지 않으면 예외가 fetch() 의 ETF 단위 격리에 잡혀 이 ETF 의 행이
                    # 통째로 버려지고, 실패 사유 칸에 관측 메시지가 수집 실패인 것처럼
                    # 박힌다. iNAV 는 소급 조회가 불가라 그 유실이 영구적이다.
                    logger.exception(
                        "행 관측 실패 — 수집은 계속한다: %s(%s)", kis_symbol, our_etf_id
                    )
                return rows
            # 초당한도는 HTTP 429 가 아니라 본문 코드로 온다 — 운반 계층이 모르니 여기서 재시도.
            if data.get("msg_cd") == RATE_MSG_CD and attempt < MAX_RATE_RETRY - 1:
                self.retry_count += 1
                self.client._sleep(0.7 * (attempt + 1))
                continue
            raise ValueError(
                f"KIS rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
            )
        raise ValueError(f"KIS {RATE_MSG_CD} 재시도 소진")
