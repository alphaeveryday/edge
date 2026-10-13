import tempfile
import unittest
from edge_analysis_v2.tools.graph.provider import GraphTools
from graph_fixture import catalog


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tools=GraphTools(lambda q,p:[],catalog(),self.tmp.name,'2026-10-05T00:00:00+00:00')

    def selection(self,weights):
        selection={'etf_ref':{},'selected_date':'2026-10-02','items':[
            {'object_type':'Equity','object_id':key,'weight_ratio':weight,'status':'resolved'} for key,weight in weights]}
        r=self.tools.store.commit('fixture',{}, {'selection':selection},elapsed_ms=0,queries=[])
        return {'tool_run_id':r['tool_run_id'],'path':'/selection'}

    def test_exposure_uses_raw_weight_minimum_without_renormalizing_missing_assets(self):
        left=self.selection([('a',0.2),('b',0.4),('unknown',None)])
        right=self.selection([('a',0.5),('c',0.3)])
        result,_=self.tools.context.exposure(left,right,'security')
        self.assertEqual(result['data_scope']['overlap_weight'],'0.2')
        self.assertEqual(result['data_scope']['unresolved_left'],['unknown'])

    def test_a_holding_with_no_stored_weight_is_shared_with_an_unknown_weight_and_not_absent(self):
        # WHY: held on both sides with no weight on one, it was shown at 0 and left out of the shared count.
        left=self.selection([('a',None),('b',0.4)]);right=self.selection([('a',0.5),('b',0.1)])
        result,_=self.tools.context.exposure(left,right,'security')
        rows={row[result['columns'].index('object_id')]:dict(zip(result['columns'],row)) for row in result['rows']}
        self.assertEqual((rows['a']['left_weight'],rows['a']['overlap_weight'],rows['a']['right_weight']),(None,None,'0.5'))
        scope=result['data_scope']
        self.assertEqual((scope['shared_count'],scope['shared_with_unknown_weight'],scope['overlap_weight']),(2,1,'0.1'))
        # A company whose share classes are not all weighted has no known weight.
        self.tools.actors=lambda selection: ([],[{'security':{'object_id':r['object_id']},'actor':{'object_id':'issuer'}} for r in selection['items']])
        result,_=self.tools.context.exposure(self.selection([('common',0.2),('preferred',None)]),self.selection([('common',0.4)]),'company')
        row=dict(zip(result['columns'],result['rows'][0]))|(result.get('same_for_all_rows') or {})
        # A column that is empty in every row is not shown; the count says why.
        self.assertEqual((row.get('left_weight'),row.get('overlap_weight'),row['right_weight']),(None,None,'0.4'))
        self.assertEqual(result['data_scope']['shared_with_unknown_weight'],1)

    def test_a_company_is_a_wrong_target_for_classifications_and_not_a_security_without_one(self):
        # WHY: a company came back as an empty table that counted one security without a classification.
        self.tools.select=lambda targets:{'items':[{'object_type':'Company','object_id':'c','status':'resolved'}]}
        with self.assertRaisesRegex(ValueError,'wrong argument, not a missing classification'):
            self.tools.context.factors({},['industry'])

    def test_valuation_inputs_that_exist_are_returned_when_another_is_not_stored_and_dates_are_always_checked(self):
        # WHY: asked for by name, a company with EPS and no BPS was a wrong argument, and the gathered prices were lost.
        row=lambda metric:{'object_type':'FinancialMetric','object_id':metric,'title':metric,'properties':{'metric':metric,'value':'1'}}
        asked=[]
        def observations(targets,kind,**kw):
            asked.append(kw);return {'data_scope':{}},{'items':[row('eps_basic'),row('revenue')]}
        self.tools.select=lambda targets:{'items':[]}
        self.tools.financial.observations=observations
        _,dataset=self.tools.context.instrument_factors({},'2026-10-01','2026-10-02',['valuation'])
        self.assertEqual([i['observation']['object_id'] for i in dataset['items']],['eps_basic'])
        self.assertNotIn('metrics',asked[0])
        with self.assertRaisesRegex(ValueError,'Invalid dates'):
            self.tools.context.instrument_factors({},'2026-10-02','2026-10-01',['valuation'])

    def test_a_fund_keeps_its_prices_when_it_has_no_company_figures(self):
        # WHY: valuation asked of a fund raised, and the prices gathered in the same call were dropped with it.
        price={'object_type':'DailyBar','object_id':'p','title':'p','properties':{'tradeDate':'2026-10-02','closePrice':'100'}}
        self.tools.select=lambda targets:{'items':[]}
        self.tools.market.prices=lambda *a,**k:({'data_scope':{}},{'items':[price]})
        def refuse(*a,**k):raise ValueError('None of the targets is a company')
        self.tools.financial.observations=refuse
        _,dataset=self.tools.context.instrument_factors({},'2026-10-01','2026-10-02',['chart','valuation'])
        self.assertEqual([i['factor'] for i in dataset['items']],['chart'])
        self.assertIn('None of the targets is a company',dataset['scope']['factor_scopes']['valuation']['unavailable'])

    def test_listing_rows_say_how_many_securities_have_none_and_that_capitalization_has_no_amount(self):
        # WHY: the count was given for industry only, and capitalization rows without an amount read as data missing for these securities.
        self.tools.select=lambda targets:{'items':[{'object_type':'Equity','object_id':i,'status':'resolved'} for i in ('a','b')]}
        record=lambda ref:{'source':ref,'target':{'object_type':'X','object_id':'r','title':'r','properties':{'securityName':'가'}}}
        self.tools.context.graph=type('G',(),{'links':{k+'_ForSecurity_'+b:{} for k in ('SecurityListingSnapshot','MarketCapitalization') for b in ('Equity','ETF')},
            'linked':staticmethod(lambda refs,relation,direction:[record(r) for r in refs if r['object_id']=='a' and 'Listing' in relation])})()
        _,dataset=self.tools.context.factors({},['listing','market_capitalization'])
        self.assertEqual(dataset['scope']['securities_without_any_record'],1)
        self.assertIn('not available here',dataset['scope']['market_capitalization'])

    def test_classifications_of_a_fund_are_one_row_per_security_and_all_are_shown(self):
        # WHY: 201 holdings came back as 399 rows over two pages, and two runs named the sectors from the first page.
        ids=[f's{n}' for n in range(30)]
        self.tools.select=lambda targets:{'items':[{'object_type':'Equity','object_id':i,'status':'resolved','weight_ratio':0.01} for i in ids]}
        def linked(refs,relation,direction):
            kind='industry' if 'Industry' in relation else 'exchange'
            return [{'source':r,'target':{'object_type':'X','object_id':r['object_id']+kind,'title':'','properties':
                {'securityName':r['object_id'],'securityTicker':'0','sectorName':'Technology','industryName':'Consumer Electronics'} if kind=='industry'
                else {'securityName':r['object_id'],'securityTicker':'0','largeName':'제조','mediumName':'전기·전자'}}} for r in refs if r['object_type']=='Equity']
        self.tools.context.graph=type('G',(),{'links':{a+'_ForSecurity_'+b:{} for a in ('SecurityIndustryClassification','ExchangeSecurityClassification') for b in ('Equity','ETF')},'linked':staticmethod(linked)})()
        result,_=self.tools.context.factors({'kind':'objects','object_refs':[]},['industry','exchange_classification'])
        self.assertEqual((result['total_rows'],len(result['rows']),result['page']['complete']),(30,30,True))   # the default limit of 20 does not cut a fund
        row=dict(zip(result['columns'],result['rows'][0]))|result['same_for_all_rows']
        self.assertEqual((row['weight_percent'],row['industry'],row['exchange_medium']),(1.0,'Consumer Electronics','전기·전자'))

    def test_company_overlap_uses_declared_issuer_edges_and_preserves_two_share_classes(self):
        self.tools.actors=lambda selection: ([],[{'security':{'object_id':r['object_id']},'actor':{'object_id':'issuer'}} for r in selection['items']])
        left=self.selection([('common',0.2),('preferred',0.3)])
        right=self.selection([('common',0.4)])
        result,_=self.tools.context.exposure(left,right,'company')
        row=dict(zip(result['columns'],result['rows'][0]))
        self.assertEqual(len(result['rows']),1)
        self.assertEqual(row['left_weight'],'0.5')
        self.assertEqual(result['data_scope']['overlap_weight'],'0.4')

    def test_flow_sum_retains_its_actual_period_so_it_cannot_be_relabelled_as_a_price_window(self):
        data={'items':[{'object_id':'flow'+day,'properties':{'instrumentId':'fund','tradeDate':day,'netValForeign':value}}
            for day,value in [('2026-09-15','-600000000'),('2026-10-02','84000000')]],
            'selection':{'items':[{'object_id':'fund','object':{'properties':{'currencyCode':'KRW'}}}]},
            'scope':{'dataset_kind':'flow_observations','frequency':'daily','start_date':'2026-09-15','end_date':'2026-10-05'}}
        saved=self.tools.store.commit('fixture',{}, {},elapsed_ms=0,queries=[],dataset=data)
        result,_=self.tools.context.flow_totals(saved['result']['dataset_ref'],'netValForeign')
        row=dict(zip(result['columns'],result['rows'][0]))
        self.assertNotIn('input_ids',row)   # what was summed stays in the stored result
        self.assertEqual(row['value_text'],'-5억 1,600만원')
        self.assertEqual(row['requested_start_date'],'2026-09-15')
        self.assertEqual(row['last_observation_date'],'2026-10-02')
