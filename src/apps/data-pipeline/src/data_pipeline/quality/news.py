"""뉴스 필수필드·발행일 정합성 게이트 (ALPHA-131 / S030).

정규화된 뉴스 메타행 하나가 **분석에 쓸 수 있는 최소 요건**을 갖췄는지 검사한다 —
제목이 있고, 발행시각이 파싱 가능하며 실재 범위 안인지. 분석에 못 쓰는 뉴스가
canonical 로 흘러 후속 분석을 오염시키지 않게 막는 게이트다(스토리: "분석에 사용할
수 없는 뉴스 데이터를 걸러내기 위해 필수 필드 누락 여부를 검증").

이 함수는 **벤더 무관 순수 함수**다 — 입력은 이미 정규화(벤더 필드 → 표준 메타행)된
행이라 title·normalized_url·published_at·publisher 키를 가진다고 가정한다. 벤더별 원본
필드명(FMP title vs BigKinds TITLE 등) 해석은 그 이전 정규화 단계가 흡수한다.

사유는 **전부 수집**한다(첫 실패에서 멈추지 않음 — Rule 12). 사유는 두 종류다:
  - **blocking**(BLOCKING_REASONS): canonical 진입을 막는다 — 이게 없으면 기사를
    식별·시간축에 놓을 수 없어 분석 자체가 불가.
  - **non-blocking**(경고): 로깅만 하고 행은 통과시킨다 — url·publisher 는 벤더별로
    결측 가능하고(BigKinds 는 PROVIDER_LINK_PAGE 없이 NEWS_ID 로 식별), 스토리 AC 가
    "원본 URL **또는** 기사 식별자"라 url 은 필수가 아니다. 미확정·가변 필드에 하드
    게이트를 걸어 한 벤더를 통째로 조용히 탈락시키지 않는다(mass false-fail 방지).
"""

from __future__ import annotations

# canonical 진입을 막는 필수 사유. 나머지(missing_url·missing_publisher)는 경고로 로깅만 한다.
BLOCKING_REASONS = frozenset(
    {"missing_title", "unparseable_published_at", "implausible_published_at"}
)

# 발행일 하한 — 이보다 과거는 뉴스 파이프라인 대상이 아닌 오염된 날짜로 본다(달력유효-쓰레기).
MIN_PUBLISHED_DATE = "2000-01-01"


def _blank(value: object) -> bool:
    """사실상 빈 값인가 — None·비문자열·공백만 문자열(설정 NonBlankStr 관례와 동형)."""
    return not (isinstance(value, str) and value.strip())


def validate_news_meta(row: dict, *, max_published_date: str) -> list[str]:
    """정규화된 뉴스 메타행의 필수필드·발행일 검사. 위반 사유 코드 리스트(정상=[]).

    max_published_date: 허용 발행일 상한('YYYY-MM-DD', 보통 검증 실행일 + 며칠). 파싱은
      되지만 범위 밖인 미래 날짜(달력상 유효하나 쓰레기)가 passed 로 인증되는 걸 막는다.

    사유(전부 수집, 결정적 순서):
      - missing_title              : title 결측/공백만 (blocking)
      - unparseable_published_at   : 정규화가 published_at 을 못 만듦(결측·비달력일) (blocking)
      - implausible_published_at   : 파싱은 되나 [MIN, max] 밖(far-future/past) (blocking)
      - missing_url                : normalized_url 결측 (non-blocking 경고)
      - missing_publisher          : publisher 결측/공백만 (non-blocking 경고)
    """
    reasons: list[str] = []
    if _blank(row.get("title")):
        reasons.append("missing_title")

    published_at = row.get("published_at")
    if _blank(published_at):
        # 정규화가 parse_datetime 으로 못 만든 발행시각(결측·비달력일·None) — 시간축에
        # 못 놓아 분석 불가. published_at 은 여기서 문자열임이 보장되지 않으므로 _blank 로 검사.
        reasons.append("unparseable_published_at")
    elif not (MIN_PUBLISHED_DATE <= published_at[:10] <= max_published_date):
        # '20991231'·'19000101' 같은 달력유효-쓰레기 날짜가 records_passed 로 인증돼 엉뚱한
        # far-future/past 파티션을 만드는 걸 막는다. ISO 문자열이라 사전순 비교가 곧 날짜 비교.
        reasons.append("implausible_published_at")

    if _blank(row.get("normalized_url")):
        # 경고: BigKinds 는 URL 없이 NEWS_ID 로 식별 가능 — 탈락 아님(AC "URL 또는 기사 식별자").
        reasons.append("missing_url")
    if _blank(row.get("publisher")):
        # 경고: 언론사 결측은 provenance 손실이나 분석 자체는 가능 — 로깅만.
        reasons.append("missing_publisher")
    return reasons
