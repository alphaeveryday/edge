"""한국거래소 KIND 공시 — 키 없이 종목코드로 공시·시장조치 목록, 공시 본문, 회사 홈페이지를 받는다.

- 목록: POST /disclosure/details.do (종목코드 A+6자리, 기간). 회사 공시와 시장감시위원회 조치(투자주의·경고)가 함께 나온다.
- 본문: /common/disclsviewer.do?method=search&acptno= 에서 문서번호 → searchContents → /external/…htm
- 홈페이지: /common/companysummary.do (회사 개요). 로고를 받을 때만 쓴다.
"""
from __future__ import annotations

from html import unescape
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://kind.krx.co.kr"
VIEW = BASE + "/common/disclsviewer.do?method=search&acptno={}"
_HEADERS = {"user-agent": "Mozilla/5.0 (compatible; etforca-truth/1.0)"}
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_ACPT = re.compile(r"openDisclsViewer\('(\d{14})'")


class KindError(RuntimeError):
    """KIND 호출 실패(사유 포함)."""


def _text(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def parse_list(html: str) -> list[dict]:
    """목록 HTML → [{acptno, at, title, submitter, url}] (최신순). 행 형식이 바뀌면 빈 목록이 아니라 실패로 올린다."""
    out = []
    for row in _ROW.findall(html):
        cells = _CELL.findall(row)
        acpt = _ACPT.search(row)
        if not acpt or len(cells) < 5:
            continue
        out.append({"acptno": acpt.group(1), "at": _text(cells[1]), "title": _text(cells[3]), "submitter": _text(cells[4]),
                    "url": VIEW.format(acpt.group(1))})
    if not out and "openDisclsViewer" in html:
        raise KindError("KIND 공시 목록 형식이 바뀌어 읽지 못했습니다.")
    return out


class Kind:
    def __init__(self, *, timeout: float = 20.0):
        self.timeout = timeout

    def _open(self, url: str, data: dict | None = None) -> str:
        body = urllib.parse.urlencode(data).encode() if data else None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=_HEADERS), timeout=self.timeout) as res:
                    return res.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise KindError(f"KIND HTTP {error.code}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise KindError(f"KIND 연결 실패: {error}") from error
        raise KindError("KIND 호출 실패")  # 도달하지 않는다

    def disclosures(self, code: str, since: str, until: str, size: int = 30) -> list[dict]:
        """종목의 공시·시장조치 목록(최신순). since·until 은 YYYY-MM-DD."""
        html = self._open(BASE + "/disclosure/details.do", {
            "method": "searchDetailsSub", "currentPageSize": str(size), "pageIndex": "1", "orderMode": "1", "orderStat": "D",
            "forward": "details_sub", "searchCodeType": "", "repIsuSrtCd": f"A{code}", "fromDate": since, "toDate": until})
        return parse_list(html)

    def document(self, acptno: str, max_chars: int = 15000) -> str:
        """공시 본문 글자(앞 max_chars 자)."""
        viewer = self._open(VIEW.format(acptno))
        doc = re.search(r"<option value='(\d{10,})\|", viewer)
        if not doc:
            raise KindError(f"공시 {acptno} 의 문서를 찾지 못했습니다.")
        contents = self._open(f"{BASE}/common/disclsviewer.do?method=searchContents&docNo={doc.group(1)}")
        src = re.search(r"(?:https?://kind\.krx\.co\.kr)?(/external/[^'\"\s]+\.htm)", contents)
        if not src:
            raise KindError(f"공시 {acptno} 의 본문 주소를 찾지 못했습니다.")
        text = _text(self._open(BASE + src.group(1)))
        if not text:
            raise KindError(f"공시 {acptno} 본문이 비었습니다.")
        return text[:max_chars]

    def homepage(self, code: str) -> str | None:
        """회사 홈페이지 주소(개요에 적힌 것). 없으면 None."""
        html = self._open(BASE + "/common/companysummary.do",
                          {"method": "searchCompanySummaryOvrvwDetail", "menuIndex": "0", "strIsurCd": code[:5], "taskDd": ""})
        found = [u for u in re.findall(r"https?://[A-Za-z0-9./-]+", html) if "krx.co.kr" not in u]
        return found[0] if found else None
