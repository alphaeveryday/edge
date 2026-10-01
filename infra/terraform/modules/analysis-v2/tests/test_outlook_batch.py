"""전망 배치 정의(outlook_batch.asl.json)의 실행 계약을 AWS TestState 로 검증한다.

상태 전이·내장 함수·Retry/Catch 는 **AWS 의 실제 해석기**가 평가한다(TestState API). 대역은 서비스 호출 셋뿐이다 —
v2 상태 API(apigateway:invoke), 실행 중 목록(sfn:listExecutions), 단건 워크플로(startExecution.sync). 그래서 이 테스트는
IAM 권한·실제 API 응답 형식·부모 중단 시 자식 정리를 말하지 않는다(dev 검증 몫 — tests/loadtest/analysis-v2/README.md).

AWS 자원을 만들거나 실행하지 않는다. states:TestState 권한이 있는 자격증명이 필요해 CI 에서는 돌지 않는다.

    AWS_PROFILE=edge uv run --with boto3 python -m unittest discover -s infra/terraform/modules/analysis-v2/tests -v
"""
import hashlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
    CLIENT = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'ap-northeast-2'),
                          config=Config(retries={'mode': 'adaptive', 'max_attempts': 10}))
    CLIENT.test_state(definition='{"Type":"Pass","End":true}', input='{}')
    UNAVAILABLE = None
except Exception as exc:  # 자격증명·권한·boto3 부재 — 조용히 통과로 읽히지 않게 skip 사유로 남긴다
    UNAVAILABLE = f'{type(exc).__name__}: {exc}'

TEMPLATE = Path(__file__).resolve().parents[1]/'outlook_batch.asl.json'
SINGLE = 'arn:aws:states:ap-northeast-2:000000000000:stateMachine:single'
ETFS = ['069500', '396500']
AT = '2026-10-01T18:00:00Z'  # = 2026-10-02 03:00 KST
FUTURE = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
PAST = '2000-01-01T00:00:00Z'


def render(**overrides):
    values = {'defaults_json': json.dumps({'etf_codes': ETFS, 'max_attempts': 2}), 'deadline_utc': '23:00:00Z',
              'api_endpoint': 'abc123.execute-api.ap-northeast-2.amazonaws.com',
              'analysis_state_machine_arn': SINGLE} | overrides
    text = TEMPLATE.read_text(encoding='utf-8')
    for key, value in values.items():
        text = text.replace('${' + key + '}', value)
    assert '${' not in text, 'templatefile 자리표시자가 남았다'
    return json.loads(text)


def identity(etf, attempt, at=AT):
    return hashlib.md5(f'outlook:{etf}:{at}:{attempt}'.encode()).hexdigest()


