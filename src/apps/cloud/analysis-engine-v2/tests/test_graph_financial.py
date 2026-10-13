import tempfile
import unittest
from edge_analysis_v2.tools.graph.provider import GraphTools
from graph_fixture import catalog


class FinancialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tools=GraphTools(lambda q,p:[],catalog(),self.tmp.name,'2026-10-05T00:00:00+00:00')

    def save(self,items,kind='financial_observations'):
        return self.tools.store.commit('fixture',{}, {},elapsed_ms=0,queries=[],dataset={
            'items':items,'selection':None,'scope':{'dataset_kind':kind}})['result']['dataset_ref']

    def obs(self,id,value,**kwargs):
        return {'object_type':'FinancialMetric','object_id':id,'properties':{
            'corpCode':'c','metric':'revenue','unit':'KRW','currencyCode':'KRW','fsBasis':'CFS',
            'fiscalYear':2026,'fiscalPeriod':'Q2','periodKind':'QUARTER','derivation':'reported',
            'periodEnd':'2026-06-30','availableAt':'2026-08-01T00:00:00+00:00','value':value,**kwargs}}

    def test_a_metric_name_the_data_never_uses_is_an_error_naming_stored_values_not_an_empty_result(self):
        # WHY: a run asked for operatingIncome, got 0 rows and reported that financials were not loaded.
        from unittest.mock import patch
        company={'object_type':'Company','object_id':'c'}
        stored=[{'source':company,'target':self.obs(m,'1',metric=m)} for m in ('operating_income','revenue')]
        def linked(refs,link,direction,where=None,parameters=None):
            wanted=(parameters or {}).get('metric')
            return [r for r in stored if not wanted or r['target']['properties']['metric'] in wanted]
        with patch.object(self.tools,'select',return_value={'items':[]}),patch.object(self.tools,'actors',return_value=([company],[])),             patch.object(self.tools.graph,'linked',side_effect=linked):
            with self.assertRaisesRegex(ValueError,r"metric in \['operatingIncome'\].*operating_income, revenue.*wrong argument"):
                self.tools.financial.observations({'kind':'objects'},'actual',metrics=['operatingIncome'])
            result,_=self.tools.financial.observations({'kind':'objects'},'actual',metrics=['revenue'])
            self.assertEqual(result['total_rows'],1)
            stored.clear()   # nothing stored at all is a genuine absence and stays an empty result
            result,_=self.tools.financial.observations({'kind':'objects'},'actual',metrics=['revenue'])
            self.assertEqual(result['total_rows'],0)

    def test_cross_company_and_accounting_basis_cannot_create_growth_rate(self):
        first=self.obs('a','100',periodEnd='2026-03-31',fiscalPeriod='Q1')
        for change in ({'corpCode':'other'},{'fsBasis':'OFS'},{'periodKind':'YTD'},{'unit':'USD'}):
            ref=self.save([first,self.obs('b','200',**change)])
            with self.assertRaisesRegex(ValueError,'cannot be compared'):self.tools.financial.compare(ref,'a','b')

    def test_negative_base_has_difference_but_no_misleading_growth_percentage(self):
        ref=self.save([self.obs('a','-100',periodEnd='2026-03-31'),self.obs('b','50')])
        result,_=self.tools.financial.compare(ref,'a','b')
        self.assertEqual(result['items'][0]['difference'],'150')
        self.assertIsNone(result['items'][0]['change_ratio'])

    def test_cumulative_growth_requires_equal_period_lengths(self):
        ref=self.save([self.obs('a','100',periodEnd='2026-03-31',fiscalPeriod='Q1',periodKind='CUMULATIVE'),
                       self.obs('b','200',periodKind='CUMULATIVE')])
        with self.assertRaisesRegex(ValueError,'fiscalPeriod'):self.tools.financial.compare(ref,'a','b')
        ref=self.save([self.obs('a','100',periodEnd='2025-06-30',fiscalYear=2025,periodKind='CUMULATIVE'),
                       self.obs('b','200',periodKind='CUMULATIVE')])
        result,_=self.tools.financial.compare(ref,'a','b')
        self.assertEqual(result['items'][0]['change_ratio'],'1')

    def test_later_expectation_and_untyped_news_value_are_rejected(self):
        actual=self.save([self.obs('actual','120')])
        expected=self.save([self.obs('estimate','100',availableAt='2026-08-02T00:00:00+00:00')],'expected_financial_observations')
        with self.assertRaisesRegex(ValueError,'before'):self.tools.financial.expectation(actual,'actual',expected,'estimate')
        expected=self.save([self.obs('estimate','100')],'guidance_events')
        with self.assertRaisesRegex(ValueError,'consensus'):self.tools.financial.expectation(actual,'actual',expected,'estimate')

    def test_targets_that_lead_to_no_company_are_a_wrong_argument_and_not_a_company_without_figures(self):
        # WHY: a fund named as an object reached no company and came back as zero financial rows.
        from unittest.mock import patch
        with patch.object(self.tools,'select',return_value={'items':[{'object_type':'ETF','object_id':'f','status':'resolved'}]}),patch.object(self.tools,'actors',return_value=([],[])):
            for kind in ('actual','segment','guidance'):
                with self.assertRaisesRegex(ValueError,'wrong argument, not missing data'):self.tools.financial.observations({},kind)

    def test_a_stored_value_that_is_not_a_finite_number_cannot_be_compared(self):
        # WHY: the conversion raised an error the tool record never saw.
        for bad in ('abc','NaN','Infinity'):
            ref=self.save([self.obs('a','100'),self.obs('b',bad,periodEnd='2026-09-30',fiscalPeriod='Q3')])
            with self.assertRaisesRegex(ValueError,'finite numbers'):self.tools.financial.compare(ref,'a','b')


