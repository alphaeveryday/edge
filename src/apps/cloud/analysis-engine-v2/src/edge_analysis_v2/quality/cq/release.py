"""Numbered releases of the agent the benchmark runs, so a score always names the code that produced it.

    python -m edge_analysis_v2.quality.cq.release check
    python -m edge_analysis_v2.quality.cq.release bump --summary "what changed and why"
"""
import argparse
import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

HERE = Path(__file__).parent
MANIFEST = HERE/'releases.json'
SOURCE = HERE.parents[1]   # the edge_analysis_v2 package
# What a benchmark run loads: the harness, the tools, the prompts and skills it is given, the tool-run store, and
# of quality/ the runner, its instructions, the questions and the graph session. The rest of the engine (API,
# dashboard, cloud, publication) and the judging and measuring code cannot change an answer, so they do not move
# the release number; otherwise every unrelated merge would.
AGENT = ('agent/', 'tools/', 'prompts/', 'skills/', 'storage/tool_runs.py', 'quality/graph_probe.py',
         'quality/cq/run.py', 'quality/cq/prompt.md', 'quality/cq/cases.json', 'quality/cq/session.py')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')).hexdigest()


def source_digest(root=SOURCE):
    """Hash of the agent's sources with line endings normalised, so a checkout on Windows matches one on Linux."""
    root = Path(root)
    files = {}
    for path in root.rglob('*'):
        name = path.relative_to(root).as_posix()
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py', '.json', '.md', '.yaml', '.yml', '.toml') \
                and name.startswith(AGENT):
            files[name] = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if not files:
        raise ValueError('Cannot identify the agent sources')
    return digest(files)


def release(manifest=MANIFEST):
    """The registered release and its history.

    Raises:
        ValueError: The manifest is malformed or its numbers do not increase.
    """
    value = json.loads(Path(manifest).read_text(encoding='utf8'))
    history = value.get('history', [])
    if type(value.get('version')) is not int or value['version'] < 1 or not str(value.get('summary', '')).strip():
        raise ValueError('Invalid release manifest')
    if not isinstance(history, list) or any(not isinstance(r, dict) or type(r.get('version')) is not int for r in history):
        raise ValueError('Invalid release history')
    numbers = [r['version'] for r in history] + [value['version']]
    if numbers != sorted(set(numbers)):
        raise ValueError('Release history must increase without reuse')
    return value


def check(manifest=MANIFEST, root=SOURCE):
    """The registered release, refused when the sources no longer match it."""
    value = release(manifest)
    if value.get('source_digest') != source_digest(root):
        raise ValueError('Agent sources changed since release ' + str(value['version'])
                         + ': register the change with `python -m edge_analysis_v2.quality.cq.release bump --summary <change>`')
    return value


def bump(summary, manifest=MANIFEST, root=SOURCE):
    """Register the current sources as the next release."""
    if not summary.strip():
        raise ValueError('Describe the agent change')
    manifest = Path(manifest)
    previous = release(manifest) if manifest.exists() else None
    history = previous.get('history', []) + [{k: v for k, v in previous.items() if k != 'history'}] if previous else []
    value = {'version': previous['version'] + 1 if previous else 1, 'summary': summary.strip(),
             'source_digest': source_digest(root), 'history': history}
    manifest.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return value


def snapshot(registered, catalog_digest, model, exposed):
    """Identity of one run's agent: release, graph catalog, model, the tools it could see and the SDK underneath."""
    packages = {}
    for name in ('claude-agent-sdk', 'anthropic'):
        try:
            packages[name] = package_version(name)
        except PackageNotFoundError:
            packages[name] = 'not_installed'
    value = {'release': registered['version'], 'summary': registered['summary'], 'source_digest': registered['source_digest'],
             'catalog_digest': catalog_digest, 'model': model, 'exposed_tools': sorted(exposed),
             'runtime': {'python': platform.python_version(), 'packages': packages}}
    identifier = digest(value)[:20]
    return {**value, 'id': 'agent-' + identifier, 'label': f"v{registered['version']} · {identifier[:8]}"}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check', 'bump'])
    parser.add_argument('--summary', default='')
    args = parser.parse_args()
    try:
        result = bump(args.summary) if args.action == 'bump' else check()
        print(json.dumps({k: result[k] for k in ('version', 'summary', 'source_digest')}, ensure_ascii=False))
    except (ValueError, FileNotFoundError) as error:
        parser.exit(1, str(error) + '\n')
