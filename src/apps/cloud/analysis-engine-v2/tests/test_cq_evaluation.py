import copy
import asyncio
from dataclasses import dataclass
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from edge_analysis_v2.quality.cq import evaluation


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.run=self.root/'CQ05-suite-20261005T170000Z';self.run.mkdir()
        self.id='cq_'+'a'*32
        self.report={'case':'CQ05-suite','status':'answered','semantic_grade':'passed',
            'question':'한 달 전망','cutoff':'2026-10-05T00:00:00+00:00','elapsed_ms':1000,
            'response':{'answer':'우선협상 선정이다.','limitations':[],
                'claims':[{'claim':'우선협상 선정이다.','purpose':'사업 단계 확인','evidence_role':'support','tool_run_ids':[self.id]}]}}
        self.call={'tool':'list_events','arguments':{},'cutoff':self.report['cutoff'],
            'error':None,'elapsed_ms':10,'queries':[],'finished_at':'2026-10-05T01:00:00+00:00',
            'response':{'tool_run_id':self.id,'result':{'items':[{'stage':'preferred_bidder'}]}}}
        (self.run/'tools').mkdir();self.save()

    def save(self):
        (self.run/'benchmark.json').write_text(json.dumps(self.report),encoding='utf8')
        (self.run/'tools'/(self.id+'.json')).write_text(json.dumps(self.call),encoding='utf8')

    def result(self):return evaluation.evaluate(self.run)

    def review(self):
        result=self.result()
        return {'checks':[{'id':r['id'],'status':'pass','reason':'저장된 답변과 자료에서 확인',
            'answer_span':'우선협상 선정이다.','evidence_ids':[self.id]} for r in result['agent_checks']],
            'unnecessary_calls':[]}

    def test_legacy_pass_cannot_pass_new_contract_and_reproduction_is_excluded(self):
        result=self.result()
        self.assertEqual(result['overall'],'not_evaluated')
        self.assertTrue(all(r['status']=='pass' for r in result['code_checks']))
        self.assertNotIn('reproduction',json.dumps(result['code_checks']))
        self.assertEqual(result['efficiency']['reference_calls'],None)
        self.assertEqual(result['efficiency']['actual_calls'],1)

    def test_failed_tool_may_support_limitation_but_not_fact(self):
        self.call['error']='missing';self.save()
        self.assertEqual(self.result()['overall'],'fail')
        self.report['response']['claims'][0]['evidence_role']='limitation';self.save()
        self.assertEqual(self.result()['overall'],'not_evaluated')

    def test_empty_answer_or_missing_reference_is_a_code_failure(self):
        self.report['response']['answer']='';self.save()
        self.assertEqual(self.result()['overall'],'fail')
        self.report['response']['answer']='답변';self.report['response']['claims'][0]['tool_run_ids']=['missing'];self.save()
        self.assertEqual(self.result()['overall'],'fail')

    def test_malformed_output_is_a_failed_check_not_a_dashboard_crash(self):
        self.report['response']=['invalid'];self.save()
        self.assertEqual(self.result()['overall'],'fail')

    def test_cutoff_mismatch_is_rejected_but_finished_at_after_cutoff_is_normal(self):
        self.assertEqual(self.result()['code_checks'][-1]['status'],'pass')
        self.call['cutoff']='2026-10-06T00:00:00+00:00';self.save()
        self.assertEqual(self.result()['code_checks'][-1]['status'],'fail')

    def test_complete_review_passes_but_single_semantic_failure_cannot_be_averaged(self):
        review=self.review();evaluation.save_review(self.run,review,reviewer={'model':'test','method':'fixture'})
        self.assertEqual(self.result()['overall'],'pass')
        review['checks'][0]['status']='fail'
        evaluation.save_review(self.run,review,reviewer={'model':'test','method':'fixture'})
        self.assertEqual(self.result()['overall'],'fail')

    def test_unknown_judgment_does_not_pass(self):
        review=self.review();review['checks'][0]['status']='unknown'
        evaluation.save_review(self.run,review,reviewer={'model':'test','method':'fixture'})
        self.assertEqual(self.result()['overall'],'review_needed')

    def test_a_verdict_cites_the_workspace_only_when_the_run_kept_one(self):
        # WHY: a run with no workspace file passed a verdict whose evidence was "workspace".
        review=self.review();review['checks'][0]['evidence_ids']=['workspace']
        with self.assertRaisesRegex(ValueError,'unsaved evidence'):evaluation.save_review(self.run,review,reviewer={'model':'test'})
        (self.run/'model').mkdir();(self.run/'model/workspace.json').write_text('{}',encoding='utf8')
        review=self.review();review['checks'][0]['evidence_ids']=['workspace']
        evaluation.save_review(self.run,review,reviewer={'model':'test'})
        self.assertEqual(self.result()['review_status'],'current')

    def test_uncalibrated_agent_pass_is_a_proposal_not_a_verified_pass(self):
        evaluation.save_review(self.run,self.review(),reviewer={'model':'test','calibration':'not_calibrated'})
        self.assertEqual(self.result()['overall'],'review_needed')
        self.assertEqual(self.result()['agent_proposal'],'pass')

    def test_review_must_cover_every_criterion_once_with_real_quotes_and_evidence(self):
        valid=self.review()
        for mutate in [lambda r:r['checks'].pop(),
                       lambda r:r['checks'].append(copy.deepcopy(r['checks'][0])),
                       lambda r:r['checks'][0].update(answer_span='꾸며낸 문장'),
                       lambda r:r['checks'][0].update(evidence_ids=['not_saved']),
                       lambda r:r['checks'][0].update(reason='')]:
            value=copy.deepcopy(valid);mutate(value)
            with self.assertRaises(ValueError):evaluation.save_review(self.run,value,reviewer={})

    def test_modified_evidence_invalidates_old_review(self):
        evaluation.save_review(self.run,self.review(),reviewer={'model':'test'})
        self.call['response']['result']['items'][0]['stage']='signed';self.save()
        result=self.result()
        self.assertEqual(result['overall'],'not_evaluated')
        self.assertEqual(result['review_status'],'stale')

    def test_frozen_run_contract_survives_later_contract_changes(self):
        evaluation.freeze_contract(self.run,self.report['case'])
        evaluation.save_review(self.run,self.review(),reviewer={'model':'test'})
        with patch.object(evaluation,'contract_for',side_effect=AssertionError('Must use the run contract')):
            self.assertEqual(self.result()['overall'],'pass')
        with self.assertRaises(FileExistsError):evaluation.freeze_contract(self.run,self.report['case'])

    def test_unnecessary_call_requires_reason_and_distinct_real_id(self):
        review=self.review();review['unnecessary_calls']=[{'tool_run_id':self.id,'reason':'같은 범위의 목적 없는 반복'}]
        evaluation.save_review(self.run,review,reviewer={'model':'test'})
        self.assertEqual(self.result()['efficiency']['unnecessary_calls'],1)
        review['unnecessary_calls'].append(review['unnecessary_calls'][0])
        with self.assertRaises(ValueError):evaluation.save_review(self.run,review,reviewer={})

    def test_contracts_cover_thirteen_cqs_without_invented_reference_counts(self):
        for i in range(1,14):
            contract=evaluation.contract_for(f'CQ{i:02}')
            self.assertGreaterEqual(len(contract['required_findings']),3)
            self.assertIsNone(contract['reference_execution'])
            self.assertTrue(contract['reference_path'])

    def test_one_review_has_four_quality_verdicts_and_separate_code_checks(self):
        for i in range(1,14):
            self.report['case']=f'CQ{i:02}-suite';self.save()
            result=self.result()
            # Competing explanations are judged only where the question asks for a cause, a classification or an outlook.
            judged=i in (2,3,5,13)
            self.assertEqual([r['id'] for r in result['agent_checks']],['accuracy','reasoning','research','fulfillment']+['hypotheses']*judged)
            self.assertEqual([r['group'] for r in result['agent_checks']],['common','common','common','cq']+['cq']*judged)
            self.assertEqual(len(result['code_checks']),3)
            self.assertEqual(result['contract']['version'],5)

    def test_old_contract_review_remains_on_disk_but_cannot_pass_four_criteria(self):
        with patch.object(evaluation,'contract_for',return_value={'id':'CQ05','version':2,'common':[],
                'required_findings':[],'critical_errors':[],'completion_conditions':'old contract'}):
            evaluation.save_review(self.run,self.review(),reviewer={'model':'test'})
        previous=(self.run/'evaluation.json').read_bytes()
        result=self.result()
        self.assertEqual(result['review_status'],'stale')
        self.assertEqual(result['overall'],'not_evaluated')
        self.assertEqual((self.run/'evaluation.json').read_bytes(),previous)
        self.assertTrue(all(r['status']=='not_evaluated' for r in result['agent_checks']))

