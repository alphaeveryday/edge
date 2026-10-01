"""Pin document-only skills and enforce tool access for one SDK conversation."""
from hashlib import sha256
import json
from pathlib import Path


SKILLS = ('hypothesis-analysis-workflow', 'etf-hypothesis-analysis')
SOURCE = Path(__file__).parents[1] / 'skills'


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
        self.loaded = set()
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
                raise ValueError('Empty required skill')
            target = self.plugin / 'skills' / name / 'SKILL.md'
            target.parent.mkdir(parents=True)
            target.write_bytes(content)
            self.documents[target] = sha256(content).hexdigest()
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._record()
        self.instruction = (
            '\n\n분석 워커 실행 계약:\n'
            '- 분석 툴을 호출하기 전에 Skill 도구로 다음 두 스킬을 모두 읽는다: '
            + ', '.join(self.names) + '.\n'
            '- 스킬의 조사 규율을 적용하되 최종 출력은 제공된 JSON 스키마와 본문 편집 툴 계약을 따른다. '
            '별도 보고서·분류 필드·[INFERENCE] 라벨은 추가하지 않고 가정과 판단을 자연어로 구분한다.\n'
            '- 이 워커는 제공된 분석 MCP와 문서 스킬만 사용한다. 로컬 스크립트·셸·웹 직접 접속·'
            '다른 스킬·하위 에이전트는 사용하지 않는다. 사용할 수 없는 검증을 실행했다고 쓰지 않는다.\n'
            '- 전망 본문은 소제목 하나에 최대 5불릿. 한 불릿에 여러 요점을 압축하지 말고 나누며, '
            '5개를 넘으면 다른 소제목으로 분리한다. 원인·반론·기간·가정·근거는 보존한다.\n'
            + ('- 이번 작업은 오늘 움직임 설명이다. 스킬의 조사·문장 원칙만 해당 설명 범위에 적용하고 '
               '한 달 전망·상위 5종목 심층 보고서로 범위를 확대하지 않는다.\n' if kind == 'movement' else '')
        )

    def _intact(self, path):
        try:
            return (path in self.documents and not path.is_symlink()
                    and path.resolve() == path
                    and sha256(path.read_bytes()).hexdigest() == self.documents[path])
        except OSError:
            return False

    def _record(self):
        record = {'loaded':sorted(self.loaded), 'required':self.names,
                  'documents':[{'path':str(p.relative_to(self.plugin)).replace('\\','/'), 'sha256':digest}
                               for p,digest in self.documents.items()]}
        (self.artifacts / 'skills.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')

    def require_loaded(self):
        if set(self.names) != self.loaded or not all(self._intact(p) for p in self.documents):
            raise ValueError('Required analysis skills were not successfully loaded or changed')

    async def before(self, data, tool_use_id, context):
        name, args = data['tool_name'], data['tool_input']
        allow = False
        if name == 'Skill':
            allow = args.get('skill') in self.names and all(self._intact(p) for p in self.documents)
        elif name == 'Read':
            raw = args.get('file_path')
            if isinstance(raw, str):
                path = Path(raw)
                if not path.is_absolute():
                    path = self.workspace / path
                allow = self._intact(path)
        elif name in self.analysis_tools or name == 'StructuredOutput':
            try:
                self.require_loaded()
                allow = True
            except ValueError:
                pass
        return {'hookSpecificOutput':{'hookEventName':'PreToolUse',
                'permissionDecision':'allow' if allow else 'deny',
                'permissionDecisionReason': 'Document-only worker policy; load required skills before analysis.'}}

    async def after(self, data, tool_use_id, context):
        if data['tool_name'] == 'Skill' and data['tool_input'].get('skill') in self.names:
            response = data.get('tool_response')
            if isinstance(response, dict) and (response.get('isError') or response.get('success') is False):
                return {}
            self.loaded.add(data['tool_input']['skill'])
            self._record()
        return {}
