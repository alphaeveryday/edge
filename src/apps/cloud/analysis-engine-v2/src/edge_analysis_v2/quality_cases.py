"""Nearby-world fixtures and reviewer goals, kept out of the model context."""
from copy import deepcopy
from datetime import timedelta

from .fixture_tools import make_fixture
from .fixture_tools.common import instant


CASES = {
    'structural_base': ('공정 개선과 예상 차이', 'baseline', '지속 가능한 개선의 원인·예상 차이·배수 가정과 반론을 연결'),
    'structural_oneoff': ('같은 수율·일회성 선별 생산', 'directional', '같은 수율 숫자로 지속적 개선을 결론 내리지 않음'),
    'structural_priced': ('개선이 이미 예상에 반영', 'directional', '옛 예상치로 시장이 개선을 놓쳤다고 주장하지 않음'),
    'structural_missing': ('예상 자료 없음', 'boundary', '사업 설명은 유지하되 없는 EPS나 목표가를 만들지 않음'),
    'structural_order': ('기사 순서·중복만 변경', 'invariant', '동일 사건의 반복 보도를 새 이익 근거로 세지 않음'),
    'structural_future': ('분석 뒤 발표된 반대 자료', 'invariant', '미래 뉴스·예상치를 읽거나 오늘 판단에 쓰지 않음'),
    'structural_macro': ('원화 강세와 고객 주문 축소', 'directional', '확인된 반대 근거로 이익·밸류 판단을 조정'),
    'structural_holdout': ('보지 않은 사례·은행 예금비용', 'heldout', '반도체 용어 없이 비용 변화·예상 반영·금리를 연결'),
}


def case_spec(name):
    """Return evaluation-only expectations, never financial source input."""
    label, relation, goal = CASES[name]
    return {'case_id':name, 'label':label, 'relation':relation, 'goal':goal,
            'reference_status':'검수용 초안 · 사용자 승인 답안 아님',
            'review_questions':[
                'B: 고객에게 전달한 핵심 판단은 무엇인가?',
                'T: 대상·기간·수치와 사건 단계가 실제 제공 자료와 맞는가?',
                'J: 중요한 연결에 원문·계산 근거가 있으며 반대 자료를 다뤘는가?',
                '가능세계: 바꾼 사실에 맞게 판단이 유지되거나 바뀌었는가?',
                '전달: 결론 먼저, 구체적 주체와 짧은 완결 불릿으로 읽히는가?'],
            'failure_examples':['수율이 좋아져 전망도 좋습니다.',
                                '가격이 올랐다는 사실만 다시 설명',
                                '확인 가능한 원문은 읽지 않고 지켜봐야 한다고 끝냄'],
            'reference_example':reference_example(name)}


