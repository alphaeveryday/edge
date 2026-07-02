"""파싱·정규화 유틸 — article_id 규약의 SSOT.

article_id = sha256(normalize_url(url)). URL 이 없거나 비정상이면
title|published_at 폴백으로라도 안정적인 id 를 만든다(항상 non-empty).
프로토타입(new-data-pipeline app/common/parse.py)에서 필요한 부분만 이식.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str | None) -> str | None:
    """스킴/호스트 소문자화, 끝 슬래시 제거, 쿼리·프래그먼트 제거.

    트래킹 파라미터가 붙은 같은 기사 URL 이 같은 article_id 로 모이게 한다.
    """
    if not url:
        return None
    parsed = urlsplit(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def url_hash(url: str | None) -> str | None:
    normalized = normalize_url(url)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_article_id(url: str | None, title: str, published_at: str | None) -> str:
    """기사 안정 식별자. 정규화 URL 우선, 없으면 title|published_at 폴백."""
    norm = normalize_url(url)
    basis = norm if norm else f"{(title or '').strip()}|{published_at or ''}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def parse_datetime(text: str | None) -> str | None:
    """FMP publishedDate("YYYY-MM-DD HH:MM:SS" 또는 ISO8601) → ISO8601 UTC 문자열.

    FMP 는 오프셋 없는 벽시계 시각을 준다 — naive 는 UTC 로 간주한다
    (published_at 을 비우지 않기 위한 알려진 근사. 페이로드에 오프셋이 없다).
    """
    if not text:
        return None
    t = text.strip().replace("T", " ")
    # 붙어 있을 수 있는 타임존 토큰 제거 (예: "... +00:00" / "... Z")
    t = t.split("+")[0].rstrip("Z").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(t, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None
