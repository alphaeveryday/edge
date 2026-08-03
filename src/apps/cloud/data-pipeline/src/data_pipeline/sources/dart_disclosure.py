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
붙여 그대로 낸다. 정규화·dedup·정정 판정·정체성 병합은 후속 canonical 소관이다.

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


def _as_page_number(value: object) -> int | None:
    """페이지 번호로 읽히면 양의 int, 아니면 None.

    `int(value)` 로 강제하지 않는다 — `True`·`1.9`·`0`·`-3` 이 전부 통과값이 돼, 이 값을 쓰는
    쪽(페이지 에코 대조·순회 상한)이 malformed 응답을 정상으로 판정한다(각도 H —
    coerce-to-passing). 숫자 문자열은 받는다: 벤더가 타입을 바꿔도 **의미가 같으면** 가드가
    살아 있어야 하고, 타입만으로 가드를 끄면 그때부터 조용히 통과한다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number >= 1 else None
    return None


def _window_segments(
    from_date: str | None, to_date: str | None
) -> list[tuple[str | None, str | None]]:
    """수집 창을 소스가 받아주는 길이(WINDOW_CHUNK_DAYS)로 자른다.

    한쪽이라도 없으면 자르지 않는다 — 길이를 모르는 창을 임의로 좁히면 소스 기본 동작
    (당일)을 조용히 바꾸게 된다. 그 경우는 소스가 자기 규칙대로 처리하게 그대로 넘긴다.
    """
    if not from_date or not to_date:
        return [(from_date, to_date)]
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if start > end:
        # 뒤집힌 창은 여기서 고치지 않는다 — 그대로 넘겨 소스가 거절하게 둔다(조용한 정정 금지).
        return [(from_date, to_date)]
    segments: list[tuple[str | None, str | None]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=WINDOW_CHUNK_DAYS - 1), end)
        segments.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + timedelta(days=1)
    return segments


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
        for seg_from, seg_to in _window_segments(from_date, to_date):
            yield from self._scan_window(
                allowed, _to_dart_date(seg_from), _to_dart_date(seg_to), fetched_at
            )

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
        # 목록이 자라는 동안 페이지 경계가 밀려 **같은 행**이 두 페이지에 걸쳐 나올 수 있다 —
        # 창 안에서 그 반복만 접는다. 접지 않으면 한 런이 같은 문서를 두 번 내려받고 raw 에도
        # 같은 행이 두 번 앉는다(canonical dedup 이 있어도 대역폭은 이미 쓴 뒤다).
        #
        # ⚠️ raw 의 "전부 보존·dedup 없음" 계약과의 관계(Rule 7 — 갈리면 하나를 고르고 이유를
        # 남긴다): 그 계약이 금지하는 건 **서로 다른 관측을 하나로 접는 것**이다(정체성 병합·
        # 정정 판정은 canonical 소관). 여기서 접는 건 같은 페이지네이션 패스가 페이지 이동
        # 때문에 **같은 행을 두 번 건네준 것**이라 증거가 늘지 않는다. 페이지 이동 자체가
        # 관측 대상이면 그건 이 행 복제가 아니라 위 page_no·total_page 가드와
        # collection_log 의 list_rows_seen 이 기록한다.
        # ⚠️ 창 하나가 실패 단위다 — 종목별 질의 시절의 corp 단위 예외 격리는 **의도적으로**
        # 재현하지 않는다. 격리 축이던 corp 루프가 사라졌고, 중간 페이지의 의미 오류(비-list
        # `list`·status 이상)는 그 창을 못 믿는다는 뜻이지 한 대상의 문제가 아니다. 예외는
        # 그대로 스텝까지 올라가 status=error·exit 1 로 드러나고, 그 전에 yield 된 행은 raw 에
        # 남는다(부분 수집분 보존 + 실패 명시 = Rule 12). 여기서 삼키고 계속하면 "몇 페이지가
        # 통째로 빠진 성공 런"이 된다.
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            payload = self._list(bgn_de, end_de, page)
            status = str(payload.get("status") or "?")
            if status == "013":
                # 조회 데이터 없음 = 정상 빈 창(그 기간에 공시 없음) — 뉴스형.
                #
                # ⚠️ **1페이지에서만 정상이다.** 2페이지 이후의 013 은 "이 창은 비었다"가 아니다:
                # 1페이지가 이미 total_page>1 로 여러 페이지의 실재를 말했으므로, 뒤늦은 013 은
                # 캐시 불일치·목록 변동이라는 뜻이고 남은 페이지는 안 읽힌 것이다. 같이 묶어
                # 정상 종료로 두면 그 유실이 success 로 남는다(Rule 12).
                if page > 1:
                    self._note_failure(
                        None, None,
                        f"page={page} 에서 status=013 — 1페이지는 다중 페이지를 신고했다"
                        " (목록 변동·캐시 불일치, 뒷페이지 미수집)", page=page,
                    )
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
                raw_count = payload.get("total_count")
                self.list_total_count = raw_count if isinstance(raw_count, int) else None
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
                # 유니버스 필터가 먼저다 — 시장 전체 목록에는 우리가 수집하지 않는 회사 행이
                # 하루 수백 건 섞여 있다. 필드 게이트를 앞에 두면 **남의 회사 행의 결함**이
                # 우리 런의 failed_records 로 올라가 원장이 없는 결측을 세게 된다.
                raw_stock_code = row.get("stock_code")
                if not isinstance(raw_stock_code, str):
                    # 문자열이 아니면(숫자·null·키 자체 부재) **누구 것인지 판정할 수 없다** —
                    # 우리 종목일 수도 있다. 유니버스 밖으로 접어 버리면 그 유실이 영영 안
                    # 보인다(Rule 12). 판정 불가는 유니버스 밖과 다르므로 따로 드러낸다.
                    #
                    # ⚠️ 빈 문자열(`""`·`" "`)과 혼동하지 않는다. 그건 벤더가 **명시적으로**
                    # "단축코드 없음"이라 답한 것(비상장·펀드 신고자, 하루 수백 건)이고,
                    # 결측·null 은 응답이 깨졌다는 뜻이다. 둘을 묶으면 후자가 전자의 정상
                    # 대량 경로에 섞여 영영 안 보인다.
                    self._note_failure(
                        None, None,
                        f"stock_code 가 문자열이 아님: {type(raw_stock_code).__name__}"
                        " — 유니버스 판정 불가",
                        page=page,
                    )
                    continue
                # 빈 문자열은 정상이다 — 비상장·펀드 신고자는 단축코드가 없다(하루 수백 건).
                stock_code = raw_stock_code.strip()
                our_ticker = allowed.get(stock_code)
                if our_ticker is None:
                    continue
                # 필드 타입도 행 단위로 격리한다 — 비객체 행만 거르고 필드는 안 보면 비문자열
                # report_nm/rcept_no 가 .strip() 에서 터져 창 전체를 죽인다(각도 H —
                # crash-before-gate). OpenDART 는 문자열로 주지만 malformed 응답에 방어한다.
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
                rcept_no = rcept_no.strip()
                # dedup 키는 rcept_no 가 아니라 **행 내용 전체**다. rcept_no 만 보면 같은 문서의
                # 서로 다른 관측(예: `rm` 이 ""→"정" 으로 바뀐 정정 표시)까지 접어, 두 번째
                # 관측이 raw 에 닿기 전에 사라진다 — 그건 페이지 이동 중복이 아니라 실제 변화다.
                # 내용이 완전히 같을 때만 접으므로 증거는 하나도 잃지 않고, 접히는 건 페이지
                # 경계 이동이 같은 행을 두 번 건넨 경우뿐이다.
                fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                record = dict(row)
                record["our_ticker"] = our_ticker
                record["market"] = "KR"
                # ⚠️ `stock_code` 는 **손대지 않는다** — `dict(row)` 가 벤더 원본을 그대로
                # 담고 있다(bronze 무변형). 매칭용으로 strip 한 값을 되쓰면 raw 에서 벤더 이상
                # (패딩 등)을 재현할 수 없고, 우리 축은 our_ticker 가 이미 담는다. 여기 폴백을
                # 두지 않는 것도 같은 이유다: 여기 도달한 행은 위 유니버스 필터를 통과했으므로
                # 그 키를 반드시 갖고 있어, 폴백은 도달 불가한 채 "결측을 메운다"고 오독된다.
                record["source_url"] = VIEWER_URL.format(rcept_no=rcept_no)
                record["fetched_at"] = fetched_at
                yield record
            # 응답이 정말 **요청한 페이지**였는지 확인한다. 캐시·벤더 이상으로 page 2~N 요청에
            # 계속 1페이지가 돌아오면, 위 `seen` dedup 이 반복 행을 조용히 걷어내고 루프는
            # total_page 까지 정상 완주해 **뒷페이지 전량이 빠진 success 런**이 된다 — dedup 이
            # 그 증상을 지우기 때문에 이 확인이 없으면 사후에도 복원되지 않는다.
            # ⚠️ 타입으로도 결측으로도 가드를 끄지 않는다. `isinstance(int)` 일 때만 대조하면
            # 벤더가 `"1"` 로 주기 시작하는 순간, 결측을 통과시키면 필드가 빠지는 순간 이 가드가
            # **조용히 죽는다** — 지키려던 유실이 그대로 통과한다. page_no 는 응답 계약에 있는
            # 필드이므로(실측 2026-08-03), 없다는 건 응답을 못 믿는다는 뜻이다.
            # 검사는 행 처리 **뒤**다 — 아래 total_page 가드와 같은 자리다. 받은 행은 버리지
            # 않고(bronze: 받은 건 보존), 응답이 말이 안 되기 시작한 지점에서 멈춰 기록한다.
            echoed = payload.get("page_no")
            if _as_page_number(echoed) != page:
                self._note_failure(
                    None, None,
                    f"응답 page_no={echoed!r} 인데 요청은 page={page} — 목록 절단 가능", page=page,
                )
                return
            # total_page 순회 — 마지막 페이지면 종료. 몇 페이지인지 모르는 채 조용히 1페이지에서
            # 멈추면 목록 절단이 은폐되므로(Rule 12), 읽히지 않으면 감사 기록 후 종료한다.
            #
            # ⚠️ **결측(None)도 통과시키지 않는다.** 종전엔 "없으면 단일 페이지로 본다"였고 그건
            # 상한이 corp 당이던 시절엔 대체로 맞았다 — 한 회사의 한 창은 실제로 1페이지다.
            # 창 전체가 한 순회인 지금은 다르다: total_count=1800 인 응답에서 total_page 만
            # 빠져도 100행만 읽고 **1,700행을 실패 기록 없이 버린다**. 실측상 OpenDART 는 항상
            # 준다(2026-08-03) — 없다는 건 응답을 못 믿는다는 뜻이지 1페이지라는 뜻이 아니다.
            raw_total = payload.get("total_page")
            total_page = _as_page_number(raw_total)
            if total_page is None:
                self._note_failure(
                    None, None,
                    f"total_page 를 페이지 수로 읽을 수 없음: {raw_total!r} — 목록 절단 가능",
                    page=page,
                )
                return
            # 공유 파서는 "페이지 번호로 읽히는가"만 본다 — 두 필드의 의미까지는 못 지킨다.
            # page=2 응답이 total_page=1 을 주면 그 값은 파서엔 유효하지만 **모순**이고,
            # 아래 `page >= total_page` 가 참이 돼 조용한 정상 종료가 된다. 총 페이지 수는
            # 최소한 지금 읽고 있는 페이지만큼은 돼야 한다.
            if total_page < page:
                self._note_failure(
                    None, None,
                    f"total_page={total_page} 가 현재 page={page} 보다 작다 — 목록 변동·응답 모순",
                    page=page,
                )
                return
            # 빈 페이지는 **위치와 무관하게** 이상이다. 실측(2026-08-03)상
            # `total_page = ceil(total_count / page_count)` 이므로(page_count 100·50·7 에서
            # 각각 11·22·153, 마지막 페이지 행수 69·19·5) 마지막 페이지도 항상 1행 이상이고,
            # total_count=0 은 애초에 status=013 으로 온다. status=000 인데 0행이면 그 페이지가
            # 통째로 빠진 것이다 — 앞의 가드들은 **응답의 형식**(page_no·total_page)만 보므로
            # 형식이 온전한 빈 페이지를 전부 통과시킨다.
            #
            # ⚠️ 처음엔 "마지막 페이지가 비는 건 정상"이라는 예외를 뒀는데 위 산식이 그걸
            # 반증했다. 같은 무행 응답이 013 이면 실패로 세면서 000 이면 성공으로 두는 것도
            # 가드끼리의 모순이었다.
            if page < total_page and len(rows) != self.page_count:
                # 비최종 페이지는 **정확히 page_count 행**이다 — total_page 가
                # ceil(total_count/page_count) 이므로 마지막을 뺀 모든 페이지가 가득 찬다
                # (실측 2026-08-03: 11페이지 창에서 1·2·5·10 페이지 전부 100행, 11페이지만 69).
                # 0행만 보면 "100 중 1행"처럼 **일부만 빠진 페이지**가 그대로 통과한다.
                self._note_failure(
                    None, None,
                    f"page={page}/{total_page} 가 {len(rows)}행 — 비최종 페이지는"
                    f" {self.page_count}행이어야 한다(페이지 일부 유실)",
                    page=page,
                )
                return
            if page >= total_page:
                if not rows:
                    # 마지막 페이지도 비지 않는다 — total_count=0 인 창은 애초에 013 으로 온다.
                    self._note_failure(
                        None, None,
                        f"page={page}/{total_page} 가 status=000 인데 0행 — 페이지 유실",
                        page=page,
                    )
                return
        # ⚠️ 관용 kind 를 붙이지 않는다 — 진짜 실패로 드러낸다(status=partial·exit 1).
        # ALPHA-351 이 절단을 관용한 근거는 "다음 증분 창이 이어받는다" 였고, 그건 상한이 corp
        # 당 10 페이지이던 시절 이야기다. 축이 창 전체로 바뀐 지금 500 페이지 도달은 ~5만 행이
        # 안 읽혔다는 뜻이고, 운영자가 지정한 백필 창(`--from/--to`)은 **이어받을 다음 창이
        # 없다**. 평상시 증분 창은 ~18 페이지라 이 경로를 밟지 않는다 — 밟았다면 창을 좁히라는
        # 실행 가능한 실패다.
        self._note_failure(
            None,
            None,
            f"MAX_PAGES({self.max_pages}) 도달 — 목록 절단(창을 좁혀 재실행)",
        )

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
