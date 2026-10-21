"""사건 대표 사진·영상. 모델이 주소를 지어내지 않게, 코드가 등록된 출처에서만 채운다.

- cover: 언론 출처(web.NEWS_DOMAINS) 기사 페이지의 og:image. https 주소만.
- video: 출처 주소가 YouTube 일 때 그 주소의 영상 id. oEmbed 로 공개 영상임을 확인한 것만.
사진·영상은 게시를 막지 않는다 — 못 찾으면 비워 두고 다음 실행이 다시 찾는다.
"""
from __future__ import annotations

from html.parser import HTMLParser
import http.client
import json
import logging
import re
import time
import urllib.request

from .cases import host_of
from .tools import _check_video
from .web import kind_of

log = logging.getLogger(__name__)
SOURCES_PER_CASE = 3  # 사건 하나에서 사진·영상을 찾아볼 출처 수(매시 실행마다 다시 찾으므로 작게 둔다)
RUN_SECONDS = 120     # 한 실행에서 사진·영상 찾기에 쓰는 시간 상한. 넘으면 남은 사건은 다음 실행이 찾는다
NETWORK_ERRORS = (OSError, ValueError, http.client.HTTPException)  # URLError·시간 초과·연결 끊김·불완전 응답
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; etforca-truth/1.0)"}
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
    try:  # 주소 해석 실패(ValueError)도 여기서 잡는다 — 깨진 출처 하나가 매시 실행을 멈추면 안 된다
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as res:
            if "html" not in res.headers.get("Content-Type", "").lower():
                return None
            # ponytail: timeout 은 소켓 읽기 한 번마다라, 조금씩 흘려보내는 서버는 RUN_SECONDS 를 넘겨 붙잡을 수 있다(상한은 배치 시간 제한). 생기면 조각 읽기로 바꾼다.
            raw = res.read(PAGE_BYTES)
    except NETWORK_ERRORS:
        return None
    parser = _OgImage()
    parser.feed(raw.decode("utf-8", "replace"))  # 주소는 ASCII 라 문자셋이 달라도 깨지지 않는다
    image = parser.found
    if image and image.startswith("//"):
        image = "https:" + image
    return image if image and _HTTPS_URL.fullmatch(image) else None


def image_ok(url: str) -> bool:
    """사진 주소가 실제 이미지로 응답하는가. 깨진 사진을 넣지 않아야, 모델이 비운 사진이 다음 실행에 다시 붙지 않는다."""
    try:  # og:image 가 'https://[bad' 처럼 깨져 있어도 '못 씀'일 뿐이다
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status == 200 and res.headers.get("Content-Type", "").lower().startswith("image/")
    except NETWORK_ERRORS:
        return False


def youtube_id(url: str) -> str | None:
    """YouTube 주소의 영상 id. 다른 사이트 주소에 섞인 'youtube' 글자는 보지 않는다."""
    if host_of(url) not in YOUTUBE_HOSTS:
        return None
    match = _YOUTUBE_ID.search(url)
    return match.group(1) if match else None


def video_ok(video_id: str) -> bool:
    """공개 영상인가 (기존 check 도구와 같은 oEmbed 확인). 응답을 읽다 끊겨도 실행을 멈추지 않는다."""
    try:
        return not _check_video(video_id)
    except NETWORK_ERRORS:
        return False


def fill(case: dict, deadline: float | None = None) -> bool:
    """비어 있는 cover·video 를 사건의 출처에서 채운다. 바꿨으면 True. deadline(time.monotonic) 이 지나면 더 찾지 않는다."""
    def time_left() -> bool:
        return deadline is None or time.monotonic() < deadline

    def miss(field: str, tried: int, total: int) -> None:
        # 다 찾아보고 없을 때만 '못 찾음'이다. 시간 상한으로 건너뛴 것은 실행 단위로 따로 남긴다(runner).
        if tried == total:
            log.info(json.dumps({"event": "media_miss", "case": case.get("id"), "field": field, "sources": total}, ensure_ascii=False))

    changed = False
    sources = [s for s in case.get("sources", []) if isinstance(s.get("url"), str)]
    if case.get("cover") is None:
        news = [s["url"] for s in sources if kind_of(s["url"]) == "news" and s["url"].startswith("https://")][:SOURCES_PER_CASE]
        # ponytail: 사진이 없는 사건은 매시 실행마다 다시 요청한다(RUN_SECONDS 안에서). 사건이 많아지면 '찾아봤음' 기록을 둔다.
        tried = 0
        for url in news:
            if not time_left():
                break
            tried += 1
            if (image := og_image(url)) and image_ok(image):
                case["cover"], changed = image, True
                break
        if case.get("cover") is None and news:
            miss("cover", tried, len(news))
    if case.get("video") is None:
        videos = [(vid, s) for s in sources if (vid := youtube_id(s["url"]))][:SOURCES_PER_CASE]
        tried = 0
        for vid, s in videos:
            if not time_left():
                break
            tried += 1
            if video_ok(vid):
                case["video"], changed = {"youtube": vid, "title": s.get("title") or case.get("subject") or vid}, True
                break
        if case.get("video") is None and videos:
            miss("video", tried, len(videos))
    return changed
