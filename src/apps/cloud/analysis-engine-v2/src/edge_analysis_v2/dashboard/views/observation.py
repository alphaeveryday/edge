"""Summary, paired tool calls and raw views of persisted SDK events."""
import html
import json


def pretty_json(value):
    """Serialize before browser highlighting so numbers retain their precision."""
    return '<pre class="observation-json">' + html.escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre>'


def render_observation(detail, level):
    if level not in ('summary', 'calls', 'raw'):
        raise ValueError('Unknown observation level')
    artifacts = detail['artifacts']
    events, invalid = [], 0
    for line in artifacts.get('events.jsonl', '').splitlines():
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get('message'), dict):
                raise ValueError('Invalid event')
            events.append(event)
        except (ValueError, TypeError):
            invalid += 1
    blocks = [(event, block) for event in events for block in (
        event['message'].get('content', []) if isinstance(event['message'].get('content'), list) else []) if isinstance(block, dict)]
    calls = {b['id']: b for _, b in blocks if b.get('id') and b.get('name') and 'input' in b}
    results = {b['tool_use_id']: b for _, b in blocks if b.get('tool_use_id')}
    esc = lambda value: html.escape(str(value))
    parts = ['<h2>에이전트 관측 · ' + {'summary':'요약', 'calls':'툴 호출 상세', 'raw':'원문'}[level] + '</h2>',
             '<p class="muted">저장된 SDK 이벤트를 표시합니다. 없는 기록은 추정하지 않습니다.</p>']
    if invalid:
        parts.append('<p class="error">읽지 못한 이벤트 ' + str(invalid) + '줄 · 작성 중인 마지막 줄 또는 손상된 기록입니다. 원문에서 확인하세요.</p>')
    if level == 'raw':
        parts.append('<h3>실행 상태</h3><pre>' + esc(json.dumps(detail['job'], ensure_ascii=False, indent=2)) + '</pre>')
        for name, text in artifacts.items():
            parts.append('<details><summary>' + esc(name) + '</summary><pre>' + esc(text) + '</pre></details>')
        return ''.join(parts)
    if level == 'summary':
        pending = sum(identity not in results for identity in calls)
        failed = sum(bool(b.get('is_error')) for b in results.values())
        parts.append('<section class="panel"><p>실행 상태: ' + esc(detail['job'].get('status', '기록 없음'))
                     + ' · 이벤트 ' + str(len(events)) + '개 · 툴 호출 ' + str(len(calls))
                     + '개 · 오류 반환 ' + str(failed) + '개 · 결과 미수신 ' + str(pending) + '개</p>'
                     + pretty_json({k: detail['job'].get(k) for k in ('analysis_id', 'started_at', 'finished_at', 'prompt_version')}) + '</section>')
        for name, title in [('input.json','당시 입력'), ('system_prompt.txt','시스템 프롬프트'), ('response.json','에이전트 최종 응답')]:
            if name in artifacts:
                parts.append('<details><summary>' + title + '</summary><pre>' + esc(artifacts[name]) + '</pre></details>')

    def output(block):
        value = block.get('content')
        if isinstance(value, list):
            texts = [b['text'] for b in value if isinstance(b, dict) and isinstance(b.get('text'), str)]
            if len(texts) == len(value) and texts:
                value = '\n'.join(texts)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        return '<h3>' + ('툴 오류' if block.get('is_error') else '툴 반환값') + '</h3>' + pretty_json(value)

    for event, block in blocks:
        if event.get('message_type') == 'AssistantMessage' and (block.get('text') or block.get('thinking')):
            parts.append('<section class="panel"><h3>' + ('모델이 반환한 추론 기록' if block.get('thinking') else '모델 출력 텍스트')
                         + '</h3><p>' + esc(block.get('thinking') or block['text']) + '</p></section>')
        elif block.get('id') in calls:
            result = results.get(block['id'])
            state = '결과 미수신' if result is None else '오류' if result.get('is_error') else '반환 수신'
            parts.append('<article class="panel observation-call" data-call-id="' + esc(block['id']) + '"><h3>' + esc(block['name']) + '</h3><p class="muted">' + esc(block['id']) + ' · ' + state + '</p>')
            if level == 'calls':
                parts.append('<h3>툴 인자</h3>' + pretty_json(block['input']))
                parts.append(output(result) if result is not None else '<p class="muted">툴 결과 대기 중 · 아직 수신되지 않았습니다.</p>')
            parts.append('</article>')
        elif block.get('tool_use_id') and block['tool_use_id'] not in calls:
            parts.append('<article class="panel"><h3>호출 기록 없는 툴 결과</h3>' + output(block) + '</article>')
    if not events:
        parts.append('<p class="empty">저장된 SDK 이벤트가 없습니다. 원문에서 사용 가능한 실행 파일을 확인하세요.</p>')
    for event in events:
        message = event['message']
        if event.get('message_type') == 'ResultMessage':
            if message.get('is_error'):
                parts.append('<section class="panel error"><h3>모델 실행 오류</h3>' + pretty_json(message) + '</section>')
            elif message.get('result'):
                parts.append('<details><summary>모델 최종 출력 텍스트</summary><p>' + esc(message['result']) + '</p></details>')
        elif event.get('message_type') == 'AssistantMessage' and isinstance(message.get('content'), str):
            parts.append('<section class="panel"><h3>모델 출력 텍스트</h3><p>' + esc(message['content']) + '</p></section>')
    return ''.join(parts)
