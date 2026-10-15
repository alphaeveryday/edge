"""Stable audit names and provenance, persisted with each audit result."""

OUTLOOK = '화면 설계/화면 출력 — 전망.md'
MOVEMENT = '화면 설계/화면 출력 — 오늘 움직임.md'
FACTORS = '화면 설계/화면 출력 — 5요인 상세.md'

# These are descriptive implementation names, not quotations from the contracts.
RULES = {
    'movement-empty': ('선정 항목이 0개일 때만 summary가 null인지 확인', MOVEMENT),
    'movement-identities': ('선정 항목의 item_id가 서로 중복되지 않는지 확인', MOVEMENT),
    'movement-cutoff': ('각 항목의 source_as_of가 분석 기준시각을 넘지 않는지 확인', MOVEMENT),
    'update-date': ('오늘 업데이트 날짜가 분석 기준시각의 한국 날짜와 같은지 확인', OUTLOOK + ' · 오늘 업데이트의 수정 방식'),
    'topic-identities': ('본문과 오늘 업데이트 각각에서 논점 id가 중복되지 않는지 확인', OUTLOOK + ' · 오늘 업데이트의 수정 방식'),
    'update-links': ('업데이트의 논점·제목·근거가 본문과 맞고, 삭제 기록은 본문에서 제외되어 뒤에 배치되는지 확인', OUTLOOK + ' · 오늘 업데이트의 수정 방식'),
    'initial-body': ('최초 발행이면 모든 문장이 is_updated=false이고 오늘 업데이트 목록이 비어 있는지 확인', OUTLOOK + ' · 오늘 업데이트의 수정 방식 / 본문 응답: 신규·수정·재작성'),
    'parent-factor': ('요인 상세의 sticker가 전체 전망의 같은 요인 sticker와 같은지 확인', FACTORS),
    'parent-time': ('요인 상세의 analysis_at이 전체 전망의 분석 기준시각과 같은지 확인', FACTORS),
    'metric-order': ('metrics의 지표 key가 중복되지 않고 계약에 정한 순서를 따르는지 확인', FACTORS),
    'observation-cutoff': ('각 지표의 observed_at이 분석 기준 날짜·시각을 넘지 않는지 확인', FACTORS),
    'band-policy': ('정책 미확정 지표 weighted_per_band_5y_pct가 출력에서 제외됐는지 확인', FACTORS),
    'projection-equality': ('요약·상세를 따로 조회한 값이 전체 JSON의 같은 필드 값과 일치하는지 확인', '구현 검증 · contracts/audit.py / read_contract_audit'),
    'child-contracts': ('5요인 상세 묶음의 결과에 각 요인의 검사 실패·미검증이 반영되는지 확인', '구현 검증 · contracts/audit.py / read_contract_audit'),
    'semantic-precondition': ('JSON 구조 오류로 계약 규칙 검사를 진행할 수 없음', '검사 실행 조건 · contracts/screen_validation.py / audit_payload'),
}


def describe_check(check):
    title, source = RULES[check['id']]
    return dict(check, title=title, source=source,
                category='implementation' if check['id'] in ('projection-equality', 'child-contracts', 'semantic-precondition') else 'contract')


def schema_checks(schema, errors):
    """Summarize the same validator result without claiming blocked checks passed."""
    groups = [
        ('json-types', '필드 값의 타입이 계약과 같은지 확인', {'type', 'finite'}, '문자열·숫자·정수·boolean·null 허용 여부'),
        ('json-structure', '객체·배열의 중첩 계층이 계약과 같은지 확인', {'type', 'oneOf', 'anyOf'}, '각 경로의 object/array 및 허용된 하위 구조'),
        ('json-required', '중첩 객체의 필수 필드가 빠짐없이 있는지 확인', {'required'}, 'required에 기록된 필수 키'),
        ('json-extra', '계약에 없는 필드가 추가되지 않았는지 확인', {'additionalProperties'}, 'additionalProperties 제한'),
        ('json-values', '허용값·문자열 길이·숫자 범위를 지키는지 확인', {'enum', 'const', 'minimum', 'maximum', 'minLength', 'maxLength', 'pattern'}, 'enum·const 및 명시된 범위'),
        ('json-arrays', '배열의 개수·고정 위치 규칙을 지키는지 확인', {'minItems', 'maxItems', 'items', 'prefixItems', 'uniqueItems'}, '배열 길이, prefixItems의 위치별 계약'),
        ('json-format', '날짜와 시각 문자열이 계약 형식에 맞는지 확인', {'format'}, 'date·date-time 및 타임존'),
    ]
    def keywords(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keywords(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(keywords(v) for v in value))
        return set()
    present = keywords(schema)
    result = []
    for identity, title, rules, detail in groups:
        if not rules & present:
            continue
        matching = []
        for error in errors:
            rule = error['rule']
            hierarchy = rule == 'type' and any(t in (error.get('expected') if isinstance(error.get('expected'), list) else [error.get('expected')]) for t in ('object', 'array'))
            matches = rule in rules
            if rule == 'type':
                matches = identity == ('json-structure' if hierarchy else 'json-types')
            if identity == 'json-arrays' and '/prefixItems/' in error.get('schema_path', ''):
                matches = True
            if matches:
                matching.append(error)
        result.append(dict(id=identity, title=title, category='schema',
            source='contracts/screen-output.schema.json · ' + schema['title'], description=detail,
            status='failed' if matching else 'not_checked' if errors else 'passed', errors=matching))
    return result