class StoredVersionTests(unittest.TestCase):
    def test_a_figure_stored_several_times_with_one_value_is_returned_once(self):
        # WHY: one quarter's operating income came back as eight rows for four figures, each stored by two collection runs.
        from edge_analysis_v2.tools.graph.financial import one_per_figure
        row=lambda value,received,period='Q2':{'object_id':received+period,'properties':{'corpCode':'c','fiscalPeriod':period,'value':value,'receivedAt':received}}
        kept=one_per_figure([row('10','2026-10-03'),row('10','2026-10-04'),row('7','2026-10-03','Q1')],('corpCode','fiscalPeriod'),'receivedAt')
        self.assertEqual([(r['object_id'],r['stored_versions']) for r in kept],[('2026-10-04Q2',2),('2026-10-03Q1',1)])
        split=one_per_figure([row('10','2026-10-03'),row('11','2026-10-04')],('corpCode','fiscalPeriod'),'receivedAt')
        self.assertEqual(len(split),2);self.assertTrue(all(r['versions_disagree'] for r in split))   # disagreeing versions are never dropped


def test_a_contract_is_scaled_to_yearly_revenue_and_profit_with_its_assumptions_named():
    # WHY: asked what an order means for earnings, answers said "limited" or "cannot be determined"; figures must
    # come from a tool and no tool did this arithmetic.
    from types import SimpleNamespace
    from edge_analysis_v2.tools.graph.financial import FinancialTools
    event = {'object_type': 'SourceEvent', 'object_id': 'e', 'title': 'x', 'properties': {}}
    measure = lambda code, value, unit: {'object_id': code, 'properties': {'metricCode': code, 'value': value, 'unit': unit}}
    metric = lambda name, value: {'target': {'object_type': 'FinancialMetric', 'object_id': name, 'properties': {
        'corpCode': 'c', 'fiscalYear': 2025, 'metric': name, 'periodKind': 'CUMULATIVE', 'derivation': 'REPORTED', 'unit': 'KRW', 'value': value}}}
    graph = SimpleNamespace(get=lambda refs: {'items': [{'status': 'resolved', 'object': event}]},
        linked=lambda refs, link, *a, **k: [] if 'SourceEvent' in link else [metric('revenue', '10000000000000'), metric('operating_income', '1000000000000')])
    features = [measure('CONTRACT_VALUE', '600000000000', 'KRW'), measure('CONTRACT_DURATION', '1095', 'DAY')]
    owner = SimpleNamespace(graph=graph, register=lambda *a, **k: None, result=lambda items, **k: (items, k),
        events=SimpleNamespace(enrich=lambda found: [{'event': event, 'features': features}]))
    tool = FinancialTools(owner)
    refs = ({'object_type': 'SourceEvent', 'object_id': 'e'}, {'object_type': 'Company', 'object_id': 'c'})
    (row,), _ = tool.impact(*refs, holding_weight=0.25)
    assert 'not an expected price move' in row['estimate'].pop('weighted_by_fund_holding_means')
    assert 'per year' in row['estimate'].pop('basis') and row['estimate'].pop('contract_value_used') == '6,000억원'
    assert row['estimate'] == {'yearly_revenue': '2,000억원', 'share_of_last_year_revenue': '2.0%', 'yearly_operating_profit': '200억원',
        'share_of_last_year_operating_profit': '2.0%', 'weighted_by_fund_holding': '0.5%'}
    assert 'the whole contract' in row['assumptions']['company_share']
    assert 'stored contract period' in row['assumptions']['recognition_years'] and 'last full-year margin' in row['assumptions']['operating_margin']
    (ranged,), _ = tool.impact(*refs, years_low=2, years_high=6, margin_low=0.05, margin_high=0.15)
    assert ranged['estimate']['yearly_revenue'] == '1,000억원 ~ 3,000억원' and ranged['estimate']['yearly_operating_profit'] == '50억원 ~ 450억원'
    assert 'supplied by the caller' in ranged['assumptions']['recognition_years'] and ranged['most_sensitive_assumption'] == 'recognition_years'
    (half,), _ = tool.impact(*refs, company_share=0.5)   # a contract shared with another company
    assert half['estimate']['yearly_revenue'] == '1,000억원' and '50.0% of the reported amount' in half['estimate']['contract_value_used']
    features.pop()   # no stored period: the caller has to state the assumption
    import pytest
    with pytest.raises(ValueError, match='years_low and years_high'):
        tool.impact(*refs)


