"""Agent response schemas; body text is submitted through editing tools."""
from copy import deepcopy

from edge_analysis_v2.contracts.outlook_limits import MAX_BULLETS_PER_TOPIC, MAX_TOPICS, MOVEMENT_LIMITS, TEXT_LIMITS, TITLE_MAX_LENGTH


def obj(properties, required=None):
    """Declare a closed object with explicit required fields."""
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
            'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1}


def limited(field):
    """Outlook screen text with its approved maximum length."""
    return TEXT | {'maxLength': TEXT_LIMITS[field]}


REFS = {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': TEXT}
STICKER = {'type': 'string', 'enum': ['강력상승', '상승', '중립', '하락', '강력하락']}
SENTIMENT = {'type': 'string', 'enum': ['positive', 'neutral', 'negative']}
ITEM = obj({'title_keyword': TEXT | {'maxLength': TITLE_MAX_LENGTH},
            'sentence': TEXT, 'sentiment': SENTIMENT, 'tool_run_ids': REFS})
TOPIC = obj({'id': TEXT,
             # The field is named a keyword, and the model wrote keywords; say here what the screen shows it as.
             'title_keyword': limited('detail.items[].title_keyword') | {'description': '논점의 제목입니다. 핵심어가 아니라 이 논점의 결론을 평소 말로 쓴 해요체 문장입니다. 제목만 읽어도 누구에게 무슨 일이 있고 그래서 어떻다는 것인지 알 수 있어야 합니다. 예: "LIG는 새 주문이 끊겼는데 주가는 여전히 비싸요"'},
             'sentences': {'type': 'array', 'minItems': 1, 'maxItems': MAX_BULLETS_PER_TOPIC,
                           'items': limited('detail.items[].sentences[].sentence')},
             'sentiment': SENTIMENT | {'description': 'Required for every new topic. When editing, submit sentiment together with changed sentence text; sentiment-only edits are invalid.'},
             'tool_run_ids': REFS | {'description': '이 논점의 전체 근거 목록을 교체합니다. 바뀐 문장뿐 아니라 유지한 문장의 숫자·비중 근거도 포함합니다. 서로 다른 종류의 숫자를 쓰면 각 계산 호출 ID를 모두 넣습니다.'},
             'updated_sentence_numbers': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}}},
            ['id', 'title_keyword', 'sentences', 'sentiment', 'tool_run_ids'])
# The lengths are said here and checked before publication with a chance to shorten; a schema maximum would
# fail the whole explanation over one long sentence.
MOVEMENT = obj({
    'new_items': {'type': 'array', 'items': obj(ITEM['properties'] | {
        'sentence': TEXT | {'description': f"공백·문장부호 포함 최대 {MOVEMENT_LIMITS['sentence']}자. 사건과 그것이 이 ETF에 뜻하는 것을 한두 문장으로."},
        'title_keyword': ITEM['properties']['title_keyword'] | {'description': '누가 + 무슨 일을 쉬운 말로. 수치·줄임말·업계 용어 없이.'},
        'candidate_id': TEXT, 'type': {'enum': ['이슈', '차트', '매크로', '수급']}})},
    'selected_item_ids': {'type': 'array', 'maxItems': 5, 'uniqueItems': True, 'items': TEXT},
    'summary': {'type': ['string', 'null'], 'description': f"공백·문장부호 포함 최대 {MOVEMENT_LIMITS['summary']}자의 한 문장. 그날 가장 중요한 일 하나와 그것이 이 ETF에 뜻하는 것을 쉬운 말로. 수치는 쓰지 않음."},
})
def keywords(side):
    return {'type': 'array', 'items': obj({'label': limited(f'conclusion.{side}[].label'), 'tool_run_ids': REFS})}
# A staged outlook answers in three parts: the investigator's report, what the writer decides with the body,
# and the remaining screen fields filled from the finished body. OUTLOOK is the last two together.
INVESTIGATION = obj({'report': TEXT})
WRITING = obj({
    'outlook': obj({'direction': STICKER}),
    'summary_card': obj({'title': limited('summary_card.title'), 'summary': limited('summary_card.summary')}),
})
FIELDS = obj({
    'factors': {'type': 'array', 'minItems': 5, 'maxItems': 5, 'items': obj({
        'type': {'enum': ['이슈', '차트', '매크로', '밸류', '수급']}, 'sticker': STICKER, 'sentence': limited('factors[].sentence')})},
    'conclusion': obj({'title': limited('conclusion.title'), 'supports': keywords('supports'), 'burdens': keywords('burdens'),
                       'sentence': limited('conclusion.sentence'),
                       'change_condition': limited('conclusion.change_condition')}, ['title', 'supports', 'burdens', 'sentence']),
    'issue_detail': obj({'headline': TEXT | {'maxLength': TITLE_MAX_LENGTH},
                         'items': {'type': 'array', 'items': ITEM}}),
})
OUTLOOK = obj(WRITING['properties'] | FIELDS['properties'])


def _optional_object(schema):
    # Arrays replace the complete list, so their entries retain all required fields.
    if schema.get('type') != 'object':
        return deepcopy(schema)
    return schema | {'required': [], 'properties': {
        key: _optional_object(value) for key, value in schema['properties'].items()}}


OUTLOOK_REVISION = _optional_object(OUTLOOK)


def merge_outlook_revision(previous, changes):
    """Retain omitted properties and replace explicitly submitted arrays or scalars."""
    result = deepcopy(previous)
    for key, value in changes.items():
        result[key] = (merge_outlook_revision(result.get(key, {}), value)
                       if isinstance(value, dict) else deepcopy(value))
    return result


EDIT_SCHEMAS = [
    {'type': 'function', 'function': {'name': 'write_outlook_body',
     'description': '최초 본문을 작성합니다. 기존 본문은 apply_outlook_body_changes로 바뀐 부분만 수정하는 것이 기본입니다. 전체 재작성이 꼭 필요한 경우에도 유지할 논점 ID는 보존하고 새 정보의 불릿 번호만 updated_sentence_numbers에 지정합니다. 저장 전 초안과 오늘 업데이트를 반환합니다.',
     'parameters': obj({'title': limited('detail.title'), 'items': {'type': 'array', 'maxItems': MAX_TOPICS, 'items': TOPIC}})}},
    {'type': 'function', 'function': {'name': 'apply_outlook_body_changes',
     'description': '논점을 ID로 추가·수정·삭제합니다. 문구 정정은 text_replacements로 지정한 문장의 일부만 정확히 치환합니다. 여러 변경은 한 번에 적용하고 실패하면 모두 유지합니다. 문장 번호는 1부터 시작합니다. 생략한 필드는 유지하며 수정 후 전체 본문을 반환합니다.',
     'parameters': obj({'changes': {'type': 'array', 'items': obj(TOPIC['properties'] | {
         'action': {'enum': ['add', 'update', 'remove']},
         'text_replacements': {'type': 'array', 'minItems': 1, 'description': 'update 전용. 문장마다 한 번만 지정하며 old_text가 해당 문장에 정확히 한 번 있어야 합니다. new_text는 다른 문자열입니다. sentences/updated_sentence_numbers와 함께 보내지 않습니다. sentiment와 전체 논점의 tool_run_ids도 보냅니다.',
             'items': obj({'sentence_number': {'type': 'integer', 'minimum': 1}, 'old_text': TEXT,
                           'new_text': {'type': 'string'}})},
         'updated_sentence_numbers': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}}}, ['action', 'id'])},
         'title': limited('detail.title'), 'item_order': {'type': 'array', 'items': TEXT}}, ['changes'])}},
]
