"""KRX ETF 구성종목(PDF/holdings) 소스 어댑터 (ALPHA-336 — KR ETF Step1 원본저장).

엔드포인트: POST data.krx.co.kr/comm/bldAttendant/getJsonData.cmd
(bld=dbms/MDC/STAT/standard/MDCSTAT05001, isuCd=ISIN, trdDd=기준일). 로그인 계정 게이트
뒤라 KrxAuth 로 얻은 JSESSIONID 세션 쿠키를 붙여 호출한다.

US(FmpEtfSource)와 같은 관례 인터페이스(source_name·enabled·plan·fetch·fetch_failures·
planned_etfs)를 지켜 기존 `ingest_raw_etf` 스텝을 그대로 재사용한다. 차이는 (1) 인증이 KRX
계정 로그인(run 당 1회, krx_auth) (2) KR 시장 전용이라 market 은 항상 KR (3) etf_map 이
our_etf_id → ISIN(표준코드)이고, ETF 당 1콜로 현재 PDF 구성종목 전량(스냅샷)을 받는다.

기준일(as-of)은 우리가 지정하는 trdDd 다 — 매 run 이 그날의 PDF 전량을 받아 append 한다
(재무·US ETF 스냅샷과 동형). raw 존에는 output 행 원본에 수집 provenance(our_etf_id·market·
isin·trd_dd·fetched_at)만 붙여 그대로 낸다 — 해외기초 ETF 는 비중·금액이 `-`(대시)로 오는데
그것도 무변형 보존한다(필드 선별·정규화·기준일 SCD 는 후속 canonical, ALPHA-342 소관).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

from ..config import KrxEtfSource as KrxEtfSourceConfig
from ..ops.trading_calendar import is_trading_day
from .fanout import fanout
from .http import PoliteClient, StopFetch
from .krx_auth import USER_AGENT, KrxAuth

logger = logging.getLogger(__name__)

GETJSONDATA_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
BLD = "dbms/MDC/STAT/standard/MDCSTAT05001"  # ETF PDF 구성종목 서비스
# getJsonData 는 XHR 인 척(브라우저 로더 페이지 Referer) 요청해야 열린다(라이브 실측).
REFERER = "https://data.krx.co.kr/contents/MDC/mdiLoader/index.cmd?menuId=MDC0201030108"

KST = timezone(timedelta(hours=9))

# 직전 거래일 탐색 상한. 최장 연휴(설·추석 + 앞뒤 주말)보다 넉넉하다 — 넘어가면 달력이 아니라
# OPS_KR_HOLIDAYS 주입이 잘못된 것이라 fail-loud 한다.
MAX_LOOKBACK_DAYS = 10


def _as_of(today: date) -> date:
    """기준일(as-of) — 오늘이 KR 거래일이면 오늘, 아니면 직전 거래일 (ALPHA-387).

    스케줄이 장 마감 후(15:40 KST)라 거래일 런에서는 그날 PDF 가 이미 게시돼 있다(dev 실측:
    07-22·23·24 연속 스냅샷 내용 상이). 문제는 **비거래일 런**이다 — KRX 는 빈 응답이 아니라
    직전 거래일 PDF 를 그대로 돌려주므로(dev 실측: 토 07-18 응답이 금 07-17 과 바이트 동일)
    오늘로 라벨하면 존재하지 않는 거래일의 스냅샷이 canonical 에 as-of 로 남는다. 라벨을 실제
    기준일로 되돌리면 그 런은 직전 거래일 스냅샷을 같은 as-of 로 다시 쓴다(멱등).

    휴장일 집합은 Planner 와 **같은** `OPS_KR_HOLIDAYS`(env)를 본다 — 달력이 갈리면 Planner 가
    비거래일로 건너뛴 날을 수집은 거래일로 라벨하는 모순이 생긴다.
    """
    for back in range(MAX_LOOKBACK_DAYS):
        day = today - timedelta(days=back)
        if is_trading_day(day):
            return day
    raise ValueError(
        f"{today} 부터 {MAX_LOOKBACK_DAYS}일 안에 거래일이 없다 — OPS_KR_HOLIDAYS 주입 확인"
    )


def _short_code(isin: str) -> str:
    """ISIN 표준코드(예 KR7069500007) → 6자리 단축코드(069500).

    한국 표준코드는 12자리 'KR' + 1 + 6자리 종목코드 + 3 체크숫자 구조라 [3:9]가 단축코드다.
    isuCd2 로 함께 보내 라이브 실측 요청 본문과 정확히 일치시킨다(getJsonData 재현성).
    """
    # ponytail: KR 표준코드 12자리 가정(실측 대상 전부 부합). 비정형 코드는 원본을 그대로 넘긴다.
    return isin[3:9] if len(isin) >= 9 else isin


class KrxEtfSource:
    source_name = "krx"

    def __init__(self, config: KrxEtfSourceConfig, client: PoliteClient, concurrency: int = 1):
        self.config_enabled = config.enabled
        self.mbr_id = config.mbr_id
        self.pw = config.pw
        # our_etf_id → KRX ISIN(표준코드). 종목맵과 별개 — 이 맵의 키가 곧 수집 유니버스다.
        self.etf_map = config.etf_map
        self.client = client
        # ETF 단위 동시 요청 수. 기본 1 = 직렬(이전과 동일). getJsonData 는 조회마다 집계를
        # 도느라 콜당 12~17초인데 그 시간이 전부 서버 대기라, 동시성이 그대로 이득이 된다
        # (2026-07-26 라이브 프로브 31종: N=1 대비 N=6 에서 3.9배, N=12 에서 6.8배. 콜 지연은
        # 9.1s→10.8s 로 +19% 에 그쳐 세션 단위 직렬화가 없음을 확인).
        self.concurrency = concurrency
        self.auth = KrxAuth(config.mbr_id or "", config.pw or "")
        # ETF 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다(격리≠은폐).
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한(매핑된) 대상 수. 활성인데 0이면 스텝이 skip 으로 드러낸다.
        self.planned_etfs: int | None = None

    @property
    def enabled(self) -> bool:
        # 자격증명은 env 로만 주입(커밋 금지) — 둘 중 하나라도 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.mbr_id) and bool(self.pw)

    def plan(self) -> list[tuple[str, str]]:
        """수집 대상 → [(our_etf_id, isin)]. etf_map 이 곧 유니버스다(US 어댑터와 동형)."""
        return sorted(self.etf_map.items())

    def _note_failure(self, isin: str, our_etf_id: str, reason: str) -> None:
        """ETF 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("krx ETF 건너뜀: %s (%s)", isin, reason)
        self.fetch_failures.append(
            {"isin": isin, "our_etf_id": our_etf_id, "error": reason}
        )

    def fetch(self) -> Iterator[dict]:
        """ETF 별로 그날(trdDd) PDF 구성종목 행을 낸다(ETF 당 1콜, 스냅샷).

        로그인은 run 당 1회 — 실패는 소스 전체 문제(자격증명·중복세션)라 격리하지 않고 예외로
        올린다(스텝이 error 로 드러냄). ETF 단위 실패(요청 실패·깨진 JSON·이상 응답·빈 output)는
        격리·기록하고 남은 ETF 를 계속 수집한다. StopFetch(4xx/429 — 미로그인 400 LOGOUT 포함)만
        소스 전체를 중단한다(세션·쿼터 문제라 재시도·격리 대상이 아니다). 단 400 QUERYTIMEOUT
        은 예외 — 그 질의 하나가 서버 계산 제한을 넘긴 것이라 ETF 단위로 격리한다(_fetch_etf).
        """
        self.fetch_failures = []
        plan = self.plan()
        self.planned_etfs = len(plan)  # 빈 plan(매핑 대상 0)을 스텝이 감지하게
        if not plan:
            return
        fetched_at = datetime.now(timezone.utc).isoformat()
        # 기준일 = 거래일이면 오늘, 비거래일이면 직전 거래일(_as_of 주석에 근거).
        # ponytail: trdDd 백필(--to 배선)은 필요 시 추가.
        trd_dd = _as_of(datetime.now(KST).date()).strftime("%Y%m%d")
        # 로그인 1회(ETF마다 로그인 금지). 실패는 fetch 밖으로 전파해 소스 전체를 중단한다.
        # 세션 쿠키는 문자열이라 워커가 공유해도 된다(프로브 실증: 6·8·12 동시에서 로그아웃·
        # CD011 0건). 유량은 self.client(PoliteClient)가 묶는다 — 워커별 클라이언트 금지.
        jsessionid = self.auth.session()
        try:
            yield from fanout(
                plan,
                lambda pair: self._fetch_etf(pair[0], pair[1], trd_dd, jsessionid, fetched_at),
                concurrency=self.concurrency,
                on_failure=lambda pair, exc: self._note_failure(pair[1], pair[0], str(exc)),
            )
        finally:
            # malformed row 는 `_fetch_etf` 안에서 기록한다 — 그 경로는 **워커 스레드**라
            # 팬아웃의 입력순 기록을 우회한다. 두 경로가 섞이면 collection_log 의 실패 목록
            # 순서가 실행마다 달라져 감사·회귀 비교가 흔들리므로 plan 순으로 되돌린다.
            # **finally 인 이유**: StopFetch 로 빠져나가는 경로에서도 스텝이 그때까지의
            # fetch_failures 를 status=stopped 로그에 쓴다 — 그 목록도 결정적이어야 한다.
            order = {our_etf_id: i for i, (our_etf_id, _) in enumerate(plan)}
            self.fetch_failures.sort(key=lambda f: order.get(f["our_etf_id"], len(order)))

    def _fetch_etf(
        self, our_etf_id: str, isin: str, trd_dd: str, jsessionid: str, fetched_at: str
    ) -> Iterator[dict]:
        """한 ETF 의 PDF 구성종목을 한 번 호출해 holdings 행을 낸다.

        실패는 예외로 올려 호출부가 ETF 단위로 격리한다. 빈 output(미게시·잘못된 ISIN) 은
        정상 ETF 로는 나올 수 없어 fail-loud(격리해 partial/error 로 드러냄) — US 어댑터의
        '빈 holdings' 처리와 동형이다.
        """
        body = urllib.parse.urlencode({
            "bld": BLD, "locale": "ko_KR",
            "isuCd": isin, "isuCd2": _short_code(isin), "trdDd": trd_dd,
            "share": "1", "money": "1", "csvxls_isNo": "false",
        }).encode("utf-8")
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": REFERER,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"JSESSIONID={jsessionid}",
        }
        try:
            raw = self.client.request(
                "POST", GETJSONDATA_URL, headers=headers, data=body, decode=True
            )
        except StopFetch as exc:
            # 400 QUERYTIMEOUT 은 세션·쿼터가 아니라 **그 질의가 서버 계산 제한(≈61초)을 넘긴
            # 것**이다(2026-07-27 열화 장애 라이브 실측 — 클라이언트 타임아웃 300초에도 서버가
            # 60~61초에 스스로 끊었다). 소스 전체를 중단하면 ETF 하나의 열화가 나머지 전부를
            # 죽이므로 이 ETF 만 격리한다. 본문 의미 판정은 어댑터 몫이라는 StopFetch.status/
            # body 계약 그대로다(KIS 토큰 403 EGW00133 선례). LOGOUT 등 나머지 4xx 는 전체 중단.
            if exc.status == 400 and "QUERYTIMEOUT" in exc.body:
                raise ValueError(f"QUERYTIMEOUT: {exc}") from exc  # → ETF 단위 실패
            raise
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"json: {exc}") from exc  # → ETF 단위 실패
        if not isinstance(data, dict):
            raise ValueError(f"KRX 응답이 객체가 아님: {type(data).__name__}")
        output = data.get("output")
        if not isinstance(output, list):
            # 200 인데 output 이 없거나 비-list(스키마 드리프트·오류 응답)면 조용한 0행 처리
            # 금지 — ETF 실패로 올린다(US 어댑터의 '비배열 응답' 처리와 동형).
            raise ValueError(f"output 이상: {type(output).__name__}")
        if not output:
            # 빈 output 은 정상 ETF 로는 나올 수 없다(미게시·잘못된 ISIN) — fail-loud.
            raise ValueError("empty output")
        for row in output:
            # 배열 안에 dict 아닌 행이 섞여도 한 행이 남은 수집을 끊지 않게 — 기록 후 스킵.
            if not isinstance(row, dict):
                self._note_failure(isin, our_etf_id, f"malformed row: {type(row).__name__}")
                continue
            record = dict(row)
            record["our_etf_id"] = our_etf_id
            record["market"] = "KR"  # KRX 는 국내 전용
            record["isin"] = isin
            record["trd_dd"] = trd_dd  # as-of(우리가 지정한 기준일) — canonical 이 쓴다
            record["fetched_at"] = fetched_at
            yield record
