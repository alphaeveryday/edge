def section():
    return {'paragraphs': [text * 40 for text in ['가', '나', '다']],
            'evidence': [{'source_id': 's1', 'passage_id': 'p1'}], 'limitations': []}


def final_payload(sections=None):
    evaluation = {'scores': {'leadership': 4, 'profitability': 3.5, 'stability': 3}, 'reason': section()}
    return {**(sections or {role: section() for role in ['question', 'portfolio', 'outlook', 'opinion']}),
            'current_evaluation': evaluation,
            'recommendation': {'title': '장기 분산 구성', 'allocations': [{'instrument_id': 'US:VOO', 'account': '일반 계좌', 'weight_pct': 100}], 'evaluation': evaluation},
            'insights': [{'title': '지금 가장 먼저 판단할 것', 'content': section()}]}
