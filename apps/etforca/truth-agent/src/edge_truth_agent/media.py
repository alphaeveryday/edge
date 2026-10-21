"""사건 대표 사진·영상. 모델이 주소를 지어내지 않게, 코드가 등록된 출처에서만 채운다.

- cover: 언론 출처(web.NEWS_DOMAINS) 기사 페이지의 og:image. https 주소만.
- video: 출처 주소가 YouTube 일 때 그 주소의 영상 id. oEmbed 로 공개 영상임을 확인한 것만.
사진·영상은 게시를 막지 않는다 — 못 찾으면 비워 두고 다음 실행이 다시 찾는다.
"""
from __future__ import annotations

from html.parser import HTMLParser
import http.client
import re
import urllib.request

from .cases import host_of
from .tools import _check_video
from .web import kind_of

SOURCES_PER_CASE = 3  # 사건 하나에서 사진을 찾아볼 언론 출처 수(매시 실행마다 다시 찾으므로 작게 둔다)
PAGE_BYTES = 512_000  # og:image 는 <head> 에 있다 — 본문 전체를 받지 않는다
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_YOUTUBE_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_HTTPS_URL = re.compile(r"https://[^\s\"'<>]+")


class _OgImage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "meta" and self.found is None:
            a = dict(attrs)
            content = a.get("content")
            if (a.get("property") or a.get("name")) in ("og:image", "og:image:secure_url") and content:
                self.found = content.strip()


def og_image(url: str) -> str | None:
    """기사 페이지의 og:image(https). 없거나 가져오지 못하면 None — 예외를 올리지 않는다."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; etforca-truth/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            if "html" not in res.headers.get("Content-Type", ""):
                return None
            raw = res.read(PAGE_BYTES)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    parser = _OgImage()
    parser.feed(raw.decode("utf-8", "replace"))  # 주소는 ASCII 라 문자셋이 달라도 깨지지 않는다
    image = parser.found
    if image and image.startswith("//"):
        image = "https:" + image
    return image if image and _HTTPS_URL.fullmatch(image) else None


def youtube_id(url: str) -> str | None:
    """YouTube 주소의 영상 id. 다른 사이트 주소에 섞인 'youtube' 글자는 보지 않는다."""
    if host_of(url) not in YOUTUBE_HOSTS:
        return None
    match = _YOUTUBE_ID.search(url)
    return match.group(1) if match else None


def video_ok(video_id: str) -> bool:
    """공개 영상인가 (기존 check 도구와 같은 oEmbed 확인)."""
    return not _check_video(video_id)


def fill(case: dict) -> bool:
    """비어 있는 cover·video 를 사건의 출처에서 채운다. 바꿨으면 True."""
    changed = False
    sources = case.get("sources", [])
    if case.get("cover") is None:
        news = [s["url"] for s in sources if kind_of(s["url"]) == "news" and s["url"].startswith("https://")]
        # ponytail: 사진이 없는 사건은 매시 실행마다 최대 SOURCES_PER_CASE 번 다시 요청한다. 사건이 많아지면 '찾아봤음' 기록을 둔다.
        for url in news[:SOURCES_PER_CASE]:
            if image := og_image(url):
                case["cover"], changed = image, True
                break
    if case.get("video") is None:
        for s in sources:
            if (vid := youtube_id(s["url"])) and video_ok(vid):
                case["video"], changed = {"youtube": vid, "title": s.get("title") or case.get("subject") or vid}, True
                break
    return changed
