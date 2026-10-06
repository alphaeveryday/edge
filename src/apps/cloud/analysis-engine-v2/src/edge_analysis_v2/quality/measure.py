"""Record where an analysis ran and what it cost, from the artifacts every run already leaves.

``describe_environment`` is written at the start of a run; ``measure`` reads the finished folder.
Both exist so that a local container run and a Fargate run of the same image can be compared.
"""
import argparse
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

PACKAGES = ('analysis-engine-v2', 'claude-agent-sdk', 'psycopg', 'jsonschema', 'PyYAML', 'boto3', 'neo4j')


def _cli():
    """The agent loop runs in this executable; a different version is a different harness."""
    import claude_agent_sdk
    bundled = Path(claude_agent_sdk.__file__).parent/'_bundled'
    found = next((p for p in sorted(bundled.glob('claude*')) if p.is_file()), None) if bundled.is_dir() else None
    path = str(found) if found else shutil.which('claude')
    if not path:
        return {'path': None, 'version': None, 'bundled': False}
    try:
        text = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        text = None
    return {'path': path, 'version': text, 'bundled': found is not None}


def describe_environment():
    """Return the facts that must match between two runs before their timings are compared."""
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {'python': platform.python_version(), 'platform': platform.platform(), 'cpu_count': os.cpu_count(),
            'packages': packages, 'agent_cli': _cli(),
            # Set by the image build and by ECS; absent outside a container.
            'image_revision': os.environ.get('IMAGE_REVISION'), 'ecs_metadata': bool(os.environ.get('ECS_CONTAINER_METADATA_URI_V4'))}


def measure(folder):
    """Summarize one finished run folder.

    Args:
        folder: Artifact folder containing events.jsonl written by the runner.

    Returns:
        Wall time, model turns, token usage and per-tool call counts and seconds. Tool seconds are
        the time between the model's request and the result it received, including audit commits.
    """
    events = [json.loads(line) for line in (Path(folder)/'events.jsonl').read_text(encoding='utf8').splitlines()]
    stamped = [e for e in events if e.get('at')]
    pending, tools, turns, usage, rounds = {}, {}, 0, {}, []
    for event in events:
        content = event['message'].get('content')
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if event['message_type'] == 'AssistantMessage':
            turns += 1
        for block in blocks:
            if block.get('name') and block.get('id'):
                pending[block['id']] = (block['name'].replace('mcp__analysis__', ''), event.get('at'))
            elif block.get('tool_use_id') in pending:
                name, started = pending.pop(block['tool_use_id'])
                row = tools.setdefault(name, {'calls': 0, 'errors': 0, 'seconds': 0.0})
                row['calls'] += 1
                row['errors'] += bool(block.get('is_error'))
                if started and event.get('at'):
                    row['seconds'] = round(row['seconds'] + (datetime.fromisoformat(event['at']) - datetime.fromisoformat(started)).total_seconds(), 3)
        if event['message_type'] == 'ResultMessage':
            # A run that was reminded of unfinished tasks has one result per round; usage is reported per round.
            rounds.append(event['message'])
            for key, value in (event['message'].get('usage') or {}).items():
                if key.endswith('tokens') and type(value) is int:
                    usage[key] = usage.get(key, 0) + value
    wall = (datetime.fromisoformat(stamped[-1]['at']) - datetime.fromisoformat(stamped[0]['at'])).total_seconds() if len(stamped) > 1 else None
    total = lambda key: sum(r.get(key) or 0 for r in rounds) if rounds else None
    return {'wall_seconds': wall, 'model_turns': turns, 'rounds': len(rounds), 'sdk_turns': total('num_turns'),
            'sdk_duration_ms': total('duration_ms'), 'sdk_api_ms': total('duration_api_ms'), 'usage': usage, 'tool_calls': sum(t['calls'] for t in tools.values()),
            'tool_seconds': round(sum(t['seconds'] for t in tools.values()), 3), 'tools': dict(sorted(tools.items())),
            'unanswered_tool_requests': len(pending)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    folder = parser.parse_args().folder
    value = measure(folder)
    (folder/'measurement.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(value, ensure_ascii=False))
