"""Record task completion separately from a syntactically valid report."""
from datetime import date
import calendar
import re


def retain_recommendation(final, previous):
    """Retain an accepted judgment when only optional research or inputs remain."""
    if previous is None or previous.recommendation is None or final.recommendation is not None or final.withheld_reason not in {'quality_failed', 'missing_data', 'missing_input'}:
        return final
    fields = ('recommendation', 'selected_candidate_id')
    return type(final).model_validate({**final.model_dump(), **{key: previous.model_dump()[key] for key in fields},
                                      'withheld_reason': None, 'return_forecast': None})


def retain_research(final, previous, *, retain_answers=True):
    """Keep product evidence across allocations; answers belong to the same decision."""
    if previous is None:
        return final
    def target(report):
        return sorted((row.instrument_id, row.account, row.weight_pct) for row in report.recommendation.allocations) if report.recommendation else None
    answer_ids = {answer.question_index for answer in final.answers}
    answers = [*final.answers, *(answer for answer in previous.answers
        if retain_answers and target(final) == target(previous) and answer.question_index not in answer_ids)]
    coverage_ids = {(item.instrument_id, item.field) for item in final.evidence_coverage}
    coverage = [*final.evidence_coverage, *(item for item in previous.evidence_coverage if (item.instrument_id, item.field) not in coverage_ids)]
    return type(final).model_validate({**final.model_dump(), 'answers': answers, 'evidence_coverage': coverage})


def data_status(market, optimizer, sources, observed=None):
    observed = observed or {}
    results = list(market.results.values())
    history = 'available' if observed.get('history') in {'available', 'partial'} or any(row.get('status') == 'available' for row in results) else 'unavailable' if market.failures else 'not_requested'
    if history == 'available' and (market.failures or observed.get('history') == 'partial'):
        history = 'partial'
    if history == 'not_requested' and optimizer.result and optimizer.result.get('status') == 'unavailable' and optimizer.result.get('reason') != 'CURRENT_WEIGHTS_REQUIRED':
        history = 'unavailable'
    if optimizer.result and optimizer.result.get('start_date') and optimizer.result.get('end_date'):
        history = 'partial' if market.failures else 'available'
    if history == 'not_requested':
        history = observed.get('history', history)
    source_status = 'available' if sources else observed.get('sources', 'not_requested')
    if sources and observed.get('sources') == 'unavailable':
        source_status = 'partial'
    return {'history': history, 'price_failures': dict(market.failures),
            'candidate_search': optimizer.result['status'] if optimizer.result else 'not_requested',
            'sources': source_status, 'forecast': 'not_estimated'}


def research_inventory(request, decision, published, coverage=(), considered_ids=()):
    holdings = {row.instrument_id for row in request.holdings if row.kind != 'cash' and row.instrument_id}
    recommendation = published.get('recommendation')
    selected = {row['instrument_id'] for row in recommendation['allocations']} if recommendation else set()
    optimization = published.get('optimization') or {}
    eligible = {row['instrument_id'] for candidate in optimization.get('candidates', []) for row in candidate.get('allocations', [])}
    candidates = set(considered_ids) | eligible
    by_key = {(row['instrument_id'], row['field']): row for row in coverage}
    fields = ['structure', 'holdings', 'fees'] + (['distributions'] if 'income' in decision.objectives else [])
    rejected = optimization.get('reason') in {'RATING_BELOW_MINIMUM', 'EXECUTION_INFEASIBLE', 'CUSTOMER_CONSTRAINT_NOT_SATISFIED'}
    calculation_only = not recommendation and bool(published.get('scenarios') or decision.execute_saved_target and optimization.get('reason') == 'SAVED_TARGET_ONLY')
    active = set() if calculation_only else holdings | selected | (eligible if not recommendation and not rejected else set())
    # 구조·구성종목·보수·분배는 ETF 조사 항목이다. 고객이 넣은 개별주에는 요구하지 않는다.
    stocks = {row.instrument_id for row in request.holdings if row.kind == 'stock'}
    return [{'instrument_id': key, 'field': field, 'status': by_key.get((key, field), {}).get('status', 'missing'),
             'roles': [role for role, ids in [('holding', holdings), ('candidate', candidates), ('selected', selected)] if key in ids],
             'required': key in active}
            for key in sorted((holdings | selected | candidates | {key for key, _ in by_key}) - {'CASH:KRW'} - stocks) for field in fields]


