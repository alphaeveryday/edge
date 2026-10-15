"""Length and count limits of the customer outlook screen, in one place.

Characters are counted as written, including spaces and punctuation. A longer text is not a
better report: evidence goes into more bullets or topics, never into a longer field.
"""

TITLE_MAX_LENGTH = 20

# Field path in the outlook screen -> maximum characters.
TEXT_LIMITS = {
    'summary_card.title': 25,
    'summary_card.summary': 80,
    'detail.title': 50,
    'detail.items[].title_keyword': 40,
    'detail.items[].sentences[].sentence': 60,
    'factors[].sentence': 60,
    'conclusion.title': 12,
    'conclusion.supports[].label': 15,
    'conclusion.burdens[].label': 15,
    'conclusion.sentence': 80,
    'conclusion.change_condition': 60,
}
# The movement explanation: one summary and up to five items, each a title and one short paragraph.
# Without a limit the item paragraph grew to a median of 193 characters and the summary to 193. The summary is read
# where a headline would be, so it is one sentence (user decision 2026-10-14; 45 to 53 characters were enough).
MOVEMENT_LIMITS = {'summary': 70, 'title_keyword': TITLE_MAX_LENGTH, 'sentence': 120}
MAX_TOPICS = 15
MAX_BULLETS_PER_TOPIC = 5
FACTOR_COUNT = 5


def _texts(screen):
    """Yield (limit key, concrete location, text) for every limited text on the screen."""
    card, detail, conclusion = screen.get('summary_card') or {}, screen.get('detail') or {}, screen.get('conclusion') or {}
    yield 'summary_card.title', 'summary_card.title', card.get('title')
    yield 'summary_card.summary', 'summary_card.summary', card.get('summary')
    yield 'detail.title', 'detail.title', detail.get('title')
    for i, item in enumerate(detail.get('items') or []):
        yield 'detail.items[].title_keyword', f'detail.items[{i}].title_keyword', item.get('title_keyword')
        for j, sentence in enumerate(item.get('sentences') or []):
            text = sentence.get('sentence') if isinstance(sentence, dict) else sentence
            yield 'detail.items[].sentences[].sentence', f'detail.items[{i}].sentences[{j}]', text
    for i, update in enumerate((detail.get('updates') or {}).get('items') or []):
        # Today's updates repeat topic titles on the screen.
        yield 'detail.items[].title_keyword', f'detail.updates.items[{i}].title_keyword', update.get('title_keyword')
    for i, factor in enumerate(screen.get('factors') or []):
        yield 'factors[].sentence', f'factors[{i}].sentence', factor.get('sentence')
    yield 'conclusion.title', 'conclusion.title', conclusion.get('title')
    for side in ('supports', 'burdens'):
        for i, entry in enumerate(conclusion.get(side) or []):
            yield f'conclusion.{side}[].label', f'conclusion.{side}[{i}].label', entry.get('label')
    yield 'conclusion.sentence', 'conclusion.sentence', conclusion.get('sentence')
    yield 'conclusion.change_condition', 'conclusion.change_condition', conclusion.get('change_condition')


def violations(screen):
    """List every limit the outlook screen breaks.

    Args:
        screen: Outlook screen or any part of it (missing parts are skipped, not reported).

    Returns:
        Rows of {location, limit, actual, kind}; kind is 'characters' or 'count'. Empty when compliant.
    """
    found = []
    for key, location, text in _texts(screen):
        if isinstance(text, str) and len(text) > TEXT_LIMITS[key]:
            found.append({'location': location, 'limit': TEXT_LIMITS[key], 'actual': len(text), 'kind': 'characters'})
    items = (screen.get('detail') or {}).get('items')
    if isinstance(items, list):
        if len(items) > MAX_TOPICS:
            found.append({'location': 'detail.items', 'limit': MAX_TOPICS, 'actual': len(items), 'kind': 'count'})
        for i, item in enumerate(items):
            count = len(item.get('sentences') or [])
            if count > MAX_BULLETS_PER_TOPIC:
                found.append({'location': f'detail.items[{i}].sentences', 'limit': MAX_BULLETS_PER_TOPIC, 'actual': count, 'kind': 'count'})
    factors = screen.get('factors')
    if isinstance(factors, list) and len(factors) != FACTOR_COUNT:
        found.append({'location': 'factors', 'limit': FACTOR_COUNT, 'actual': len(factors), 'kind': 'count'})
    return found


def movement_violations(response, previous_items=()):
    """Texts of a movement response over their limit, with what to do about each.

    Args:
        response: The agent's movement response (new_items, selected_item_ids, summary).
        previous_items: Earlier items of the day; one that is selected again is shown again, so it counts.

    Returns:
        One sentence per violation, in Korean, for the agent. Empty when compliant.
    """
    found = []
    summary = response.get('summary')
    if isinstance(summary, str) and len(summary) > MOVEMENT_LIMITS['summary']:
        found.append(f"summary가 {len(summary)}자입니다. {MOVEMENT_LIMITS['summary']}자 안으로 줄이세요.")
    for item in response.get('new_items') or []:
        for field in ('title_keyword', 'sentence'):
            text = item.get(field)
            if isinstance(text, str) and len(text) > MOVEMENT_LIMITS[field]:
                found.append(f"새 항목 {item.get('candidate_id')}의 {field}가 {len(text)}자입니다. {MOVEMENT_LIMITS[field]}자 안으로 줄이세요.")
    selected = set(response.get('selected_item_ids') or [])
    for item in previous_items or ():
        if item.get('item_id') in selected and any(len(item.get(field) or '') > MOVEMENT_LIMITS[field] for field in ('title_keyword', 'sentence')):
            found.append(f"기존 항목 {item['item_id']}는 지금의 글자 수 상한을 넘습니다. 다시 고르지 말고, 여전히 중요하면 근거를 다시 확인해 "
                         '상한 안의 새 항목으로 바꾸세요.')
    return found


def checked_texts(screen):
    """Number of limited texts present on the screen, the denominator for a violation rate."""
    return sum(isinstance(text, str) for _, _, text in _texts(screen))
