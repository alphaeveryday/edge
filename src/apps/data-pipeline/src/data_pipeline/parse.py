"""파싱·정규화 유틸 — article_id 규약의 SSOT.

article_id = sha256(normalize_url(url)). URL 이 없거나 비정상이면
title|published_at 폴백으로라도 안정적인 id 를 만든다(항상 non-empty).
프로토타입(new-data-pipeline app/common/parse.py)에서 필요한 부분만 이식.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 추적 파라미터만 제거한다. 쿼리 전체를 지우면 ?id=1 / ?id=2 처럼 쿼리가
# 기사 식별자인 URL 이 같은 article_id 로 붕괴해 별개 기사가 유실된다.
_TRACKING_PARAMS = {"fbclid", "gclid", "yclid", "igshid", "mc_cid", "mc_eid"}


def _clean_query(query: str) -> str:
    pairs = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if not (k.startswith("utm_") or k in _TRACKING_PARAMS)
    ]
    # 정렬: 파라미터 순서만 다른 같은 URL 이 같은 해시로 모이게.
    return urlencode(sorted(pairs))


def normalize_url(url: str | None) -> str | None:
    """스킴/호스트 소문자화, 끝 슬래시·프래그먼트·추적 파라미터 제거.

    트래킹 파라미터만 다른 같은 기사 URL 은 같은 article_id 로 모이고,
    식별자성 쿼리(?id=…)는 보존돼 별개 기사로 남는다.
    """
    if not url:
        return None
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        # 문법이 깨진 URL(예: 닫히지 않은 IPv6 대괄호)은 urlsplit 이 ValueError 를
        # 낸다 — 여기서 삼켜 None 을 돌려줘야 make_article_id 가 title|published 로
        # 폴백한다(기사 하나가 런 전체를 죽이지 않게).
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (parsed.scheme.lower(), host, path, _clean_query(parsed.query), "")
    )


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