def assess_completion(final, request, decision, published, sources, as_of, *, considered_ids=()):
    questions = decision.questions or ([request.question] if request.question else [])
    answers = {}
    for answer in final.answers:
        index = 1 if len(questions) == 1 else answer.question_index
        if index > len(questions) or len(questions) != 1 and index in answers:
            raise ValueError('ANSWER_QUESTION_INDEX: answer each actual question once')
        if answer.status != 'answered' and not answer.missing:
            raise ValueError('ANSWER_MISSING_REASON: state the exact input or research still needed')
        part = {**answer.model_dump(mode='json'), 'question_index': index}
        if index in answers:
            previous = answers[index]
            part['answer'] = previous['answer'] + '\n' + part['answer']
            part['missing'] = list(dict.fromkeys([*previous['missing'], *part['missing']]))
            part['status'] = next(status for status in ('needs_input', 'partial', 'answered')
                                  if status in {previous['status'], part['status']})
        answers[index] = part
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
            text.append((evidence.source_id, passage['text']))
        status = 'missing'
        if item.status == 'supported' and text:
            claim_date = item.claim_as_of
            date_texts = {claim_date.isoformat(), claim_date.strftime('%Y.%m.%d'), claim_date.strftime('%Y/%m/%d'),
                f'{claim_date.year}-{claim_date.month}-{claim_date.day}', f'{claim_date.year}.{claim_date.month}.{claim_date.day}',
                f'{claim_date.year}/{claim_date.month}/{claim_date.day}', f'{claim_date.year}년{claim_date.month}월{claim_date.day}일',
                *[f'{month}{claim_date.day}{claim_date.year}' for month in (calendar.month_name[claim_date.month], calendar.month_abbr[claim_date.month])],
                *[f'{claim_date.day}{month}{claim_date.year}' for month in (calendar.month_name[claim_date.month], calendar.month_abbr[claim_date.month])]} if claim_date else set()
            normalized = [(source_id, passage.replace(',', '').casefold()) for source_id, passage in text if not re.match(r'^\s*(?:URL|Published|Author):', passage)]
            if claim_date and (claim_date.day > 12 or claim_date.day == claim_date.month):
                date_texts.update({claim_date.strftime('%m/%d/%Y'), claim_date.strftime('%d/%m/%Y'),
                    f'{claim_date.month}/{claim_date.day}/{claim_date.year}', f'{claim_date.day}/{claim_date.month}/{claim_date.year}'})
            dated_sources = {source_id for source_id, passage in normalized if any(
                re.search(r'(?<!\d)' + r'\s*'.join(re.escape(char) for char in value.casefold()) + r'(?!\d)', passage) for value in date_texts)}
            if claim_date and dated_sources == {source_id for source_id, _ in text}:
                age = (today - claim_date).days
                status = 'supported' if 0 <= age <= max_age[item.field] else 'stale'
            else:
                status = 'date_unknown'
        coverage[key] = {**item.model_dump(mode='json'), 'status': status}
    recommendation = published.get('recommendation')
    inventory = research_inventory(request, decision, published, coverage.values(), considered_ids)
    missing_research = [{key: item[key] for key in ('instrument_id', 'field', 'status')}
                        for item in inventory if item['required'] and item['status'] != 'supported']
    execution = recommendation.get('execution') if recommendation else published.get('execution')
    optional_trade_amounts = bool(recommendation
        and not decision.execute_saved_target and not decision.execution_requested and execution
        and execution.get('reasons') == ['CURRENT_AMOUNTS_REQUIRED'])
    required_execution = None if optional_trade_amounts else execution
    reasons = []
    if any(task['status'] != 'answered' for task in tasks):
        reasons.append('UNANSWERED_QUESTIONS')
    if missing_research:
        reasons.append('RESEARCH_COVERAGE_INCOMPLETE')
    if decision.execution_requested and recommendation and not execution:
        reasons.append('EXECUTION_PLAN_MISSING')
    # Interpretation-stage questions are suggestions, not requirements for every task.
    # Only an unanswered customer task or a calculator can require customer input.
    missing_inputs = list(dict.fromkeys(value for task in tasks if task['status'] == 'needs_input' for value in task['missing']))
    needs_input = bool(any(task['status'] == 'needs_input' for task in tasks) or required_execution and required_execution['status'] == 'needs_input')
    scenario_available = published.get('scenarios', {}).get('status') == 'available'
    if decision.scenario_requested or published.get('scenarios'):
        if not scenario_available:
            reasons.append('SCENARIO_CALCULATION_REQUIRED')
    if published.get('scenarios', {}).get('status') == 'needs_input':
        needs_input = True
    if final.withheld_reason == 'missing_data':
        reasons.append('MISSING_RESEARCH_DATA')
    optimization = published.get('optimization') or {}
    if not recommendation and optimization.get('status') == 'unavailable':
        reasons.append('COMPARISON_DATA_UNAVAILABLE')
    if not recommendation and optimization.get('reason') == 'CURRENT_WEIGHTS_REQUIRED':
        needs_input = True
    input_labels = {
        'CURRENT_WEIGHTS_REQUIRED': '종목별 평가금액 또는 보유 비중',
        'CURRENT_AMOUNTS_REQUIRED': '종목별 현재 평가금액 또는 투자 원금',
        'ADDITIONAL_FUNDING_REQUIRED': '추가 투자금 또는 매달 납입할 금액',
        'INVESTMENT_AMOUNT_REQUIRED': '투자할 금액',
        'ACCOUNT_OPENING_REQUIRED': '계좌 보유 여부 또는 개설 가능 여부',
        'ACCOUNT_WITHDRAWAL_REVIEW_REQUIRED': '계좌에서 인출 가능한 금액과 중도 인출 조건',
        'ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED': '계좌의 남은 납입 가능 금액',
    }
    input_reasons = [optimization.get('reason')] if not recommendation else []
    for calculation in (required_execution, published.get('scenarios')):
        if calculation and calculation.get('status') == 'needs_input':
            input_reasons.extend(calculation.get('reasons', []) or [calculation.get('reason')])
    for reason in input_reasons:
        code, _, account = (reason or '').partition(':')
        if code in input_labels:
            missing_inputs.append((account + ' ' if account else '') + input_labels[code])
        elif code == 'CUSTOMER_CONSTRAINT_UNVERIFIED':
            if any(rule.kind == 'no_sell' for rule in decision.constraints):
                from .execution import resolve_funding
                funding = resolve_funding(request, decision)
                if funding is None:
                    missing_inputs.append(input_labels['ADDITIONAL_FUNDING_REQUIRED'])
    missing_inputs = list(dict.fromkeys(missing_inputs))
    if final.withheld_reason == 'missing_input' and not needs_input:
        reasons.append('INPUT_REQUIREMENT_UNVERIFIED')
    if not recommendation and optimization.get('reason') in (None, 'RECOMMENDATION_WITHHELD') and not final.withheld_reason:
        reasons.append('WITHHOLDING_REASON_MISSING')
    executed_saved_target = decision.execute_saved_target and not recommendation and execution and execution['status'] in {'ready', 'conditional'}
    status = 'needs_input' if needs_input else 'partial' if reasons else 'complete' if scenario_available or executed_saved_target else 'no_match' if not recommendation else 'complete'
    return {'status': status, 'tasks': tasks, 'reasons': reasons, 'missing_inputs': missing_inputs,
            'evidence_coverage': list(coverage.values()), 'missing_research': missing_research, 'research_inventory': inventory,
            'note': '자료 항목·날짜·실행 조건의 충족 상태이며 주장 자체의 사실 정확성을 보증하지 않습니다.'}
