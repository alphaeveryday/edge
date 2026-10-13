"""Record task completion separately from a syntactically valid report."""
from datetime import date


def data_status(market, optimizer, sources):
    results = list(market.results.values())
    history = 'available' if any(row.get('status') == 'available' for row in results) else 'unavailable' if market.failures else 'not_requested'
    if history == 'available' and market.failures:
        history = 'partial'
    if history == 'not_requested' and optimizer.result and optimizer.result.get('status') == 'unavailable':
        history = 'unavailable'
    return {'history': history, 'price_failures': dict(market.failures),
            'candidate_search': optimizer.result['status'] if optimizer.result else 'not_requested',
            'sources': 'available' if sources else 'not_requested', 'forecast': 'not_estimated'}


def assess_completion(final, request, decision, published, sources, as_of):
    questions = decision.questions or ([request.question] if request.question else [])
    answers = {}
    for answer in final.answers:
        if answer.question_index > len(questions) or answer.question_index in answers:
            raise ValueError('ANSWER_QUESTION_INDEX: answer each actual question once')
        if answer.status != 'answered' and not answer.missing:
            raise ValueError('ANSWER_MISSING_REASON: state the exact input or research still needed')
        answers[answer.question_index] = answer.model_dump(mode='json')
    tasks = [{'question': question, **answers.get(index, {'question_index': index, 'status': 'partial',
               'answer': '', 'missing': ['QUESTION_NOT_ANSWERED']})} for index, question in enumerate(questions, 1)]
    today = date.fromisoformat(as_of[:10])
    by_id = {source['id']: source for source in sources}
    coverage = {}
    max_age = {'holdings': 90, 'fees': 365, 'structure': 365, 'distributions': 450}
    for item in final.evidence_coverage:
        key = (item.instrument_id, item.field)
        if key in coverage:
            raise ValueError('DUPLICATE_EVIDENCE_COVERAGE')
        text = []
        for evidence in item.evidence:
            passage = next((p for p in by_id.get(evidence.source_id, {}).get('passages', []) if p['id'] == evidence.passage_id), None)
            if passage is None:
                raise ValueError('COVERAGE_EVIDENCE_NOT_REGISTERED')
            text.append(passage['text'])
        status = 'missing'
        if item.status == 'supported' and text:
            claim_date = item.claim_as_of
            date_texts = {claim_date.isoformat(), claim_date.strftime('%Y.%m.%d'), claim_date.strftime('%Y/%m/%d')} if claim_date else set()
            if claim_date and any(value in '\n'.join(text) for value in date_texts):
                age = (today - claim_date).days
                status = 'supported' if 0 <= age <= max_age[item.field] else 'stale'
            else:
                status = 'date_unknown'
        coverage[key] = {**item.model_dump(mode='json'), 'status': status}
    recommendation = published.get('recommendation')
    fields = ['structure', 'holdings', 'fees'] + (['distributions'] if 'income' in decision.objectives else [])
    required = {(row['instrument_id'], field) for row in recommendation['allocations'] for field in fields} if recommendation else set()
    missing_research = [{'instrument_id': key, 'field': field, 'status': coverage.get((key, field), {}).get('status', 'missing')}
                        for key, field in sorted(required) if coverage.get((key, field), {}).get('status') != 'supported']
    execution = recommendation.get('execution') if recommendation else None
    reasons = []
    if any(task['status'] != 'answered' for task in tasks):
        reasons.append('UNANSWERED_QUESTIONS')
    if missing_research:
        reasons.append('RESEARCH_COVERAGE_INCOMPLETE')
    if decision.kind in {'rebalance', 'new_money'} and recommendation and not execution:
        reasons.append('EXECUTION_PLAN_MISSING')
    needs_input = bool(decision.missing_inputs or final.withheld_reason == 'missing_input' or any(task['status'] == 'needs_input' for task in tasks) or execution and execution['status'] == 'needs_input')
    if final.withheld_reason == 'missing_data':
        reasons.append('MISSING_RESEARCH_DATA')
    optimization = published.get('optimization') or {}
    if not recommendation and optimization.get('reason') in {'CURRENT_WEIGHTS_REQUIRED', 'CUSTOMER_CONSTRAINT_UNVERIFIED'}:
        needs_input = True
    if not recommendation and not optimization.get('reason') and not final.withheld_reason:
        reasons.append('WITHHOLDING_REASON_MISSING')
    status = 'needs_input' if needs_input else 'partial' if reasons else 'no_match' if not recommendation else 'complete'
    return {'status': status, 'tasks': tasks, 'reasons': reasons, 'missing_inputs': decision.missing_inputs,
            'evidence_coverage': list(coverage.values()), 'missing_research': missing_research,
            'note': '자료 항목·날짜·실행 조건의 충족 상태이며 주장 자체의 사실 정확성을 보증하지 않습니다.'}
