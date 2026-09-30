import json
import html
import re

from edge_analysis_v2.dashboard.views.observation import render_observation


def test_call_detail_pairs_ids_not_event_positions_and_keeps_pending_and_orphans():
    events = [
        {'message_type':'AssistantMessage', 'message':{'content':[
            {'id':'a', 'name':'first', 'input':{'value':9007199254740993}},
            {'id':'b', 'name':'pending', 'input':{}}, {'text':'<script>text</script>'}]}},
        {'message_type':'UserMessage', 'message':{'content':[
            {'tool_use_id':'orphan', 'content':'orphan output', 'is_error':True},
            {'tool_use_id':'a', 'content':[{'text':json.dumps({'result':{'answer':42}})}]}]}},
    ]
    detail = {'job':{'status':'running'}, 'artifacts':{'events.jsonl':'\n'.join(map(json.dumps, events))+'\n{"partial":'}}
    page = render_observation(detail, 'calls')
    assert page.index('first') < page.index('answer') < page.index('pending')
    assert '9007199254740993' in page and '툴 결과 대기' in page
    assert '호출 기록 없는 툴 결과' in page and '툴 오류' in page
    assert '읽지 못한 이벤트 1줄' in page
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert 'field-key' not in page and 'structured' not in page
    blocks = re.findall(r'<pre class="observation-json">(.*?)</pre>', page, re.S)
    values = [json.loads(html.unescape(block)) for block in blocks]
    assert values[0] == {'value':9007199254740993}
    assert values[1] == {'result':{'answer':42}}
    assert '\n  &quot;value&quot;: 9007199254740993\n' in blocks[0]


def test_summary_and_raw_are_distinct_and_keep_original_artifacts():
    detail = {'job':{'status':'completed'}, 'artifacts':{'events.jsonl':'', 'raw_response.txt':'<raw>', 'system_prompt.txt':'prompt'}}
    summary = render_observation(detail, 'summary')
    raw = render_observation(detail, 'raw')
    assert '에이전트 관측 · 요약' in summary
    assert '에이전트 관측 · 원문' in raw and '&lt;raw&gt;' in raw
    assert 'system_prompt.txt' in raw and 'prompt' in raw
