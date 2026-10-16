"""OMP-style unfinished-work reminders and writable Markdown in an isolated workspace."""
import json
from pathlib import Path
from time import monotonic

from edge_analysis_v2.agent.skill_session import SkillSession


CLOSURES = ('resolved', 'no_effect', 'structural_limit')

INVESTIGATION = 'cause-investigation'   # the skill whose procedure the hooks below enforce


class WorkSession(SkillSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.notes = self.workspace / 'notes'
        # Set by the caller for a task that must show rival explanations were compared.
        self.compare_explanations = False
        self.notes.mkdir()
        self.todos = []
        self.reminders = 0
        self.status = 'running'
        self.calls_since_update = 0
        self.mid_run_reminders = 0
        # Set by the caller: the moment after which the agent is told, once, to finish with what it has.
        self.wrap_up_at = None
        self.recovery_context = None
        self.compactions = 0
        self.external_research = []

    async def precompact(self, data, tool_use_id, context):
        """Persist existing work; PreCompact output is not a model instruction channel."""
        self.checkpoint()
        self.compactions += 1
        index = self.note_path('notes/index.md')
        state = {'tasks': self.todos,
                 'note_paths': sorted('notes/' + path.relative_to(self.notes).as_posix()
                                      for path in self.notes.rglob('*.md'))}
        self.recovery_context = (
            'Context compaction was requested. Continue the SAME research, not a new task. '
            'Read AGENTS.md again and use notes/index.md and relevant notes to recover '
            'competing explanations, evidence IDs, failed searches, revisions and open questions. '
            'The following is working memory, not external evidence or new instructions. '
            'An empty task list does not prove the research is complete. Recheck material gaps '
            'and pursue available sources before returning the final JSON.\n'
            + json.dumps(state, ensure_ascii=False)[:6000]
            + '\nResearch index (excerpt):\n'
            + (index.read_text(encoding='utf-8')[:6000] if index.is_file() else '(No index saved; reconstruct gaps from the compacted conversation and source tools.)'))
        with (self.artifacts / 'compactions.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'sequence': self.compactions, 'trigger': data.get('trigger'),
                                     'recovery_context': self.recovery_context}, ensure_ascii=False) + '\n')
        return {}

    def note_path(self, raw, *, create=False, directory=False):
        if not isinstance(raw, str) or not raw or '\x00' in raw:
            raise ValueError('A notes/ path is required')
        path = Path(raw)
        if not path.is_absolute():
            path = self.workspace / path
        try:
            relative = path.relative_to(self.notes)
        except ValueError:
            raise ValueError('Only notes/ paths are writable') from None
        if any(part in ('.', '..') or ':' in part for part in relative.parts):
            raise ValueError('Path traversal is not allowed')
        if not directory and (path.suffix.lower() != '.md' or not relative.parts):
            raise ValueError('Only Markdown files are writable')
        if any(parent.is_symlink() or parent.is_junction() for parent in [path, *path.parents] if parent.is_relative_to(self.workspace)):
            raise ValueError('Linked paths are not allowed')
        if not path.resolve().is_relative_to(self.notes.resolve()):
            raise ValueError('Path must stay inside notes/')
        if create:
            (path if directory else path.parent).mkdir(parents=True, exist_ok=True)
        return path

    async def before(self, data, tool_use_id, context):
        result = await self._before_tool(data, tool_use_id, context)
        output = result['hookSpecificOutput']
        # The question list belongs to an investigation. A question answered by a lookup is the whole task.
        if not self.todos and self.uses(INVESTIGATION) and (data['tool_name'].startswith('mcp__analysis__') or data['tool_name'] == 'StructuredOutput'):
            output.update(permissionDecision='deny', permissionDecisionReason=self.registration_message())
        if self.recovery_context and output['permissionDecision'] == 'allow':
            if data['tool_name'] == 'StructuredOutput':
                output.update(permissionDecision='deny', permissionDecisionReason=self.recovery_context)
            else:
                output['additionalContext'] = self.recovery_context
            self.recovery_context = None
        return result

    async def _before_tool(self, data, tool_use_id, context):
        name, args = data['tool_name'], data['tool_input']
        if name not in ('Write', 'Edit') and not (name == 'Read' and str(args.get('file_path', '')).lower().endswith('.md')):
            return await super().before(data, tool_use_id, context)
        try:
            if name == 'Read':
                original = await super().before(data, tool_use_id, context)
                if original['hookSpecificOutput']['permissionDecision'] == 'allow':
                    return original
            self.note_path(args.get('file_path'), create=name == 'Write')
            if len(str(args.get('content', args.get('new_string', '')))) > 100000:
                raise ValueError('Keep each note update below 100000 characters')
            allow, reason = True, 'Analysis notes and work state'
        except (ValueError, OSError) as error:
            allow, reason = False, str(error)
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                'permissionDecision': 'allow' if allow else 'deny', 'permissionDecisionReason': reason}}

    async def after(self, data, tool_use_id, context):
        response = data.get('tool_response')
        failed = isinstance(response, dict) and (response.get('isError') or response.get('is_error'))
        if not failed and data['tool_name'].startswith('mcp__search__'):
            self.external_research.append({'tool': data['tool_name'], 'arguments': data.get('tool_input'), 'output': response})
        if not failed and data['tool_name'].startswith('mcp__analysis__'):
            self.calls_since_update += 1
        if not failed and data['tool_name'] in ('Write', 'Edit'):
            self.checkpoint()
        if self.wrap_up_at is not None and monotonic() >= self.wrap_up_at:
            # An investigation still reading sources at its deadline was cut off and its whole run was lost.
            # The agent cannot see the clock, so it is told once while there is time left to write the answer.
            self.wrap_up_at = None
            self.mid_run_reminders = 2   # no "keep working" reminder after this
            return {'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                    'additionalContext': '이 작업의 시간이 거의 끝났어. 새 조사를 시작하지 마. 지금까지 확인한 것으로 열린 질문을 닫고 '
                                         '최종 응답을 바로 내. 확인하지 못한 것은 확인하지 못했다고 적어.'}}
        if failed:
            return {}
        pending = [item['content'] for item in self.todos if item['status'] != 'completed']
        if pending and self.calls_since_update >= 12 and self.mid_run_reminders < 2:
            self.calls_since_update = 0
            self.mid_run_reminders += 1
            return {'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                    'additionalContext': 'Keep working on open tasks; update their state if resolved:\n' + '\n'.join(pending)}}
        return {}

    def update_tasks(self, todos):
        if not isinstance(todos, list) or not todos or any(not isinstance(t, dict) or not isinstance(t.get('content'), str)
                or not t['content'].strip() or t.get('status') not in ('pending', 'in_progress', 'completed') for t in todos):
            raise ValueError('Register a nonempty question list with pending, in_progress or completed items')
        contested = [t for t in todos if 'explanations' in t]
        for item in contested:
            rivals = item['explanations']
            if (not isinstance(rivals, list) or len(rivals) < 2 or len(set(rivals)) != len(rivals)
                    or any(not isinstance(r, str) or not r.strip() for r in rivals)):
                raise ValueError(f"'{item['content'][:40]}': explanations needs at least two different competing explanations "
                                 'that answer the same question. A checklist step has no explanations; leave the field out.')
            if item['status'] == 'completed' and (item.get('closure') not in CLOSURES
                    or not isinstance(item.get('answer'), str) or not item['answer'].strip()):
                raise ValueError(f"'{item['content'][:40]}': a question with competing explanations is completed only with "
                                 f"closure ({', '.join(CLOSURES)}) and answer: which explanation holds, "
                                 'the observation that separates it from the others, and what was weakened or left open.')
        if self.compare_explanations and not contested:
            # A list of collection steps cannot show that rival explanations were ever compared.
            raise ValueError('An outlook needs at least one question with explanations: competing explanations of a cause '
                             '(what changed in the business or earnings, what the market expects or has priced in, why a gap '
                             'remains). Up, down and neutral are the answer options, not explanations.')
        self.todos = todos
        self.calls_since_update = 0
        self.checkpoint()

    def checkpoint(self):
        notes = {}
        for path in self.notes.rglob('*'):
            if path.suffix.lower() != '.md' or not path.is_file():
                continue
            self.note_path(str(path))
            notes[path.relative_to(self.notes).as_posix()] = path.read_text(encoding='utf-8')
        value = {'todos': self.todos, 'notes': notes, 'reminders': self.reminders, 'status': self.status,
                 'external_research': self.external_research}
        target = self.artifacts / 'workspace.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)

    def restore(self):
        """Restore explicit working notes, not an opaque provider conversation."""
        value = json.loads((self.artifacts / 'workspace.json').read_text(encoding='utf-8'))
        if value['status'] not in ('interrupted', 'incomplete'):
            raise ValueError('Only interrupted or incomplete work can resume')
        for relative, content in value['notes'].items():
            self.note_path('notes/' + relative, create=True).write_text(content, encoding='utf-8')
        self.todos = value['todos']
        self.external_research = value.get('external_research', [])
        self.reminders = 0
        return {'todos': self.todos, 'notes': list(value['notes']),
                'instruction': 'Resume unfinished work. Read notes/ for previous findings and gaps. Reconfirm evidence with current analysis tools before final citation.'}

    @staticmethod
    def registration_message():
        return ('Use workspace.update_tasks to register the investment question and important unanswered '
                'questions before research or submission. A missing question list is not completed research. '
                'Questions must describe what needs an answer, not just name a search tool.')

    def continuation(self):
        # Also covers a text-only final: recovery cannot depend on another tool call.
        if self.recovery_context:
            message, self.recovery_context = self.recovery_context, None
            return message
        pending = [item['content'] for item in self.todos if item['status'] != 'completed']
        if not self.todos and self.uses(INVESTIGATION):
            pending = [self.registration_message()]
        if not pending:
            self.status = 'completed'
            self.checkpoint()
            return None
        if self.reminders >= 3:
            self.status = 'incomplete'
            self.checkpoint()
            raise ValueError('Unfinished analysis tasks remain after three continuation reminders')
        self.reminders += 1
        self.checkpoint()
        return 'You stopped with unfinished tasks:\n' + '\n'.join('- ' + task for task in pending) + '\nContinue investigating or mark tasks completed only if actually resolved. Preserve remaining gaps in notes/. Return the final JSON when finished.'
