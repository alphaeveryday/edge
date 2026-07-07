"""OpenDART 국내 재무제표 소스 어댑터 (financial_statements raw).

엔드포인트:
    {base_url}/corpCode.xml?crtfc_key=...
    {base_url}/fnlttSinglAcnt.json?crtfc_key=&corp_code=&bsns_year=&reprt_code=

FMP 재무 어댑터와 같은 관례 인터페이스(enabled·plan·fetch·fetch_failures·
planned_symbols)를 따른다. corpCode.xml 로 종목코드(stock_code)→corp_code 를 확보한 뒤,
종목 × 사업연도 × 보고서유형으로 주요계정(list[])을 조회한다.

raw 존에는 DART list 행 원본에 수집 provenance(our_ticker·market·stock_code·corp_code·
bsns_year·reprt_code·fetched_at)만 붙여 그대로 낸다. 정규화·dedup·point-in-time 판정은
후속 canonical 소관이다.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.parse
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from ..config import DartFinancialSource as DartFinancialSourceConfig
from .http import PoliteClient, StopFetch

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

CORP_CODE_PATH = "/corpCode.xml"
FINANCIAL_PATH = "/fnlttSinglAcnt.json"

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
}

# 키·IP·쿼터·점검은 특정 종목 문제가 아니라 소스 전체 문제라 즉시 중단한다.
STOP_STATUS_CODES = {"010", "011", "012", "020", "800"}


class DartFinancialSource:
    source_name = "dart"

    def __init__(self, config: DartFinancialSourceConfig, client: PoliteClient):
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.config_enabled = config.enabled
        self.symbol_map = config.symbol_map
        self.years = list(config.years)
        self.reprt_codes = list(config.reprt_codes)
        self.client = client
        self.fetch_failures: list[dict] = []
        self.planned_symbols: int | None = None
        self._corp_map: dict[str, dict[str, str]] | None = None

    @property
    def enabled(self) -> bool:
        return self.config_enabled and bool(self.api_key)

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, stock_code)]. 매핑 없는 심볼은 DART 로는 제외."""
        out: list[tuple[str, str]] = []
        for our_ticker in symbols:
            stock_code = self.symbol_map.get(our_ticker)
            if not stock_code:
                logger.info("dart 재무 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, stock_code))
        return out

    def _target_years(self) -> list[str]:
        if self.years:
            return self.years
        year = datetime.now(KST).year
        return [str(year), str(year - 1)]

    def _note_failure(
        self,
        stock_code: str,
        our_ticker: str,
        reason: str,
        *,
        bsns_year: str | None = None,
        reprt_code: str | None = None,
    ) -> None:
        logger.warning(
            "dart 재무 대상 건너뜀: %s year=%s reprt=%s (%s)",
            stock_code, bsns_year, reprt_code, reason,
        )
        self.fetch_failures.append(
            {
                "symbol": stock_code,
                "our_ticker": our_ticker,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "error": reason,
            }
        )

    def fetch(self, symbols: list[str]) -> Iterator[dict]:
        """심볼 × 사업연도 × 보고서유형으로 DART 주요계정 list[] 행을 낸다."""
        self.fetch_failures = []
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)
        if not plan:
            return
        corp_map = self._load_corp_map()
        fetched_at = datetime.now(timezone.utc).isoformat()
        for our_ticker, stock_code in plan:
            corp = corp_map.get(stock_code)
            if not corp:
                self._note_failure(stock_code, our_ticker, "corpCode.xml 에 corp_code 없음")
                continue
            corp_code = corp["corp_code"]
            corp_name = corp.get("corp_name")
            for bsns_year in self._target_years():
                for reprt_code in self.reprt_codes:
                    try:
                        yield from self._fetch_report(
                            our_ticker,
                            stock_code,
                            corp_code,
                            corp_name,
                            bsns_year,
                            reprt_code,
                            fetched_at,
                        )
                    except StopFetch:
                        raise
                    except Exception as exc:
                        self._note_failure(
                            stock_code,
                            our_ticker,
                            str(exc),
                            bsns_year=bsns_year,
                            reprt_code=reprt_code,
                        )
                        continue

    def _load_corp_map(self) -> dict[str, dict[str, str]]:
        """corpCode.xml ZIP → {stock_code: {corp_code, corp_name}}. 런 내 메모리 캐시."""
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

    def _fetch_report(
        self,
        our_ticker: str,
        stock_code: str,
        corp_code: str,
        corp_name: str | None,
        bsns_year: str,
        reprt_code: str,
        fetched_at: str,
    ) -> Iterator[dict]:
        query = urllib.parse.urlencode(
            {
                "crtfc_key": self.api_key or "",
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            }
        )
        body = self.client.request(
            "GET",
            f"{self.base_url}{FINANCIAL_PATH}?{query}",
            headers={"Accept": "application/json"},
            decode=True,
        )
        if not isinstance(body, str):
            raise ValueError("DART 재무 응답이 str 이 아님")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"json: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"DART 재무 응답이 객체가 아님: {type(payload).__name__}")
        status = str(payload.get("status") or "?")
        if status == "013":
            return
        if status in STOP_STATUS_CODES:
            msg = payload.get("message") or STATUS_MESSAGES.get(status, "?")
            raise StopFetch(f"DART status={status} ({STATUS_MESSAGES.get(status, '?')}) msg={msg}")
        if status != "000":
            msg = payload.get("message") or STATUS_MESSAGES.get(status, "?")
            raise ValueError(f"DART status={status} ({STATUS_MESSAGES.get(status, '?')}) msg={msg}")
        rows = payload.get("list")
        if not isinstance(rows, list):
            raise ValueError(f"DART status=000 인데 list 이상: {type(rows).__name__}")
        for row in rows:
            if not isinstance(row, dict):
                self._note_failure(
                    stock_code,
                    our_ticker,
                    f"malformed row: {type(row).__name__}",
                    bsns_year=bsns_year,
                    reprt_code=reprt_code,
                )
                continue
            record = dict(row)
            record["our_ticker"] = our_ticker
            record["market"] = "KR"
            record.setdefault("stock_code", stock_code)
            record.setdefault("corp_code", corp_code)
            if corp_name:
                record.setdefault("corp_name", corp_name)
            record.setdefault("bsns_year", bsns_year)
            record.setdefault("reprt_code", reprt_code)
            record["fetched_at"] = fetched_at
            yield record
