"""OpenDART 국내 공시(disclosure filing) 소스 어댑터 (disclosures raw).

엔드포인트(재무 fnlttSinglAcnt 와 **다른 API**):
    {base_url}/corpCode.xml?crtfc_key=...                     # 종목코드→corp_code 매핑
    {base_url}/list.json?crtfc_key=&corp_code=&bgn_de=&end_de=&page_no=&page_count=  # 공시목록
    {base_url}/document.xml?crtfc_key=&rcept_no=              # 공시서류 원본(ZIP)

corpCode.xml 로 corp_code 를 확보한 뒤, corp_code × 날짜창으로 공시목록을 페이지네이션하며
report_nm 부분일치로 대상 유형(공급계약·사업보고서 등)만 낸다(공시목록은 전 유형을 준다).
공시서류 원본 본문(document.xml)은 step 이 rcept_no 별로 fetch_document 로 받아 별도 객체에
무변형 저장한다 — 이 어댑터의 fetch() 는 메타 행만 낸다.

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
from datetime import datetime, timezone
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
        self._corp_map: dict[str, dict[str, str]] | None = None

    @property
    def enabled(self) -> bool:
        return self.config_enabled and bool(self.api_key)

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, stock_code)]. KR 단축코드는 **항등 매핑**이 기본이고
        (KRX 코드가 곧 corpCode.xml 의 stock_code), symbol_map 은 항등이 아닌 예외의 오버라이드
        축으로만 남는다(투자자 수급 plan 과 동형, ALPHA-477). KRX 코드 형태가 아닌 미매핑 심볼
        (US 등)은 제외 — OpenDART 는 국내 전용이다.

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
        stock_code: str,
        our_ticker: str,
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
        """corp_code × 날짜창으로 공시목록을 페이지네이션해 대상 유형 메타 행을 낸다."""
        self.fetch_failures = []
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)
        if not plan:
            return
        corp_map = self.load_corp_map()
        fetched_at = datetime.now(timezone.utc).isoformat()
        bgn_de, end_de = _to_dart_date(from_date), _to_dart_date(to_date)
        for our_ticker, stock_code in plan:
            corp = corp_map.get(stock_code)
            if not corp:
                # corpCode.xml 에 없는 종목(비상장 편입·해외 등)은 재시도로 낫지 않는 미매핑이라
                # 진짜 실패(네트워크·쿼터)와 **exit code 상으로만** 가른다 — 이걸로 raw 페이즈를
                # 막으면 다음 런도 같은 이유로 막힌다(ADR-0030 과 같은 축, ALPHA-477).
                #
                # ⚠️ 원장에서까지 빼지는 않는다: 해소 못 한 회사의 공시는 실제로 수집이 빠진
                # 것이라 failed_targets·ops.failed_records 에 남고, 그 결과 data_status 는
                # INCOMPLETE 가 된다(`ops/wrapper.py`). 그게 사실이다 — 커버리지 구멍을 VALID 로
                # 위장하지 않는다(Rule 12). 이 수가 상시 0 이 아니면 줄일 곳은 여기가 아니라
                # corp_code enrichment(ALPHA-491) 쪽이다.
                self._note_failure(stock_code, our_ticker, "corpCode.xml 에 corp_code 없음",
                                   kind="unmapped")
                continue
            corp_code = corp["corp_code"]
            try:
                yield from self._paginate(
                    our_ticker, stock_code, corp_code, bgn_de, end_de, fetched_at
                )
            except StopFetch:
                raise
            except Exception as exc:
                self._note_failure(stock_code, our_ticker, str(exc))
                continue

    def _is_target(self, report_nm: str) -> bool:
        """report_nm(문자열)이 대상 유형인지 — strip 후 부분일치(ㆍ·패딩·[기재정정] 접두 안전)."""
        return any(f in report_nm.strip() for f in self.report_name_filters)

    def _paginate(
        self,
        our_ticker: str,
        stock_code: str,
        corp_code: str,
        bgn_de: str | None,
        end_de: str | None,
        fetched_at: str,
    ) -> Iterator[dict]:
        for page in range(1, self.max_pages + 1):
            payload = self._list(corp_code, bgn_de, end_de, page)
            status = str(payload.get("status") or "?")
            if status == "013":
                # 조회 데이터 없음 = 정상 빈 창(그 corp·기간에 공시 없음) — 뉴스형.
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
            for row in rows:
                if not isinstance(row, dict):
                    self._note_failure(
                        stock_code, our_ticker,
                        f"malformed row: {type(row).__name__}", page=page,
                    )
                    continue
                # 필드 타입도 행 단위로 격리한다 — 비객체 행만 거르고 필드는 안 보면 비문자열
                # report_nm/rcept_no 가 .strip() 에서 터져 이 corp 전체를 죽인다(각도 H —
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
                record = dict(row)
                record["our_ticker"] = our_ticker
                record["market"] = "KR"
                record.setdefault("stock_code", stock_code)
                record["source_url"] = VIEWER_URL.format(rcept_no=rcept_no)
                record["fetched_at"] = fetched_at
                yield record
            # total_page 순회 — 마지막 페이지면 종료. total_page 가 present 인데 파싱 불가면
            # 몇 페이지인지 몰라 조용히 1페이지에서 멈추면 목록 절단이 은폐된다(Rule 12) —
            # 감사 기록 후 종료한다. 아예 없으면(None) 단일 페이지로 본다.
            raw_total = payload.get("total_page")
            try:
                total_page = max(1, int(raw_total)) if raw_total is not None else 1
            except (TypeError, ValueError):
                self._note_failure(
                    stock_code, our_ticker,
                    f"total_page 파싱 불가: {raw_total!r} — 목록 절단 가능", page=page,
                )
                return
            if page >= total_page:
                return
        self._note_failure(
            stock_code,
            our_ticker,
            f"MAX_PAGES({self.max_pages}) 도달 — 목록 절단 가능(창 좁혀 재실행)",
            kind="truncation",
        )

    def _list(
        self, corp_code: str, bgn_de: str | None, end_de: str | None, page: int
    ) -> dict:
        params = {
            "crtfc_key": self.api_key or "",
            "corp_code": corp_code,
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

        공시 수집(fetch)의 ticker→corp_code 해소에 쓰지만, corp_code enrichment 스텝
        (ALPHA-491)도 같은 인덱스를 재사용한다 — 그래서 public 이다."""
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