def contract_tool(features=None, participants=None, metrics=None, reports=None, holdings=None, cutoff_day='2026-10-05'):
    """The contract estimate over one event 'e' of company 'c' with last-year revenue 10조 and operating profit 1조."""
    from datetime import date
    from types import SimpleNamespace
    from edge_analysis_v2.tools.graph.financial import FinancialTools
    event = {'object_type': 'SourceEvent', 'object_id': 'e', 'title': 'x', 'properties': {'eventType': 'COMPANY.CONTRACT.SIGNING', 'eventDate': '2026-09-04'}}
    measure = lambda code, value, unit: {'object_id': code, 'properties': {'metricCode': code, 'value': value, 'unit': unit}}
    metric = lambda name, value, at='1': {'target': {'object_type': 'FinancialMetric', 'object_id': name + at, 'properties': {
        'corpCode': 'c', 'fiscalYear': 2025, 'metric': name, 'periodKind': 'CUMULATIVE', 'derivation': 'REPORTED', 'unit': 'KRW', 'value': value, 'receivedAt': at}}}
    metrics = metrics or [metric('revenue', '10000000000000'), metric('operating_income', '1000000000000')]
    features = [measure('CONTRACT_VALUE', '600000000000', 'KRW'), measure('CONTRACT_DURATION', '1095', 'DAY')] if features is None else features
    reports = reports or [{'event': event, 'features': features, 'participants': participants or []}]
    graph = SimpleNamespace(get=lambda refs: {'items': [{'status': 'resolved', 'object': event}]},
        linked=lambda refs, link, *a, **k: [] if 'SourceEvent' in link else metrics)
    asked = []
    def held(etf_ref, day, policy):
        asked.append(day)
        return None, {'selection': {'items': holdings or []}}
    owner = SimpleNamespace(graph=graph, register=lambda *a, **k: None, result=lambda items, **k: (items, k),
        events=SimpleNamespace(enrich=lambda found: reports), holdings=held, cutoff_day=lambda: date.fromisoformat(cutoff_day),
        actors=lambda selection: ([], [{'security': {'object_id': i['object_id']}, 'actor': {'object_id': 'c'}} for i in selection['items'] if i.get('issuer') == 'c']))
    return FinancialTools(owner), asked, metric, measure, event


