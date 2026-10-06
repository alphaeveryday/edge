"""Bounded local job control for real model calls and fixed synthetic scenarios."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
from threading import Lock, Thread
from uuid import uuid4

from edge_analysis_v2.quality.scenarios import CASES, case_spec, make_quality_fixture


SCENARIOS = {'baseline':'기본', 'unusual_flow':'특이 수급', 'competing_signals':'상충 지표',
             'followup':'후속 기사', 'quiet':'변화 없음'}
SCENARIOS.update({f'replay_{day}':f'연속 재생 {day}/5' for day in range(1,6)})
SCENARIOS.update({name:values[0] for name,values in CASES.items()})
ARTIFACTS = ('workspace.json', 'model_input.json', 'compactions.jsonl', 'environment.json', 'measurement.json', 'AGENTS.md', 'skills.json', 'system_prompt.yaml', 'prompt_version.json', 'contract_audit.json', 'case_spec.json', 'verification.json', 'quality_review.md', 'input.json', 'system_prompt.txt', 'events.jsonl', 'raw_response.txt',
             'response.json', 'screen.json', 'factor_details.json', 'tool_schemas.json', 'output_schema.json')


def read_settings(path: Path) -> dict:
    """Read only DeepSeek key and model; never modify the process environment."""
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        name, separator, value = line.strip().partition('=')
        if separator and name in ('DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL'):
            values[name] = value.strip().strip('\"\'')
    key = values.get('DEEPSEEK_API_KEY', '')
    if not key:
        raise ValueError('DEEPSEEK_API_KEY is required')
    return {'key':key, 'model':values.get('DEEPSEEK_MODEL') or 'deepseek-flash'}


def scenario_cutoff(kind: str, scenario: str) -> str:
    """Choose a fixed historical cutoff for repeatable intraday/daily checks."""
    if scenario in CASES:
        return '2026-09-21T08:30:00+09:00' if kind == 'outlook' else '2026-09-21T10:00:00+09:00'
    index = int(scenario[-1])-1 if scenario.startswith('replay_') else list(SCENARIOS).index(scenario)
    return f'2026-09-{14+index:02d}T08:30:00+09:00' if kind == 'outlook' else f'2026-09-14T{10+index:02d}:00:00+09:00'


class ExecutionDashboard:
    """Run one fixed request at a time and expose only allowlisted artifacts.

    Args:
        runs_dir: Server-selected local artifact directory.
        key: API credential kept on the server.
        model: Server-configured model identifier.
        connection_factory: Creates a fresh result database connection.
        runner: Optional injected execution function for offline tests.
        fixture_factory: Optional synthetic source factory for offline tests.
    """

    def __init__(self, runs_dir, *, key, model, connection_factory, runner=None, fixture_factory=None, auditor=None, prompt_versions=None):
        self.runs_dir = Path(runs_dir).resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.key, self.model = key, model
        self.auditor = auditor
        self.prompt_versions = prompt_versions
        self.connection_factory, self.runner, self.fixture_factory = connection_factory, runner, fixture_factory
        self.csrf_token = secrets.token_urlsafe(32)
        self.lock, self.worker = Lock(), None
        for directory in self.runs_dir.iterdir():
            if directory.is_dir() and re.fullmatch('[a-f0-9]{32}', directory.name):
                path = directory / 'job.json'
                if path.exists():
                    job = json.loads(path.read_text(encoding='utf-8'))
                    if job.get('status') == 'running' and job.get('origin') != 'cloud':
                        self._save(job | {'status':'interrupted', 'error':'검수 서버가 재시작되어 실행이 중단됐습니다.'})

    def _save(self, job):
        folder = self.runs_dir / job['analysis_id']
        folder.mkdir(exist_ok=True)
        temporary = folder / 'job.tmp'
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(folder / 'job.json')

    def start(self, body: dict) -> dict:
        """Validate a fixed request and start it without accepting paths or code."""
        if (not isinstance(body, dict) or not {'kind','scenario'} <= set(body) or not set(body) <= {'kind','scenario'}
                or body['kind'] not in ('movement','outlook') or body['scenario'] not in SCENARIOS):
            raise ValueError('Choose a supported kind and scenario')
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise ValueError('An analysis is already running')
            previous_id = None
            if body['scenario'].startswith('replay_') and body['scenario'] != 'replay_1':
                preceding = 'replay_' + str(int(body['scenario'][-1])-1)
                previous = next((job for job in self.jobs() if job['kind']==body['kind']
                                 and job['scenario']==preceding and job['status']=='completed'), None)
                if previous is None:
                    raise ValueError('Complete the preceding replay step first')
                previous_id = previous['analysis_id']
            job = body | {'analysis_id':uuid4().hex, 'status':'running', 'previous_analysis_id':previous_id,
                          'analysis_at':scenario_cutoff(body['kind'],body['scenario']),
                          'started_at':datetime.now(timezone.utc).isoformat()}
            snapshot = self.prompt_versions.read(body['kind']) if self.prompt_versions else None
            if snapshot:
                job['prompt_version'] = snapshot['version']
            self._save(job)
            if snapshot:
                folder = self.runs_dir/job['analysis_id']
                (folder/'system_prompt.yaml').write_text(snapshot['yaml'], encoding='utf-8')
                (folder/'prompt_version.json').write_text(json.dumps({k:snapshot[k] for k in ('kind','version','source')}, ensure_ascii=False, indent=2), encoding='utf-8')
            self.worker = Thread(target=self._execute, args=(job,snapshot), daemon=True)
            self.worker.start()
            return dict(job)

    def _execute(self, job, snapshot=None):
        try:
            runner = self.runner
            if runner is None:
                from edge_analysis_v2.analysis.service import execute_request
                runner = execute_request
            factory = self.fixture_factory
            if job['scenario'] in CASES and factory is None:
                factory = make_quality_fixture
                (self.runs_dir/job['analysis_id']/'case_spec.json').write_text(json.dumps(case_spec(job['scenario']),ensure_ascii=False,indent=2),encoding='utf-8')
            if factory is None:
                from edge_analysis_v2.tools.fixture_data import make_fixture, make_replay_fixture
                fixture = make_replay_fixture(job['analysis_at']) if job['scenario'].startswith('replay_') else make_fixture(job['scenario'], job['analysis_at'])
            else:
                fixture = factory(job['scenario'], analysis_at=job['analysis_at'])
            screen = runner(kind=job['kind'], fixture=fixture,
                   connection_factory=self.connection_factory, key=self.key,
                   artifacts=self.runs_dir/job['analysis_id'], analysis_id=job['analysis_id'], model=self.model,
                   previous_analysis_id=job['previous_analysis_id'],
                   **({'system_prompt':snapshot['system_prompt']} if snapshot else {}))
            if job['scenario'] in CASES and self.runner is None:
                from edge_analysis_v2.quality.audit import verify_execution
                verification = verify_execution(self.connection_factory, fixture, job['kind'], job['analysis_id'], screen, self.runs_dir/job['analysis_id'])
                job = job | {'verification_status':verification['mechanical_status'], 'quality_status':'pending_review'}
            job = job | {'status':'completed'}
        except Exception as exc:
            detail = str(exc)[:1500].replace(self.key,'[redacted]') if isinstance(exc, ValueError) else '실행 실패. 아래 모델 기록과 DB 상태를 확인하세요.'
            job = job | {'status':'failed', 'error':type(exc).__name__ + ': ' + detail}
        # Audit failures must never turn a successful execution into a model retry.
        job['contract_status'] = 'not_checked'
        try:
            report = self.auditor(job['kind'], job['analysis_id']) if self.auditor else None
            if report is not None:
                (self.runs_dir/job['analysis_id']/'contract_audit.json').write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                job['contract_status'] = report['status']
            else:
                job['contract_reason'] = '검사 가능한 DB 발행본 또는 검사 설정이 없습니다.'
        except Exception:
            job['contract_reason'] = '계약 검사 조회 실패. 계약 검사 탭에서 다시 검사하세요.'
        self._save(job | {'finished_at':datetime.now(timezone.utc).isoformat()})

    def jobs(self) -> list:
        """List persisted server-created jobs, including interrupted executions."""
        result = []
        for path in self.runs_dir.glob('*/job.json'):
            if re.fullmatch('[a-f0-9]{32}', path.parent.name):
                result.append(json.loads(path.read_text(encoding='utf-8')))
        return sorted(result, key=lambda job:job['started_at'], reverse=True)

    def detail(self, identity: str) -> dict | None:
        """Read fixed artifact names as text, preserving partial streams and numbers."""
        if not re.fullmatch('[a-f0-9]{32}', identity):
            return None
        folder = self.runs_dir/identity
        if not (folder/'job.json').is_file():
            return None
        artifacts = {}
        for name in ARTIFACTS:
            path = folder/name
            if path.is_file():
                artifacts[name] = path.read_text(encoding='utf-8').replace(self.key,'[redacted]')
        return {'job':json.loads((folder/'job.json').read_text(encoding='utf-8')), 'artifacts':artifacts}
