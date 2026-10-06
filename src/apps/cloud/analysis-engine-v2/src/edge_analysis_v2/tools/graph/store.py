"""Analysis-local immutable tool evidence. A reference exists only after atomic persistence."""
import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


class EvidenceStore:
    def __init__(self, directory, cutoff):
        self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        parsed=datetime.fromisoformat(cutoff.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError('cutoff requires an explicit timezone')
        self.cutoff=parsed.astimezone(timezone.utc).isoformat()
        self.calls=[]

    def commit(self, name, arguments, result, *, elapsed_ms, queries, error=None, dataset=None):
        identifier='cq_'+uuid.uuid4().hex
        response={'tool_run_id':identifier,'result':copy.deepcopy(result)}
        if dataset is not None:
            response['result']['dataset_ref']={'tool_run_id':identifier,'path':'/dataset'}
        record={'response':response,'tool':name,'arguments':arguments,'cutoff':self.cutoff,
                'elapsed_ms':elapsed_ms,'queries':queries,'error':error,
                'dataset':dataset,
                'finished_at':datetime.now(timezone.utc).isoformat()}
        path=self.directory/(identifier+'.json');temporary=path.with_suffix('.tmp')
        try:
            with temporary.open('x',encoding='utf8') as out:
                json.dump(record,out,ensure_ascii=False,allow_nan=False)
                out.flush();os.fsync(out.fileno())
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)
        self.calls.append(record)
        return response

    def reference(self, ref, kind):
        if not isinstance(ref,dict) or set(ref)!={'tool_run_id','path'}:
            raise ValueError('Reference requires tool_run_id and path')
        identifier=ref['tool_run_id']
        if not isinstance(identifier,str) or not re.fullmatch(r'cq_[a-f0-9]{32}',identifier):
            raise ValueError('Invalid reference identifier')
        if kind not in ('selection','dataset') or ref['path']!='/'+kind:
            raise ValueError('Reference kind/path mismatch')
        # An on-disk run from another analysis/session is not implicitly authorized.
        if not any(r['response']['tool_run_id']==identifier for r in self.calls):
            raise ValueError('Reference does not belong to this analysis')
        record=json.loads((self.directory/(identifier+'.json')).read_text(encoding='utf8'))
        if record['cutoff']!=self.cutoff or record['error']:
            raise ValueError('Reference cutoff or execution status mismatch')
        if kind=='dataset' and record.get('dataset') is not None:
            return copy.deepcopy(record['dataset'])
        try:return copy.deepcopy(record['response']['result'][kind])
        except KeyError:raise ValueError('Requested reference kind is unavailable') from None

    def page(self, ref, offset, limit):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Page requires nonnegative offset and limit 1..100')
        dataset=self.reference(ref,'dataset')
        items=dataset.get('display_items',dataset['items'])
        return {'items':items[offset:offset+limit],'page':{'offset':offset,'complete':offset+limit>=len(items),
            'next_offset':offset+limit if offset+limit<len(items) else None},'data_scope':dataset['scope'],
            'dataset_ref':ref}