REFS = ({'object_type': 'SourceEvent', 'object_id': 'e'}, {'object_type': 'Company', 'object_id': 'c'})


def test_a_loss_making_margin_gives_the_whole_range_of_the_loss():
    # WHY: the low period was paired with the low margin, so margins of -15% to -5% over 2 to 6 years came back as -150억 alone.
    tool = contract_tool()[0]
    (row,), _ = tool.impact(*REFS, years_low=2, years_high=6, margin_low=-0.15, margin_high=-0.05)
    assert row['estimate']['yearly_operating_profit'] == '-450억원 ~ -50억원'


def test_a_margin_that_starts_at_zero_is_a_varied_assumption():
    # WHY: the width was a ratio of the two ends, so 0% to 20% was "none varied" while the profit ran from 0 to 400억.
    tool = contract_tool()[0]
    (row,), _ = tool.impact(*REFS, margin_low=0, margin_high=0.2)
    assert row['most_sensitive_assumption'] == 'operating_margin' and row['estimate']['yearly_operating_profit'] == '0원 ~ 400억원'
    (fixed,), _ = tool.impact(*REFS)
    assert fixed['most_sensitive_assumption'].startswith('none varied')


def test_stored_versions_of_the_base_year_that_disagree_stop_the_estimate():
    # WHY: both versions were kept as rows and the last one read became the base, so the order of the rows set the margin.
    import pytest
    tool, _, metric, _, _ = contract_tool()
    tool.graph.linked = lambda refs, link, *a, **k: [] if 'SourceEvent' in link else [
        metric('revenue', '10000000000000'), metric('operating_income', '1000000000000', '1'), metric('operating_income', '2000000000000', '2')]
    with pytest.raises(ValueError, match='versions of 2025 full-year operating_income disagree'):
        tool.impact(*REFS)


def test_the_company_must_take_part_in_the_contract_and_not_be_its_customer():
    # WHY: the contract of one company was scaled against the accounts of another, and of the customer.
    import pytest
    party = lambda kind, identifier, role: {'actor': {'object_type': kind, 'object_id': identifier}, 'role': role}
    with pytest.raises(ValueError, match='not a participant'):
        contract_tool(participants=[party('Company', 'other', 'SUPPLIER')])[0].impact(*REFS)
    with pytest.raises(ValueError, match='customer of this contract'):
        contract_tool(participants=[party('Company', 'c', 'CUSTOMER')])[0].impact(*REFS)
    (row,), _ = contract_tool(participants=[party('Company', 'c', 'SUPPLIER'), party('Organization', 'buyer', 'CUSTOMER')])[0].impact(*REFS)
    assert row['estimate']['yearly_revenue'] == '2,000억원'


def test_another_contract_of_the_same_day_does_not_lend_its_amount():
    # WHY: two contracts of one company on one day were read as one, and the filing amount of the other was scaled.
    tool, _, _, measure, event = contract_tool()
    party = lambda kind, identifier, role: {'actor': {'object_type': kind, 'object_id': identifier}, 'role': role}
    mine = party('Company', 'c', 'SUPPLIER')
    filed = {'object_id': 'big', 'properties': {'metricCode': 'CONTRACT_VALUE', 'value': '9000000000000', 'unit': 'KRW', 'valueSource': 'DART'}}
    reports = [{'event': event, 'features': [measure('CONTRACT_VALUE', '600000000000', 'KRW'), measure('CONTRACT_DURATION', '1095', 'DAY')],
                'participants': [mine, party('Organization', 'buyer-a', 'CUSTOMER')]},
               {'event': {**event, 'object_id': 'other'}, 'features': [filed], 'participants': [mine, party('Organization', 'buyer-b', 'CUSTOMER')]},
               {'event': {**event, 'object_id': 'news'}, 'features': [], 'participants': [mine]}]
    tool.tools.events.enrich = lambda found: reports
    tool_metrics = tool.graph.linked
    (row,), _ = tool.impact(*REFS)
    # WHY: the report that names no counterparty is not shown to be this contract either; a missing link is not a match.
    assert row['confirmed_inputs']['contract_value'] == '6,000억원' and row['confirmed_inputs']['reports_of_this_event'] == 1
    assert row['same_day_reports_of_other_contracts_not_used'] == 2 and 'other_reported_amounts' not in row
    # A report the data puts in the same thread is this contract, whatever it names.
    tool.graph.linked = lambda refs, link, *a, **k: [{'source': {'object_id': i}, 'target': {'object_type': 'EventThread', 'object_id': 't'}} for i in ('e', 'news')] if 'EventThread' in link else tool_metrics(refs, link, *a, **k)
    (row,), _ = tool.impact(*REFS)
    assert row['confirmed_inputs']['reports_of_this_event'] == 2 and row['same_day_reports_of_other_contracts_not_used'] == 1


