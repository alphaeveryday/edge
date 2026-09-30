"""Content-addressed snapshots and incremental complete JSONL event chunks."""
from hashlib import sha256
import json
from pathlib import Path
import re

from edge_analysis_v2.cloud.contract import decode_json, decode_request
from edge_analysis_v2.dashboard.jobs import ARTIFACTS

PREFIX = 'analysis-v2/runs/'
FILES = frozenset(ARTIFACTS) - {'events.jsonl'} | {'evidence.json', 'storage.json', 'screens.json'}
LIMIT = 32 * 1024 * 1024


def encode(value):
    """Serialize administrative JSON without rounding application numbers."""
    return json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2).encode('utf-8')


def atomic(path, data):
    """Publish a complete local file; incomplete downloads never replace it."""
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    temp.write_bytes(data)
    temp.replace(path)


class Publisher:
    """Upload files first and advertise only durable objects in a manifest.

    Args:
        s3: S3 client using the cloud task role.
        bucket: Server-configured private observation bucket.
        identity: Validated analysis ID.
        folder: Local artifact directory of this execution.
    """
    def __init__(self,s3,bucket,identity,folder):
        if not re.fullmatch('[a-f0-9]{32}',identity):
            raise ValueError('Invalid analysis ID')
        self.s3,self.bucket,self.folder = s3,bucket,Path(folder)
        self.prefix = PREFIX+identity+'/'
        self.files,self.events,self.offset = {},[],0
        self.claimed = False

    def _put(self, data, name):
        if len(data)>LIMIT:
            raise ValueError('Observation file exceeds 32 MiB')
        digest = sha256(data).hexdigest()
        entry = {'key':self.prefix+'objects/'+digest+'/'+name, 'sha256':digest,'size':len(data)}
        self.s3.put_object(Bucket=self.bucket,Key=entry['key'],Body=data,ContentType='application/octet-stream')
        return entry

    def publish(self,job):
        """Flush complete events and changed files, then return the advertised snapshot."""
        for name in sorted(FILES):
            path = self.folder/name
            if not path.is_file():
                continue
            data = path.read_bytes()
            # A model may still be writing JSON; advertise it only after parsing succeeds.
            if name.endswith('.json'):
                try:
                    decode_json(data)
                except (ValueError,UnicodeError):
                    continue
            if self.files.get(name,{}).get('sha256') != sha256(data).hexdigest():
                self.files[name] = self._put(data,name)
        path = self.folder/'events.jsonl'
        if path.is_file():
            with path.open('rb') as stream:
                stream.seek(self.offset)
                while True:
                    pending = stream.read(LIMIT)
                    if not pending:
                        break
                    data = pending[:pending.rfind(b'\n')+1]
                    if not data:
                        if len(pending)==LIMIT or job['status'] in ('completed','failed'):
                            raise ValueError('Incomplete or oversized observation event')
                        break
                    for row in data.splitlines():
                        decode_json(row)
                    self.events.append(self._put(data,'events.jsonl'))
                    self.offset += len(data)
                    stream.seek(self.offset)
        manifest = {'job':job,'files':dict(self.files),'events':list(self.events)}
        validate_manifest(manifest)
        self.s3.put_object(Bucket=self.bucket,Key=self.prefix+'manifest.json',Body=encode(manifest),ContentType='application/json',
                           **({} if self.claimed else {'IfNoneMatch':'*'}))
        self.claimed=True
        return manifest


def validate_manifest(manifest):
    """Reject cross-run keys and filesystem paths supplied by remote objects."""
    if not isinstance(manifest,dict) or set(manifest)!={'job','files','events'}:
        raise ValueError('Invalid observation manifest')
    job = manifest['job']
    decode_request(encode({k:job.get(k) for k in ('analysis_id','kind','etf_code','analysis_at')}))
    if job.get('origin')!='cloud' or job.get('status') not in ('queued','running','completed','failed','interrupted'):
        raise ValueError('Invalid cloud status')
    if not isinstance(manifest['files'],dict) or set(manifest['files'])-FILES or not isinstance(manifest['events'],list):
        raise ValueError('Unknown observation artifact')
    if len(manifest['events'])>10000:
        raise ValueError('Too many event chunks')
    total_size=0
    for name,entry in list(manifest['files'].items())+[('events.jsonl',e) for e in manifest['events']]:
        if not isinstance(entry,dict) or set(entry)!={'key','sha256','size'}:
            raise ValueError('Invalid object reference')
        digest=entry['sha256']
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
            raise ValueError('Invalid content hash')
        if type(entry['size']) is not int or not 0<=entry['size']<=LIMIT:
            raise ValueError('Invalid object size')
        total_size+=entry['size']
        if entry['key']!=PREFIX+job['analysis_id']+'/objects/'+digest+'/'+name:
            raise ValueError('Cross-run object reference')
    if total_size>128*1024*1024:
        raise ValueError('Observation snapshot exceeds 128 MiB')


def download(s3,bucket,manifest,folder):
    """Resume from verified cached objects and atomically replace visible artifacts."""
    validate_manifest(manifest)
    folder=Path(folder)
    def get(entry):
        cache=folder/'.objects'/entry['sha256']
        data=cache.read_bytes() if cache.is_file() else None
        if data is None or sha256(data).hexdigest()!=entry['sha256']:
            data=s3.get_object(Bucket=bucket,Key=entry['key'])['Body'].read(LIMIT+1)
        if len(data)!=entry['size'] or sha256(data).hexdigest()!=entry['sha256']:
            raise ValueError('Observation object integrity failure')
        atomic(cache,data)
        return data
    files={name:get(entry) for name,entry in manifest['files'].items()}
    events=b''.join(get(entry) for entry in manifest['events'])
    for name,data in files.items():
        atomic(folder/name,data)
    atomic(folder/'events.jsonl',events)
    atomic(folder/'job.json',encode(manifest['job']))
