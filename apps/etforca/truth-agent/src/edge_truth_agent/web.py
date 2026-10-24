"""찾기 창구 ②: Exa 웹 검색·원문 가져오기 (포트폴리오 워커와 같은 Exa 키·제공자).

가져온 원문은 세션 폴더에 보관해 인용 출처를 등록할 때 근거로 남긴다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .cases import host_kind, host_of

API = "https://api.exa.ai"

# 공식·공시 도메인은 판정 검사와 같은 목록(cases.host_kind)을 쓴다. 여기서는 언론 도메인만 더한다.
NEWS_DOMAINS = ("yna.co.kr", "donga.com", "chosun.com", "joongang.co.kr", "hani.co.kr", "khan.co.kr", "hankyung.com", "mk.co.kr",
                "sedaily.com", "edaily.co.kr", "ytn.co.kr", "kbs.co.kr", "imbc.com", "sbs.co.kr", "newsis.com", "news1.kr",
                "bloter.net", "thebell.co.kr", "bizwatch.co.kr", "fnnews.com", "mt.co.kr", "asiae.co.kr", "heraldcorp.com",
                "newspim.com", "ajunews.com", "news.nate.com", "v.daum.net", "n.news.naver.com", "jtbc.co.kr", "mbn.co.kr")


class WebError(RuntimeError):
    """웹 도구 호출 실패 (에이전트에게 그대로 보여준다)."""


def kind_of(url: str) -> str:
    """URL 도메인으로 출처 종류를 정한다: disclosure · official · news · web."""
    if kind := host_kind(url):
        return kind
    host = host_of(url)
    if any(host == d or host.endswith("." + d) for d in NEWS_DOMAINS):
        return "news"
    return "web"


class Exa:
    """Exa REST 클라이언트. 키가 없으면 만들지 않는다(웹 도구를 끈다)."""

    def __init__(self, key: str, timeout: float = 45.0):
        self.key = key
        self.timeout = timeout

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(API + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"x-api-key": self.key, "Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return json.load(res)
        except urllib.error.HTTPError as error:
            raise WebError(f"웹 검색 서비스가 {error.code} 로 거절했습니다. 잠시 뒤 다시 하거나 내부 뉴스 검색을 쓰세요.") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise WebError(f"웹 검색 서비스에 연결하지 못했습니다 ({error}). 내부 뉴스 검색을 쓰세요.") from error

    def search(self, query: str, limit: int = 6) -> list[dict]:
        """검색 결과: url·title·publishedDate·text(앞부분)."""
        data = self._post("/search", {"query": query, "numResults": limit, "type": "auto",
                                      "contents": {"text": {"maxCharacters": 700}}})
        return data.get("results", [])

    def fetch(self, url: str, max_chars: int = 15000) -> dict:
        """원문 한 건. 실패하면 WebError."""
        data = self._post("/contents", {"urls": [url], "text": {"maxCharacters": max_chars}})
        statuses = {s.get("id"): s.get("status") for s in data.get("statuses", [])}
        results = data.get("results") or []
        if not results or statuses.get(url, "success") != "success" or not results[0].get("text"):
            raise WebError(f"원문을 가져오지 못했습니다: {url}. 주소를 확인하거나 다른 출처를 쓰세요.")
        return results[0]
