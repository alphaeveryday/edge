"""OpenDART 로 공시 원문을 받는다 (ALPHA-1457).

KIND 공시 뷰어(/common/disclsviewer.do)는 AWS 출구 IP 를 Akamai 에서 막는다(2026-10-25 ECS 실측: 목록은 되고
뷰어만 'Access Denied'). 같은 공시가 DART 에도 올라오지만 접수번호가 다르다(삼성물산 대량보유: KIND 20261023000989 ·
DART 20261023000484). 그래서 회사·날짜로 DART 공시목록을 받아 제목·제출인이 맞는 건의 원문(document.xml)을 읽는다.
거래소 시장조치(시장감시위원회·시장본부)는 DART 에 없다.
"""
from __future__ import annotations

import http.client
import io
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from .kind import _text

API = "https://opendart.fss.or.kr/api"


class DartError(RuntimeError):
    """DART 에서 원문을 받지 못했다(사유 포함, 인증키는 담지 않는다)."""


def _norm(text: str) -> str:
    """제목 비교용: 글자·숫자만. KIND '투자판단 관련 주요경영사항(임상시험 결과)' ≈ DART '투자판단관련주요경영사항(임상시험결과)'."""
    return re.sub(r"[\W_]+", "", text or "")


def _unzip(raw: bytes, what: str) -> str:
    """DART ZIP 응답의 첫 파일 글자. ZIP 이 아니면(한도 초과·키 오류 등은 XML 로 온다) DartError."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return z.read(z.namelist()[0]).decode("utf-8", "replace")
    except (zipfile.BadZipFile, IndexError):
        message = re.search(r"<message>(.*?)</message>", raw[:500].decode("utf-8", "replace"))
        raise DartError(f"DART {what} 오류: {message.group(1)[:80] if message else '알 수 없는 응답'}") from None


def parse_corp_codes(xml: str) -> dict[str, str]:
    """corpCode.xml → {종목코드: 고유번호}. 항목(<list>)마다 따로 읽는다 — 비상장사는 종목코드가 빈칸이라, 항목을 넘나드는
    한 번의 정규식은 상장사 종목코드를 바로 앞 비상장사 고유번호에 붙인다(다른 회사의 공시를 읽게 된다)."""
    table = {}
    for block in re.findall(r"<list>(.*?)</list>", xml, re.S):
        corp, stock = re.search(r"<corp_code>(\d+)</corp_code>", block), re.search(r"<stock_code>\s*(\w{6})\s*</stock_code>", block)
        if corp and stock:
            table[stock.group(1)] = corp.group(1)
    return table


def match(items: list[dict], title: str, submitter: str) -> str | None:
    """DART 목록에서 KIND 공시와 같은 건의 접수번호. DART 제목은 잘려 올 수 있어 앞부분이 맞으면 같다고 본다.
    같은 날 같은 제목이 여럿이면 제출인으로 가르고, 그래도 여럿이면 고르지 않는다(엉뚱한 원문을 읽지 않게)."""
    want = _norm(title)
    same = [i for i in items if (got := _norm(i.get("report_nm", ""))) and (want.startswith(got) or got.startswith(want))]
    if len(same) > 1:
        same = [i for i in same if _norm(i.get("flr_nm", "")) == _norm(submitter)]
    return same[0]["rcept_no"] if len(same) == 1 else None


class Dart:
    def __init__(self, key: str | None = None, *, timeout: float = 20.0, cache: Path | None = None):
        self.key = key or os.environ.get("DART_API_KEY", "")
        if not self.key:
            raise DartError("DART 인증키(DART_API_KEY)가 없습니다")
        self.timeout = timeout
        self.cache = cache or Path(tempfile.gettempdir()) / "truth-dart-corp.json"

    def _get(self, path: str, **params) -> bytes:
        url = f"{API}/{path}?" + urllib.parse.urlencode({"crtfc_key": self.key, **params})
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as res:
                return res.read()
        except urllib.error.HTTPError as error:
            raise DartError(f"DART HTTP {error.code}") from None  # 예외 문자열에 주소(인증키)가 실리지 않게
        except (OSError, http.client.HTTPException) as error:  # URLError·시간 초과·연결 끊김·응답 잘림
            raise DartError(f"DART 연결 실패: {type(error).__name__}") from None

    def corp_code(self, stock_code: str) -> str | None:
        """종목코드 → DART 고유번호. 전체 표(corpCode.xml)는 한 번 받아 임시 파일에 둔다(도구는 호출마다 새 프로세스다)."""
        try:
            table = json.loads(self.cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            table = parse_corp_codes(_unzip(self._get("corpCode.xml"), "고유번호 표"))
            if not table:
                raise DartError("DART 고유번호 표가 비었습니다") from None
            tmp = self.cache.with_name(f"{self.cache.name}.{os.getpid()}")  # 동시 세션이 반쯤 쓴 파일을 읽지 않게
            tmp.write_text(json.dumps(table), encoding="utf-8")
            os.replace(tmp, self.cache)
        return table.get(stock_code)

    def document(self, stock_code: str, acptno: str, title: str, submitter: str, max_chars: int = 15000) -> str:
        """KIND 공시(접수번호·제목·제출인)와 같은 DART 공시의 본문 글자(앞 max_chars 자)."""
        corp = self.corp_code(stock_code)
        if not corp:
            raise DartError(f"DART 에 종목 {stock_code} 이 없습니다")
        day = acptno[:8]
        try:
            listing = json.loads(self._get("list.json", corp_code=corp, bgn_de=day, end_de=day, page_count="100"))
        except ValueError:
            raise DartError("DART 목록 응답을 읽지 못했습니다") from None
        if listing.get("status") not in ("000", "013"):  # 013 = 조회된 데이터 없음
            raise DartError(f"DART 목록 오류 {listing.get('status')}: {listing.get('message', '')[:80]}")
        rcept = match(listing.get("list") or [], title, submitter)
        if not rcept:
            raise DartError(f"DART 에서 같은 공시를 찾지 못했습니다({day} '{title[:40]}')")
        raw = self._get("document.xml", rcept_no=rcept)
        text = _text(_unzip(raw, f"공시 {rcept} 원문"))
        if not text:
            raise DartError(f"DART 공시 {rcept} 본문이 비었습니다")
        return text[:max_chars]
