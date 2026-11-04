"""한국거래소 KIND 공시 — 키 없이 종목코드로 공시·시장조치 목록, 공시 본문, 회사 홈페이지를 받는다.

- 목록: POST /disclosure/details.do (종목코드 A+6자리, 기간). 회사 공시와 시장감시위원회 조치(투자주의·경고)가 함께 나온다.
- 본문: /common/disclsviewer.do?method=search&acptno= 에서 문서번호 → searchContents → /external/…htm
  (이 뷰어는 AWS 출구 IP 를 Akamai 에서 막는다 — 운영의 회사 공시 원문은 dart.py 가 받는다, ALPHA-1457)
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
# KIND 는 클라우드(AWS)에서 오는 봇 이름 User-Agent 를 403 으로 막는다(2026-10-22 ECS 실측). 브라우저와 같은 머리글이면 받는다.
_HEADERS = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
            "accept": "text/html,application/xhtml+xml,*/*;q=0.8", "accept-language": "ko-KR,ko;q=0.9",
            "referer": "https://kind.krx.co.kr/disclosure/details.do"}
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_ACPT = re.compile(r"openDisclsViewer\('(\d{14})'")
_EMPTY = "조회된 결과값이 없습니다"
_HOME = re.compile(r"<th[^>]*>\s*홈페이지\s*</th>\s*<td[^>]*>(?:(?!</td>).)*?href=[\"'](https?://[^\"']+)", re.S)  # 그 칸 안의 링크만
MARKET_BODIES = ("시장감시위원회", "시장본부", "한국거래소")  # 시장조치(투자주의·경고 등) 제출 주체 — 회사 공시가 아니다


WARNINGS = ("투자주의", "투자경고", "투자위험", "단기과열", "공매도 과열")  # 급등주에 거의 매시 붙는 시장경보


def is_market_notice(item: dict) -> bool:
    """거래소 시장조치인가(회사가 낸 공시가 아니다)."""
    return any(b in item.get("submitter", "") for b in MARKET_BODIES)


def is_market_warning(item: dict) -> bool:
    """급등에 따라붙는 시장경보인가(투자주의·경고·위험 등). 매매정지·관리종목 같은 조치는 아니다."""
    title = item.get("title", "")
    # 매매정지 제목에도 '투자경고'가 들어간다(예: 매매거래정지(투자경고종목 지정중 주가상승)) — 정지·관리종목은 경보가 아니다
    return is_market_notice(item) and any(w in title for w in WARNINGS) and not any(x in title for x in ("정지", "관리종목", "불성실"))


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
    # 행이 없으면 '결과 없음' 표시가 있어야 '공시 없음'이다 — 점검·차단 페이지를 '공시 없음'으로 읽으면 테마로 몰게 된다
    if not out and _EMPTY not in html:
        raise KindError("KIND 공시 목록 응답을 읽지 못했습니다(형식 변경·점검 페이지).")
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

    def disclosures(self, code: str, since: str, until: str, size: int = 100) -> list[dict]:  # ponytail: 한 쪽만 받는다 — 14일에 100건 넘는 종목이 생기면 쪽 넘김
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
        found = _HOME.search(html)  # '홈페이지' 칸의 링크만 — 문서 앞의 다른 주소(DTD·스크립트)를 집지 않게
        return found.group(1) if found and "krx.co.kr" not in found.group(1) else None
