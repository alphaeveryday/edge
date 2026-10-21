"""OpenDART — 오늘의 급등주용 공시 목록·원문·기업개황. truth 전용 키를 쓴다.

- 종목코드 → 고유번호(corp_code) 대응표는 corpCode.xml(zip) 한 번 받아 쓴다(호출자가 저장소에 하루 단위로 캐시).
- 공시 원문 보기 주소는 dart.fss.or.kr(법정공시 도메인)이라 판정 근거 출처로 그대로 쓸 수 있다.
"""
from __future__ import annotations

import html
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

API = "https://opendart.fss.or.kr/api"
VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"


class DartError(RuntimeError):
    """DART 호출 실패."""


class Dart:
    def __init__(self, key: str, *, timeout: float = 20.0):
        self.key, self.timeout = key, timeout

    def _fetch(self, path: str, params: dict) -> bytes:
        url = f"{API}/{path}?{urllib.parse.urlencode({'crtfc_key': self.key, **params})}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as res:
                return res.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise DartError(f"{path} 연결 실패: {error}") from error

    def _json(self, path: str, params: dict) -> dict:
        data = json.loads(self._fetch(path, params).decode("utf-8"))
        status = str(data.get("status"))
        if status == "013":  # 조회된 데이터 없음
            return {"list": []}
        if status != "000":
            raise DartError(f"{path} 실패: {status} {data.get('message')}")
        return data

    def corp_codes(self) -> dict[str, str]:
        """상장 종목코드 → corp_code."""
        raw = self._fetch("corpCode.xml", {})
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                root = ET.fromstring(z.read(z.namelist()[0]))
        except zipfile.BadZipFile as error:
            raise DartError(f"corpCode.xml 이 zip 이 아닙니다: {raw[:120]!r}") from error
        out = {}
        for item in root.iter("list"):
            stock = (item.findtext("stock_code") or "").strip()
            if stock:
                out[stock] = (item.findtext("corp_code") or "").strip()
        return out

    def filings(self, corp_code: str, since_ymd: str, until_ymd: str, limit: int = 20) -> list[dict]:
        """기간 내 공시 목록(최신순): rcept_no, report_nm, rcept_dt, flr_nm, url."""
        data = self._json("list.json", {"corp_code": corp_code, "bgn_de": since_ymd, "end_de": until_ymd, "page_count": limit})
        return [{"rcept_no": r["rcept_no"], "report_nm": " ".join(str(r.get("report_nm", "")).split()),
                 "rcept_dt": f"{r['rcept_dt'][:4]}-{r['rcept_dt'][4:6]}-{r['rcept_dt'][6:8]}", "flr_nm": r.get("flr_nm", ""),
                 "url": VIEW.format(r["rcept_no"])} for r in data.get("list", [])]

    def document_text(self, rcept_no: str, limit: int = 20000) -> str:
        """공시 원문(XML 묶음)을 글자만 남겨 돌려준다."""
        raw = self._fetch("document.xml", {"rcept_no": rcept_no})
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                parts = [z.read(n).decode("utf-8", "replace") for n in sorted(z.namelist())]
        except zipfile.BadZipFile as error:
            raise DartError(f"공시 원문을 받지 못했습니다: {raw[:120]!r}") from error
        text = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(parts)))
        return " ".join(text.split())[:limit]

    def homepage(self, corp_code: str) -> str | None:
        """기업개황의 홈페이지 주소(없으면 None)."""
        url = str(self._json("company.json", {"corp_code": corp_code}).get("hm_url") or "").strip()
        if not url or url in ("-", "없음"):
            return None
        return url if url.startswith(("http://", "https://")) else "https://" + url