def test_a_stored_amount_that_is_not_a_finite_number_is_an_amount_not_stated():
    # WHY: "not-a-number" and "Infinity" raised errors the tool record never saw.
    import pytest
    for bad in ('not-a-number', 'Infinity', 'NaN'):
        tool, _, _, measure, _ = contract_tool(features=[])
        tool.tools.events.enrich = lambda found, bad=bad: [{'event': {'object_id': 'e', 'properties': {}}, 'features': [measure('CONTRACT_VALUE', bad, 'KRW')]}]
        with pytest.raises(ValueError, match='states a won contract value'):
            tool.impact(*REFS, years_low=1, years_high=2)


def test_the_fund_weight_is_read_on_the_korean_day_and_its_absence_is_said():
    # WHY: the UTC date of the cutoff is a day early for nine hours of every day, and a fund that does not hold the
    # company gave the same answer as no fund at all.
    fund = {'object_type': 'ETF', 'object_id': 'f'}
    tool, asked, *_ = contract_tool(holdings=[{'object_id': 's', 'issuer': 'c', 'weight_ratio': '0.25'}])
    (row,), _ = tool.impact(*REFS, etf_ref=fund)
    assert asked == ['2026-10-05'] and row['estimate']['weighted_by_fund_holding'] == '0.5%'
    assert row['confirmed_inputs']['holding_weight'] == '25.0%' and 'holding_weight' not in row['assumptions']
    (absent,), _ = contract_tool(holdings=[{'object_id': 's', 'issuer': 'x', 'weight_ratio': '0.25'}])[0].impact(*REFS, etf_ref=fund)
    assert 'holds no security issued by this company' in absent['fund_weight_not_found'] and 'weighted_by_fund_holding' not in absent['estimate']
    (unknown,), _ = contract_tool(holdings=[{'object_id': 's', 'issuer': 'c', 'weight_ratio': None}])[0].impact(*REFS, etf_ref=fund)
    assert 'not stored for every security' in unknown['fund_weight_not_found']


def test_a_weight_the_caller_passes_is_an_assumption_and_not_a_stored_input():
    # WHY: a weight passed by hand was listed under confirmed_inputs, which the description calls stored.
    (row,), _ = contract_tool()[0].impact(*REFS, holding_weight=0.25)
    assert 'holding_weight' not in row['confirmed_inputs'] and 'supplied by the caller' in row['assumptions']['holding_weight']


def test_an_amount_a_year_is_not_spread_over_the_contract_period():
    # WHY: 100억 a year over five years was divided by five and came back as 20억 a year.
    import pytest
    tool, _, _, measure, _ = contract_tool()
    yearly = measure('CONTRACT_VALUE', '10000000000', 'KRW'); yearly['properties']['periodBasis'] = 'ANNUAL'
    tool.tools.events.enrich = lambda found: [{'event': {'object_id': 'e', 'properties': {}}, 'features': [yearly, measure('CONTRACT_DURATION', '1825', 'DAY')]}]
    with pytest.raises(ValueError, match='an amount a year \\(100억원\\), not a contract total'):
        tool.impact(*REFS)


def test_an_amount_the_pipeline_did_not_read_from_the_text_is_not_scaled():
    # WHY: a value left beside parseStatus unit_mismatch or valueSource UNRESOLVED became the confirmed contract value.
    import pytest
    for mark in ({'parseStatus': 'unit_mismatch'}, {'valueSource': 'UNRESOLVED'}):
        tool, _, _, measure, _ = contract_tool()
        bad = measure('CONTRACT_VALUE', '600000000000', 'KRW'); bad['properties'].update(mark)
        tool.tools.events.enrich = lambda found, bad=bad: [{'event': {'object_id': 'e', 'properties': {}}, 'features': [bad]}]
        with pytest.raises(ValueError, match='states a won contract value'):
            tool.impact(*REFS, years_low=1, years_high=2)


