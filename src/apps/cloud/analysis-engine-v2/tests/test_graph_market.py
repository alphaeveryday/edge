import tempfile
import unittest
from edge_analysis_v2.tools.graph.provider import GraphTools
from graph_fixture import catalog


class MarketCalculationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tools=GraphTools(lambda q,p:[],catalog(),self.tmp.name,'2026-10-05T23:00:00+00:00')

    def save(self,kind,items,selection):
        r=self.tools.store.commit('fixture',{}, {},elapsed_ms=0,queries=[],dataset={'scope':{'dataset_kind':kind},
            'items':items,'selection':{'items':selection}})
        return r['result']['dataset_ref']

    def member(self,id,weight=None,kind='Equity'):
        return {'object_type':kind,'object_id':id,'status':'resolved','weight_ratio':weight,
            'object':{'properties':{'currencyCode':'KRW'}}}

    def test_nav_change_uses_nav_endpoints_and_cannot_become_price_breadth(self):
        rows=[{'object_type':kind,'object_id':kind+day,'properties':{
            'tradeDate':day,('etfInstrumentId' if kind=='DailyNAV' else 'instrumentId'):'fund',
            ('nav' if kind=='DailyNAV' else 'closePrice'):value}}
            for kind,day,value in [('DailyNAV','2026-10-01','100'),('DailyNAV','2026-10-02','110'),
                ('DailyBar','2026-10-01','90'),('DailyBar','2026-10-02','99')]]
        ref=self.save('price_observations',rows,[self.member('fund',kind='ETF')])
        response=self.tools.call('calculate_returns',{'dataset_ref':ref,'start_date':'2026-10-01',
            'end_date':'2026-10-02','price_field':'nav'})
        item=response['result']['items'][0]
        self.assertEqual(item['return_ratio'],'0.1')
        self.assertEqual(item['input_ids'],['DailyNAV2026-10-01','DailyNAV2026-10-02'])
        self.assertIn('not market-price or total return',item['interpretation'])
        with self.assertRaisesRegex(ValueError,'NAV'):
            self.tools.market.breadth(response['result']['dataset_ref'])
        missing,_=self.tools.market.returns(ref,'2026-09-30','2026-10-02','nav')
        self.assertEqual(missing['items'][0]['status'],'unavailable')

    def test_intraday_rows_carry_unknown_aggregation_basis_to_prevent_daily_total_inference(self):
        items=[{'object_type':'IntradayInvestorFlow','object_id':str(n),'properties':{'instrumentId':'a','tradeDate':'2026-10-02','netQtyForeign':120+n}} for n in (0,1)]
        self.tools.market.observations=lambda *args:(items,{'items':[self.member('a')]},{})
        result,_=self.tools.market.flows({},'2026-10-02','2026-10-02','intraday')
        # Said once for the table, not on each of its rows.
        self.assertIn('neither cumulative nor incremental',result['same_for_all_rows']['aggregation_basis'])
        self.assertEqual((result['columns'],result['rows']),(['netQtyForeign'],[[120],[121]]))

    def test_daily_flows_say_how_many_days_in_a_row_each_investor_has_bought_or_sold(self):
        # WHY: seven answers said institutions "turned" to net buying on a day they had already been buying for days.
        items=[{'object_type':'DailyInvestorFlow','object_id':day,'properties':{'instrumentId':'a','securityName':'한화시스템','tradeDate':day,'netValInstitutionTotal':value}}
            for day,value in [('2026-09-26',-5),('2026-09-29',19),('2026-09-30',67),('2026-10-01',151),('2026-10-02',80)]]
        self.tools.market.observations=lambda *args:(items,{'items':[self.member('a')]},{})
        _,dataset=self.tools.market.flows({},'2026-09-26','2026-10-02','daily')
        self.assertEqual(dataset['scope']['net_value_runs'],[{'investor':'InstitutionTotal','direction':'net buying','since':'2026-09-29',
            'days_in_a_row':4,'securities':['한화시스템']}])
        # WHY: a run that filled the whole range was written as "since 9/22, 7 days", the length of the query.
        del items[0]
        _,whole=self.tools.market.flows({},'2026-09-29','2026-10-02','daily')
        self.assertEqual(whole['scope']['net_value_runs'][0]['since'],'before 2026-09-29 (every day returned)')
        self.assertEqual(whole['scope']['net_value_runs'][0]['days_in_a_row'],'at least 4')

    def test_breadth_preserves_large_down_weight_and_missing_not_flat(self):
        items=[{'object_id':str(i),'status':'calculated','return_ratio':'0.01','weight_ratio':0.02} for i in range(9)]
        items+=[{'object_id':'large','status':'calculated','return_ratio':'-0.02','weight_ratio':0.75},
                {'object_id':'missing','status':'unavailable','return_ratio':None,'weight_ratio':None}]
        ref=self.save('returns',items,[])
        result,_=self.tools.market.breadth(ref)
        rows={r['direction']:r for r in result['items']}
        self.assertEqual(rows['up']['count'],9);self.assertEqual(rows['up']['raw_weight_sum'],'0.18')
        self.assertEqual(rows['down']['raw_weight_sum'],'0.75')
        self.assertEqual(rows['unavailable']['count'],1);self.assertEqual(rows['flat']['count'],0)

    def test_returns_do_not_substitute_nearby_date_or_guess_unknown_basis(self):
        prices=[{'object_type':'DailyBar','object_id':'p'+str(i),'properties':{
            'instrumentId':'a','tradeDate':day,'closePrice':str(value),'priceBasis':None}} for i,(day,value) in enumerate([
            ('2026-10-01',100),('2026-10-02',110)])]
        ref=self.save('price_observations',prices,[self.member('a')])
        result,_=self.tools.market.returns(ref,'2026-10-01','2026-10-02','closePrice','require_recorded_basis')
        self.assertIsNone(result['items'][0]['return_ratio'])
        self.assertEqual(result['items'][0]['reason'],'unknown_or_mismatched_price_basis')
        result,_=self.tools.market.returns(ref,'2026-10-01','2026-10-02','closePrice')
        self.assertEqual(result['items'][0]['return_ratio'],'0.1')
        self.assertIn('not certified',result['items'][0]['interpretation'])
        result,_=self.tools.market.returns(ref,'2026-09-30','2026-10-02','closePrice')
        self.assertIsNone(result['items'][0]['return_ratio'])   # no nearby date is substituted
        self.assertIn('2026-09-30',result['items'][0]['reason'])

    def test_nav_requires_exact_date_and_positive_per_unit_value(self):
        rows=[{'object_type':'DailyBar','object_id':'p','properties':{'instrumentId':'fund','tradeDate':'2026-10-02','closePrice':'105'}},
              {'object_type':'DailyNAV','object_id':'n','properties':{'etfInstrumentId':'fund','tradeDate':'2026-10-02','nav':'100'}}]
        ref=self.save('price_observations',rows,[self.member('fund',kind='ETF')])
        result,_=self.tools.market.nav(ref,'2026-10-02')
        self.assertEqual(result['items'][0]['premium_ratio'],'0.05')
        result,_=self.tools.market.nav(ref,'2026-10-01')
        self.assertIsNone(result['items'][0]['premium_ratio'])
        rows[1]['properties']['nav']='0';ref=self.save('price_observations',rows,[self.member('fund',kind='ETF')])
        result,_=self.tools.market.nav(ref,'2026-10-02');self.assertIsNone(result['items'][0]['premium_ratio'])


class PriceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tools=GraphTools(lambda q,p:[],catalog(),self.tmp.name,'2026-10-05T23:00:00+00:00')

    def dataset(self,days):
        data={'items':[{'object_type':'DailyBar','object_id':day,'properties':{'instrumentId':'fund','tradeDate':day,'closePrice':close}} for day,close in days],
            'selection':{'items':[{'object_id':'fund','object_type':'ETF','object':{'properties':{'currencyCode':'KRW'}}}]},'scope':{'dataset_kind':'price_observations'}}
        return self.tools.store.commit('fixture',{},{},elapsed_ms=0,queries=[],dataset=data)['result']['dataset_ref']

    def test_a_return_is_calculated_from_recorded_closes_by_default_and_a_holiday_names_the_days_that_exist(self):
        # WHY: the stored prices carry no price basis, so the old default refused every calculation; and a start
        # date on a holiday came back as "missing_or_ambiguous_exact_endpoint" with no hint of which days exist.
        ref=self.dataset([('2026-09-28','50500'),('2026-10-02','52250')])
        row=self.tools.market.returns(ref,'2026-09-28','2026-10-02','closePrice')[0]['items'][0]
        self.assertEqual((row['status'],row['return_ratio'][:8]),('calculated','0.034653'))
        holiday=self.tools.market.returns(ref,'2026-09-25','2026-10-02','closePrice')[0]['items'][0]
        self.assertEqual(holiday['status'],'unavailable');self.assertIn('2026-09-25',holiday['reason'])
        self.assertEqual(holiday['dates_with_price'],['2026-09-28','2026-10-02'])
        strict=self.tools.market.returns(ref,'2026-09-28','2026-10-02','closePrice','require_recorded_basis')[0]['items'][0]
        self.assertEqual(strict['reason'],'unknown_or_mismatched_price_basis')

    def test_risers_and_fallers_are_counted_straight_from_prices(self):
        # WHY: every run handed the price dataset to the breadth tool first and was refused.
        data={'items':[{'object_type':'DailyBar','object_id':name+day,'properties':{'instrumentId':name,'tradeDate':day,'closePrice':close}}
                for name,day,close in [('a','2026-09-28','100'),('a','2026-10-02','110'),('b','2026-09-28','100'),('b','2026-10-02','90')]],
            'selection':{'items':[{'object_id':name,'object_type':'Equity','weight_ratio':weight,'object':{'properties':{'currencyCode':'KRW'}}}
                for name,weight in (('a',0.6),('b',0.3))]},'scope':{'dataset_kind':'price_observations'}}
        ref=self.tools.store.commit('fixture',{},{},elapsed_ms=0,queries=[],dataset=data)['result']['dataset_ref']
        result,_=self.tools.market.breadth(ref,'2026-09-28','2026-10-02')
        groups={row['direction']:(row['count'],row['raw_weight_sum']) for row in result['items']}
        self.assertEqual((groups['up'],groups['down']),((1,'0.6'),(1,'0.3')))
        self.assertEqual(result['data_scope']['start_date'],'2026-09-28')
        with self.assertRaisesRegex(ValueError,'give start_date and end_date'):
            self.tools.market.breadth(ref)

    def test_an_empty_recent_day_comes_with_the_latest_day_that_was_published(self):
        # WHY: answers said next-day prices "are not provided" when the day was simply not published by the cutoff.
        self.tools.select=lambda targets:{'items':[{'object_id':'fund','object_type':'ETF','status':'resolved','object':{'properties':{'currencyCode':'KRW'}}}]}
        self.tools.graph.nodes=lambda *a,**kw:[]
        self.tools.graph.query=lambda statement,parameters,**kw:[{'day':'2026-10-02'}]
        result,_=self.tools.market.prices({},'2026-10-05','2026-10-05')
        self.assertEqual(result['total_rows'],0)
        self.assertEqual((result['data_scope']['latest_date_available_at_cutoff'],result['data_scope']['dates_returned']),('2026-10-02',[]))

