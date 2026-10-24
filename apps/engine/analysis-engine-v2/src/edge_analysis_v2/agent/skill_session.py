"""Prepare optional document skills and enforce tool access for one SDK conversation."""
from hashlib import sha256
import json
from pathlib import Path


SKILLS = ('cause-investigation', 'revision-investigation', 'hypothesis-analysis-workflow', 'etf-hypothesis-analysis', 'analysis-report-writing', 'focused-analysis-writing')
SOURCE = Path(__file__).parents[1] / 'skills'
BASE = SOURCE.parent / 'prompts' / 'base.md'
ABOUT_SKILLS = SOURCE.parent / 'prompts' / 'skills.md'   # added to the base rules only when a run has skills
METHOD = ('cause-investigation', 'revision-investigation')   # research scope comes before the task


def body(document: str) -> str:
    """A skill document without its YAML frontmatter."""
    return document.split('---', 2)[2].strip() if document.startswith('---') else document.strip()


class SkillSession:
    """SDK hooks are tool gates, not an OS sandbox. No model-controlled paths are loaded."""

    def __init__(self, workspace: Path, artifacts: Path, declared: tuple[str, ...], analysis_tools: list[str],
                 optional: tuple[str, ...] = ()):
        """
        Args:
            declared: Skills this task always uses; their text is given with the task.
            optional: Skills the agent may open; it sees only their names and descriptions until it does.
                A skill in neither list does not exist for the run, so a task's depth is set by its caller.
        """
        if (set(declared) | set(optional)) - set(SKILLS):
            raise ValueError('Unknown skill: ' + ', '.join(sorted((set(declared) | set(optional)) - set(SKILLS))))
        self.declared, self.optional = tuple(declared), tuple(optional)
        self.opened = []
        self.workspace = workspace.resolve()
        self.plugin = self.workspace / 'plugin'
        self.artifacts = artifacts
        self.names = ['analysis:' + name for name in SKILLS if name in self.optional]
        self.analysis_tools = frozenset(analysis_tools)
        self.documents = {}
        descriptor = self.plugin / '.claude-plugin' / 'plugin.json'
        descriptor.parent.mkdir(parents=True)
        descriptor.write_text(json.dumps({'name':'analysis', 'version':'1.0.0'}), encoding='utf-8')
        for name in self.optional:
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
        agents.write_text('', encoding='utf-8')
        self.readable = frozenset(self.documents) | {agents}

    def system_prompt(self, task_prompt: str) -> str:
        """Write the task document (method skills, the task instructions, then the other declared skills) and return the base rules.

        The base rules are the same few lines for every task. Depth lives in skills: a task that needs an
        investigation or a report declares those skills, and a question that needs neither is not handed them.
        When every task carried the investigation and report rules, a one-line question cost an investigation.
        """
        section = lambda name: ('# 이 작업에 쓰는 스킬: ' + name + '\n\n'
                                + body((SOURCE / name / 'SKILL.md').read_text(encoding='utf-8')))
        # How to investigate is read before the task and how to write the result after it, as it was before the
        # rules became skills. With every skill after the task, two of three outlook reports dropped the
        # business-to-earnings chain the investigation rules ask for.
        before = [section(name) for name in self.declared if name in METHOD]
        after = [section(name) for name in self.declared if name not in METHOD]
        content = '\n\n'.join(before + [task_prompt] + after)
        (self.workspace / 'AGENTS.md').write_text(content, encoding='utf-8')
        (self.artifacts / 'AGENTS.md').write_text(content, encoding='utf-8')
        base = BASE.read_text(encoding='utf-8')
        return base + '\n' + ABOUT_SKILLS.read_text(encoding='utf-8') if self.declared or self.optional else base

    def uses(self, name: str) -> bool:
        """True when the task declared this skill or the agent opened it."""
        return name in self.declared or name in self.opened

    def _readable(self, path):
        try:
            return (path in self.readable and not path.is_symlink()
                    and path.resolve() == path and path.is_file())
        except OSError:
            return False

    def _record(self):
        record = {'available':self.names, 'declared':list(self.declared), 'opened':self.opened,
                  'documents':[{'path':str(p.relative_to(self.plugin)).replace('\\','/'), 'sha256':digest}
                               for p,digest in self.documents.items()]}
        (self.artifacts / 'skills.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')

    async def before(self, data, tool_use_id, context):
        name, args = data['tool_name'], data['tool_input']
        allow = False
        if name == 'Skill':
            allow = args.get('skill') in self.names
            opened = str(args.get('skill')).removeprefix('analysis:')
            if allow and opened not in self.opened:
                # Which skills a run actually used was never recorded; now a run shows whether it went deep.
                self.opened.append(opened)
                self._record()
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
