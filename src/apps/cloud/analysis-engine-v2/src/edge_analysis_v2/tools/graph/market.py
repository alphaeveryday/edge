"""Market observations and exact Decimal calculations over stored graph evidence."""
from decimal import Decimal
from edge_analysis_v2.tools.graph.provider import SELECTOR,STRING,RESULT_REF
from edge_analysis_v2.tools.graph.shape import number,won_text


class MarketTools:
    def __init__(self,tools):
        self.tools=tools;self.graph=tools.graph
        common={'targets':SELECTOR,'start_date':STRING,'end_date':STRING}
        tools.register('get_price_observations',{**common,'include_nav':{'type':'boolean'}},['targets','start_date','end_date'],self.prices,sources=['DailyBar', 'DailyNAV'])
        tools.register('get_flow_observations',{**common,'frequency':{'enum':['daily','intraday']}},['targets','start_date','end_date','frequency'],self.flows,sources=['DailyInvestorFlow', 'IntradayInvestorFlow'])
        tools.register('calculate_returns',{'dataset_ref':RESULT_REF,'start_date':STRING,'end_date':STRING,'price_field':{'enum':['closePrice','adjustedClosePrice','nav']},
             'basis_policy':{'enum':['observed_close_only','require_recorded_basis'],'description':'Default observed_close_only: the change between two recorded closes; it does not certify a corporate-action-adjusted or total return. require_recorded_basis answers only when both prices carry the same recorded price basis, and the stored prices carry none, so it reports unavailable.'}},
            ['dataset_ref','start_date','end_date','price_field'],self.returns,sources=['get_price_observations로 저장한 가격'])
        tools.register('count_rising_and_falling',{'dataset_ref':RESULT_REF,'start_date':STRING,'end_date':STRING},['dataset_ref'],self.breadth,sources=['get_price_observations로 저장한 가격 또는 calculate_returns로 저장한 등락률'])
        tools.register('calculate_nav_premium',{'dataset_ref':RESULT_REF,'trade_date':STRING},['dataset_ref','trade_date'],self.nav,sources=['get_price_observations로 저장한 종가와 NAV'])

    def window(self,kind,foreign_key,ids,start_date,end_date):
        """The records of one kind for these securities over the days, and the last day published at the cutoff."""
        if not ids:return [],None
        items=self.graph.nodes(kind,where='n.'+foreign_key+' IN $instruments AND n.tradeDate>=date($start) AND n.tradeDate<=date($end)',
            parameters={'instruments':ids,'start':start_date,'end':end_date})
        # A day's record is published after that day, so the last days before the cutoff are often not there yet.
        # Without the latest date that is there, an empty answer for a recent day read as "no such data".
        latest=self.graph.query('MATCH (n:'+kind+') WHERE n.'+foreign_key+' IN $instruments AND n.availableAt<=datetime($cutoff) RETURN max(n.tradeDate) AS day',
            {'instruments':ids,'cutoff':self.tools.store.cutoff},objects=[kind])
        return items,latest[0]['day'] if latest else None

    def observations(self,targets,start_date,end_date,kind,foreign_key):
        start=self.tools.check_date(start_date);end=self.tools.check_date(end_date)
        if start>end:raise ValueError('Invalid observation date range')
        selection=self.tools.select(targets)
        selected=[x for x in selection['items'] if x['status']=='resolved' and x['object_type'] in ('Equity','ETF')]
        items,latest=self.window(kind,foreign_key,[x['object_id'] for x in selected],start_date,end_date)
        return items,selection,{'start_date':start_date,'end_date':end_date,'object_type':kind,
            'latest_date_available_at_cutoff':latest,
            'dates_returned':sorted({o['properties']['tradeDate'] for o in items}),
            'outside_supported_scope':[x['object_id'] for x in selection['items'] if x not in selected]}

    def prices(self,targets,start_date,end_date,include_nav=False):
        items,selection,scope=self.observations(targets,start_date,end_date,'DailyBar','instrumentId')
        if include_nav:
            # NAV is published on its own schedule: a day with a NAV and no close yet was returned under an empty list of days.
            navs,latest=self.window('DailyNAV','etfInstrumentId',
                [x['object_id'] for x in selection['items'] if x['status']=='resolved' and x['object_type']=='ETF'],start_date,end_date)
            scope.update(nav_dates_returned=sorted({o['properties']['tradeDate'] for o in navs}),latest_nav_date_available_at_cutoff=latest)
            items+=navs
        return self.tools.result(items,selection=selection,scope={**scope,'dataset_kind':'price_observations',
            'ohlc_status':'Open/high/low source enrichment is deferred. Nulls are not replaced by close.',
            'price_adjustment':'Not recorded for these prices. Do not call them adjusted and do not call them unadjusted; write "observed close".',
            'nav_comparability':'DailyNAV.nav is per ETF unit in its ETF currency, as defined in the reviewed ontology. The currency is resolved from the ETF object.'},table=True)

    def flows(self,targets,start_date,end_date,frequency):
        kind={'daily':'DailyInvestorFlow','intraday':'IntradayInvestorFlow'}[frequency]
        items,selection,scope=self.observations(targets,start_date,end_date,kind,'instrumentId')
        currencies={r['object_id']:(r.get('object') or {}).get('properties',{}).get('currencyCode') for r in selection['items']}
        for index,item in enumerate(items):
            if frequency=='intraday':
                item['aggregation_basis']='unknown: neither cumulative nor incremental is certified; do not sum slots'
            money={k:won_text(v) for k,v in item['properties'].items() if k.startswith('netVal') and v is not None}
            if money and currencies.get(item['properties'].get('instrumentId'))=='KRW':
                # Written amounts lead the row: the trailing decimal-억 field was passed over for the raw won.
                items[index]={'money_text':money,**item}
        runs=[]
        if frequency=='daily':
            # Seven answers said an investor "turned" to buying on a day it had already been buying for days.
            # Keyed by the security's id: the name is not unique and may be empty, and two securities became one run.
            series={};names={}
            for item in sorted(items,key=lambda o:o['properties'].get('tradeDate') or ''):
                p=item['properties'];names[p.get('instrumentId')]=p.get('securityName') or p.get('instrumentId')
                for field in ('netValIndividual','netValForeign','netValInstitutionTotal'):
                    series.setdefault((p.get('instrumentId'),field),[]).append((p['tradeDate'],None if p.get(field) is None else number(p[field])))
            for (identifier,field),days in series.items():
                # A day with no figure ends the run: [1, none, 1] was three days of buying, and [1, none] buying up to the last day.
                if days[-1][1] is None:continue
                sign=lambda v:(v>0)-(v<0)
                last=sign(days[-1][1]);count=0
                for _,value in reversed(days):
                    if value is None or sign(value)!=last:break
                    count+=1
                # A run that fills the whole range was written as "9/22부터 7일 연속", the length of the query. The day
                # before the range was not read, so the run began on the first day returned or earlier.
                # The same holds for a run that stops at a day with no figure: the direction of that day is not known,
                # so the first day after it is not the day the investor turned.
                whole=count==len(days);gap=not whole and days[-count-1][1] is None
                since=days[-count][0]+(' or earlier (every day returned)' if whole else ' or earlier (no figure for the day before)' if gap else '')
                runs.append((field[6:],{1:'net buying',-1:'net selling',0:'flat'}[last],since,f'at least {count}' if whole or gap else count,names[identifier]))
            # One line per investor, direction and start day, with the securities it holds for: five securities written
            # as "turned on 10/1" included one that had turned on 9/29.
            grouped={}
            for investor,direction,since,length,name in runs:grouped.setdefault((investor,direction,since,length),[]).append(name)
            runs=[{'investor':k[0],'direction':k[1],'since':k[2],'days_in_a_row':k[3],'securities':v}
                for k,v in sorted(grouped.items(),key=lambda kv:(kv[0][0],kv[0][1],str(kv[0][2])))]
        return self.tools.result(items,selection=selection,scope={**scope,'dataset_kind':'flow_observations',
            'frequency':frequency,'intraday_interval_semantics':'not_certified' if frequency=='intraday' else None,
            **({'net_value_runs':runs,'net_value_runs_mean':'Up to the last day returned: which securities each investor has been net buying or selling, and since when. '
                'A security turned direction on its own `since` day; say "turned" only for the securities listed under that day. '
                '"or earlier" means the day before that day was not read or has no figure: the run may have begun earlier, so do not say the investor turned on that day.'} if runs else {})},
            table=True)

    def returns(self,dataset_ref,start_date,end_date,price_field,basis_policy='observed_close_only'):
        if basis_policy=='observed_close_only' and price_field=='adjustedClosePrice':raise ValueError('Observed-close policy cannot certify adjusted prices')
        dataset=self.tools.store.reference(dataset_ref,'dataset')
        if dataset['scope'].get('dataset_kind')!='price_observations':raise ValueError('A stored price dataset is required')
        if self.tools.check_date(start_date)>=self.tools.check_date(end_date):raise ValueError('Return start must precede end')
        prices={}
        for obj in dataset['items']:
            if obj['object_type']!=('DailyNAV' if price_field=='nav' else 'DailyBar'):continue
            prop=obj['properties'];identifier=prop['etfInstrumentId' if price_field=='nav' else 'instrumentId']
            prices.setdefault((identifier,prop['tradeDate']),[]).append(obj)
        rows=[]
        for member in dataset['selection']['items']:
            identifier=member['object_id'];left=prices.get((identifier,start_date),[]);right=prices.get((identifier,end_date),[])
            row={'object_id':identifier,'object_type':member['object_type'],'weight_ratio':member.get('weight_ratio'),
                 'start_date':start_date,'end_date':end_date,
                 'status':'unavailable','return_ratio':None,'input_ids':[o['object_id'] for o in left+right]}
            if len(left)!=1 or len(right)!=1:
                # "Unavailable" alone sent the agent guessing dates; say which end is missing and which days exist.
                days=sorted(day for key,day in prices if key==identifier)
                missing=[day for day,found in ((start_date,left),(end_date,right)) if len(found)!=1]
                row.update(reason='no single price on '+' and '.join(missing)+'; choose dates from dates_with_price',dates_with_price=days)
            else:
                a,b=left[0]['properties'],right[0]['properties']
                x,y=number(a.get(price_field)),number(b.get(price_field))
                if price_field!='nav' and basis_policy=='require_recorded_basis' and (not a.get('priceBasis') or a.get('priceBasis')!=b.get('priceBasis')):
                    row['reason']='unknown_or_mismatched_price_basis'
                elif x is None or y is None or x<=0 or y<=0:
                    row['reason']='missing_or_nonpositive_price'
                elif not (member.get('object') or {}).get('properties',{}).get('currencyCode'):
                    row['reason']='unknown_currency'
                else:
                    row.update(status='calculated',return_ratio=str(y/x-1),start_price=str(x),end_price=str(y),
                        price_basis=a.get('priceBasis'),price_field=price_field,
                        currency=member['object']['properties']['currencyCode'],
                        interpretation='change in NAV per ETF unit in ETF currency; not market-price or total return' if price_field=='nav' else
                            'arithmetic change in observed closing quotes; adjustment comparability not certified' if basis_policy=='observed_close_only' else 'price return; not certified total return')
            rows.append(row)
        return self.tools.result(rows,selection=dataset['selection'],scope={'dataset_kind':'returns',
            'start_date':start_date,'end_date':end_date,'input_ref':dataset_ref,'formula':'end/start - 1','basis_policy':basis_policy,
            'value_kind':'nav_per_unit' if price_field=='nav' else 'market_price'},limit=100)

    def breadth(self,dataset_ref,start_date=None,end_date=None):
        dataset=self.tools.store.reference(dataset_ref,'dataset')
        if dataset['scope'].get('dataset_kind')=='price_observations':
            # All three runs passed the prices here first, were refused, and had to calculate returns and come back.
            if not start_date or not end_date:raise ValueError('With a price dataset, give start_date and end_date: the two trading days to compare')
            dataset=self.returns(dataset_ref,start_date,end_date,'closePrice')[1]
        elif start_date or end_date:raise ValueError('A return dataset already has its dates; pass dataset_ref alone')
        if dataset['scope'].get('dataset_kind')!='returns':raise ValueError('A saved price or return dataset is required')
        if dataset['scope'].get('value_kind')=='nav_per_unit':raise ValueError('NAV changes cannot describe market-price breadth')
        groups={key:{'count':0,'raw_weight_sum':Decimal(0),'missing_weights':0} for key in ('up','down','flat','unavailable')}
        for row in dataset['items']:
            value=number(row['return_ratio']) if row['status']=='calculated' else None
            group='unavailable' if value is None else 'up' if value>0 else 'down' if value<0 else 'flat'
            groups[group]['count']+=1
            if row.get('weight_ratio') is None:groups[group]['missing_weights']+=1;continue
            weight=number(row['weight_ratio'])
            if weight is None or weight<0:raise ValueError('Invalid holdings weights')
            groups[group]['raw_weight_sum']+=weight
        rows=[{'direction':k,**v,'raw_weight_sum':str(v['raw_weight_sum'])} for k,v in groups.items()]
        return self.tools.result(rows,selection=dataset['selection'],scope={'dataset_kind':'breadth','input_ref':dataset_ref,
            'weights':'original raw weights, no normalization','price_basis_policy':dataset['scope'].get('basis_policy'),
            'start_date':dataset['scope'].get('start_date'),'end_date':dataset['scope'].get('end_date'),
            'interpretation':'directional breadth of selected price observations; not contribution to ETF return'})

    def nav(self,dataset_ref,trade_date):
        dataset=self.tools.store.reference(dataset_ref,'dataset')
        if dataset['scope'].get('dataset_kind')!='price_observations':raise ValueError('Price/NAV dataset required')
        self.tools.check_date(trade_date)
        rows=[]
        for member in dataset['selection']['items']:
            if member['object_type']!='ETF':continue
            prices=[x for x in dataset['items'] if x['object_type']=='DailyBar' and x['properties'].get('instrumentId')==member['object_id'] and x['properties'].get('tradeDate')==trade_date]
            navs=[x for x in dataset['items'] if x['object_type']=='DailyNAV' and x['properties'].get('etfInstrumentId')==member['object_id'] and x['properties'].get('tradeDate')==trade_date]
            row={'object_id':member['object_id'],'trade_date':trade_date,'status':'unavailable',
                'input_ids':[x['object_id'] for x in prices+navs],'premium_ratio':None}
            currency=(member.get('object') or {}).get('properties',{}).get('currencyCode')
            if len(prices)!=1 or len(navs)!=1:row['reason']='Exact date price/NAV missing or ambiguous'
            elif not currency:row['reason']='ETF currency is unknown'
            else:
                close=number(prices[0]['properties'].get('closePrice'));nav=number(navs[0]['properties'].get('nav'))
                if close is None or nav is None or close<=0 or nav<=0:
                    row['reason']='Price and NAV must be present and positive'
                else:row.update(status='calculated',premium_ratio=str(close/nav-1),close_price=str(close),nav=str(nav),currency=currency,
                    basis='Daily closing price versus daily NAV per ETF unit; not intraday premium')
            rows.append(row)
        return self.tools.result(rows,selection=dataset['selection'],scope={'input_ref':dataset_ref,'dataset_kind':'nav_premium',
            'formula':'close/nav - 1','unit_contract':'DailyNAV.nav and ETF.currencyCode'})
