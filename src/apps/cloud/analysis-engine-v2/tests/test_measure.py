"""Timings from two runs are comparable only if their environment and tool waits are recorded."""
import json
from pathlib import Path
import tomllib

from edge_analysis_v2.quality.measure import describe_environment, measure


def event(kind, at, **message):
    return json.dumps({'message_type': kind, 'at': at, 'message': message})


def test_tool_seconds_are_the_wait_between_request_and_result_and_errors_are_counted(tmp_path):
    (tmp_path/'events.jsonl').write_text('\n'.join([
        event('SystemMessage', '2026-10-06T00:00:00+00:00', subtype='init'),
        event('AssistantMessage', '2026-10-06T00:00:02+00:00', content=[
            {'id': 'a', 'name': 'mcp__analysis__get_etf_holdings', 'input': {}}, {'id': 'b', 'name': 'mcp__analysis__get_etf_holdings', 'input': {}}]),
        event('UserMessage', '2026-10-06T00:00:03+00:00', content=[{'tool_use_id': 'a', 'type': 'tool_result'}]),
        event('UserMessage', '2026-10-06T00:00:05.5+00:00', content=[{'tool_use_id': 'b', 'type': 'tool_result', 'is_error': True}]),
        event('AssistantMessage', '2026-10-06T00:00:09+00:00', content=[{'id': 'c', 'name': 'Read', 'input': {}}]),
        event('ResultMessage', '2026-10-06T00:00:10+00:00', num_turns=3, duration_ms=9000, duration_api_ms=6000,
              usage={'input_tokens': 100, 'output_tokens': 7, 'service_tier': 'standard'}),
    ]), encoding='utf8')
    result = measure(tmp_path)
    assert result['wall_seconds'] == 10 and result['model_turns'] == 2 and result['sdk_turns'] == 3
    assert result['tools'] == {'get_etf_holdings': {'calls': 2, 'errors': 1, 'seconds': 4.5}}
    assert (result['tool_calls'], result['tool_seconds'], result['unanswered_tool_requests']) == (2, 4.5, 1)
    assert result['usage'] == {'input_tokens': 100, 'output_tokens': 7}


def test_a_run_resumed_for_unfinished_tasks_adds_up_every_round(tmp_path):
    lines = [event('ResultMessage', '2026-10-06T00:00:10+00:00', num_turns=104, duration_ms=423000,
                   usage={'input_tokens': 141, 'cache_read_input_tokens': 3100}),
             event('ResultMessage', '2026-10-06T00:00:40+00:00', num_turns=9, duration_ms=30000,
                   usage={'input_tokens': 12, 'cache_read_input_tokens': 1252})]
    (tmp_path/'events.jsonl').write_text(chr(10).join(lines), encoding='utf8')
    result = measure(tmp_path)
    assert (result['rounds'], result['sdk_turns'], result['sdk_duration_ms']) == (2, 113, 453000)
    assert result['usage'] == {'input_tokens': 153, 'cache_read_input_tokens': 4352}


def test_a_run_that_never_received_a_message_is_still_measured_from_its_own_boundaries(tmp_path):
    from datetime import datetime, timedelta, timezone
    began = datetime(2026, 10, 6, tzinfo=timezone.utc)
    result = measure(tmp_path, started_at=began, finished_at=began + timedelta(seconds=600))
    assert (result['wall_seconds'], result['model_turns'], result['tool_calls'], result['rounds']) == (600, 0, 0, 0)


def test_time_after_the_last_message_counts_toward_the_run(tmp_path):
    from datetime import datetime, timedelta, timezone
    (tmp_path/'events.jsonl').write_text(event('AssistantMessage', '2026-10-06T00:00:05+00:00', content=[]), encoding='utf8')
    began = datetime(2026, 10, 6, tzinfo=timezone.utc)
    assert measure(tmp_path, started_at=began, finished_at=began + timedelta(seconds=600))['wall_seconds'] == 600


def test_the_environment_names_the_agent_executable_and_pinned_packages():
    value = describe_environment()
    assert value['python'] and value['packages']['claude-agent-sdk']
    assert set(value['agent_cli']) == {'path', 'version', 'bundled'}


def test_the_image_installs_exactly_the_versions_in_the_workspace_lock():
    # WHY: the image used to resolve dependencies at build time, so two builds of one commit could differ.
    root = Path(__file__).parents[1]
    locked = {p['name']: p['version'] for p in tomllib.loads((root.parents[2]/'uv.lock').read_text(encoding='utf8'))['package']}
    pins = [line.split()[0] for line in (root/'requirements.lock').read_text(encoding='utf8').splitlines()
            if line and line[0].isalnum()]
    assert len(pins) > 20
    for pin in pins:
        name, _, number = pin.partition('==')
        assert locked.get(name) == number, pin
    dockerfile = (root/'Dockerfile').read_text(encoding='utf8')
    assert '--require-hashes -r ./analysis-engine-v2/requirements.lock' in dockerfile and '--no-deps ./analysis-engine-v2' in dockerfile