def test_a_weight_stored_for_only_some_securities_of_the_company_is_not_its_weight():
    # WHY: common shares at 10% and preferred shares with no weight were confirmed as a 10% holding.
    fund = {'object_type': 'ETF', 'object_id': 'f'}
    held = [{'object_id': 'common', 'issuer': 'c', 'weight_ratio': '0.10'}, {'object_id': 'preferred', 'issuer': 'c', 'weight_ratio': None}]
    (row,), _ = contract_tool(holdings=held)[0].impact(*REFS, etf_ref=fund)
    assert 'not stored for every security' in row['fund_weight_not_found'] and 'holding_weight' not in row['confirmed_inputs']


def graph_tools():
    import tempfile
    directory = tempfile.TemporaryDirectory()
    return GraphTools(lambda q, p: [], catalog(), directory.name, '2026-10-05T00:00:00+00:00'), directory


def test_a_reported_quarter_is_compared_with_the_calculated_fourth_quarter():
    # WHY: asking for the same derivation refused every change from the third quarter to the fourth.
    tools, directory = graph_tools()
    row = lambda identifier, value, **more: {'object_type': 'FinancialMetric', 'object_id': identifier, 'properties': {
        'corpCode': 'c', 'metric': 'revenue', 'unit': 'KRW', 'fsBasis': 'CFS', 'periodKind': 'QUARTER', 'value': value, **more}}
    ref = tools.store.commit('fixture', {}, {}, elapsed_ms=0, queries=[], dataset={'selection': None, 'scope': {'dataset_kind': 'financial_observations'}, 'items': [
        row('q3', '100', fiscalPeriod='Q3', periodEnd='2025-09-30', derivation='REPORTED'),
        row('q4', '120', fiscalPeriod='Q4', periodEnd='2025-12-31', derivation='FY_MINUS_9M')]})['result']['dataset_ref']
    result = tools.financial.compare(ref, 'q3', 'q4')[0]['items'][0]
    assert result['change_ratio'] == '0.2' and result['derivations'] == ['REPORTED', 'FY_MINUS_9M']
    directory.cleanup()


def test_copies_of_one_value_written_two_ways_are_one_figure():
    # WHY: "10" and "10.0" were kept as two versions that disagree.
    from edge_analysis_v2.tools.graph.financial import one_per_figure
    rows = [{'object_id': at, 'properties': {'k': 1, 'value': value, 'receivedAt': at}} for at, value in (('1', '10'), ('2', '10.0'))]
    (kept,) = one_per_figure(rows, ('k',), 'receivedAt')
    assert kept['stored_versions'] == 2 and 'versions_disagree' not in kept
    assert len(one_per_figure([*rows, {'object_id': '3', 'properties': {'k': 1, 'value': '11', 'receivedAt': '3'}}], ('k',), 'receivedAt')) == 3


def test_a_figure_that_is_not_a_number_is_returned_as_stored_without_a_written_amount():
    # WHY: writing "Infinity" or "abc" as an amount raised an error the tool record never saw.
    from unittest.mock import patch
    tools, directory = graph_tools()
    company = {'object_type': 'Company', 'object_id': 'c'}
    stored = [{'source': company, 'target': {'object_type': 'FinancialMetric', 'object_id': value, 'title': value, 'properties': {'unit': 'KRW', 'value': value, 'metric': 'revenue'}}}
              for value in ('Infinity', 'abc', '100000000')]
    with patch.object(tools, 'select', return_value={'items': []}), patch.object(tools, 'actors', return_value=([company], [])), \
         patch.object(tools.graph, 'linked', return_value=stored):
        _, dataset = tools.financial.observations({}, 'actual')
    assert [item.get('value_text') for item in dataset['items']] == [None, None, '1억원']
    directory.cleanup()


