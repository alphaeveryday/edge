"""미국 SEC EDGAR 공시 — 키 없이 티커로 공시 목록과 원문을 받는다. 국내 KIND(kind.py)와 같은 모양을 돌려준다.

- 티커 → CIK: www.sec.gov/files/company_tickers.json (실행마다 한 번)
- 목록: data.sec.gov/submissions/CIK##########.json 의 최근 공시(서식·접수 시각·주 문서)
- 원문: Archives/edgar/data/<CIK>/<접수번호>/<주 문서>. 8-K·6-K 는 본문이 짧고 보도자료가 첨부(EX-99)에 있어 첫 첨부를 함께 읽는다.
SEC 는 연락처(이메일)가 든 User-Agent 를 요구한다(없으면 www.sec.gov 가 403). TRUTH_SEC_CONTACT 로 넣는다. 초당 10회 제한.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .kind import _text
from .markets import US

TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"
LIST_PAGE = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&owner=include&count=40"
# 8-K 항목 → 무슨 일인지(모델이 목록만 보고 읽을 공시를 고르게)
ITEMS = {"1.01": "중요 계약 체결", "1.02": "중요 계약 해지", "1.05": "사이버 보안 사고", "2.01": "자산 인수·처분 완료",
         "2.02": "실적", "2.03": "채무 발생", "3.01": "상장 기준 미달·상장폐지 통지", "3.02": "미등록 주식 발행",
         "3.03": "주주 권리 변경", "5.01": "경영권 변경", "5.02": "임원·이사 변동", "5.03": "정관 변경(주식 병합 등)",
         "5.07": "주주총회 결과", "7.01": "공정공시(보도자료)", "8.01": "기타 주요 사건"}
_EXHIBIT = re.compile(r"ex[-_]?99|exhibit[-_]?99", re.I)


class SecError(RuntimeError):
    """SEC 호출 실패(사유 포함)."""


class Sec:
    def __init__(self, *, contact: str | None = None, timeout: float = 20.0):
        self.contact = contact if contact is not None else os.environ.get("TRUTH_SEC_CONTACT", "")
        self.timeout = timeout
        self._ciks: dict[str, int] | None = None

    def _open(self, url: str) -> bytes:
        if not self.contact:
            raise SecError("TRUTH_SEC_CONTACT(연락처 이메일)가 없어 SEC 에 요청할 수 없습니다.")
        headers = {"user-agent": f"etforca.com truth-research {self.contact}", "accept-encoding": "identity"}
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=self.timeout) as res:
                    return res.read()
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise SecError(f"SEC HTTP {error.code}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise SecError(f"SEC 연결 실패: {error}") from error
        raise SecError("SEC 호출 실패")  # 도달하지 않는다

    def cik(self, ticker: str) -> int:
        """티커의 CIK. SEC 는 반 클래스 주식을 BRK-B 처럼 쓴다. 목록에 없으면 실패 — '공시 없음'으로 넘기지 않는다."""
        if self._ciks is None:
            rows = json.loads(self._open(TICKERS)).values()
            self._ciks = {str(r["ticker"]).upper(): int(r["cik_str"]) for r in rows}
        found = self._ciks.get(ticker.upper().replace(".", "-"))
        if found is None:
            raise SecError(f"SEC 티커 목록에 {ticker} 가 없습니다.")
        return found

    def disclosures(self, code: str, since: str, until: str, size: int = 40) -> list[dict]:
        """종목의 공시 목록(최신순). since·until 은 YYYY-MM-DD(접수일). KIND 목록과 같은 칸: acptno(접수번호)·at(뉴욕 시각)·title·submitter·url."""
        cik = self.cik(code)
        data = json.loads(self._open(SUBMISSIONS.format(cik=cik)))
        recent = (data.get("filings") or {}).get("recent") or {}
        need = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "primaryDocument", "primaryDocDescription", "items")
        if not all(isinstance(recent.get(k), list) for k in need):  # 형식이 바뀌었으면 '공시 없음'이 아니라 실패다
            raise SecError(f"SEC {code} 공시 목록 응답을 읽지 못했습니다(형식 변경).")
        out = []
        for i, acc in enumerate(recent["accessionNumber"]):
            if not since <= recent["filingDate"][i] <= until:
                continue
            at = datetime.fromisoformat(recent["acceptanceDateTime"][i].replace("Z", "+00:00")).astimezone(US.tz)
            form, desc = recent["form"][i], (recent["primaryDocDescription"][i] or "").strip()
            items = [f"{x} {ITEMS[x]}" if x in ITEMS else x for x in str(recent["items"][i] or "").split(",") if x]
            title = " · ".join(dict.fromkeys(p for p in [form, desc if desc.upper() != form.upper() else "", *items] if p))
            doc = recent["primaryDocument"][i]
            out.append({"acptno": acc, "at": at.strftime("%Y-%m-%d %H:%M"), "title": title, "submitter": data.get("name") or code,
                        "url": ARCHIVE.format(cik=cik, folder=acc.replace("-", "")) + doc})
            if len(out) >= size:  # ponytail: 최근 40건만 — 2주에 그보다 많은 회사는 드물다(내부자 거래 Form 4 가 몰리면 넘긴다)
                break
        return out

    def document(self, url: str, max_chars: int = 15000) -> str:
        """공시 본문 글자(앞 max_chars 자). 주 문서에 첫 EX-99 첨부(보도자료)를 붙인다 — 8-K 본문은 '첨부 참조'뿐인 일이 많다."""
        if not url.startswith("https://www.sec.gov/Archives/edgar/data/"):
            raise SecError("SEC 공시 원문 주소가 아닙니다.")
        text = _text(self._open(url).decode("utf-8", "replace"))
        folder = url.rsplit("/", 1)[0] + "/"
        try:
            files = json.loads(self._open(folder + "index.json"))["directory"]["item"]
            exhibit = next((f["name"] for f in files if _EXHIBIT.search(f["name"]) and f["name"].lower().endswith((".htm", ".html", ".txt"))), None)
        except (SecError, KeyError, TypeError, json.JSONDecodeError):
            exhibit = None  # 첨부 목록은 덤이다 — 본문만으로 돌려준다
        if exhibit:
            text += "\n\n[첨부 " + exhibit + "]\n" + _text(self._open(folder + urllib.parse.quote(exhibit)).decode("utf-8", "replace"))
        if not text.strip():
            raise SecError("SEC 공시 본문이 비었습니다.")
        return text[:max_chars]

    @staticmethod
    def list_url(code: str) -> str:
        """화면 출처에 싣는 공시 목록 주소."""
        return LIST_PAGE.format(ticker=urllib.parse.quote(code))
