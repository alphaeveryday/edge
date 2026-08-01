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
import time
import urllib.parse
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

from ..config import KrxEtfSource as KrxEtfSourceConfig
from ..ops.trading_calendar import latest_kr_trading_day
from .http import PoliteClient, StopFetch
from .krx_auth import USER_AGENT, KrxAuth

logger = logging.getLogger(__name__)

GETJSONDATA_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
BLD = "dbms/MDC/STAT/standard/MDCSTAT05001"  # ETF PDF 구성종목 서비스
# getJsonData 는 XHR 인 척(브라우저 로더 페이지 Referer) 요청해야 열린다(라이브 실측).
REFERER = "https://data.krx.co.kr/contents/MDC/mdiLoader/index.cmd?menuId=MDC0201030108"

KST = timezone(timedelta(hours=9))

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
    return latest_kr_trading_day(today)


def _short_code(isin: str) -> str:
    """ISIN 표준코드(예 KR7069500007) → 6자리 단축코드(069500).

    한국 표준코드는 12자리 'KR' + 1 + 6자리 종목코드 + 3 체크숫자 구조라 [3:9]가 단축코드다.
    isuCd2 로 함께 보내 라이브 실측 요청 본문과 정확히 일치시킨다(getJsonData 재현성).
    """
    # ponytail: KR 표준코드 12자리 가정(실측 대상 전부 부합). 비정형 코드는 원본을 그대로 넘긴다.
    return isin[3:9] if len(isin) >= 9 else isin


class KrxEtfSource:
    source_name = "krx"

    def __init__(
        self, config: KrxEtfSourceConfig, client: PoliteClient, deadline_sec: float | None = None
    ):
        self.config_enabled = config.enabled
        self.mbr_id = config.mbr_id
        self.pw = config.pw
        # our_etf_id → KRX ISIN(표준코드). 종목맵과 별개 — 이 맵의 키가 곧 수집 유니버스다.
        self.etf_map = config.etf_map
        self.client = client
        # 수집 루프의 벽시계 상한(초). None = 무제한(기본 — 이전과 동일).
        #
        # **왜 필요한가** (2026-07-27 실증): KRX 가 마감 직후 혼잡 구간에 느려지자 브랜치가
        # 25분을 넘겨 끝날 기미가 없었고, 사람이 ECS 태스크를 SIGKILL 로 죽였다. 그 순간
        # **이미 받아둔 24종이 저장 코드에 닿기 전에 함께 날아갔다**(스텝은 부분 저장 경로를
        # 갖고 있었는데 프로세스가 그 앞에서 끊겼다). 끝나는 방식을 벤더나 사람이 아니라
        # 우리가 정해야 한다 — 상한에 닿으면 스스로 접고 **받은 것은 저장**한다.
        self.deadline_sec = deadline_sec
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

    def _deadline_exceeded(self, started_at: float) -> bool:
        return self.deadline_sec is not None and time.monotonic() - started_at >= self.deadline_sec

    def _note_unattempted(self, remaining: list[tuple[str, str]], elapsed: float) -> None:
        """상한에 걸려 **시도조차 못 한** 대상을 하나씩 기록한다.

        개수만 남기지 않고 정체(ISIN)를 남기는 이유: "7종 실패"와 "12종은 시도조차 못 함"은
        다른 사실이고, 뒤엣것의 목록이 없으면 다음 날 결손 범위를 알 수 없다. KRX 는 trdDd
        백필 수단이 없어(ALPHA-387) 그날 못 받은 ETF 는 그날로 끝이다 — 무엇이 빠졌는지가
        곧 복구 대상 목록이다.

        기록 자리는 `fetch_failures` 그대로다. 스텝이 이 목록을 보고 status=partial 로 남기고
        collection_log 에 실어 보내므로, 새 통로를 만들 이유가 없다(어휘 확장 금지).
        """
        logger.warning(
            "krx ETF 수집 상한(%.0f초) 도달 — %.0f초 경과, 남은 %d종은 시도하지 않는다",
            self.deadline_sec, elapsed, len(remaining),
        )
        reason = f"수집 상한 {self.deadline_sec:.0f}초 초과로 미시도(경과 {elapsed:.0f}초)"
        for our_etf_id, isin in remaining:
            self._note_failure(isin, our_etf_id, reason)

    def fetch(self) -> Iterator[dict]:
        """ETF 별로 그날(trdDd) PDF 구성종목 행을 낸다(ETF 당 1콜, 스냅샷).

        로그인은 run 당 1회 — 실패는 소스 전체 문제(자격증명·중복세션)라 격리하지 않고 예외로
        올린다(스텝이 error 로 드러냄). ETF 단위 실패(요청 실패·깨진 JSON·이상 응답·빈 output)는
        격리·기록하고 남은 ETF 를 계속 수집한다. StopFetch(4xx/429 — 미로그인 400 LOGOUT 포함)만
        소스 전체를 중단한다(세션·쿼터 문제라 재시도·격리 대상이 아니다).
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
        jsessionid = self.auth.session()
        started_at = time.monotonic()
        for index, (our_etf_id, isin) in enumerate(plan):
            # 상한을 넘겼으면 **대상 사이에서** 접는다. 여기서 return 하면 제너레이터가 정상
            # 종료해 스텝이 지금까지 yield 한 행을 그대로 저장한다(중단이 아니라 조기 마감).
            if self._deadline_exceeded(started_at):
                self._note_unattempted(plan[index:], time.monotonic() - started_at)
                return
            try:
                yield from self._fetch_etf(our_etf_id, isin, trd_dd, jsessionid, fetched_at)
            except StopFetch:
                raise  # 4xx/429(미로그인 LOGOUT 포함) 는 소스 전체 문제 — 중단이 맞다
            except Exception as exc:
                # 요청 실패·깨진 JSON·이상/빈 응답은 ETF 단위로 격리 — 남은 ETF 계속.
                self._note_failure(isin, our_etf_id, str(exc))
                continue

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
        raw = self.client.request("POST", GETJSONDATA_URL, headers=headers, data=body, decode=True)
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
