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


def test_the_agent_schemas_carry_the_same_limits_as_the_checker():
    from edge_analysis_v2.agent.output_schema import EDIT_SCHEMAS, OUTLOOK
    properties = OUTLOOK['properties']
    assert properties['summary_card']['properties']['summary']['maxLength'] == TEXT_LIMITS['summary_card.summary']
    assert properties['factors']['items']['properties']['sentence']['maxLength'] == TEXT_LIMITS['factors[].sentence']
    assert properties['conclusion']['properties']['title']['maxLength'] == TEXT_LIMITS['conclusion.title']
    assert properties['conclusion']['properties']['supports']['items']['properties']['label']['maxLength'] == 15
    topic = EDIT_SCHEMAS[0]['function']['parameters']['properties']['items']['items']['properties']
    assert topic['sentences']['maxItems'] == 5 and topic['sentences']['items']['maxLength'] == 60
    assert topic['title_keyword']['maxLength'] == 20
    assert EDIT_SCHEMAS[0]['function']['parameters']['properties']['title']['maxLength'] == 25


def test_the_editor_names_the_overlong_bullet_and_an_inherited_long_body_must_be_rewritten():
    from datetime import datetime, timezone
    import pytest
    from edge_analysis_v2.analysis.body_editor import BodyEditor
    at = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
    topic = {'id': 't1', 'title_keyword': '수주 확대', 'sentences': ['마' * 61], 'tool_run_ids': ['r1']}
    with pytest.raises(ValueError, match=r'detail.items\[0\].sentences\[0\] 61>60'):
        BodyEditor(None, at).write('제목', [topic])
    ok = BodyEditor(None, at).write('제목', [topic | {'sentences': ['마' * 60]}])
    assert len(ok['items']) == 1
    old_body = {'title': '제목', 'items': [{'id': 't1', 'title_keyword': '수주 확대',
        'sentences': [{'sentence': '마' * 140, 'is_updated': False}], 'tool_run_ids': ['r1']}]}
    editor = BodyEditor(old_body, at)
    with pytest.raises(ValueError, match='rewrite the whole body'):
        editor.apply([{'action': 'add', 'id': 't2', 'title_keyword': '새 논점', 'sentences': ['짧은 문장'], 'tool_run_ids': ['r2']}])
    assert len(editor.write('제목', [topic | {'sentences': ['마' * 60]}])['items']) == 1


def test_an_overlong_summary_cannot_be_published():
    import pytest
    from edge_analysis_v2.contracts import publication_validation
    factors = [{'type': t, 'sticker': '중립', 'sentence': '바'} for t in ('이슈', '차트', '매크로', '밸류', '수급')]
    features = {'outlook': {'direction': '중립'}, 'summary_card': {'title': '가', 'summary': '나' * 81}, 'factors': factors,
                'conclusion': {'title': '사', 'supports': [], 'burdens': [], 'sentence': '차'}}
    body = {'title': '다', 'mode': 'create', 'updates': {'date': '2026-10-06', 'items': []}, 'items': [
        {'id': 't1', 'title_keyword': '라', 'sentences': [{'sentence': '마', 'is_updated': False}], 'tool_run_ids': ['r1']}]}
    with pytest.raises(ValueError, match='summary_card.summary 81>80'):
        publication_validation.outlook(features, body)
    features['summary_card']['summary'] = '나' * 80
    publication_validation.outlook(features, body)


def test_a_schema_error_is_described_by_location_and_size_without_echoing_the_text():
    from jsonschema import Draft202012Validator
    from edge_analysis_v2.agent.output_schema import OUTLOOK
    from edge_analysis_v2.agent.runner import describe_schema_error
    value = {'summary_card': {'title': '가', 'summary': '비밀' * 50}}
    error = next(e for e in Draft202012Validator(OUTLOOK).iter_errors(value) if e.validator == 'maxLength')
    message = describe_schema_error(error)
    assert message.startswith('summary_card/summary: 100 characters, limit 80') and '비밀' not in message
