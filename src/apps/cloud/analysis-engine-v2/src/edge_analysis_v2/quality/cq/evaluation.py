"""Code checks and evidence-bound semantic reviews. No numerical recomputation."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator
HERE=Path(__file__).parent
CONTRACT=HERE/'contract.json'
CASES=HERE/'cases.json'
TITLES=['기업·업종 분산','거시·가격·수급 점검','수주의 신규·변경 확인','두 기업의 실적 비교',
        '한 달 투자 전망','오늘 가격변동 설명','조건에 맞는 사건 탐색','이전 설명 이후 변화',
        '사건 당시와 현재 보유','구성종목 상승·하락 분포','발표 계획의 실제 진행','기대 대비 실적','전망의 반대 근거']
OUTPUT={'type':'object','required':['answer','claims','limitations'],'properties':{
    'answer':{'type':'string','minLength':1},'limitations':{'type':'array','items':{'type':'string'}},
    'claims':{'type':'array','minItems':1,'items':{'type':'object',
        'required':['claim','purpose','tool_run_ids','evidence_role'],'properties':{
            'claim':{'type':'string','minLength':1},'purpose':{'type':'string','minLength':1},
            'tool_run_ids':{'type':'array','minItems':1,'uniqueItems':True,'items':{'type':'string'}},
            'evidence_role':{'enum':['support','limitation']}}}}}}


def read(path):return json.loads(Path(path).read_text(encoding='utf8'))


def contract_for(case):
    match=re.match(r'^(CQ(?:0[1-9]|1[0-3]))(?:-|$)',case)
    if not match:return None
    identifier=match[1];definition=read(CONTRACT);cases=read(CASES)
    question=next(c for c in cases['cases'] if c['id']==identifier)
    scope=definition['cases'][identifier]
    result={'id':identifier,'title':TITLES[int(identifier[2:])-1],'version':definition['version'],'question':question['canonical_question'],
        'common':definition['common'],'excluded':definition['excluded'],
        'required_findings':[{'id':f'{identifier}.{i+1}','name':name,'group':'cq',
            'rule':scope['completion']+' 각 항목은 실제 질문의 대상·날짜를 적용한다.'}
            for i,name in enumerate(question['criteria'])],
        'critical_errors':scope['critical_errors'],'completion_conditions':scope['completion'],
        'reference_path':scope['path'],'reference_execution':None,
        'process':[{k:r[k] for k in ('id','name','group','rule')} for r in definition.get('process',[]) if identifier in r['cases']]}
    result['criteria']=quality_rules(result)
    return result


def quality_rules(contract):
    return contract['common']+[
        {'id':'fulfillment','name':'요구 충족','group':'cq',
         'rule':'CQ 계약의 required_findings와 completion_conditions를 실제 질문의 대상·날짜에 맞게 모두 확인한다. '
                'critical_errors는 별도 점수가 아닌 실패 조건이다. 한 가지라도 해당하면 fail이며 다른 장점으로 상쇄하지 않는다. '
                '자료 부재를 올바르게 처리한 경우와 필수 목적을 누락한 경우를 구별한다. 판단 자료 부족이면 unknown이다. '
                # Two judges split on a run whose answer said "disclosed" and whose claim said "signed".
                '치명 오류와 충족 조건은 고객이 읽는 답변 본문과 한계 항목으로 판정한다. 근거 항목(claims)에만 있는 표현은 사실 정확성에서 본다.'}]+contract.get('process',[])


def freeze_contract(directory,case):
    contract=contract_for(case)
    if contract:
        path=Path(directory)/'evaluation-contract.json'
        with path.open('x',encoding='utf8') as stream:json.dump(contract,stream,ensure_ascii=False,indent=2)


def records(directory):
    calls=[]
    for path in sorted((directory/'tools').glob('cq_*.json')):
        value=read(path)
        if value['response']['tool_run_id']!=path.stem:raise ValueError('Evidence filename/ID mismatch')
        calls.append(value)
    return sorted(calls,key=lambda r:(r.get('finished_at',''),r['response']['tool_run_id']))


def fingerprint(directory, report, calls, contract):
    payload={k:report.get(k) for k in ('question','cutoff','status','response','agent_version')}
    payload.update(calls=calls,contract=contract)
    digest=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode())
    for name in ('events.jsonl','workspace.json'):
        path=directory/'model'/name
        if path.exists():digest.update(path.read_bytes())
    return digest.hexdigest()


def code_checks(report,calls):
    response=report.get('response') or {};problems=[]
    for error in Draft202012Validator(OUTPUT).iter_errors(response):
        problems.append('/'+ '/'.join(map(str,error.absolute_path))+': '+error.message)
    if not isinstance(response,dict):response={}
    if isinstance(response.get('answer'),str) and not response['answer'].strip():problems.append('빈 답변')
    saved={r['response']['tool_run_id']:r for r in calls};bad=[]
    for index,claim in enumerate(response.get('claims',[]) if isinstance(response.get('claims'),list) else []):
        if not isinstance(claim,dict):continue
        refs=claim.get('tool_run_ids',[])
        if not isinstance(refs,list):bad.append(f'claims/{index}: 잘못된 참조 형식');continue
        for identifier in refs:
            record=saved.get(identifier) if isinstance(identifier,str) else None
            if record is None or (record.get('error') and claim.get('evidence_role')!='limitation'):
                bad.append(f'claims/{index}: {identifier}')
    def instant(value):
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError('Timezone required')
        return parsed
    mismatch=[]
    try:
        cutoff=instant(report['cutoff'])
        for r in calls:
            if instant(r['cutoff'])!=cutoff:mismatch.append(r['response']['tool_run_id'])
    except (ValueError,KeyError,TypeError,AttributeError):mismatch.append('기준시각 형식 미확인')
    return [{'id':identifier,'name':name,'method':'code','status':'fail' if errors else 'pass',
             'reason':' / '.join(errors) if errors else success}
        for identifier,name,errors,success in [
            ('output','출력 구조·필수 답변',problems,'답변·주장·근거 필드 형식 확인'),
            ('references','저장 근거 참조',bad,'저장 ID 존재와 성공/실패 근거의 사용 범위 확인. 의미 지지는 별도 평가'),
            ('cutoff','도구 기준시각 일치',mismatch,'도구 실행 기준시각 일치. 개별 자료의 가용시각·의미는 별도 평가')]]


def efficiency(directory,report,calls):
    cost=[];usage={};events=directory/'model/events.jsonl'
    if events.exists():
        with events.open(encoding='utf8') as stream:
            for line in stream:
                try:event=json.loads(line)
                except json.JSONDecodeError:continue
                if event.get('message_type')!='ResultMessage':continue
                message=event['message'];value=message.get('total_cost_usd')
                if isinstance(value,(float,int)) and not isinstance(value,bool):cost.append(value)
                for key,value in (message.get('usage') or {}).items():
                    if key.endswith('tokens') and type(value) is int:usage[key]=usage.get(key,0)+value
    return {'actual_calls':len(calls),'model_calls':report.get('model_tool_calls'),
        'graph_queries':report.get('graph_queries'),'elapsed_ms':report.get('elapsed_ms'),
        'tool_errors':sum(bool(c.get('error')) for c in calls),
        'sdk_estimated_usd':sum(cost) if cost else None,'usage':usage,
        'reference_calls':None,'reference_reason':'같은 질문·자료·하네스의 검증된 참조 실행이 아직 지정되지 않았습니다.',
        'unnecessary_calls':None,'unnecessary_tool_ms':None}


def validate_review(review,checks,report,calls):
    if not isinstance(review,dict):raise ValueError('Review must be an object')
    expected={r['id'] for r in checks};rows=review.get('checks',[])
    if not isinstance(rows,list) or not all(isinstance(r,dict) and isinstance(r.get('id'),str) for r in rows) or len(rows)!=len(expected) or {r.get('id') for r in rows}!=expected:
        raise ValueError('Every criterion must be reviewed exactly once')
    evidence={c['response']['tool_run_id'] for c in calls}|{'workspace','trace'}
    response=report.get('response') or {}
    if not isinstance(response,dict):response={}
    text='\n'.join([s for s in [response.get('answer',''),*(response.get('limitations') or []),
        *[r.get('claim','') for r in response.get('claims',[]) if isinstance(r,dict)]] if isinstance(s,str)])
    for row in rows:
        if row.get('status') not in ('pass','fail','unknown') or not isinstance(row.get('reason'),str) or not row['reason'].strip():
            raise ValueError('A verdict and specific reason are required')
        span=row.get('answer_span');refs=row.get('evidence_ids')
        if not isinstance(span,str) or (span and span not in text):raise ValueError('Answer quote must exist verbatim')
        if not isinstance(refs,list) or any(not isinstance(r,str) or r not in evidence for r in refs):
            raise ValueError('Review cites unsaved evidence')
        if row['status']=='pass' and (not span or not refs):raise ValueError('Passing needs an answer quote and saved evidence')
    seen=set()
    duplicates=review.get('unnecessary_calls',[])
    if not isinstance(duplicates,list) or not all(isinstance(r,dict) for r in duplicates):raise ValueError('Invalid unnecessary calls')
    for row in duplicates:
        identifier=row.get('tool_run_id')
        if identifier not in evidence or identifier in seen or not str(row.get('reason','')).strip():
            raise ValueError('Unnecessary calls need distinct saved IDs and reasons')
        seen.add(identifier)


def evaluate(directory):
    directory=Path(directory);report=read(directory/'benchmark.json');calls=records(directory)
    frozen=directory/'evaluation-contract.json'
    contract=read(frozen) if frozen.exists() else contract_for(report.get('case',directory.name));code=code_checks(report,calls)
    rules=contract.get('criteria',quality_rules(contract)) if contract else []
    checks=[{**r,'method':'agent','status':'not_evaluated','reason':'새 계약으로 평가하지 않았습니다.',
        'answer_span':'','evidence_ids':[]} for r in rules]
    signature=fingerprint(directory,report,calls,contract);review_status='missing';reviewer=None;review=None
    path=directory/'evaluation.json'
    if path.exists():
        try:
            review=read(path)
            if review['fingerprint']!=signature:review_status='stale'
            else:
                validate_review(review,checks,report,calls)
                review_status='current';reviewer=review.get('reviewer')
                by_id={r['id']:r for r in review['checks']}
                checks=[{**r,**by_id[r['id']]} for r in checks]
        except (ValueError,KeyError,TypeError):review_status='invalid'
    stats=efficiency(directory,report,calls)
    if review_status=='current':
        duplicate={r['tool_run_id'] for r in review.get('unnecessary_calls',[])}
        stats.update(unnecessary_calls=len(duplicate),unnecessary_tool_ms=sum(c.get('elapsed_ms',0) for c in calls if c['response']['tool_run_id'] in duplicate))
    all_checks=code+checks
    if any(r['status']=='fail' for r in all_checks):overall='fail'
    elif report.get('status')!='answered':overall='incomplete'
    elif not contract or review_status!='current':overall='not_evaluated'
    elif any(r['status']=='unknown' for r in checks):overall='review_needed'
    else:overall='pass'
    proposal=overall
    if overall=='pass' and reviewer and reviewer.get('calibration')=='not_calibrated':overall='review_needed'
    return {'version':1,'contract':contract,'fingerprint':signature,'code_checks':code,'agent_checks':checks,
        'overall':overall,'agent_proposal':proposal,'review_status':review_status,'reviewer':reviewer,'efficiency':stats,
        'unnecessary_calls':review.get('unnecessary_calls',[]) if review_status=='current' else [],
        'excluded':['계산 재현','문장 내 수치의 자동 일치 판정']}


def save_review(directory,review,*,reviewer,expected_fingerprint=None):
    directory=Path(directory);result=evaluate(directory)
    if expected_fingerprint and result['fingerprint']!=expected_fingerprint:raise ValueError('Run changed during evaluation')
    if not result['contract']:raise ValueError('Canonical CQ contract required')
    validate_review(review,result['agent_checks'],read(directory/'benchmark.json'),records(directory))
    value={**review,'fingerprint':result['fingerprint'],'reviewer':reviewer,
        'reviewed_at':datetime.now(timezone.utc).isoformat(),'version':1}
    path=directory/'evaluation.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temp,path)
    return evaluate(directory)