def test_a_stored_series_with_no_observation_in_the_range_is_an_empty_range_and_not_a_wrong_argument():
    # WHY: a weekend asked of a series that exists came back as "no macro series matches, wrong argument".
    import pytest
    tools, directory = graph_tools()
    series = [{'series_id': 'FX.USDKRW', 'name': 'USD/KRW exchange rate', 'first_date': '2020-01-02', 'last_date': '2026-10-02'}]
    tools.graph.nodes = lambda *a, **k: []
    tools.financial.series = lambda: ({'items': series}, None)
    for asked in ({'query': 'USD/KRW'}, {'series_ids': ['FX.USDKRW']}):
        result, _ = tools.financial.macro('2026-10-03', '2026-10-04', **asked)
        assert result['total_rows'] == 0 and 'FX.USDKRW (2020-01-02..2026-10-02)' in result['data_scope']['no_observation_in_range']
    for asked in ({'query': '환율'}, {'series_ids': ['NOPE']}):
        with pytest.raises(ValueError, match='wrong argument, not missing data'):
            tools.financial.macro('2026-10-03', '2026-10-04', **asked)
    # WHY: beside a series that had rows, an id that is not stored was dropped without a word, and so was a stored one with no rows.
    series.append({'series_id': 'RATE.KR', 'name': 'Korea base rate', 'first_date': '2020-01-02', 'last_date': '2026-09-30'})
    tools.graph.nodes = lambda *a, **k: [{'object_type': 'MacroObservation', 'object_id': 'o', 'title': 'o', 'properties': {'seriesId': 'FX.USDKRW', 'observationDate': '2026-10-02', 'value': '1400'}}]
    with pytest.raises(ValueError, match='wrong argument, not missing data'):
        tools.financial.macro('2026-10-01', '2026-10-02', series_ids=['FX.USDKRW', 'NOPE'])
    result, _ = tools.financial.macro('2026-10-01', '2026-10-02', series_ids=['FX.USDKRW', 'RATE.KR'])
    assert result['total_rows'] == 1 and result['data_scope']['no_observation_in_range'].endswith('RATE.KR (2020-01-02..2026-09-30)')
    assert 'no_observation_in_range' not in tools.financial.macro('2026-10-01', '2026-10-02', series_ids=['FX.USDKRW'])[0]['data_scope']
    directory.cleanup()


def test_every_role_of_the_company_is_read_and_an_event_without_participants_says_so():
    # WHY: the last role read replaced the others, so a customer that is also a partner was scaled as the earner;
    # and with no participants stored, any company passed was taken as the earner without a word.
    import pytest
    party = lambda role: {'actor': {'object_type': 'Company', 'object_id': 'c'}, 'role': role}
    with pytest.raises(ValueError, match='customer of this contract'):
        contract_tool(participants=[party('CUSTOMER'), party('PARTNER')])[0].impact(*REFS)
    (row,), _ = contract_tool(participants=[party('SUPPLIER')])[0].impact(*REFS)
    assert 'company_not_confirmed' not in row
    (bare,), _ = contract_tool()[0].impact(*REFS)
    assert 'stores no participants' in bare['company_not_confirmed']


def test_a_weight_by_hand_and_a_fund_to_read_it_from_are_not_passed_together():
    # WHY: with a weight passed by hand the fund was never read, so a fund that does not exist went through.
    import pytest
    fund = {'object_type': 'ETF', 'object_id': 'f'}
    with pytest.raises(ValueError, match='not both'):
        contract_tool()[0].impact(*REFS, holding_weight=0.25, etf_ref=fund)
    with pytest.raises(ValueError, match='pass etf_ref with it'):
        contract_tool()[0].impact(*REFS, holdings_date='2026-10-01')


def test_the_recognition_period_has_a_floor_the_arithmetic_can_carry():
    # WHY: 1e-30 years passed the schema and overflowed the rounding with an error the tool record never saw.
    import jsonschema, pytest
    tools, directory = graph_tools()
    arguments = {'event_ref': REFS[0], 'company_ref': REFS[1], 'years_low': 1e-30, 'years_high': 1}
    with pytest.raises(jsonschema.ValidationError):
        tools.call('estimate_contract_profit', arguments)
    directory.cleanup()
