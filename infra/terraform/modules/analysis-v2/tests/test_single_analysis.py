"""Validate the actual SFN interpreter without launching Lambda or Fargate.

Run: AWS_PROFILE=work python -m unittest discover -s infra/terraform/modules/analysis-v2/tests -p test_single_analysis.py -v
"""
import json
from pathlib import Path
import re
import unittest

import boto3


def render():
    text = (Path(__file__).parents[1]/'single_analysis.asl.json').read_text()
    values = {'control_arn':'arn:aws:lambda:ap-northeast-2:393229433969:function:edge-dev-analysis-v2-control',
              'cluster_arn':'arn:aws:ecs:ap-northeast-2:393229433969:cluster/edge-dev-worker',
              'task_family':'edge-dev-analysis-v2','security_group':'sg-test','subnets_json':'["subnet-test"]','slots':'3'}
    for key,value in values.items():
        text=text.replace('${'+key+'}',value)
    return json.loads(text)


class SingleAnalysis(unittest.TestCase):
    def setUp(self):
        self.client=boto3.client('stepfunctions',region_name='ap-northeast-2')
        self.definition=render()
        self.request={'analysis_id':'a'*32,'kind':'movement','etf_code':'091160','analysis_at':'2026-10-02T06:20:00Z'}

    def step(self, state, data, mock=None):
        args=dict(definition=json.dumps(self.definition),stateName=state,input=json.dumps(data),
                  inspectionLevel='DEBUG')
        if mock is not None:
            args['mock']=mock
            args['context']=json.dumps({'Execution':{'Id':'arn:aws:states:ap-northeast-2:393229433969:execution:edge-dev-analysis-v2:test'}})
        result=self.client.test_state(**args)
        self.assertIn(result['status'],['SUCCEEDED','CAUGHT_ERROR'],result)
        return result

    def test_busy_capacity_waits_and_worker_receives_only_original_request(self):
        data=json.loads(self.step('Input',self.request)['output'])
        data['slot']={'acquired':False,'started_by':'b'*32}
        self.assertEqual(self.step('Capacity',data)['nextState'],'Wait')
        data['slot']['acquired']=True
        self.assertEqual(self.step('Capacity',data)['nextState'],'Analyze')
        result=self.step('Analyze',data,{'result':json.dumps({'Containers':[{'ExitCode':0}]}),'fieldValidationMode':'NONE'})
        sent=json.loads(result['inspectionData']['afterParameters'])
        env=sent['Overrides']['ContainerOverrides'][0]['Environment']
        self.assertEqual(json.loads(env[0]['Value']),self.request)
        self.assertEqual(env[1]['Value'],'3')
        # The worker publishes only while this execution still owns its slot row.
        self.assertEqual(env[2],{'Name':'ANALYSIS_EXECUTION_ARN','Value':'arn:aws:states:ap-northeast-2:393229433969:execution:edge-dev-analysis-v2:test'})
        self.assertEqual(result['nextState'],'Release')
        output=json.loads(result['output'])
        released=self.step('Release',output,{'result':'{}','fieldValidationMode':'NONE'})
        self.assertEqual(json.loads(released['output']),output)
        self.assertEqual(self.step('CheckExit',output)['nextState'],'Completed')

    def test_task_failure_and_nonzero_exit_cannot_report_success(self):
        data={'request':self.request,'slot':{'acquired':True,'started_by':'b'*32}}
        result=self.step('Analyze',data,{'errorOutput':{'error':'States.Timeout','cause':'task timeout'}})
        self.assertEqual(result['nextState'],'ReleaseFailed')
        result=self.step('ReleaseFailed',json.loads(result['output']),{'result':'{}','fieldValidationMode':'NONE'})
        self.assertEqual(result['nextState'],'Failed')
        data['task']={'Containers':[{'ExitCode':1}]}
        self.assertEqual(self.step('CheckExit',data)['nextState'],'Failed')

    def retry(self, state, error, count):
        result=self.client.test_state(
            definition=json.dumps(self.definition),stateName=state,input=json.dumps({'request':self.request}),
            inspectionLevel='DEBUG',mock={'errorOutput':{'error':error,'cause':'mocked'}},
            context=json.dumps({'Execution':{'Id':'arn:aws:states:ap-northeast-2:393229433969:execution:edge-dev-analysis-v2:test'}}),
            stateConfiguration={'retrierRetryCount':count})
        return result['status'],result.get('inspectionData',{}).get('errorDetails',{})

    def test_control_throttle_retries_longer_with_jitter_than_function_errors(self):
        # A throttled control call never reached the slot table, so it may wait; a function error must not hide.
        # Acquire waits longer: 95 workflows started in one minute on 2026-10-13 and 39 ended without analysis
        # after 8 throttled attempts. Release keeps 8: while it retries, the finished analysis still owns its slot.
        for state,limit in (('Acquire',12),('Release',8),('ReleaseFailed',8)):
            for count in sorted({0,1,2,3,4,7,limit-1}):
                status,details=self.retry(state,'Lambda.TooManyRequestsException',count)
                self.assertEqual((status,details['retryIndex']),('RETRIABLE',0),(state,count))
                self.assertLessEqual(details['retryBackoffIntervalSeconds'],20,(state,count))
            self.assertEqual(self.retry(state,'Lambda.TooManyRequestsException',limit)[0],'FAILED',state)
            # The sampled waits above are jittered, so the cap itself is asserted on the definition.
            self.assertEqual(self.definition['States'][state]['Retry'][0]['MaxDelaySeconds'],20,state)
            status,details=self.retry(state,'RuntimeError',0)
            self.assertEqual((status,details['retryIndex'],details['retryBackoffIntervalSeconds']),('RETRIABLE',1,5),state)
            self.assertEqual(self.retry(state,'RuntimeError',3)[0],'FAILED',state)

    def test_throttle_waits_and_analysis_fit_inside_the_batch_wait_for_one_workflow(self):
        # The outlook batch waits a fixed time for each workflow. If throttled Acquire and Release retries,
        # the control calls that finally run, and the longest analysis could exceed it, the batch would give
        # up on a workflow that still finishes. Waiting 30 seconds for a free slot is outside this sum: the
        # batch starts only as many workflows as there are slots.
        def wait(state):
            rule=self.definition['States'][state]['Retry'][0]
            return sum(min(rule['IntervalSeconds']*rule['BackoffRate']**n,rule['MaxDelaySeconds']) for n in range(rule['MaxAttempts']))
        module=Path(__file__).parents[1]
        batch_wait=int(re.search(r'startExecution\.sync:2".*?"TimeoutSeconds": (\d+)',(module/'outlook_batch.asl.json').read_text(),re.S).group(1))
        call=int(re.search(r'resource "aws_lambda_function" "control".*?\n  timeout\s*=\s*(\d+)',(module/'control.tf').read_text(),re.S).group(1))
        analysis=self.definition['States']['Analyze']['TimeoutSeconds']
        for release in ('Release','ReleaseFailed'):
            self.assertLess(wait('Acquire')+call+analysis+wait(release)+call,batch_wait,release)

    def test_slot_key_is_kind_and_etf_not_analysis_id(self):
        result=self.step('Acquire',{'request':self.request},{'result':'{"acquired":true,"started_by":"x"}','fieldValidationMode':'NONE'})
        sent=json.loads(result['inspectionData']['afterParameters'])
        self.assertEqual(sent['request_key'],'movement:091160')
        self.assertEqual(sent['action'],'acquire')
