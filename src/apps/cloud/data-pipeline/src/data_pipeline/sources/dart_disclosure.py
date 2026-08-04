"""OpenDART 국내 공시(disclosure filing) 소스 어댑터 (disclosures raw).

엔드포인트(재무 fnlttSinglAcnt 와 **다른 API**):
    {base_url}/corpCode.xml?crtfc_key=...                     # 종목코드→corp_code (enrich 전용)
    {base_url}/list.json?crtfc_key=&bgn_de=&end_de=&page_no=&page_count=  # 공시목록
    {base_url}/document.xml?crtfc_key=&rcept_no=              # 공시서류 원본(ZIP)

**날짜창의 시장 전체 공시목록**을 페이지네이션하며 유니버스(stock_code)와 report_nm 부분일치로
대상만 낸다(공시목록은 전 종목·전 유형을 준다). 공시서류 원본 본문(document.xml)은 step 이
rcept_no 별로 fetch_document 로 받아 별도 객체에 무변형 저장한다 — 이 어댑터의 fetch() 는 메타
행만 낸다.

⚠️ `corp_code` 는 **선택 파라미터**다(실측 2026-08-03). 종목별로 질의하면 호출 수가 유니버스
크기에 비례해(311 종 = 311 콜, PoliteClient 1.0s ⇒ ~311초) 어떤 잦은 실행도 불가능한데, 생략하면
창 전체가 페이지 수에만 비례한다(하루 ~700~1,070건 = 7~11 콜). 그래서 유니버스는 **질의 축이
아니라 필터**다. 부수 효과로 수집 경로에서 corpCode.xml 해소가 사라진다 — list 행이 stock_code 를
직접 주므로, 매 런 상수로 걸리던 미매핑 실패(kind=unmapped)가 구조적으로 없어진다(ALPHA-477 의
`data_status=INCOMPLETE` 고착 요인). `load_corp_map()` 은 enrich-corp-code 스텝이 계속 쓴다.

⚠️ 유형 파라미터(`pblntf_ty`)로 좁히지 않는다. 실측(2026-07-28~08-01)상 "사업보고서" 부분일치는
A(정기공시) 34건 외에 H(자산유동화) 27건에도 걸려, 유형으로 먼저 자르면 필터 대상이 **조용히**
줄어든다. 전 유형을 받아 report_nm 으로 거르는 편이 호출 몇 개 더 쓰고 가정을 하나 없앤다.

raw 존에는 list 행 원본에 수집 provenance(our_ticker·market·stock_code·source_url·fetched_at)만
붙여 그대로 낸다. 정규화·정정 판정·정체성 병합은 후속 canonical 소관이다 — 여기서 접는 건
한 순회 안의 **완전히 같은 행**(페이지 이동 중복)뿐이라 증거가 늘지 않는다.

⚠️ **`rcept_no` 앞 8자리는 날짜가 아니다.** 접수번호 발번일이라 공시일(`rcept_dt`)과 어긋날
수 있다 — 실측(2026-07-31) 1,069건 중 94건이 `20260730…` 으로 시작한다(효력발생안내처럼 이전
접수번호에 딸린 후속 공시). 접수번호를 워터마크로 쓰려는 시도는 여기서 막힌다. 애초에 이 API
에는 증분 커서가 없어(시각 필드도 없다) **매번 날짜창 전체를 다시 읽는 것 외에 방법이 없다**.
그 재독이 낭비처럼 보이지만 하루 4~11 콜이고, 부작용으로 매 런이 그날에 대한 독립된 완전
관측이 된다 — 런 사이의 rcept_no 집합 비교가 곧 완전성 근거다(판정은 원장·EOD 소관).

⚠️ **18:00~19:00 제출분은 다음 날 창에 들어간다.** DART 접수시스템은 당일접수 07:30~18:00,
익일접수 18:00~19:00 이라, 18:38 제출 공시의 `rcept_dt` 는 다음 날이다(실측 2026-08-03).
"오늘 하루"만 질의하면 매일 마지막 1시간을 놓친다 — 기본 증분 창(어제~오늘)이 이걸 덮는다.

⚠️ **웹 최신공시(dsac001)를 주 소스로 쓰지 않는다.** 그쪽은 접수시각(HH:MM)과 엄격한 시간
내림차순을 줘서 워터마크가 실제로 동작하지만(틱당 1 콜), **커버리지가 다르다**: 07-31 하루
기준 웹 701건 vs API 1,069건으로 지분공시 계열(임원·주요주주 소유상황 121, 대량보유 86 등)이
통째로 빠진다. 축도 다르다 — 웹은 제출·갱신 시각, API 는 접수일자. 현 필터(공급계약·사업
보고서)에 한해선 2일치 누락 0 이었지만 그건 **지금 필터 기준**이라, 대상이 넓어지는 순간
조용히 샌다. 접수시각 축이 필요해지면(장중 트리거 정렬·지연 측정) 그때 보조로 다시 본다.

⚠️ 실측(2026-07-10): list.json 은 source_url 을 안 줘 rcept_no 로 구성한다. report_nm 은 꼬리
공백 패딩·가운뎃점 ㆍ(U+318D)·[기재정정] 접두가 있어 필터는 strip 후 부분일치로 잡는다.
문서 본문은 euc-kr HTML(UTF-8 XML 아님) 이라 무변형 ZIP bytes 로만 보존한다.

corpCode.xml 처리·status 코드는 재무 어댑터(dart_financial)와 같은 관례다 — 소스 파일
self-contained 관례(bigkinds·kis 도 동일)에 따라 여기 재기술한다. 둘의 통일은 후속 정리.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.parse
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from ..config import DartDisclosureSource as DartDisclosureSourceConfig
from ..parse import krx_short_code
from .http import PoliteClient, StopFetch

logger = logging.getLogger(__name__)

CORP_CODE_PATH = "/corpCode.xml"
LIST_PATH = "/list.json"
DOCUMENT_PATH = "/document.xml"

# 공시서류 원본 뷰어 URL — list.json 이 안 주므로 rcept_no 로 구성한다(파생 provenance).
VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
# 본문 무변형 저장 포맷 표기 — 실측상 ZIP 내부는 euc-kr HTML(xforms).
BODY_FORMAT = "zip/html;charset=euc-kr"

STATUS_MESSAGES = {
    "000": "정상",
    "010": "미등록 키",
    "011": "사용할 수 없는 키",
    "012": "접근 불가 IP",
    "013": "조회 데이터 없음",
    "020": "일 사용한도 초과",
    "100": "필드값 오류",
    "800": "시스템 점검",
    "900": "정의되지 않은 오류",
    "901": "사용자 계정 만료/차단",
}

# 키·IP·쿼터·점검은 특정 종목 문제가 아니라 소스 전체 문제라 즉시 중단한다.
STOP_STATUS_CODES = {"010", "011", "012", "020", "800", "901"}

# corp_code 없는 질의의 창 상한 — 실측(2026-08-03): 4개월 창은
# `status=100 "corp_code가 없는 경우 검색기간은 3개월만 가능합니다."` 로 거절된다. 종목별
# 질의엔 없던 제약이라, 분할하지 않으면 3개월 초과 백필(`--from/--to`)이 통째로 실패한다.
#
# 30일로 자르는 건 상한 3개월보다 **훨씬 짧다** — 페이지 상한도 같이 지켜야 하기 때문이다:
# 3개월 창은 실측 54,716건 = page_count 100 기준 548 페이지로 max_pages(500)를 넘는다.
# 30일이면 ~18,000건 ≈ 180 페이지로 두 제약 안에 들어온다.
WINDOW_CHUNK_DAYS = 30

# 창 끝을 못 받았을 때 채울 기준 시각. 파이프라인 전체가 KR 시장 기준이라 KST 다
# (steps/dart_values·assemble_events 와 같은 관례).
_KST = timezone(timedelta(hours=9))


def _window_segments(
    from_date: str | None, to_date: str | None
) -> tuple[str | None, str | None, list[tuple[str | None, str | None]]]:
    """수집 창을 소스가 받아주는 길이(WINDOW_CHUNK_DAYS)로 자른다. 실제로 쓴 창을 함께 낸다.

    - 양끝이 다 있으면: 그대로 자른다.
    - 시작일만 있으면: 끝을 KST 오늘로 **확정하고** 자른다(아래 이유).
    - 끝일만 있거나 둘 다 없으면: 자르지 않고 그대로 넘긴다 — 소스 기본 시작일이 당일이라
      상한 안이고, 여기서 시작일을 만들어내면 소스 기본 동작을 조용히 바꾸는 것이다.

    반환: (실제 창 from, 실제 창 to, 세그먼트 목록). 실제 창을 함께 내는 이유는 호출자가
    그걸 collection_log 에 남겨야 하기 때문이다 — 끝일을 여기서 확정하면 같은 명령도 자정을
    사이에 두고 다른 창이 되는데, 로그에 원래 인자(None)만 남으면 **어떤 창을 수집했는지
    복원할 수 없고** 런 사이 rcept_no 집합 비교(이 설계의 완전성 근거)가 성립하지 않는다.
    """
    if from_date and not to_date:
        # ⚠️ 시작일만 준 백필(`--from 2026-01-01`)은 CLI 가 허용하는 조합이다. 그대로 넘기면
        # 자르지 못한 채 소스 기본 끝일(당일)까지의 창이 되어, 3개월을 넘는 순간 status=100
        # 으로 **통째로 실패**한다 — 종목별 질의 시절엔 되던 백필이다. 끝을 오늘로 확정해
        # 자른다: 어차피 소스가 쓰던 값과 같고, 여기서 확정해야 분할이 성립한다.
        to_date = datetime.now(_KST).date().isoformat()
    if not from_date or not to_date:
        # 끝일만 준 경우는 자르지 않는다 — 소스 기본 시작일은 당일이라 창이 3개월을 넘지
        # 않는다. 양쪽 다 없으면 소스 기본(당일) 그대로다.
        return from_date, to_date, [(from_date, to_date)]
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if start > end:
        # 뒤집힌 창은 여기서 고치지 않는다 — 그대로 넘겨 소스가 거절하게 둔다(조용한 정정 금지).
        return from_date, to_date, [(from_date, to_date)]
    segments: list[tuple[str | None, str | None]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=WINDOW_CHUNK_DAYS - 1), end)
        segments.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + timedelta(days=1)
    return from_date, to_date, segments


def _to_dart_date(value: str | None) -> str | None:
    """수집 날짜창(YYYY-MM-DD) → DART bgn_de/end_de(YYYYMMDD). None 은 그대로 None."""
    if value is None:
        return None
    return value.replace("-", "")


class DartDisclosureSource:
    source_name = "dart"

    def __init__(self, config: DartDisclosureSourceConfig, client: PoliteClient):
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.config_enabled = config.enabled
        self.symbol_map = config.symbol_map
        self.report_name_filters = list(config.report_name_filters)
        self.page_count = config.page_count
        self.max_pages = config.max_pages
        self.client = client
        # 수집 유니버스를 canonical KR holdings 최신 스냅샷에서 파생한다(ALPHA-477) — 가격
        # (ALPHA-419)·수급(ALPHA-482)과 같은 축. 정적 targets 는 유니버스와 어긋난다: 구성종목이
        # 309 종으로 커지는 동안 공시는 KR 9 종에 묶여 있었다. 스텝이 이 플래그를 보고 union 한다.
        self.universe_from_holdings = True
        self.fetch_failures: list[dict] = []
        self.planned_symbols: int | None = None
        # 소스가 스스로 신고한 창 전체 건수(1페이지 total_count)와 실제로 훑은 행 수. **판정이
        # 아니라 관측**이다 — 목록은 수집 중에도 자란다(접수 피크 16시). 페이지 경계가 밀려
        # 둘이 어긋나는 것과 진짜 누락은 구분되지 않으므로, 여기서 완전성을 단언하면 관대한
        # 쪽이든 엄격한 쪽이든 거짓이 된다. 스텝은 이 값을 collection_log 에 기록만 한다.
        self.list_total_count: int | None = None
        self.list_rows_seen: int = 0
        # 실제로 수집한 날짜창 — 인자와 다를 수 있다(시작일만 준 창은 끝을 오늘로 확정한다).
        # 스텝이 이걸 collection_log 에 남겨야 어떤 창이었는지 사후에 복원된다.
        self.resolved_window: tuple[str | None, str | None] = (None, None)
        # 직전 세그먼트가 **끝까지 못 갔는지**. 행 단위 격리(malformed row·비문자열 필드 등)와
        # 구분하려고 따로 둔다 — "fetch_failures 가 늘었는가"로 보면 행 하나가 이상해도 창
        # 전체가 멈춘다. 절단은 페이지 순회가 중간에 끊긴 것이고, 격리는 그 행만 빠진 것이다.
        self._segment_truncated = False
        self._corp_map: dict[str, dict[str, str]] | None = None

    @property
    def enabled(self) -> bool:
        return self.config_enabled and bool(self.api_key)

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, stock_code)]. KR 단축코드는 **항등 매핑**이 기본이고
        (KRX 코드가 곧 list.json 의 stock_code), symbol_map 은 항등이 아닌 예외의 오버라이드
        축으로만 남는다(투자자 수급 plan 과 동형, ALPHA-477). KRX 코드 형태가 아닌 미매핑 심볼
        (US 등)은 제외 — OpenDART 는 국내 전용이다.

        질의 축이 아니라 **필터 집합**이다(모듈 docstring) — fetch() 가 이 목록을
        stock_code→our_ticker 로 뒤집어 시장 전체 목록에서 우리 것만 고른다.

        형태 판정은 `parse.krx_short_code` 가 한다(ALPHA-463) — 문자 섞인 신형 단축코드
        (0093A0 등)도 corpCode.xml 이 그대로 주므로 항등 대상이고, `ABCDEF` 같은 6자 US 심볼은
        국내 API 로 새지 않는다. 항등 폴백 전에는 symbol_map 에 손으로 적은 9 종만 통과해,
        holdings 로 넓힌 유니버스가 여기서 도로 잘려나갔다.
        """
        out: list[tuple[str, str]] = []
        for our_ticker in symbols:
            stock_code = self.symbol_map.get(our_ticker) or krx_short_code(our_ticker)
            if not stock_code:
                logger.info("dart 공시 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, stock_code))
        return out

    def _note_failure(
        self,
        stock_code: str | None,
        our_ticker: str | None,
        reason: str,
        *,
        rcept_no: str | None = None,
        page: int | None = None,
        kind: str = "failure",
    ) -> None:
        logger.warning(
            "dart 공시 대상 건너뜀: %s rcept_no=%s page=%s (%s)",
            stock_code, rcept_no, page, reason,
        )
        self.fetch_failures.append(
            {
                "symbol": stock_code,
                "our_ticker": our_ticker,
                "rcept_no": rcept_no,
                "page": page,
                "error": reason,
                "kind": kind,
            }
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """날짜창의 시장 전체 공시목록을 페이지네이션해 유니버스∩대상 유형 메타 행을 낸다."""
        self.fetch_failures = []
        self.list_total_count = None
        self.list_rows_seen = 0
        self.resolved_window = (from_date, to_date)
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)
        if not plan:
            return
        # stock_code → our_ticker 역인덱스. 항등 매핑이 기본이라 실질 충돌은 symbol_map
        # 오버라이드가 같은 단축코드로 겹칠 때뿐이고, 그때는 먼저 온 쪽을 남긴다.
        allowed: dict[str, str] = {}
        for our_ticker, stock_code in plan:
            allowed.setdefault(stock_code, our_ticker)
        fetched_at = datetime.now(timezone.utc).isoformat()
        actual_from, actual_to, segments = _window_segments(from_date, to_date)
        self.resolved_window = (actual_from, actual_to)
        for seg_from, seg_to in segments:
            yield from self._scan_window(
                allowed, _to_dart_date(seg_from), _to_dart_date(seg_to), fetched_at
            )
            if self._segment_truncated:
                # 세그먼트가 절단됐으면 **뒤 세그먼트도 멈춘다.** 계속 돌면 "앞 날짜는 잘렸는데
                # 뒤 날짜는 온전한" raw 가 남고, partial 런도 후속 정제 대상이라 canonical 이
                # 날짜 중간에 구멍을 가진 채 완성된 것처럼 보인다. 창을 쪼갠 건 소스 제약
                # 때문이지 세그먼트가 서로 독립적인 실패 단위여서가 아니다 — 실패 단위는
                # 여전히 요청받은 창 하나다.
                logger.error(
                    "공시 창 %s~%s 에서 절단 — 이후 세그먼트 중단(창 전체가 온전치 않다)",
                    seg_from, seg_to,
                )
                return

    def _is_target(self, report_nm: str) -> bool:
        """report_nm(문자열)이 대상 유형인지 — strip 후 부분일치(ㆍ·패딩·[기재정정] 접두 안전)."""
        return any(f in report_nm.strip() for f in self.report_name_filters)

    def _scan_window(
        self,
        allowed: dict[str, str],
        bgn_de: str | None,
        end_de: str | None,
        fetched_at: str,
    ) -> Iterator[dict]:
        """한 세그먼트를 페이지 끝까지 훑어 대상 행을 낸다.

        **완전성을 판정하지 않는다.** 이 루프가 하는 일은 두 가지다: ① 끝까지 읽었는가(못
        읽었으면 크게 말한다) ② 무엇을 봤는가(기록만 한다). 응답이 자기에 대해 하는 말
        (`total_page`·`total_count`)로 "다 받았다"를 증명하려 들면 반례가 무한하다 — 그건
        벤더가 거짓말할 수 있는 방법을 세는 일이지 증거가 아니다. 실제 증거는 **독립된 두
        번째 관측**이고, 이 소스에선 그게 공짜로 나온다: 매 틱·매 런이 같은 날짜창을 통째로
        다시 읽으므로(증분 커서가 없어서 그럴 수밖에 없다) 런 사이의 rcept_no 집합 비교가
        곧 완전성 근거다. 그 판정은 여기가 아니라 원장·EOD 소관이다.

        창 하나가 실패 단위다 — 종목별 질의 시절의 corp 단위 예외 격리는 재현하지 않는다.
        격리 축이던 corp 루프가 사라졌고, 페이지 응답의 의미 오류는 그 창을 못 믿는다는
        뜻이지 한 대상의 문제가 아니다. 예외는 스텝까지 올라가 status=error 로 드러나고,
        그 전에 yield 된 행은 raw 에 남는다(부분 수집분 보존 + 실패 명시 = Rule 12).
        """
        self._segment_truncated = False
        # 목록이 자라는 동안 페이지 경계가 밀려 **같은 행**이 두 페이지에 걸쳐 나올 수 있다 —
        # 창 안에서 그 반복만 접는다. 접지 않으면 한 런이 같은 문서를 두 번 내려받는다.
        # 키가 rcept_no 가 아니라 행 내용인 이유: rcept_no 만 보면 같은 문서의 서로 다른
        # 관측(`rm` 이 ""→"정" 으로 바뀐 정정 표시)까지 접혀 raw 에 닿기 전에 사라진다.
        # raw 의 "전부 보존" 계약이 금지하는 건 서로 다른 관측을 접는 것이고, 여기서 접는 건
        # 완전히 같은 행이라 증거가 늘지 않는다.
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            payload = self._list(bgn_de, end_de, page)
            status = str(payload.get("status") or "?")
            if status == "013":
                # 조회 데이터 없음 = 정상 빈 창(그 기간에 대상 공시 없음) — 뉴스형.
                return
            if status in STOP_STATUS_CODES:
                msg = payload.get("message") or STATUS_MESSAGES.get(status, "?")
                raise StopFetch(
                    f"DART status={status} ({STATUS_MESSAGES.get(status, '?')}) msg={msg}"
                )
            if status != "000":
                msg = payload.get("message") or STATUS_MESSAGES.get(status, "?")
                raise ValueError(
                    f"DART status={status} ({STATUS_MESSAGES.get(status, '?')}) msg={msg}"
                )
            rows = payload.get("list")
            if not isinstance(rows, list):
                raise ValueError(f"DART status=000 인데 list 이상: {type(rows).__name__}")
            if page == 1:
                # 세그먼트마다 1페이지가 그 세그먼트 건수를 준다 — **누적**해야 창 전체 규모가
                # 된다(대입하면 마지막 세그먼트 값만 남아 list_rows_seen 과 축이 어긋난다).
                raw_count = payload.get("total_count")
                if isinstance(raw_count, int) and not isinstance(raw_count, bool):
                    self.list_total_count = (self.list_total_count or 0) + raw_count
            for row in rows:
                self.list_rows_seen += 1
                if not isinstance(row, dict):
                    # 행 형상이 깨졌으면 누구 것인지도 모른다 — 유니버스 필터 앞에 둘 수밖에
                    # 없고, 그래서 우리 종목이 아닌 행도 여기 잡힌다. 시장 전체를 훑는 이상
                    # 감수하는 오탐이다(조용히 버리는 쪽이 더 나쁘다).
                    self._note_failure(
                        None, None, f"malformed row: {type(row).__name__}", page=page,
                    )
                    continue
                # 유니버스 필터가 **먼저다.** 시장 전체 목록에는 우리가 수집하지 않는 회사 행이
                # 하루 수백 건 섞여 있어, 필드 게이트를 앞에 두면 남의 회사 행의 결함이 우리
                # 런의 failed_records 로 올라가 원장이 없는 결측을 센다. 종목별 질의 시절엔
                # 남의 행을 볼 일이 없어 없던 경로다.
                raw_stock_code = row.get("stock_code")
                if not isinstance(raw_stock_code, str):
                    # 문자열이 아니면(숫자·null·키 부재) 누구 것인지 판정할 수 없다 — 우리
                    # 종목일 수도 있어 유니버스 밖으로 접으면 그 유실이 영영 안 보인다.
                    # 빈 문자열과는 다르다: 그건 벤더가 명시적으로 "단축코드 없음"이라 답한
                    # 것이고(비상장·펀드 신고자, 하루 수백 건) 정상이다.
                    self._note_failure(
                        None, None,
                        f"stock_code 가 문자열이 아님: {type(raw_stock_code).__name__}"
                        " — 유니버스 판정 불가",
                        page=page,
                    )
                    continue
                stock_code = raw_stock_code.strip()
                our_ticker = allowed.get(stock_code)
                if our_ticker is None:
                    continue
                # 필드 타입도 행 단위로 격리한다 — 비문자열 report_nm/rcept_no 가 .strip()
                # 에서 터지면 창 전체가 죽는다(각도 H — crash-before-gate).
                report_nm = row.get("report_nm")
                if not isinstance(report_nm, str):
                    self._note_failure(
                        stock_code, our_ticker,
                        f"report_nm 비문자열: {type(report_nm).__name__}", page=page,
                    )
                    continue
                if not self._is_target(report_nm):
                    continue
                rcept_no = row.get("rcept_no")
                if not isinstance(rcept_no, str) or not rcept_no.strip():
                    self._note_failure(
                        stock_code, our_ticker,
                        "대상 공시인데 rcept_no 결측/비문자열", page=page,
                    )
                    continue
                fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                record = dict(row)
                record["our_ticker"] = our_ticker
                record["market"] = "KR"
                # stock_code 는 손대지 않는다 — dict(row) 가 벤더 원본을 그대로 담고 있다
                # (bronze 무변형). 우리 축은 our_ticker 가 담는다.
                record["source_url"] = VIEWER_URL.format(rcept_no=rcept_no.strip())
                record["fetched_at"] = fetched_at
                yield record
            # 순회를 계속할지 — 여기가 "끝까지 읽었는가"를 판정하는 **유일한 자리**다.
            #
            # ⚠️ `max(1, int(raw))` 로 강제하지 않는다. 그 조합은 `False`·`0`·`-3`·`1.9` 를
            # 전부 **1페이지**라는 통과값으로 바꿔, 뒷페이지가 실재해도 실패 기록 없이 끝난다
            # (각도 H — coerce-to-passing). 창 전체가 한 순회라 1페이지 오판은 100행만 남기고
            # 나머지를 통째로 버린다. 실측상 OpenDART 는 양의 int 를 준다(2026-08-03).
            raw_total = payload.get("total_page")
            valid = (
                isinstance(raw_total, int)
                and not isinstance(raw_total, bool)
                and raw_total >= 1
            )
            if not valid:
                self._stop_early(
                    f"total_page 를 페이지 수로 읽을 수 없음: {raw_total!r}", page=page
                )
                return
            if page >= raw_total:
                return
        self._stop_early(
            f"MAX_PAGES({self.max_pages}) 도달 — 창을 좁혀 재실행", page=self.max_pages
        )

    def _stop_early(self, reason: str, *, page: int) -> None:
        """창을 **끝까지 못 읽고** 멈췄다 — 기록하고 뒤 세그먼트도 중단시킨다.

        행 단위 격리(`_note_failure`)와 구분한다: 절단은 "이 창을 다 못 읽었다"라 뒤
        세그먼트를 돌면 날짜 중간에 구멍 난 raw 가 남지만, 격리는 "그 행만 빠졌다"라 나머지
        수집을 계속하는 게 맞다. 같은 신호로 묶으면 malformed 행 하나가 창 전체를 멈춘다.
        """
        self._note_failure(None, None, f"목록을 끝까지 읽지 못함 — {reason}", page=page)
        self._segment_truncated = True

    def _list(self, bgn_de: str | None, end_de: str | None, page: int) -> dict:
        params = {
            "crtfc_key": self.api_key or "",
            "page_no": page,
            "page_count": self.page_count,
        }
        if bgn_de:
            params["bgn_de"] = bgn_de
        if end_de:
            params["end_de"] = end_de
        query = urllib.parse.urlencode(params)
        body = self.client.request(
            "GET",
            f"{self.base_url}{LIST_PATH}?{query}",
            headers={"Accept": "application/json"},
            decode=True,
        )
        if not isinstance(body, str):
            raise ValueError("DART 공시목록 응답이 str 이 아님")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"json: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"DART 공시목록 응답이 객체가 아님: {type(payload).__name__}")
        return payload

    def fetch_document(self, rcept_no: str) -> bytes:
        """공시서류 원본(document.xml) 을 무변형 ZIP bytes 로 받는다.

        본문은 ZIP(내부 euc-kr HTML). 4xx/429/키·쿼터 오류는 StopFetch(수집 전체 중단),
        그 외 오류는 ValueError(대상 격리는 호출자 몫)로 드러낸다.
        """
        query = urllib.parse.urlencode({"crtfc_key": self.api_key or "", "rcept_no": rcept_no})
        raw = self.client.request(
            "GET", f"{self.base_url}{DOCUMENT_PATH}?{query}", decode=False
        )
        if not isinstance(raw, bytes):
            raise ValueError("DART 공시서류 응답이 bytes 가 아님")
        if raw[:2] != b"PK":
            # ZIP 이 아니면 에러 XML(status) — 재무 corpCode 에러 처리와 동형.
            self._raise_document_error(raw)
        return raw

    def _raise_document_error(self, raw: bytes) -> None:
        try:
            root = ET.fromstring(raw.decode("utf-8", errors="replace"))
        except ET.ParseError as exc:
            raise ValueError(f"DART 공시서류 예상외 응답: {raw[:120]!r}") from exc
        status = (root.findtext("status") or "?").strip()
        message = (root.findtext("message") or STATUS_MESSAGES.get(status, "?")).strip()
        detail = (
            f"DART 공시서류 status={status} ({STATUS_MESSAGES.get(status, '?')}) msg={message}"
        )
        if status in STOP_STATUS_CODES:
            raise StopFetch(detail)
        raise ValueError(detail)

    def load_corp_map(self) -> dict[str, dict[str, str]]:
        """corpCode.xml ZIP → {stock_code: {corp_code, corp_name}}. 런 내 메모리 캐시.

        ⚠️ **수집(fetch)은 더 이상 이걸 부르지 않는다** — 시장 전체 목록이 stock_code 를 직접
        주므로 종목→corp_code 해소가 필요 없다(모듈 docstring). 남아 있는 유일한 소비자는
        corp_code enrichment 스텝(ALPHA-491)이고, 그래서 여전히 public 이다."""
        if self._corp_map is not None:
            return self._corp_map
        query = urllib.parse.urlencode({"crtfc_key": self.api_key or ""})
        raw = self.client.request("GET", f"{self.base_url}{CORP_CODE_PATH}?{query}", decode=False)
        if not isinstance(raw, bytes):
            raise ValueError("DART corpCode 응답이 bytes 가 아님")
        if raw[:2] != b"PK":
            self._raise_corpcode_error(raw)
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                xml_bytes = zf.read(zf.namelist()[0])
        except (zipfile.BadZipFile, IndexError, KeyError) as exc:
            raise ValueError(f"DART corpCode ZIP 파싱 실패: {exc}") from exc
        try:
            root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
        except ET.ParseError as exc:
            raise ValueError(f"DART corpCode XML 파싱 실패: {exc}") from exc
        corp_map: dict[str, dict[str, str]] = {}
        for item in root.iter("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            corp_name = (item.findtext("corp_name") or "").strip()
            if stock_code and len(stock_code) == 6 and corp_code:
                corp_map[stock_code] = {"corp_code": corp_code, "corp_name": corp_name}
        if not corp_map:
            raise ValueError("DART corpCode 상장사 매핑 0건")
        self._corp_map = corp_map
        return corp_map

    def _raise_corpcode_error(self, raw: bytes) -> None:
        try:
            root = ET.fromstring(raw.decode("utf-8", errors="replace"))
        except ET.ParseError as exc:
            raise ValueError(f"DART corpCode 예상외 응답: {raw[:120]!r}") from exc
        status = (root.findtext("status") or "?").strip()
        message = (root.findtext("message") or STATUS_MESSAGES.get(status, "?")).strip()
        detail = f"DART corpCode status={status} ({STATUS_MESSAGES.get(status, '?')}) msg={message}"
        if status in STOP_STATUS_CODES:
            raise StopFetch(detail)
        raise ValueError(detail)
