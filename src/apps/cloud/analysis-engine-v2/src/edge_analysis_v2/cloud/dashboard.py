"""Local-only dashboard over automatically downloaded cloud observations."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
from threading import Event, Thread, RLock

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from edge_analysis_v2.cloud.artifacts import PREFIX, LIMIT, atomic, encode, download
from edge_analysis_v2.cloud.contract import decode_json, decode_request
from edge_analysis_v2.dashboard.jobs import ARTIFACTS
from edge_analysis_v2.dashboard.server import make_handler, ThreadingHTTPServer


class CloudDashboard:
    """Cache remote executions without possessing DB or model credentials.

    Args:
        folder: Local cache dedicated to cloud observations.
        s3: S3 client with read permission for the observation prefix.
        sfn: Step Functions client allowed to start and inspect the v2 workflow.
        bucket: Private observation bucket.
        state_machine: Exact server-configured v2 workflow ARN.
    """
    mode='cloud'

    def __init__(self,folder,*,s3,sfn,bucket,state_machine):
        self.folder=Path(folder)
        self.folder.mkdir(parents=True,exist_ok=True)
        self.s3,self.sfn,self.bucket,self.state_machine=s3,sfn,bucket,state_machine
        self.csrf_token=secrets.token_urlsafe(32)
        self.etags={}
        self.lock=RLock()
        self.sync_status={'status':'waiting','last_synced_at':None}

    def _file(self,identity,name):
        if not re.fullmatch('[a-f0-9]{32}',identity):
            raise ValueError('Invalid analysis ID')
        return self.folder/identity/name

    def _json(self,identity,name):
        path=self._file(identity,name)
        return decode_json(path.read_bytes()) if path.is_file() else None

    def jobs(self):
        """Return cached cloud states without inferring failure from a local restart."""
        records=[decode_json(p.read_bytes()) for p in self.folder.glob('*/job.json')
                 if re.fullmatch('[a-f0-9]{32}',p.parent.name)]
        return sorted(records,key=lambda j:j['started_at'],reverse=True)

    def detail(self,identity):
        job=self._json(identity,'job.json')
        if job is None:
            return None
        return {'job':job,'artifacts':{name:self._file(identity,name).read_text(encoding='utf-8')
                for name in ARTIFACTS if self._file(identity,name).is_file()}}

    def read(self,kind=None,identity=None):
        if kind is None:
            return self.jobs()
        job=self._json(identity,'job.json')
        return self._json(identity,'evidence.json') if job and job['kind']==kind else None

    def feature(self,kind,identity,feature):
        job=self._json(identity,'job.json')
        screens=self._json(identity,'screens.json')
        return screens.get(feature) if job and job['kind']==kind and screens else None

    def record(self,kind,identity,name):
        job=self._json(identity,'job.json')
        return self._json(identity,name) if job and job['kind']==kind else None

    def start(self,body):
        """Submit the exact validated request; AWS execution names make retries idempotent."""
        with self.lock:
            return self._start(body)

    def _start(self,body):
        request=decode_request(encode(body))
        cached=self._json(request['analysis_id'],'job.json')
        if cached:
            if {k:cached[k] for k in request}!=request:
                raise ValueError('Analysis ID already belongs to another request')
            return cached
        raw=encode(request).decode('utf-8')
        try:
            result=self.sfn.start_execution(stateMachineArn=self.state_machine,name=request['analysis_id'],input=raw)
            arn=result['executionArn']
        except ClientError as exc:
            if exc.response['Error']['Code']!='ExecutionAlreadyExists':
                raise
            arn=self.state_machine.replace(':stateMachine:',':execution:')+':'+request['analysis_id']
            previous=self.sfn.describe_execution(executionArn=arn)
            if decode_request(previous['input'])!=request:
                raise ValueError('Analysis ID already belongs to another request') from None
            cached=self._json(request['analysis_id'],'job.json')
            if cached:
                return cached
        job=request | {'origin':'cloud','scenario':'database','data_source':'database','status':'queued',
                       'started_at':datetime.now(timezone.utc).isoformat(),'execution_arn':arn}
        atomic(self._file(request['analysis_id'],'job.json'),encode(job))
        return job

    def sync(self):
        """Fetch changed snapshots and reconcile jobs that died before writing any files."""
        with self.lock:
            self._sync()

    def _sync(self):
        try:
            for page in self.s3.get_paginator('list_objects_v2').paginate(Bucket=self.bucket,Prefix=PREFIX):
                for obj in page.get('Contents',[]):
                    match=re.fullmatch(re.escape(PREFIX)+r'([a-f0-9]{32})/manifest\.json',obj['Key'])
                    if not match or self.etags.get(obj['Key'])==obj.get('ETag'):
                        continue
                    raw=self.s3.get_object(Bucket=self.bucket,Key=obj['Key'])['Body'].read(LIMIT+1)
                    if len(raw)>LIMIT:
                        raise ValueError('Oversized manifest')
                    manifest=decode_json(raw)
                    if manifest['job']['analysis_id']!=match[1]:
                        raise ValueError('Manifest identity mismatch')
                    download(self.s3,self.bucket,manifest,self.folder/match[1])
                    self.etags[obj['Key']]=obj.get('ETag')
            executions=[]
            token=None
            while True:
                page=self.sfn.list_executions(stateMachineArn=self.state_machine,maxResults=100,**({'nextToken':token} if token else {}))
                executions.extend(page['executions'])
                token=page.get('nextToken')
                if not token:
                    break
            arns={row['executionArn'] for row in executions}
            arns.update(j['execution_arn'] for j in self.jobs() if j['status'] in ('queued','running') and j.get('execution_arn'))
            for arn in sorted(arns):
                identity=arn.rsplit(':',1)[-1]
                cached=self._json(identity,'job.json') if re.fullmatch('[a-f0-9]{32}',identity) else None
                if cached and cached.get('workflow_status') in ('SUCCEEDED','FAILED','TIMED_OUT','ABORTED'):
                    continue
                state=self.sfn.describe_execution(executionArn=arn)
                # Workflow inputs include the worker's trigger coordinates; only the start endpoint is public.
                request=decode_request(state['input'],internal=True)
                identity=request['analysis_id']
                job=self._json(identity,'job.json') or request | {'origin':'cloud','scenario':'database','data_source':'database',
                    'status':'queued','started_at':state['startDate'].isoformat()}
                job['execution_arn']=arn
                job['workflow_status']=state['status']
                if state['status']!='RUNNING' and job['status'] in ('queued','running'):
                    job.update(status='interrupted',observation_status='incomplete',
                        error='Cloud execution ended without a terminal observation record; publication status is unconfirmed')
                if state['status']=='FAILED' and job['status']=='completed':
                    job.update(observation_status='incomplete')
                atomic(self._file(identity,'job.json'),encode(job))
            self.sync_status={'status':'ok','last_synced_at':datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            # Do not overwrite known execution states when local credentials/network fail.
            self.sync_status=self.sync_status | {'status':'error','error':type(exc).__name__+': cloud sync unavailable; check AWS login and connectivity'}


def main():
    """Run the existing dashboard against the cloud cache on loopback only."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',default='edge-v2-observer')
    parser.add_argument('--bucket',default='edge-dev-pipeline-lake')
    parser.add_argument('--state-machine',default='arn:aws:states:ap-northeast-2:393229433969:stateMachine:edge-dev-analysis-v2')
    parser.add_argument('--runs-dir',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    session=boto3.Session(profile_name=args.profile,region_name='ap-northeast-2')
    config=Config(connect_timeout=5,read_timeout=15,retries={'max_attempts':2})
    observer=CloudDashboard(args.runs_dir,s3=session.client('s3',config=config),sfn=session.client('stepfunctions',config=config),
                            bucket=args.bucket,state_machine=args.state_machine)
    stop=Event()
    def sync_loop():
        while not stop.is_set():
            observer.sync()
            active=any(j['status'] in ('queued','running') for j in observer.jobs())
            stop.wait(5 if active else 30)
    thread=Thread(target=sync_loop,daemon=True)
    thread.start()
    handler=make_handler(observer.read,execution=observer,screen_reader=observer.feature,
        storage_reader=lambda k,i:observer.record(k,i,'storage.json'),
        audit_reader=lambda k,i:observer.record(k,i,'contract_audit.json'))
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler)
    print(f'Cloud observation: http://127.0.0.1:{args.port}',flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


if __name__=='__main__':
    main()
