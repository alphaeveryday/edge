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

    def test_eps_and_unmatched_revision_are_not_derived_as_fourth_quarter(self):
        for metric in ('EPS','revenue'):
            ref=self.save([self.obs('a','100',metric=metric,fiscalPeriod='FY',periodKind='YTD',rceptNo='r1'),
                self.obs('b','60',metric=metric,fiscalPeriod='Q3',periodKind='YTD',rceptNo='r2')])
            with self.assertRaises(ValueError):self.tools.financial.derive(ref,'a','b')

    def test_later_expectation_and_untyped_news_value_are_rejected(self):
        actual=self.save([self.obs('actual','120')])
        expected=self.save([self.obs('estimate','100',availableAt='2026-08-02T00:00:00+00:00')],'expected_financial_observations')
        with self.assertRaisesRegex(ValueError,'before'):self.tools.financial.expectation(actual,'actual',expected,'estimate')
        expected=self.save([self.obs('estimate','100')],'guidance_events')
        with self.assertRaisesRegex(ValueError,'consensus'):self.tools.financial.expectation(actual,'actual',expected,'estimate')


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
