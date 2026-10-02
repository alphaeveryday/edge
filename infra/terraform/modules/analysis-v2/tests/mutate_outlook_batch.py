"""변이 확인 — 정의의 가드를 하나씩 무력화하면 그 가드를 지키는 계약 테스트가 실패해야 한다.

통과하는 테스트만으로는 테스트가 가드에 물려 있는지 알 수 없다. 정의나 테스트를 고친 뒤 한 번 돌린다.

    AWS_PROFILE=edge uv run --with boto3 python infra/terraform/modules/analysis-v2/tests/mutate_outlook_batch.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_outlook_batch as t

original = t.render
def item(d): return d['States']['RunItems']['ItemProcessor']['States']
MUTATIONS = {
    'gate-bypass': (lambda d: item(d)['GateOpen'].update(Choices=[{'Variable': '$.gate.busy', 'NumericGreaterThan': 999, 'Next': 'StillTimeToWait'}]),
                    ['test_waits_while_another_v2_execution_is_running']),
    'attempt-not-incremented': (lambda d: item(d)['NextAttempt']['Parameters'].update({'attempt.$': '$.attempt'}),
                    ['test_only_failed_item_is_retried_with_a_new_id']),
    'skip-publication-check': (lambda d: item(d)['OnTime'].update(Choices=[{'Variable': '$.attempt', 'NumericLessThan': -1, 'Next': 'Late'}]),
                    ['test_publication_after_deadline_is_late_not_completed']),
    'no-deadline-check': (lambda d: item(d)['StillTimeToRun'].update(Choices=[{'Variable': '$.attempt', 'NumericLessThan': -1, 'Next': 'DeadlineExceeded'}]),
                    ['test_start_after_absolute_deadline_runs_nothing_and_fails']),
    'deadline-verdict-without-reread': (lambda d: item(d)['WaitForOther'].update(Next='StillRunning'),
                    ['test_deadline_is_judged_right_before_start_and_after_rereading_status']),
    'accept-zero-attempts': (lambda d: d['States']['CheckInput']['Choices'][0]['And'].pop(),
                    ['test_malformed_limits_are_rejected_before_any_run']),
    'accept-fractional-seconds': (lambda d: d['States']['CheckInput']['Choices'][0]['And'].pop(3),
                    ['test_non_canonical_reference_time_is_rejected']),
    'accept-malformed-deadline': (lambda d: d['States']['HasDeadline'].update(Choices=[{'Variable': '$.deadline', 'IsPresent': True, 'Next': 'GivenDeadline'}]),
                    ['test_malformed_limits_are_rejected_before_any_run']),
    'slot-wait-without-reread': (lambda d: item(d)['WaitForSlot'].update(Next='Gate'),
                    ['test_job_started_by_another_batch_while_waiting_for_a_slot_is_picked_up']),
    'parallel-items': (lambda d: d['States']['RunItems'].update(MaxConcurrency=2), ['test_items_run_one_at_a_time']),
    'id-ignores-reference-time': (lambda d: item(d)['Identify']['Parameters'].update({'analysis_id.$': "States.Hash(States.Format('outlook:{}:{}', $.etf_code, $.attempt), 'MD5')"}),
                    ['test_other_reference_time_same_day_is_a_different_job']),
    'trust-exit-code': (lambda d: item(d)['Run'].update(Catch=[{'ErrorEquals': ['States.ALL'], 'ResultPath': '$.run_error', 'Next': 'NextAttempt'}]),
                    ['test_saved_but_exit_failed_attempt_counts_by_its_own_stored_result']),
    'date-level-reuse': (lambda d: item(d)['Identify']['Parameters'].update({'analysis_id.$': "States.Hash(States.Format('outlook:{}:{}:{}', $.etf_code, States.ArrayGetItem(States.StringSplit($.analysis_at, 'T'), 0), $.attempt), 'MD5')"}),
                    ['test_other_reference_time_same_day_is_a_different_job']),
    'any-404-means-unstarted': (lambda d: item(d)['Unstarted']['Choices'].pop(0),
                    ['test_gateway_404_is_not_read_as_never_started']),
    'trust-unreadable-publication-time': (lambda d: item(d)['OnTime']['Choices'].pop(0),
                    ['test_unreadable_publication_time_is_not_counted_as_on_time']),
    # ReadStatus 의 Catch 를 Unstarted 로 돌리는 변이는 두지 않는다 — Unstarted 의 404 판별이 같은 결과를 내 동치다.
}
survived = []
for name, (mutate, tests) in MUTATIONS.items():
    def render(**kw):
        d = original(**kw); mutate(d); return d
    t.render = render
    suite = unittest.TestSuite(t.OutlookBatchContract(x) for x in tests)
    result = unittest.TextTestRunner(stream=open('/dev/null', 'w')).run(suite)
    # 검출은 계약 단언의 실패만 센다. 오류(정의가 깨진 변이·AWS 호출 장애)는 가드를 검증한 것이 아니다.
    killed = bool(result.failures) and not result.errors
    print(('ERROR   ' if result.errors else 'killed  ' if killed else 'SURVIVED'), name, tests)
    for _, trace in result.errors:
        print('   ', trace.strip().splitlines()[-1][:200])
    if not killed: survived.append(name)
sys.exit(1 if survived else 0)
