"""Analysis-local immutable tool evidence. A reference exists only after atomic persistence."""
import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from edge_analysis_v2.tools.graph.shape import fit


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
            if dataset.get('selection') is not None:
                response['result']['selection_ref']={'tool_run_id':identifier,'path':'/selection'}
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
        if not isinstance(ref,dict) or 'tool_run_id' not in ref or set(ref)-{'tool_run_id','path'}:
            raise ValueError('Reference requires tool_run_id')
        identifier=ref['tool_run_id']
        if not isinstance(identifier,str) or not re.fullmatch(r'cq_[a-f0-9]{32}',identifier):
            raise ValueError('Invalid reference identifier')
        # The path names a part of the run, and the tool already knows which part it reads. Requiring the
        # caller to match it failed 9 calls in 45 runs that passed the right run with the other path.
        if kind not in ('selection','dataset') or ref.get('path','/'+kind) not in ('/selection','/dataset'):
            raise ValueError('Reference path must be /selection or /dataset')
        # An on-disk run from another analysis/session is not implicitly authorized.
        if not any(r['response']['tool_run_id']==identifier for r in self.calls):
            raise ValueError('Reference does not belong to this analysis')
        record=json.loads((self.directory/(identifier+'.json')).read_text(encoding='utf8'))
        if record['cutoff']!=self.cutoff or record['error']:
            raise ValueError('Reference cutoff or execution status mismatch')
        if kind=='dataset' and record.get('dataset') is not None:
            return copy.deepcopy(record['dataset'])
        if kind=='selection' and (record.get('dataset') or {}).get('selection') is not None:
            return copy.deepcopy(record['dataset']['selection'])
        try:return copy.deepcopy(record['response']['result'][kind])
        except KeyError:raise ValueError('Requested reference kind is unavailable') from None

    def page(self, ref, offset, limit):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Page requires nonnegative offset and limit 1..100')
        dataset=self.reference(ref,'dataset')
        items=dataset.get('display_items',dataset['items'])
        same=dataset.get('display_same') or {}
        limit=fit(items[offset:],limit,used=len(json.dumps(same,ensure_ascii=False,default=str)) if same else 0)
        shown=({'columns':dataset['display_columns'],'rows':items[offset:offset+limit],'same_for_all_rows':same}
               if 'display_columns' in dataset else {'items':items[offset:offset+limit]})
        return {**shown,'page':{'offset':offset,'complete':offset+limit>=len(items),
            'next_offset':offset+limit if offset+limit<len(items) else None},'data_scope':dataset['scope'],
            'dataset_ref':ref}
