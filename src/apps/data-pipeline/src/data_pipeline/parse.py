"""파싱·정규화 유틸 — article_id 규약의 SSOT.

article_id = sha256(normalize_url(url)). URL 이 없거나 비정상이면
title|published_at 폴백으로라도 안정적인 id 를 만든다(항상 non-empty).
프로토타입(new-data-pipeline app/common/parse.py)에서 필요한 부분만 이식.
"""

from __future__ import annotations

import hashlib
import re
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
    if norm:
        basis = norm
    else:
        # 폴백도 정규화한다 — 날짜 표기(공백/T/Z)·제목 공백만 다른 같은 기사가
        # 다른 id 로 갈려 dedup 을 빠져나가지 않게(URL 경로와 동일한 안정성).
        canonical_dt = parse_datetime(published_at) or (published_at or "").strip()
        canonical_title = " ".join((title or "").split())
        basis = f"{canonical_title}|{canonical_dt}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def parse_datetime(text: str | None) -> str | None:
    """FMP publishedDate("YYYY-MM-DD HH:MM:SS" 또는 ISO8601) → ISO8601 UTC 문자열.

    FMP 는 오프셋 없는 벽시계 시각을 준다 — naive 는 UTC 로 간주한다
    (published_at 을 비우지 않기 위한 알려진 근사. 페이로드에 오프셋이 없다).

    NOTE(ALPHA-104): offset 포함 ISO(예: '...+09:00', '...-04:00')는 지금 오파싱된다
    (+오프셋은 잘려 UTC 로 오인, -오프셋은 파싱 실패). Step1 은 이 값을 coarse 파티션
    날짜로만 써서 raw 보존엔 영향이 작다. 정확한 published_at 은 S003(정규화·품질)의 AC라,
    offset-aware 파싱(fromisoformat 기반)은 ALPHA-104 에서 다룬다.
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


_BIGKINDS_NEWS_ID_TS = re.compile(r"\.(\d{8})\d{6}")


def bigkinds_date(record: dict) -> str | None:
    """BigKinds row 의 발행일(YYYY-MM-DD) 파생 — DATE 우선, 없으면 NEWS_ID 임베드 타임스탬프.

    BigKinds 벤더 date 파싱의 SSOT다. ingest(raw 파티션 published_date)와 normalize(canonical
    published_at)가 같은 규약을 쓰도록 여기 한 곳에 둔다 — 스텝별로 재구현하면 두 단계의 발행일이
    드리프트한다. `str()` 강제로 비문자열 DATE/NEWS_ID 에도 크래시하지 않는다(달력 유효성 검증은
    이 값을 받는 parse_datetime 이 한다 — 여기선 자릿수만 슬라이싱)."""
    date_digits = re.sub(r"\D", "", str(record.get("DATE") or ""))
    if len(date_digits) >= 8:
        return f"{date_digits[:4]}-{date_digits[4:6]}-{date_digits[6:8]}"
    match = _BIGKINDS_NEWS_ID_TS.search(str(record.get("NEWS_ID") or ""))
    if match:
        day = match.group(1)
        return f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    return None


def news_article_id(record: dict) -> str:
    """raw 뉴스 레코드(벤더 무관) → 안정 article_id. 벤더별 정체성 규약의 SSOT.

    **BigKinds 는 NEWS_ID 를 우선**한다 — 국내 뉴스는 제목·발행일이 같아도 별개 기사가 있어
    (제목|날짜 폴백으로 묶으면 별개 기사가 같은 id 로 붕괴한다), NEWS_ID 라는 벤더 고유 식별자를
    쓴다. NEWS_ID 가 없으면(FMP 등) 정규화 URL 우선, 그것도 없으면 제목|발행일 폴백.
    ingest(raw 적재)와 normalize(정제 재계산)가 이 한 함수를 공유해 정체성이 드리프트하지 않는다."""
    news_id = str(record.get("NEWS_ID") or "").strip()
    if news_id:
        return make_article_id(None, news_id, None)
    title = record.get("title") or record.get("TITLE") or ""
    published = record.get("publishedDate") or record.get("DATE") or bigkinds_date(record)
    return make_article_id(record.get("url") or record.get("PROVIDER_LINK_PAGE"), title, published)
