"""Deterministic adversarial contract benchmark, explicitly not live-web accuracy."""
from copy import deepcopy
from hashlib import sha256
import json
from .pit import Gate, inspect, instant

CUTOFF = '2025-01-03T18:00:00+09:00'
OLD = '2025-01-03T08:00:00Z'
FUTURE = '2025-01-03T09:00:01Z'
SURFACES = ('news','search_title','search_snippet','pdf','redirect','cache','filing','macro_revision','holdings','price')


def document(surface):
    content = f'{surface}: previously announced event scheduled for 2027'
    if surface == 'price':
        content=json.dumps(dict(date='2025-01-03',open='100',close='101'))
    return dict(id=surface,kind=surface,content=content,url='https://archive.example/'+surface,
        published_at=OLD,available_at=OLD,version_at=OLD,captured_at=OLD,
        evidence='test:independently-labelled-fixture',sha256=sha256(content.encode()).hexdigest())


def cases():
    output=[]
    for surface in SURFACES:
        base=document(surface)
        variants = {}
        for key in ('published_at','available_at','version_at','captured_at'):
            value=deepcopy(base)
            value[key]=FUTURE
            variants['future_'+key]=value
        # Different payload routes, not repetitions with shifted calendar dates.
        for route in ('snippet','attachment','redirect_target','cached_body','revised_table','nested_attachment'):
            value=deepcopy(base)
            child=document(route)
            child['available_at']=FUTURE
            value['components']=[dict(document('container'),components=[child])] if route=='nested_attachment' else [child]
            variants[route]=value
        for attack,value in variants.items():
            output.append(dict(id=surface+':'+attack,category=attack,surface=surface,label='future',record=value,
                rationale='At least one delivered component became available or was captured after the fixed cutoff.'))
        for variant in ('ordinary','future_schedule','same_instant_other_timezone'):
            value=deepcopy(base)
            if variant=='same_instant_other_timezone':
                value.update({k:CUTOFF for k in ('published_at','available_at','version_at','captured_at')})
            output.append(dict(id=surface+':'+variant,category=variant,surface=surface,label='past',record=value,
                rationale='All version and capture evidence is at/before cutoff; future event date is allowed.'))
        for missing in ('published_at','version_at','evidence'):
            value=deepcopy(base)
            del value[missing]
            output.append(dict(id=surface+':missing_'+missing,category='missing_'+missing,surface=surface,label='unknown',record=value,
                rationale='Insufficient evidence; strict mode must refuse.'))
        for attack in ('hash_tampering','timezone_missing','conflicting_capture'):
            value=deepcopy(base)
            if attack=='hash_tampering':
                value['content']='Different body under the old digest'
            elif attack=='timezone_missing':
                value['version_at']='2025-01-03T08:00:00'
            else:
                value['captured_at']='2025-01-02T00:00:00Z'
            output.append(dict(id=surface+':'+attack,category=attack,surface=surface,label='unknown',record=value,
                rationale='Conflicting/incomplete archive proof cannot establish historical availability.'))
    return output


def run_benchmark():
    results=[]
    for case in cases():
        status,reason=inspect(case['record'],instant(CUTOFF))
        gate=Gate(CUTOFF,[case['record']])
        delivered=bool(gate.price_history('2025-01-03',60) if case['record']['kind']=='price'
                       else gate.call('read',{'id':case['record']['id']})['items'])
        results.append({k:v for k,v in case.items() if k!='record'} | dict(status=status,reason=reason,
            delivered=delivered,passed=delivered==(case['label']=='past')))
    bypass=[]
    for tool,args in [('sql',{'sql':'SELECT * FROM document'}),('fetch',{'url':'https://example.org'}),
        ('search',{'query':'x','cutoff':FUTURE}),('read',{'id':'x','as_of':FUTURE}),
        ('shell',{'command':'curl example.org'}),('search',{'query':{'$ne':''}})]:
        try:
            Gate(CUTOFF,[]).call(tool,args)
            refused=False
        except ValueError:
            refused=True
        bypass.append(dict(tool=tool,arguments=args,refused=refused))
    future=[r for r in results if r['label']=='future']
    past=[r for r in results if r['label']=='past']
    categories={}
    for r in results:
        counts=categories.setdefault(r['category'],dict(total=0,passed=0))
        counts['total']+=1
        counts['passed']+=r['passed']
    return dict(kind='synthetic_contract_benchmark',live_web_verified=False,total=len(results),
        future_count=len(future),past_count=len(past),
        block_rate=sum(not r['delivered'] for r in future)/len(future),
        leak_rate=sum(r['delivered'] for r in future)/len(future),
        false_block_rate=sum(not r['delivered'] for r in past)/len(past),
        past_delivery_rate=sum(r['delivered'] for r in past)/len(past),
        unknown_rate=sum(r['status']=='unknown' for r in results)/len(results),
        categories=categories,cases=results,bypass=bypass,
        passed=all(r['passed'] for r in results) and all(r['refused'] for r in bypass))
