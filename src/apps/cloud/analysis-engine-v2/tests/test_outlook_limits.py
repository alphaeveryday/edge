"""The outlook screen must fit the approved field lengths; overflow is a failure, never a warning."""
from edge_analysis_v2.contracts.outlook_limits import TEXT_LIMITS, checked_texts, violations


def screen(**changes):
    value = {
        'summary_card': {'title': '가' * 25, 'summary': '나' * 80},
        'detail': {'title': '다' * 25, 'items': [
            {'title_keyword': '라' * 20, 'sentences': [{'sentence': '마' * 60, 'is_updated': False} for _ in range(5)]}
            for _ in range(15)]},
        'factors': [{'type': t, 'sticker': '중립', 'sentence': '바' * 60} for t in ('이슈', '차트', '매크로', '밸류', '수급')],
        'conclusion': {'title': '사' * 12, 'supports': [{'label': '아' * 15}], 'burdens': [{'label': '자' * 15}],
                       'sentence': '차' * 80, 'change_condition': '카' * 60},
    }
    for path, replacement in changes.items():
        node, keys = value, path.split('.')
        for key in keys[:-1]:
            node = node[int(key)] if key.isdigit() else node[key]
        node[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = replacement
    return value


def test_a_screen_exactly_at_every_limit_is_compliant():
    assert violations(screen()) == []
    assert checked_texts(screen()) == 3 + 15 * 6 + 5 + 1 + 2 + 2


def test_one_character_over_any_limit_is_reported_with_its_location_and_size():
    found = violations(screen(**{'summary_card.summary': '나' * 81}))
    assert found == [{'location': 'summary_card.summary', 'limit': 80, 'actual': 81, 'kind': 'characters'}]
    found = violations(screen(**{'detail.items.3.sentences.2': {'sentence': '마' * 61}}))
    assert found == [{'location': 'detail.items[3].sentences[2]', 'limit': 60, 'actual': 61, 'kind': 'characters'}]
    assert violations(screen(**{'conclusion.title': '상승 전망 유지와 그 이유'}))[0]['limit'] == TEXT_LIMITS['conclusion.title']


def test_spaces_and_punctuation_count_as_characters():
    text = '가 ' * 13   # 26 characters including the spaces
    assert violations(screen(**{'summary_card.title': text}))[0]['actual'] == 26


def test_too_many_topics_bullets_or_a_missing_factor_are_count_violations():
    many = screen()
    many['detail']['items'] = many['detail']['items'] + [many['detail']['items'][0]]
    assert {'location': 'detail.items', 'limit': 15, 'actual': 16, 'kind': 'count'} in violations(many)
    six = screen(**{'detail.items.0': {'title_keyword': '라', 'sentences': [{'sentence': '마'}] * 6}})
    assert violations(six) == [{'location': 'detail.items[0].sentences', 'limit': 5, 'actual': 6, 'kind': 'count'}]
    four = screen()
    four['factors'] = four['factors'][:4]
    assert violations(four) == [{'location': 'factors', 'limit': 5, 'actual': 4, 'kind': 'count'}]


def test_a_partial_screen_and_an_absent_change_condition_are_not_violations():
    assert violations({'summary_card': {'title': '가' * 26}}) == [
        {'location': 'summary_card.title', 'limit': 25, 'actual': 26, 'kind': 'characters'}]
    assert violations(screen(**{'conclusion.change_condition': None})) == []
