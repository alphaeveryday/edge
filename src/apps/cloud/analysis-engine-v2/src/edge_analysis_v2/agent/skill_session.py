"""Prepare optional document skills and enforce tool access for one SDK conversation."""
from hashlib import sha256
import json
from pathlib import Path


SKILLS = ('hypothesis-analysis-workflow', 'etf-hypothesis-analysis')
SOURCE = Path(__file__).parents[1] / 'skills'
COMMON_INSTRUCTIONS = ('research.md', 'output-contract.md')


class SkillSession:
    """SDK hooks are tool gates, not an OS sandbox. No model-controlled paths are loaded."""

    def __init__(self, workspace: Path, artifacts: Path, kind: str, analysis_tools: list[str]):
        if kind not in ('outlook', 'movement'):
            raise ValueError('Unknown analysis kind')
        self.workspace = workspace.resolve()
        self.plugin = self.workspace / 'plugin'
        self.artifacts = artifacts
        self.names = ['analysis:' + name for name in SKILLS]
        self.analysis_tools = frozenset(analysis_tools)
        self.documents = {}
        descriptor = self.plugin / '.claude-plugin' / 'plugin.json'
        descriptor.parent.mkdir(parents=True)
        descriptor.write_text(json.dumps({'name':'analysis', 'version':'1.0.0'}), encoding='utf-8')
        for name in SKILLS:
            source = SOURCE / name / 'SKILL.md'
            if source.is_symlink() or not source.resolve().is_relative_to(SOURCE.resolve()):
                raise ValueError('Skill source must stay inside the package')
            content = source.read_bytes()
            if not content.strip():
                raise ValueError('Empty skill document')
            target = self.plugin / 'skills' / name / 'SKILL.md'
            target.parent.mkdir(parents=True)
            target.write_bytes(content)
            self.documents[target] = sha256(content).hexdigest()
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._record()
        agents = self.workspace / 'AGENTS.md'
        content = (Path(__file__).parent / 'workspace' / 'AGENTS.md').read_text(encoding='utf-8')
        agents.write_text(content, encoding='utf-8')
        (self.artifacts / 'AGENTS.md').write_text(content, encoding='utf-8')
        self.readable = frozenset(self.documents) | {agents}
        # The SDK uses Claude, so explicitly include AGENTS.md without enabling project settings.
        shared = [(SOURCE.parent / 'prompts' / name).read_text(encoding='utf-8')
                  for name in COMMON_INSTRUCTIONS]
        self.instruction = '\n\n' + '\n\n'.join([*shared, content])

    def _readable(self, path):
        try:
            return (path in self.readable and not path.is_symlink()
                    and path.resolve() == path and path.is_file())
        except OSError:
            return False

    def _record(self):
        record = {'available':self.names,
                  'documents':[{'path':str(p.relative_to(self.plugin)).replace('\\','/'), 'sha256':digest}
                               for p,digest in self.documents.items()]}
        (self.artifacts / 'skills.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')

    async def before(self, data, tool_use_id, context):
        name, args = data['tool_name'], data['tool_input']
        allow = False
        if name == 'Skill':
            allow = args.get('skill') in self.names
        elif name == 'Read':
            raw = args.get('file_path')
            if isinstance(raw, str):
                path = Path(raw)
                if not path.is_absolute():
                    path = self.workspace / path
                allow = self._readable(path)
        elif name in self.analysis_tools or name == 'StructuredOutput':
            allow = True
        return {'hookSpecificOutput':{'hookEventName':'PreToolUse',
                'permissionDecision':'allow' if allow else 'deny',
                'permissionDecisionReason': 'Document-only worker tool and path policy.'}}
