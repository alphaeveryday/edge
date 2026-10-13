"""Retrieval coverage is distinct from the truth of a cited claim."""
from datetime import date
import re


def inspect_body(body, limit):
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    content = [line for line in lines if not re.match(r'^(?:URL|Published|Author):|^#', line)]
    # Navigation labels and link lists alone are not a readable product document.
    cleaned = [re.sub(r'https?://\S+', '', re.sub(r'\[([^]]*)\]\([^)]*\)', r'\1', line)) for line in content]
    readable = [line for line in cleaned if len(line) >= 60 or len(line) >= 20 and line.endswith(('.', '。'))]
    tabular = any(re.search(r'[A-Za-z가-힣]', line) and re.search(r'\d+(?:\.\d+)?\s*%', line) for line in cleaned)
    failure = bool(re.search(r'^(?:Error fetching|No content found|Access denied|403 Forbidden|404 Not Found)', body, re.I | re.M))
    if failure or not (readable or tabular):
        return {'status': 'unavailable', 'reason': 'INSUFFICIENT_SOURCE_BODY'}
    published = re.search(r'^Published:\s*(\d{4}-\d{2}-\d{2})', body, re.M)
    published_at = None
    if published:
        try:
            published_at = date.fromisoformat(published[1]).isoformat()
        except ValueError:
            pass
    return {'status': 'partial' if len(body) >= limit else 'available',
            'truncated': len(body) >= limit, 'published_at': published_at,
            'claim_as_of': None, 'coverage_status': 'requires_field_review'}


def source_page(source, offset=0):
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or offset >= max(1, len(source['passages'])):
        raise ValueError('INVALID_PASSAGE_OFFSET')
    passages = source['passages'][offset:offset + 24]
    next_offset = offset + len(passages)
    return {**{key: value for key, value in source.items() if key not in {'excerpt', 'passages'}},
            'passages': passages, 'next_offset': next_offset if next_offset < len(source['passages']) else None,
            'instruction': '수집일은 사실 기준일이 아닙니다. 필요한 보유내역·보수·분배·상품 구조가 있는지 확인하고 없거나 절단됐으면 공식 팩트시트/PDF/다른 페이지를 찾으세요.'}
