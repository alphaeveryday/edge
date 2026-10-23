"""회사 로고 — 회사 홈페이지의 아이콘(apple-touch-icon → icon → /favicon.ico)을 받아 우리 버킷에 둔다.

보안: 우리 도메인에서 그대로 서빙되므로 PNG·JPG·ICO·WebP 만 받고 파일 앞머리(매직 바이트)로 형식을 확인한다.
SVG 는 스크립트를 담을 수 있어 받지 않는다. 크기는 MAX_BYTES 까지.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 300_000
UA = {"User-Agent": "Mozilla/5.0 (compatible; etforca-truth/1.0; +https://etforca.com/truth/)"}
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"\x00\x00\x01\x00", "ico"))
_LINK = re.compile(r"<link\b[^>]*>", re.I)


def image_kind(data: bytes) -> str | None:
    """허용 형식이면 확장자, 아니면 None."""
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _get(url: str, timeout: float, limit: int) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read(limit + 1)


def icon_candidates(page: str, base: str) -> list[str]:
    """홈페이지 HTML 의 아이콘 링크(큰 것 먼저) + /favicon.ico."""
    touch, icons = [], []
    for tag in _LINK.findall(page):
        rel = re.search(r"rel\s*=\s*[\"']?([^\"'>]+)", tag, re.I)
        href = re.search(r"href\s*=\s*[\"']?([^\"'\s>]+)", tag, re.I)
        if not rel or not href or href.group(1).lower().endswith(".svg"):
            continue
        kinds = rel.group(1).lower().split()
        url = urllib.parse.urljoin(base, href.group(1))
        if "apple-touch-icon" in kinds or "apple-touch-icon-precomposed" in kinds:
            touch.append(url)
        elif "icon" in kinds:
            icons.append(url)
    return touch + icons + [urllib.parse.urljoin(base, "/favicon.ico")]


def fetch_image(url: str, *, timeout: float = 8.0) -> tuple[str, bytes] | None:
    """이미지 주소 하나를 그대로 받는다(미국: 순위가 주는 Naver 로고 PNG). 형식·크기 검사는 홈페이지 아이콘과 같다."""
    try:
        data = _get(url, timeout, MAX_BYTES)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    kind = image_kind(data) if len(data) <= MAX_BYTES else None
    return (kind, data) if kind else None


def fetch_logo(homepage: str, *, timeout: float = 8.0) -> tuple[str, bytes] | None:
    """(확장자, 바이트) 또는 찾지 못하면 None. 실패는 조용히 None(로고는 없어도 글자 배지로 대신한다)."""
    try:
        page = _get(homepage, timeout, 600_000).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        page = ""
    for url in icon_candidates(page, homepage)[:4]:
        if not url.startswith(("http://", "https://")):
            continue
        try:
            data = _get(url, timeout, MAX_BYTES)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
        if len(data) <= MAX_BYTES and (kind := image_kind(data)):
            return kind, data
    return None