class World:
    """v2 대역 — 분석 ID 별 저장 상태와 단건 워크플로의 결과.

    plan: (etf, attempt) → 'ok' | 'fail' | 'saved_then_failed' | 'late' | 'never_started'. 없으면 'ok'.
    busy: Gate 호출마다 앞에서 꺼내 쓰는 '다른 v2 실행 수'. 다 쓰면 0.
    foreign: 분석 ID → 상태 조회마다 앞에서 꺼내 쓰는 상태열(다른 실행이 같은 ID 를 돌리는 중).
    status_down: 상태 API 가 계속 503.
    """

    def __init__(self, plan=None, busy=(), foreign=None, status_down=False):
        self.plan, self.busy, self.foreign = plan or {}, list(busy), foreign or {}
        self.status_down = status_down
        self.status, self.published, self.runs, self.trace = {}, {}, [], []

    def mock(self, state, data):
        self.trace.append(state)
        ident = data.get('analysis_id')
        if state == 'ReadStatus':
            if self.status_down:
                return {'errorOutput': {'error': 'ApiGateway.503', 'cause': 'down'}}
            if self.foreign.get(ident):
                value = self.foreign[ident].pop(0)
                if value == 'completed':
                    self.status[ident], self.published[ident] = 'completed', datetime.now(timezone.utc).isoformat()
                return ok({'ResponseBody': {'status': value}, 'StatusCode': 200})
            if ident not in self.status:
                return {'errorOutput': {'error': 'ApiGateway.404', 'cause': 'NOT_FOUND'}}
            return ok({'ResponseBody': {'status': self.status[ident]}, 'StatusCode': 200})
        if state == 'Gate':
            return ok({'Executions': [{'Name': 'other'}] * (self.busy.pop(0) if self.busy else 0)})
        if state == 'Run':
            behaviour = self.plan.get((data['etf_code'], data['attempt']), 'ok')
            self.runs.append((data['etf_code'], data['attempt'], ident))
            if behaviour == 'never_started':
                return {'errorOutput': {'error': 'StepFunctions.ExecutionLimitExceededException', 'cause': 'x'}}
            if behaviour == 'fail':
                self.status[ident] = 'failed'
                return {'errorOutput': {'error': 'States.TaskFailed', 'cause': 'AnalysisFailed'}}
            self.status[ident] = 'completed'
            self.published[ident] = (datetime.now(timezone.utc) + timedelta(days=2 if behaviour == 'late' else 0)).isoformat()
            if behaviour == 'saved_then_failed':  # DB 저장 뒤 관측 내보내기 실패 → 종료 코드 1
                return {'errorOutput': {'error': 'States.TaskFailed', 'cause': 'AnalysisFailed'}}
            return ok({'Status': 'SUCCEEDED'})
        if state == 'ReadPublication':
            return ok({'ResponseBody': {'publication': {'published_at': self.published[ident]}}, 'StatusCode': 200})
        raise AssertionError('대역이 없는 Task: ' + state)


def ok(result):
    return {'result': json.dumps(result), 'fieldValidationMode': 'NONE'}


def step(definition, state, data, world=None):
    """한 상태를 AWS 해석기로 평가한다. Retry 대상 오류는 소진될 때까지 같은 상태를 다시 부른다."""
    retried = 0
    while True:
        args = dict(definition=json.dumps(definition), stateName=state, input=json.dumps(data), inspectionLevel='DEBUG')
        if definition['States'][state]['Type'] == 'Task':
            args['mock'] = world.mock(state, data)
            if retried:
                args['stateConfiguration'] = {'retrierRetryCount': retried}
        result = CLIENT.test_state(**args)
        if result['status'] != 'RETRIABLE':
            return result
        retried += 1


def run_item(processor, item, world):
    state, data = processor['StartAt'], item
    for _ in range(200):
        result = step(processor, state, data, world)
        if state == 'Run':  # 단건 워크플로에 넘기는 요청 계약(추가 키는 워커의 요청 검증에서 거절된다)
            sent = json.loads(result['inspectionData']['afterParameters'])
            assert sent['Name'] == data['analysis_id'] and sent['StateMachineArn'] == SINGLE, sent
            assert sent['Input'] == {'analysis_id': data['analysis_id'], 'kind': 'outlook',
                                     'etf_code': data['etf_code'], 'analysis_at': data['analysis_at']}, sent
        assert result['status'] in ('SUCCEEDED', 'CAUGHT_ERROR'), (state, result)
        data = json.loads(result['output'])
        if 'nextState' not in result:
            return data
        state = result['nextState']
    raise AssertionError('항목 처리가 끝나지 않는다: ' + state)


def processor_of(definition):
    """Map 항목 처리기를 독립 정의로 떼어 낸다(TestState 는 최상위 정의의 상태만 고른다)."""
    inner = definition['States']['RunItems']['ItemProcessor']
    return {'StartAt': inner['StartAt'], 'States': inner['States']}


