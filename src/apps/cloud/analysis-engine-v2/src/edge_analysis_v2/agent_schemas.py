"""Agent response schemas; body text is submitted through editing tools."""


def obj(properties, required=None):
    """Declare a closed object with explicit required fields."""
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
            'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1}
REFS = {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': TEXT}
STICKER = {'type': 'string', 'enum': ['강력상승', '상승', '중립', '하락', '강력하락']}
SENTIMENT = {'type': 'string', 'enum': ['positive', 'neutral', 'negative']}
ITEM = obj({'title_keyword': TEXT, 'sentence': TEXT, 'sentiment': SENTIMENT, 'tool_run_ids': REFS})
TOPIC = obj({'id': TEXT, 'title_keyword': TEXT, 'sentences': {'type': 'array', 'minItems': 1, 'items': TEXT},
             'tool_run_ids': REFS, 'updated_sentence_numbers': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}}},
            ['id', 'title_keyword', 'sentences', 'tool_run_ids'])
MOVEMENT = obj({
    'new_items': {'type': 'array', 'items': obj(ITEM['properties'] | {
        'candidate_id': TEXT, 'type': {'enum': ['이슈', '차트', '매크로', '수급']}})},
    'selected_item_ids': {'type': 'array', 'maxItems': 5, 'uniqueItems': True, 'items': TEXT},
    'summary': {'type': ['string', 'null']},
})
KEYWORDS = {'type': 'array', 'items': obj({'label': TEXT, 'tool_run_ids': REFS})}
OUTLOOK = obj({
    'outlook': obj({'direction': STICKER}),
    'summary_card': obj({'title': TEXT, 'summary': TEXT}),
    'factors': {'type': 'array', 'minItems': 5, 'maxItems': 5, 'items': obj({
        'type': {'enum': ['이슈', '차트', '매크로', '밸류', '수급']}, 'sticker': STICKER, 'sentence': TEXT})},
    'conclusion': obj({'title': TEXT, 'supports': KEYWORDS, 'burdens': KEYWORDS, 'sentence': TEXT,
                       'change_condition': TEXT}, ['title', 'supports', 'burdens', 'sentence']),
    'issue_detail': obj({'type': {'const': '이슈'}, 'sticker': STICKER, 'headline': TEXT,
                         'items': {'type': 'array', 'items': ITEM}}),
})
EDIT_SCHEMAS = [
    {'type': 'function', 'function': {'name': 'write_outlook_body',
     'description': '최초 본문 또는 중요한 논점 10개 이상 변경 시 전체 본문을 작성합니다. 유지할 논점 ID는 그대로 씁니다. 재작성에서 새 정보가 있는 불릿은 updated_sentence_numbers에 1부터 시작하는 번호로 지정합니다. 단순 표현 변경은 지정하지 않습니다. 저장 전 초안과 오늘 업데이트를 반환합니다.',
     'parameters': obj({'title': TEXT, 'items': {'type': 'array', 'maxItems': 15, 'items': TOPIC}})}},
    {'type': 'function', 'function': {'name': 'apply_outlook_body_changes',
     'description': '논점을 ID로 추가·수정·삭제합니다. 여러 변경을 한 번에 적용합니다. 수정 불릿 번호는 1부터 시작합니다. 생략한 필드는 유지합니다. 수정 후 전체 본문을 반환합니다.',
     'parameters': obj({'changes': {'type': 'array', 'items': obj(TOPIC['properties'] | {
         'action': {'enum': ['add', 'update', 'remove']},
         'updated_sentence_numbers': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}}}, ['action', 'id'])},
         'title': TEXT, 'item_order': {'type': 'array', 'items': TEXT}}, ['changes'])}},
]