def reference_example(name):
    """Describe acceptable reasoning without requiring an exact investment rating."""
    if name == 'structural_oneoff':
        return '수율 상승은 저난도 제품을 골라 만든 결과입니다.\n- SK하이닉스의 고객 인증 물량은 아직 늘지 않았어요.\n- 고난도 HBM에서도 같은 수율이 유지되는지 다음 출하 자료로 확인해야 합니다.\n- 생산성 개선을 전제로 한 이익 상향은 현재 자료로 뒷받침되지 않습니다.'
    if name == 'structural_priced':
        return '생산 회복은 확인됐지만, 최신 예상도 이미 높아졌습니다.\n- 공정 개선과 출하 회복을 함께 반영한 예상치입니다.\n- 예전 보고서보다 이익이 높다는 이유만으로 추가 상승 여력을 계산하면 같은 개선을 두 번 셉니다.'
    if name == 'structural_missing':
        return '출하 회복은 확인됐습니다.\n- 본딩 온도 조정 후 재작업이 줄고 고객 인증 물량이 늘었습니다.\n- 비용 개선을 설명하되, 제공되지 않은 예상 EPS와 PER 범위를 채우지는 않습니다. 마지막 문장은 검수 원칙이며 고객 글에는 넣지 않습니다.'
    if name == 'structural_holdout':
        return '예금비용 하락이 대출금리 인하 부담을 줄이고 있습니다.\n- 한결은행은 고금리 정기예금 만기 후 낮은 금리로 재조달했습니다.\n- 다만 기업대출 충당금이 늘어 순이익 개선 폭을 제한합니다.\n- 예금비용 감소와 충당금 증가를 함께 반영한 최신 예상으로 가격을 비교해야 합니다.'
    if name == 'structural_macro':
        return '공정은 개선됐지만, 델이 다음 달 인도 물량 축소를 요청했습니다.\n- 서버 재고 조정으로 인도 물량 20%가 이연될 수 있어요.\n- 이를 반영한 세 증권사의 내년 EPS 예상은 6,100원으로 낮아졌습니다.\n- 원화 강세는 수출 매출 환산에도 부담입니다.\n- 발표가 장 마감 뒤였다는 이유만으로 시장 미반영을 단정하지 않습니다. 마지막 문장은 검수 원칙이며 고객 글에는 그대로 넣지 않습니다.'
    return ('공정 개선은 출하로 이어졌지만, 삼성전자 인증 지연이 남았습니다.\n'
            '- SK하이닉스는 본딩 온도를 조정해 재작업을 줄였고, 같은 제품의 수율이 70%에서 82%로 올랐습니다.\n'
            '- 델 인증 출하도 15% 늘어 개선이 생산라인 안에만 머물지는 않았어요.\n'
            '예상 이익은 높아졌지만, 과거 배수를 그대로 받을지는 별개입니다.\n'
            '- 세 증권사의 2027년 EPS 예상 7,200원에는 생산 회복이 일부 반영됐습니다. 추가 고객 인증은 아직 들어가지 않았어요.\n'
            '- 과거 정상화 때의 9~11배를 적용하면 64,800~79,200원입니다. 고객 집중 위험이 남아 이번에도 같은 배수를 받을지는 확인해야 합니다.\n'
            'ETF 전체에서는 인증 지연과 금리도 함께 봐야 합니다.\n'
            '- 비중 40%인 삼성전자는 HBM 고객 인증 지연으로 다음 달 출하 일정을 조정했습니다.\n'
            '- 원/달러는 최근 한 달 거의 변하지 않았지만, 한·미 10년 금리는 각각 0.2%p 올라 배수 확대에는 부담입니다.\n'
            '이 예시는 핵심 사업 논리의 초안입니다. 차트·수급은 해당 실행의 중요도에 따라 추가하고 모든 문구를 복제하지 않습니다.')


