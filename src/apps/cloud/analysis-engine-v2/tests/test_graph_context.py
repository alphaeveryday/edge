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