class CompetingExplanationsCriterionTests(unittest.TestCase):
    def test_the_process_verdict_cites_the_work_record_and_never_turns_a_failed_fact_into_a_pass(self):
        # WHY: registering rival explanations is cheap; the verdict must rest on the work record and cannot offset an error.
        from edge_analysis_v2.quality.cq.evaluation import validate_review,contract_for
        checks=[{'id':c['id']} for c in contract_for('CQ05')['criteria']]
        self.assertIn('hypotheses',[c['id'] for c in checks])
        self.assertNotIn('hypotheses',[c['id'] for c in contract_for('CQ07')['criteria']])
        report={'response':{'answer':'배수 조정이 원인이다.','claims':[],'limitations':[]}}
        row=lambda i,status,refs:{'id':i,'status':status,'reason':'확인','answer_span':'배수 조정이 원인이다.','evidence_ids':refs}
        calls=[{'response':{'tool_run_id':'cq_'+'a'*32}}]
        review={'checks':[row(c['id'],'pass',['cq_'+'a'*32]) for c in checks[:-1]]+[row('hypotheses','pass',['workspace'])],'unnecessary_calls':[]}
        validate_review(review,checks,report,calls)
        review['checks'][-1]['evidence_ids']=['notes-not-read']
        with self.assertRaises(ValueError):validate_review(review,checks,report,calls)


if __name__=='__main__':unittest.main()


def test_questions_the_data_cannot_answer_are_switched_off_not_removed():
    # WHY: PLUS K방산 was compared with a fund that is not stored and an expectation type the graph does not have,
    # so two questions scored "say it is absent" instead of an analysis.
    cases = {c['id']: c for c in evaluation.read(evaluation.CASES)['cases']}
    assert len(cases) == 13 and [k for k, c in cases.items() if c.get('deferred')] == ['CQ12']
    assert 'KODEX 200' in cases['CQ01']['question'] and 'KODEX 방산' not in cases['CQ01']['question']
    assert '한국 시간 2026-10-02 자정까지 발행된' in cases['CQ08']['question']
