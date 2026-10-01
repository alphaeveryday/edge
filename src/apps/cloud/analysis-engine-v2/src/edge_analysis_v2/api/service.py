"""HTTP transport independent of AWS credentials and database connection ownership."""
import base64
from datetime import datetime, timezone
import json
import logging
import re

from botocore.exceptions import ClientError

from edge_analysis_v2.cloud.contract import decode_request

LOG = logging.getLogger(__name__)
FEATURES = {'movement':{'all','summary','detail'},
            'outlook':{'all','summary','detail','factors','conclusion','factor_details'}}
IDENTITY = re.compile(r'[a-f0-9]{32}')
TICKER = re.compile(r'[A-Z0-9]{6}')


class APIError(Exception):
    """An intentional, credential-free error safe for the API consumer."""

    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class AnalysisAPI:
    """Expose a read-only publication store and a fixed analysis workflow.

    Args:
        publications: Database reader providing find, latest and screen.
        workflows: Step Functions client, limited to the configured workflow.
        state_machine: Server-owned Standard Workflow ARN.
        now: Clock used to reject requests for future data.
    """

    def __init__(self, publications, workflows, state_machine, *, now=None):
        self.publications = publications
        self.workflows = workflows
        self.state_machine = state_machine
        self.now = now or (lambda:datetime.now(timezone.utc))

    def handle(self, event):
        """Handle a trusted API Gateway payload 2.0 after IAM authentication."""
        request_id = event.get('requestContext',{}).get('requestId','unknown')
        headers = {'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',
                   'X-Request-Id':request_id}
        try:
            if not event.get('requestContext',{}).get('authorizer',{}).get('iam',{}).get('userArn'):
                raise APIError(403,'FORBIDDEN','An authorized AWS IAM caller is required.')
            if event.get('rawQueryString'):
                raise APIError(400,'INVALID_REQUEST','Query parameters are not supported.')
            method = event['requestContext']['http']['method']
            parts = event.get('rawPath','').strip('/').split('/')
            if method=='POST' and parts==['v2','analyses']:
                raw = event.get('body') or ''
                if len(raw.encode('utf-8'))>5500:
                    raise APIError(413,'REQUEST_TOO_LARGE','Request body exceeds 4096 bytes.')
                try:
                    if event.get('isBase64Encoded'):
                        raw = base64.b64decode(raw,validate=True).decode('utf-8')
                    if len(raw.encode('utf-8'))>4096:
                        raise APIError(413,'REQUEST_TOO_LARGE','Request body exceeds 4096 bytes.')
                    request = decode_request(raw)
                except (ValueError,UnicodeError):
                    raise APIError(400,'INVALID_REQUEST','Use the documented analysis request JSON.') from None
                if datetime.fromisoformat(request['analysis_at'])>self.now():
                    raise APIError(400,'INVALID_REQUEST','analysis_at must not be in the future.')
                status, body = self.submit(request)
                headers['Location'] = f"/v2/analyses/{request['kind']}/{request['analysis_id']}"
            elif method=='GET' and len(parts) in (4,6) and parts[:2]==['v2','analyses']:
                kind, identity = parts[2:4]
                self.validate(kind,identity=identity)
                if len(parts)==4:
                    body=self.status(kind,identity); status=200
                else:
                    if parts[4]!='screens':
                        raise APIError(404,'NOT_FOUND','Endpoint not found.')
                    self.validate(kind,feature=parts[5])
                    body=self.screen(kind,identity,parts[5]);status=200
                    headers['X-Analysis-Id']=identity
            elif method=='GET' and len(parts)==8 and parts[:2]==['v2','etfs'] and parts[3]=='analyses' and parts[5:7]==['latest','screens']:
                ticker, kind, feature = parts[2],parts[4],parts[7]
                self.validate(kind,ticker=ticker,feature=feature)
                latest=self.publications.latest(ticker,kind)
                if latest is None:
                    raise APIError(404,'NOT_FOUND','No completed publication for this ETF.')
                identity=latest['analysis_id']
                body=self.screen(kind,identity,feature);status=200
                headers['X-Analysis-Id']=identity
                headers['Content-Location']=f'/v2/analyses/{kind}/{identity}/screens/{feature}'
            else:
                raise APIError(404,'NOT_FOUND','Endpoint not found.')
        except APIError as exc:
            status=exc.status
            body={'error':{'code':exc.code,'message':exc.message},'request_id':request_id}
        except Exception as exc:
            # Never log raw DB errors, request bodies or credentials.
            LOG.error('Analysis API dependency failure request=%s type=%s',request_id,type(exc).__name__)
            status=503
            body={'error':{'code':'SERVICE_UNAVAILABLE','message':'Analysis service is temporarily unavailable.'},'request_id':request_id}
        return {'statusCode':status,'headers':headers,'body':json.dumps(body,ensure_ascii=False,allow_nan=False)}

    @staticmethod
    def validate(kind, *, identity=None, ticker=None, feature=None):
        """Reject unsupported resource coordinates before querying dependencies."""
        if (kind not in FEATURES or identity is not None and not IDENTITY.fullmatch(identity)
                or ticker is not None and not TICKER.fullmatch(ticker)
                or feature is not None and feature not in FEATURES.get(kind,set())):
            raise APIError(400,'INVALID_REQUEST','Invalid analysis kind, ID, ETF code or screen feature.')

    def execution(self, identity):
        """Read one workflow; permission and network errors remain service failures."""
        arn=self.state_machine.replace(':stateMachine:',':execution:')+':'+identity
        try:
            return self.workflows.describe_execution(executionArn=arn)
        except ClientError as exc:
            if exc.response['Error']['Code']=='ExecutionDoesNotExist':
                return None
            raise

    @staticmethod
    def same_request(row, request):
        """Compare identity, domain and cutoff without requiring equal timezone spelling."""
        return all(row[k]==request[k] for k in ('analysis_id','kind','etf_code')) and (
            datetime.fromisoformat(row['analysis_at']) if isinstance(row['analysis_at'],str) else row['analysis_at'])==datetime.fromisoformat(request['analysis_at'])

    def submit(self, request):
        """Reuse persisted or existing executions before creating any new paid work."""
        identity=request['analysis_id']
        row=self.publications.find(identity)
        if row is not None:
            if row['data_source']!='database' or not self.same_request(row,request):
                raise APIError(409,'ID_CONFLICT','analysis_id already belongs to a different request.')
            return 200,self.status(request['kind'],identity)
        previous=self.execution(identity)
        if previous is not None:
            if not self.same_request(json.loads(previous['input']),request):
                raise APIError(409,'ID_CONFLICT','analysis_id already belongs to a different request.')
            return 200,self.status(request['kind'],identity)
        raw=json.dumps(request,sort_keys=True,separators=(',',':'))
        try:
            self.workflows.start_execution(stateMachineArn=self.state_machine,name=identity,input=raw)
        except ClientError as exc:
            if exc.response['Error']['Code']!='ExecutionAlreadyExists':
                raise
            previous=self.execution(identity)
            if previous is None or not self.same_request(json.loads(previous['input']),request):
                raise APIError(409,'ID_CONFLICT','analysis_id already belongs to a different request.') from None
            return 200,self.status(request['kind'],identity)
        return 202,request | {'status':'queued'}

    def status(self, kind, identity):
        """Prefer the committed publication even if later observation upload failed."""
        row=self.publications.find(identity)
        if row is not None and (row['kind']!=kind or row['data_source']!='database'):
            raise APIError(404,'NOT_FOUND','Analysis not found.')
        if row is not None and row['status'] in ('completed','failed'):
            state=row['status']
        else:
            execution=self.execution(identity)
            if execution is None and row is None:
                raise APIError(404,'NOT_FOUND','Analysis not found.')
            if execution is not None:
                request=json.loads(execution['input'])
                if request['kind']!=kind:
                    raise APIError(404,'NOT_FOUND','Analysis not found.')
                state=('running' if row else 'queued') if execution['status']=='RUNNING' else 'failed'
                row=row or request
            else:
                state=row['status']
        body={k:row[k].isoformat() if isinstance(row[k],datetime) else row[k]
              for k in ('analysis_id','kind','etf_code','analysis_at')}
        return body | {'status':state}

    def screen(self, kind, identity, feature):
        """Return the existing app contract unchanged after publication completion."""
        state=self.status(kind,identity)
        if state['status']!='completed':
            raise APIError(409,'RESULT_NOT_READY','Analysis has not completed; read its status.')
        row=self.publications.find(identity)
        if row is None or row['published_at'] is None:
            raise APIError(404,'NOT_PUBLISHED','Analysis completed without publishing a screen.')
        result=self.publications.screen(kind,identity,feature)
        if result is None:
            raise APIError(404,'NOT_FOUND','Screen not found.')
        return result