def run_batch(world, batch_input, definition=None):
    """배치 전체를 걷는다. 반환: (종료 상태 이름, 마지막 출력 또는 Fail 의 cause)."""
    definition = definition or render()
    processor = processor_of(definition)
    state, data = definition['StartAt'], batch_input
    for _ in range(50):
        kind = definition['States'][state]['Type']
        if kind == 'Map':
            placeholder = CLIENT.test_state(definition=json.dumps(definition), stateName=state, input=json.dumps(data),
                                            inspectionLevel='DEBUG', mock=ok([{}] * len(data['etf_codes'])))  # 자리표시 — 항목 입력만 얻는다
            items = json.loads(placeholder['inspectionData']['afterItemSelector'])
            outputs = [run_item(processor, item, world) for item in items]  # MaxConcurrency 1 = 순차
            result = CLIENT.test_state(definition=json.dumps(definition), stateName=state, input=json.dumps(data), mock=ok(outputs))
        else:
            result = step(definition, state, data)
        if kind == 'Fail':
            return state, {'error': result['error'], 'cause': json.loads(result['cause']) if result['cause'].startswith('{') else result['cause']}
        data = json.loads(result['output'])
        if kind == 'Succeed':
            return state, data
        state = result['nextState']
    raise AssertionError('배치가 끝나지 않는다')