def make_quality_fixture(name, analysis_at):
    """Change one economically meaningful fact family, preserving the cutoff.

    Args:
        name: Registered review case; held-out case is excluded from examples.
        analysis_at: Fixed analysis cutoff with explicit timezone.

    Returns:
        Synthetic raw source fixture, without reference answers or review goals.
    """
    if name not in CASES:
        raise ValueError('unknown quality case')
    data = make_fixture('baseline', analysis_at)
    cutoff = instant(analysis_at)
    at = lambda days: (cutoff-timedelta(days=days, hours=1)).isoformat()
    content = [
        ('process', 'SK하이닉스 HBM 수율 70%에서 82%로 개선',
         'SK하이닉스는 본딩 온도 조정으로 재작업 비율을 낮췄다. 같은 HBM 제품과 같은 검사 방식으로 측정한 수율이 70%에서 82%로 올랐다. 델 인증 출하량도 전월보다 15% 증가했다. 공정 변경은 전체 해당 라인에 적용됐고 다음 달에도 유지할 계획이다.', 2),
        ('expectations', '세 가상 증권사, SK하이닉스 내년 EPS 예상 6500원에서 7200원으로 상향',
         '세 증권사 평균 2027년 EPS 예상은 6500원에서 7200원으로 올랐다. 생산 회복을 일부 반영했지만 추가 고객 인증은 반영하지 않았다. 회사 공정 자료의 적용 범위가 확인된 뒤 작성된 예상이다. 단일 보고서가 아닌 동일 회계연도의 세 보고서 평균이며 시장 전체의 생각을 직접 측정한 것은 아니다.', 1),
        ('multiple', '가상 리서치: 출하 정상화 구간 선행 PER 9~11배, 고객 집중은 할인 요인',
         '동일 사업 구성을 가진 과거 세 차례 출하 정상화 국면에서 다음 연도 EPS 기준 PER은 9~11배였다. 당시에도 주요 고객 집중과 가격 하락 위험이 있었다. 이번에도 같은 위험이 남아 9~11배를 비교 범위로 제시했다. 고객 다변화나 장기 공급가격 확정 시 이 범위 자체를 다시 검토해야 한다. 다음 달 고객 인증 출하 발표가 가장 가까운 확인 일정이다.', 1),
        ('counter', '삼성전자 일부 HBM 고객 인증 지연, 원화 강세는 수출 채산성 부담',
         '삼성전자는 일부 HBM 고객 인증이 늦어져 다음 달 출하 일정을 조정했다. 이번 ETF에 편입된 두 기업은 달러 매출 비중이 높다. 원화 강세는 원화 환산 매출에 부담이지만 수입 장비 비용도 줄인다. 이번 기사에는 기업별 순달러 노출액이 없어 순이익 영향을 수치로 제시하지 않았다.', 1),
    ]
    if name == 'structural_oneoff':
        content[0] = ('process', content[0][1], 'SK하이닉스의 수율 82%는 저난도 제품을 골라 생산한 일주일간의 결과다. 기존 수율 70%와 제품 구성이 달라 직접 생산성 비교는 어렵다. 본딩 공정 변경은 아직 시험 단계이며 델 인증 출하량은 전월과 같았다. 다음 달 전체 라인 적용 일정은 확정되지 않았다.', 2)
        content[1] = ('expectations', '가상 증권사 EPS 예상 유지, 생산 개선 확인 대기', '세 증권사는 2027년 EPS 예상을 6500원으로 유지했다. 선별 생산의 수율을 전체 라인 이익으로 확대하지 않았다.', 1)
    if name == 'structural_priced':
        content[1] = ('expectations', '세 가상 증권사, 고객 인증 회복까지 EPS 7200원에 반영', '세 증권사는 생산 개선과 추가 고객 인증 물량을 모두 반영해 2027년 EPS 7200원을 예상했다. 전월 6500원 예상은 공정 변경 전 자료다. 현재가도 이 상향 보고서 공개 후 형성됐다.', 1)
    if name == 'structural_macro':
        content.append(('orders', '델, HBM 다음 달 인도 물량 20% 축소 요청', '델은 서버 재고 조정으로 SK하이닉스의 다음 달 인도 물량을 20% 줄이도록 요청했다. 공정 개선은 유지됐지만 고객 인증 물량 중 일부가 이연된다. 세 증권사는 이를 반영해 2027년 EPS를 6100원으로 낮췄다.', .25))
        last_fx = max((r for r in data['macro'] if r['series']=='usd_krw'), key=lambda r:r['observed_at'])
        last_fx['value'] -= 80
    content.append(('old-estimate', '공정 변경 전 가상 증권사 3곳 평균 내년 EPS 6500원', '세 증권사의 2027년 EPS 평균은 6500원이다. 기존 공정과 고객 인증 출하 계획을 사용한 예상이다.', 5))
    data['news'] = [dict(news_id=identity, title=title, body=body, thread_id='production' if identity in ('process','expectations','orders') else identity,
                         stage=identity, event_id=identity, published_at=at(days), available_at=at(days))
                    for identity,title,body,days in content]
    # Sources precede the latest close; no scenario encodes a post-release price
    # with an earlier timestamp or treats publication as proof of surprise.
    released_days = {'process':5, 'expectations':4, 'multiple':4, 'counter':3, 'old-estimate':9}
    for row in data['news']:
        if row['news_id'] in released_days:
            row.update(published_at=at(released_days[row['news_id']]), available_at=at(released_days[row['news_id']]))
    eps_new = 6500 if name == 'structural_oneoff' else 6100 if name == 'structural_macro' else 7200
    common = dict(instrument_id='000660', metric='eps', unit='KRW_per_share', period='2027', kind='estimate', author='가상 증권사 3곳 평균')
    data['financial_observations'] = [common | dict(observation_id=identity, value=value, news_id='old-estimate' if identity=='eps-old' else 'orders' if value==6100 else 'expectations', published_at=at(days), available_at=at(days))
                                      for identity,value,days in [('eps-old',6500,9),('eps-new',eps_new,.25 if name == 'structural_macro' else 4)]]
    data['macro'] = [r for r in data['macro'] if r['series'] != 'kr_cpi_yoy']
    data['macro'].extend(dict(series='kr_cpi_yoy', unit='percent', value=value, reference_period=period,
                             observed_at=timestamp, available_at=timestamp)
                         for period,value,timestamp in [('2026-07',1.9,'2026-08-04T08:00:00+09:00'),
                                                        ('2026-08',2.1,'2026-09-02T08:00:00+09:00')])
    if name == 'structural_missing':
        data['financial_observations'] = []
        data['news'] = [r for r in data['news'] if r['news_id'] not in ('expectations','multiple','old-estimate')]
    if name == 'structural_priced':
        latest = max((r for r in data['prices'] if r['instrument_id']=='000660'), key=lambda r:r['date'])
        etf_scale = 1+.6*(79200/latest['close']-1)
        latest.update(close=79200,open=79200,high=79250,low=79150,turnover=79200*latest['volume'])
        etf = max((r for r in data['prices'] if r['instrument_id']=='091160'), key=lambda r:r['date'])
        for field in ('open','high','low','close'):
            etf[field] = round(etf[field]*etf_scale)
        etf['turnover'] = etf['close']*etf['volume']
        for row in data['price_snapshots']:
            if row['instrument_id']=='091160':
                for field in ('price','high','low'):
                    row[field] = round(row[field]*etf_scale)
    if name == 'structural_order':
        duplicate = deepcopy(data['news'][0]) | {'news_id':'process-copy'}
        data['news'] = list(reversed(data['news']+[duplicate]))
    if name == 'structural_future':
        data['news'].append(dict(news_id='future',title='고객 계약 전면 취소',body='아직 발표되지 않은 취소 자료.',thread_id='production',stage='cancelled',event_id='future',published_at=at(-1),available_at=at(-1)))
        data['financial_observations'].append(common | dict(observation_id='eps-future',value=1000,news_id='future',published_at=at(-1),available_at=at(-1)))
    if name == 'structural_holdout':
        data['instruments'][0]['name'] = '가상 은행 ETF'
        data['instruments'][1]['name'] = '한결은행'
        data['instruments'][2]['name'] = '새봄은행'
        for row in data['news']:
            if row['news_id'] == 'old-estimate':
                row.update(title='한결은행 이전 EPS 예상 6500원', body='예금 재조달 전 세 증권사의 한결은행 2027년 EPS 평균 예상은 6500원이었다.')
                continue
            row['title'] = {'process':'한결은행 고금리 예금 만기 후 조달비용 하락', 'expectations':'가상 증권사, 예금비용 하락 반영해 내년 EPS 상향', 'multiple':'은행 선행 PER 비교 범위, 충당금 정상화가 적용 조건', 'counter':'새봄은행 기업대출 충당금 증가'}[row['news_id']]
            row['body'] = {'process':'한결은행은 고금리 정기예금 만기 후 낮은 금리로 재조달해 조달비용이 하락했다. 대출금리도 내려 이익 개선을 일부 상쇄하지만 조달비용 하락 폭이 더 컸다.', 'expectations':'세 가상 증권사는 한결은행 2027년 EPS 평균을 6500원에서 7200원으로 상향했다. 대출금리 하락도 함께 반영했다.', 'multiple':'과거 충당금 정상화 국면의 선행 PER은 9~11배였다. 현재의 높은 충당금이 지속되면 이 배수 적용이 어렵다. 다음 달 연체율과 조달금리 공시를 확인할 수 있다.', 'counter':'새봄은행의 기업대출 충당금이 늘었다. 두 은행의 대출 구성은 달라 새봄은행 문제를 한결은행에 그대로 적용할 수 없다. 한결은행의 연체율은 아직 상승하지 않았다.'}[row['news_id']]
    return data
