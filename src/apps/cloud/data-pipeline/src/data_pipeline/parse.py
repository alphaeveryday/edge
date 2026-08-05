"""파싱·정규화 유틸 — article_id 규약의 SSOT.

article_id = sha256(normalize_url(url)). URL 이 없거나 비정상이면
title|published_at 폴백으로라도 안정적인 id 를 만든다(항상 non-empty).
프로토타입(new-data-pipeline app/common/parse.py)에서 필요한 부분만 이식.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone, tzinfo
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

    비문자열 입력(int·list 등)은 None 으로 돌려준다 — `.strip()` 이 비str 에서 크래시하면
    이 함수를 URL 후보 필터로 쓰는 news_article_id 가 한 이상치 행에 죽는다(BigKinds
    PROVIDER_LINK_PAGE 가 비str 이어도 NEWS_ID 폴백으로 안전하게 흐르도록, SSOT 에서 방어).
    """
    if not isinstance(url, str) or not url:
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


def parse_datetime(text: str | None, *, naive_tz: tzinfo = timezone.utc) -> str | None:
    """ISO 날짜·시각을 timezone-aware 문자열로 정규화한다.

    오프셋 없는 값은 벤더가 선언한 ``naive_tz`` 로 읽는다. 기본은 기존 FMP 계약인
    UTC이며, BigKinds NEWS_ID 벽시계는 호출부가 KST 를 넘겨 현지 날짜를 보존한다.
    """
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_tz)
    return parsed.isoformat()


_KRX_SHORT_CODE = re.compile(r"[0-9][0-9A-Z]{5}\Z")


def krx_short_code(value: object) -> str | None:
    """KRX 단축코드로 성립하면 정돈한 코드, 아니면 None. KR 티커 형태 판정의 SSOT.

    **숫자 6자리가 아니다** — KRX 가 번호를 소진해 신규 상장분에는 문자가 섞인 코드
    (0093A0·0005G0 등)를 발급하고, 우리 ETF 유니버스 31종 중 7종이 그렇다. `isdigit()`
    로 보면 그 7종이 조용히 빠진다(ALPHA-463·380 이 각각 가격·NAV 에서 겪은 결함).

    다만 '6자리 영숫자'로만 넓히면 반대로 `ABCDEF` 같은 6자 US 심볼과 한글·전각
    문자열까지 KR 코드로 통과한다 — KIS(국내 전용)에 엉뚱한 질의를 보내고 그 응답을
    market=KR 로 적재하게 된다. **선두는 숫자, 나머지는 ASCII 영숫자 대문자**가
    실제 형태다(`sources/fmp.py:market_for` 의 '첫 글자 숫자면 KR' 규약과 같은 축).
    """
    if not isinstance(value, str):
        return None
    code = value.strip()
    return code if _KRX_SHORT_CODE.match(code) else None


_BIGKINDS_NEWS_ID_TS = re.compile(r"\.(\d{8})(\d{6})")


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


def bigkinds_datetime(record: dict) -> str | None:
    """BigKinds row 의 발행 **시각**(YYYY-MM-DD HH:MM:SS) — 날짜는 bigkinds_date SSOT,
    시각은 NEWS_ID 임베드 타임스탬프에서 온다.

    인과귀속의 시간 분해는 τ(초 단위)로 하루를 자른다 — 날짜 해상도로는 모든 사건이
    09:00 KST 한 창에 뭉쳐 분해가 퇴화한다(2026-08-01 실측: RDB available_at distinct
    62개, 셀 하나에서 77건 병합). NEWS_ID 는 `언론사코드.YYYYMMDDHHMMSS연번` 꼴로
    시각을 이미 갖고 있었고, 종전 정규식이 날짜 8자리만 캡처해 버리고 있었다.

    두 가지를 지킨다:
    - **파티션 불변식**: 반환값의 날짜부는 bigkinds_date 와 항상 같다. NEWS_ID 의
      날짜가 DATE 필드와 어긋나면 그 시각은 버린다 — published_date 파티션과
      published_at 이 드리프트하면 멱등 병합이 다른 파티션에 같은 기사를 만든다.
    - **자정 폴백**: 시각 6자리가 시계로 성립하지 않으면(25시 등) 날짜만 돌려준다.
      쓰레기 시각이 parse_datetime 에서 None 이 되면 행 전체가 게이트에서 죽는다 —
      시각을 잃는 것과 기사를 잃는 것은 다른 사고다."""
    day = bigkinds_date(record)
    if day is None:
        return None
    match = _BIGKINDS_NEWS_ID_TS.search(str(record.get("NEWS_ID") or ""))
    if match:
        d, t = match.groups()
        same_day = f"{d[:4]}-{d[4:6]}-{d[6:8]}" == day
        if same_day and t[:2] < "24" and t[2:4] < "60" and t[4:6] < "60":
            return f"{day} {t[:2]}:{t[2:4]}:{t[4:6]}"
    return day


def news_article_id(record: dict) -> str:
    """raw 뉴스 레코드(벤더 무관) → 안정 article_id. 뉴스 정체성 규약의 SSOT.

    **원문 URL 해시를 1순위**로 쓴다 — URL 은 전역 식별자라 소스 무관하다: 같은 실기사면 FMP `url`
    이든 BigKinds `PROVIDER_LINK_PAGE`(원문 링크, 실측 확인)이든 같은 id 로 모여 canonical 이
    소스를 흡수한 통합 구조가 된다. URL 이 없을 때만 폴백 — BigKinds `NEWS_ID`(벤더 고유 식별자,
    제목|날짜 붕괴 방지) → 최후 `title|published`. ingest(raw 적재)와 normalize(정제 재계산)가 이
    한 함수를 공유해 정체성이 드리프트하지 않는다. 우선순위: url → NEWS_ID → title|date."""
    # 후보 URL 필드 중 **정규화 가능한 첫 값**을 쓴다 — truthy-but-garbage `url` 이 유효한
    # `PROVIDER_LINK_PAGE` 를 가리지 않게(깨진 필드보다 성립하는 원문 링크 우선).
    url = next((u for u in (record.get("url"), record.get("PROVIDER_LINK_PAGE")) if normalize_url(u)), None)
    if url:
        return make_article_id(url, "", None)  # 원문 URL 해시 — 소스 무관 정체성
    news_id = str(record.get("NEWS_ID") or "").strip()
    if news_id:
        return make_article_id(None, news_id, None)  # URL 없을 때 BigKinds 벤더 식별자
    title = record.get("title") or record.get("TITLE") or ""
    published = record.get("publishedDate") or record.get("DATE") or bigkinds_date(record)
    return make_article_id(None, title, published)  # 최후 폴백
