"""Length and count limits of the customer outlook screen, in one place.

Characters are counted as written, including spaces and punctuation. A longer text is not a
better report: evidence goes into more bullets or topics, never into a longer field.
"""

# Field path in the outlook screen -> maximum characters.
TEXT_LIMITS = {
    'summary_card.title': 25,
    'summary_card.summary': 80,
    'detail.title': 25,
    'detail.items[].title_keyword': 20,
    'detail.items[].sentences[].sentence': 60,
    'factors[].sentence': 60,
    'conclusion.title': 12,
    'conclusion.supports[].label': 15,
    'conclusion.burdens[].label': 15,
    'conclusion.sentence': 80,
    'conclusion.change_condition': 60,
}
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


def checked_texts(screen):
    """Number of limited texts present on the screen, the denominator for a violation rate."""
    return sum(isinstance(text, str) for _, _, text in _texts(screen))