@unittest.skipIf(UNAVAILABLE, f'TestState 를 호출할 수 없다 — {UNAVAILABLE}')
class OutlookBatchContract(unittest.TestCase):
    def batch(self, world, **extra):
        return run_batch(world, {'analysis_at': AT, 'deadline': FUTURE} | extra)

    def test_normal_run_keeps_request_identity_and_reads_back_publication(self):
        """종류·ETF·기준시각이 그대로 단건 요청이 되고, 완료는 저장된 발행본으로 확인한다."""
        world = World()
        state, out = self.batch(world)
        self.assertEqual(state, 'Done')
        self.assertEqual(world.runs, [(etf, 0, identity(etf, 0)) for etf in ETFS])  # ID = 시도 0 의 결정적 MD5(32 hex)
        self.assertEqual([i['outcome'] for i in out['items']], ['completed', 'completed'])
        self.assertTrue(all(i['published_at'] == world.published[i['analysis_id']] for i in out['items']))
        self.assertEqual((out['summary']['total'], out['summary']['completed'], out['summary']['unfinished']), (2, 2, []))

    def test_duplicate_batch_reuses_completed_without_paying_again(self):
        """같은 기준시각의 배치가 다시 떠도(스케줄러 재전달·수동 재실행) 완료분은 다시 계산하지 않는다."""
        world = World()
        self.batch(world)
        state, out = self.batch(world)
        self.assertEqual((state, len(world.runs)), ('Done', 2))
        self.assertEqual([i['attempt'] for i in out['items']], [0, 0])

    def test_other_reference_time_same_day_is_a_different_job(self):
        """같은 날 다른 기준시각은 다른 작업이다 — 날짜별 최신 발행본이 있다고 건너뛰지 않는다."""
        world = World()
        self.batch(world)
        later = '2026-10-01T21:00:00Z'
        state, _ = run_batch(world, {'analysis_at': later, 'deadline': FUTURE})
        self.assertEqual(state, 'Done')
        self.assertEqual(world.runs[2:], [(etf, 0, identity(etf, 0, later)) for etf in ETFS])

    def test_only_failed_item_is_retried_with_a_new_id(self):
        """실패한 항목만 새 ID 로 한 번 더 돈다. 성공 항목은 그대로 재사용한다."""
        world = World(plan={('396500', 0): 'fail'})
        state, out = self.batch(world)
        self.assertEqual(state, 'Done')
        self.assertEqual([(etf, attempt) for etf, attempt, _ in world.runs], [('069500', 0), ('396500', 0), ('396500', 1)])
        self.assertEqual(len({ident for *_, ident in world.runs}), 3)  # 실패한 ID 를 다시 쓰지 않는다
        self.assertEqual(out['items'][1]['attempt'], 1)

    def test_exhausted_retries_fail_the_batch_and_rerun_resumes_from_next_attempt(self):
        """재시도는 항목당 1회. 소진되면 배치가 실패로 끝나고, 상한을 올린 재실행은 실패 항목의 다음 시도만 돈다."""
        world = World(plan={('396500', 0): 'fail', ('396500', 1): 'fail'})
        state, out = self.batch(world)
        self.assertEqual((state, out['error']), ('Incomplete', 'OutlookBatch.Incomplete'))
        self.assertEqual((out['cause']['completed'], out['cause']['failed']), (1, 1))
        self.assertEqual(out['cause']['unfinished'], [{'etf_code': '396500', 'outcome': 'failed',
                                                       'last_analysis_id': identity('396500', 1), 'attempts': 2}])
        state, _ = self.batch(world, max_attempts=3)
        self.assertEqual(state, 'Done')
        self.assertEqual([(etf, attempt) for etf, attempt, _ in world.runs[3:]], [('396500', 2)])

    def test_saved_but_exit_failed_attempt_counts_by_its_own_stored_result(self):
        """DB 저장 뒤 관측 내보내기만 실패한 시도는 그 시도 ID 의 저장 상태로 완료 판정한다."""
        world = World(plan={('069500', 0): 'saved_then_failed'})
        state, out = self.batch(world)
        self.assertEqual((state, len(world.runs)), ('Done', 2))
        self.assertEqual(out['items'][0], {'etf_code': '069500', 'outcome': 'completed', 'attempt': 0,
                                           'analysis_id': identity('069500', 0), 'published_at': world.published[identity('069500', 0)]})

    def test_launch_failure_without_execution_moves_to_next_attempt(self):
        """실행이 만들어지지도 못한 시도는 같은 ID 를 붙잡고 돌지 않고 다음 시도로 넘어간다."""
        world = World(plan={('069500', 0): 'never_started'})
        state, out = self.batch(world)
        self.assertEqual((state, out['items'][0]['attempt']), ('Done', 1))

    def test_waits_while_another_v2_execution_is_running(self):
        """다른 v2 실행(다른 배치·수동 시작)이 도는 동안에는 시작하지 않는다 — 총 동시 1건."""
        world = World(busy=[1, 1, 0])
        state, _ = run_batch(world, {'analysis_at': AT, 'deadline': FUTURE, 'etf_codes': ['069500']})
        self.assertEqual(state, 'Done')
        self.assertEqual(world.trace, ['ReadStatus', 'Gate', 'Gate', 'Gate', 'Run', 'ReadStatus', 'ReadPublication'])

    def test_same_attempt_running_elsewhere_is_awaited_not_started_twice(self):
        """같은 작업을 다른 배치가 이미 돌리고 있으면 끝날 때까지 기다렸다가 그 결과를 쓴다."""
        ident = identity('069500', 0)
        world = World(foreign={ident: ['queued', 'running', 'completed']})
        state, out = run_batch(world, {'analysis_at': AT, 'deadline': FUTURE, 'etf_codes': ['069500']})
        self.assertEqual((state, world.runs, out['items'][0]['analysis_id']), ('Done', [], ident))

    def test_start_after_absolute_deadline_runs_nothing_and_fails(self):
        """마감은 절대 시각이다. 늦게 시작한 배치는 유료 분석을 시작하지 않고 마감 초과로 실패한다."""
        world = World()
        state, out = run_batch(world, {'analysis_at': AT, 'deadline': PAST})
        self.assertEqual((state, out['error'], world.runs), ('MissedDeadline', 'OutlookBatch.DeadlineExceeded', []))
        self.assertEqual((out['cause']['deadline_exceeded'], out['cause']['completed']), (2, 0))

    def test_deadline_while_waiting_for_a_slot_stops_waiting(self):
        world = World(busy=[1] * 50)
        processor = processor_of(render())
        item = {'etf_code': '069500', 'analysis_at': AT, 'deadline': PAST, 'max_attempts': 2, 'attempt': 0,
                'ran': False, 'analysis_id': identity('069500', 0)}
        self.assertEqual(step(processor, 'WaitForSlot', item)['nextState'], 'StillTimeToWait')
        self.assertEqual(step(processor, 'StillTimeToWait', item)['nextState'], 'DeadlineExceeded')

    def test_deadline_is_judged_right_before_start_and_after_rereading_status(self):
        """마감 판정은 시작 직전에 한다(Gate 재시도로 흐른 시간 포함). 남의 실행을 기다린 뒤에는
        상태를 먼저 다시 읽는다 — 기다리는 사이 마감 전에 발행된 결과를 미완료로 세지 않기 위해서다."""
        processor = processor_of(render())
        item = {'etf_code': '069500', 'analysis_at': AT, 'deadline': PAST, 'max_attempts': 2, 'attempt': 0,
                'ran': False, 'analysis_id': identity('069500', 0), 'gate': {'busy': 0}}
        self.assertEqual(step(processor, 'GateOpen', item)['nextState'], 'StillTimeToRun')
        self.assertEqual(step(processor, 'StillTimeToRun', item)['nextState'], 'DeadlineExceeded')
        self.assertEqual(step(processor, 'StillTimeToRun', item | {'deadline': FUTURE})['nextState'], 'Run')
        self.assertEqual(step(processor, 'WaitForOther', item)['nextState'], 'ReadStatus')
        self.assertEqual(step(processor, 'StillRunning', item)['nextState'], 'DeadlineExceeded')
        self.assertEqual(step(processor, 'StillRunning', item | {'deadline': FUTURE})['nextState'], 'WaitForOther')

    def test_items_run_one_at_a_time(self):
        """동시 1건은 Map 설정으로도 고정한다 — writer 역할 연결 한도 5, 워커는 건당 3개(README 기준 측정).
        run_batch 가 항목을 순차로 걷는 것은 이 값이 1일 때만 실제와 같다."""
        self.assertEqual(render()['States']['RunItems']['MaxConcurrency'], 1)

    def test_publication_after_deadline_is_late_not_completed(self):
        """마감 뒤에 저장된 결과는 완료로 세지 않고 드러낸다."""
        world = World(plan={('396500', 0): 'late'})
        state, out = self.batch(world)
        self.assertEqual((state, out['error']), ('MissedDeadline', 'OutlookBatch.DeadlineExceeded'))
        self.assertEqual((out['cause']['late'], [i['etf_code'] for i in out['cause']['unfinished']]), (1, ['396500']))

    def test_unreadable_status_never_starts_paid_work(self):
        """상태를 읽을 수 없으면(재시도 소진) 분석을 시작하지 않고 미완료로 남긴다."""
        world = World(status_down=True)
        state, out = self.batch(world)
        self.assertEqual((state, out['cause']['unknown'], world.runs), ('Incomplete', 2, []))

    def test_default_deadline_is_0800_kst_of_the_reference_day(self):
        definition = render()
        result = step(definition, 'DeriveDeadline', {'analysis_at': AT})
        self.assertEqual(json.loads(result['output'])['limits']['deadline'], '2026-10-01T23:00:00Z')  # 10-02 08:00 KST

    def test_non_canonical_reference_time_is_rejected(self):
        """분석 ID 가 기준시각 문자열에서 나오므로 같은 순간의 다른 표기를 받지 않는다."""
        for bad in ({'analysis_at': '2026-10-02T03:00:00+09:00'}, {}, {'analysis_at': AT, 'etf_codes': []}):
            state, out = run_batch(World(), bad)
            self.assertEqual((state, out['error']), ('InvalidInput', 'OutlookBatch.InvalidInput'), bad)

    def test_malformed_limits_are_rejected_before_any_run(self):
        """시도 상한·마감이 잘못되면 한 건도 시작하지 않는다 — 형식이 틀린 마감은 시각 비교가 조용히 거짓이 된다."""
        for bad in ({'max_attempts': 0}, {'max_attempts': '2'}, {'deadline': '08:00'}):
            world = World()
            state, out = run_batch(world, {'analysis_at': AT, 'deadline': FUTURE} | bad)
            self.assertEqual((state, out['error'], world.runs), ('InvalidInput', 'OutlookBatch.InvalidInput', []), bad)


if __name__ == '__main__':
    unittest.main()
