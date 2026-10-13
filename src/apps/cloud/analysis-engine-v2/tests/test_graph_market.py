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
        # WHY: every day returned having one direction does not show the direction of the day before the range.
        self.assertEqual(whole['scope']['net_value_runs'][0]['since'],'2026-09-29 or earlier (every day returned)')
        self.assertEqual(whole['scope']['net_value_runs'][0]['days_in_a_row'],'at least 4')

    def flow_runs(self,rows):
        items=[{'object_type':'DailyInvestorFlow','object_id':f'{security}{day}','properties':{'instrumentId':security,'securityName':name,'tradeDate':day,'netValForeign':value}}
            for security,name,day,value in rows]
        self.tools.market.observations=lambda *args:(items,{'items':[self.member('a'),self.member('b')]},{})
        return self.tools.market.flows({},'2026-09-29','2026-10-02','daily')[1]['scope'].get('net_value_runs',[])

    def test_a_day_with_no_figure_ends_a_run_of_buying(self):
        # WHY: days without a figure were dropped, so [1, none, 1] was a run over a day nobody knows, and [1, none] buying up to the last day.
        runs=self.flow_runs([('a','가','2026-09-29',1),('a','가','2026-09-30',None),('a','가','2026-10-01',1)])
        self.assertEqual([(r['since'],r['days_in_a_row']) for r in runs],[('2026-10-01',1)])
        self.assertEqual(self.flow_runs([('a','가','2026-09-29',1),('a','가','2026-09-30',None)]),[])

    def test_runs_are_counted_per_security_and_not_per_name(self):
        # WHY: two securities without a stored name were one series, and one day of buying each became two days in a row.
        runs=self.flow_runs([('a',None,'2026-09-29',-1),('a',None,'2026-09-30',1),('b',None,'2026-09-29',-1),('b',None,'2026-09-30',1)])
        self.assertEqual([(r['since'],r['days_in_a_row'],r['securities']) for r in runs],[('2026-09-30',1,['a','b'])])

    def test_a_price_that_is_not_a_finite_number_is_a_missing_price(self):
        # WHY: "abc" raised an error the tool record never saw, and "Infinity" was calculated with.
        for bad in ('abc','NaN','Infinity'):
            rows=[{'object_type':'DailyBar','object_id':day,'properties':{'instrumentId':'a','tradeDate':day,'closePrice':close}}
                for day,close in (('2026-10-01','100'),('2026-10-02',bad))]
            ref=self.save('price_observations',rows,[self.member('a')])
            row=self.tools.market.returns(ref,'2026-10-01','2026-10-02','closePrice')[0]['items'][0]
            self.assertEqual((row['status'],row['reason'],row['return_ratio']),('unavailable','missing_or_nonpositive_price',None),bad)

    def test_a_calculated_row_shows_the_figures_it_was_calculated_from(self):
        # WHY: the descriptions promise both prices, and a row with the ratio alone sent the agent back to the price table.
        rows=[{'object_type':'DailyBar','object_id':'p'+day,'properties':{'instrumentId':'fund','tradeDate':day,'closePrice':close}}
            for day,close in (('2026-10-01','100'),('2026-10-02','110'))]
        rows.append({'object_type':'DailyNAV','object_id':'n','properties':{'etfInstrumentId':'fund','tradeDate':'2026-10-02','nav':'100'}})
        ref=self.save('price_observations',rows,[self.member('fund',kind='ETF')])
        change=self.tools.market.returns(ref,'2026-10-01','2026-10-02','closePrice')[0]['items'][0]
        self.assertEqual((change['start_price'],change['end_price'],change['return_ratio']),('100','110','0.1'))
        premium=self.tools.market.nav(ref,'2026-10-02')[0]['items'][0]
        self.assertEqual((premium['close_price'],premium['nav'],premium['premium_ratio']),('110','100','0.1'))

    def test_a_weight_that_is_not_a_share_of_the_fund_is_refused(self):
        # WHY: a negative or NaN weight was summed into the weight of the risers; sum_holding_weights refuses the same input.
        for bad in ('-0.5','NaN'):
            ref=self.save('returns',[{'object_id':'a','status':'calculated','return_ratio':'0.01','weight_ratio':bad}],[])
            with self.assertRaisesRegex(ValueError,'Invalid holdings weights'):self.tools.market.breadth(ref)

    def test_nav_rows_come_with_their_own_days_and_their_own_last_published_day(self):
        # WHY: a day with a NAV and no close yet was returned under an empty list of days and the last day of the closes.
        self.tools.select=lambda targets:{'items':[self.member('fund',kind='ETF')]}
        nav={'object_type':'DailyNAV','object_id':'n','properties':{'etfInstrumentId':'fund','tradeDate':'2026-10-05','nav':'100'}}
        self.tools.graph.nodes=lambda kind,**kw:[nav] if kind=='DailyNAV' else []
        self.tools.graph.query=lambda statement,parameters,**kw:[{'day':'2026-10-05' if 'DailyNAV' in statement else '2026-10-02'}]
        scope=self.tools.market.prices({},'2026-10-05','2026-10-05',include_nav=True)[1]['scope']
        self.assertEqual((scope['dates_returned'],scope['latest_date_available_at_cutoff']),([],'2026-10-02'))
        self.assertEqual((scope['nav_dates_returned'],scope['latest_nav_date_available_at_cutoff']),(['2026-10-05'],'2026-10-05'))

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

