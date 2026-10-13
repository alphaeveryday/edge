"""Comparable financial and macro evidence; report absent semantic dimensions explicitly."""
from decimal import Decimal
from edge_analysis_v2.tools.graph.provider import SELECTOR,STRING,LIMIT,RESULT_REF,REF
from edge_analysis_v2.tools.graph.shape import won_text


def one_per_figure(rows, identity, version):
    """Keep one row for each figure that was stored several times with the same value.

    Every figure in the source is stored once per collection run (two to seven times), always with the same value.
    Returned as they are, a question about one quarter came back as eight rows for four figures. Rows whose
    stored values differ are all kept and marked, because then the versions disagree and none may be dropped.
    """
    groups={}
    for row in rows:groups.setdefault(tuple(row['properties'].get(k) for k in identity),[]).append(row)
    kept=[]
    for same in groups.values():
        same.sort(key=lambda r:str(r['properties'].get(version) or ''))
        if len({str(r['properties'].get('value')) for r in same})==1:kept.append({**same[-1],'stored_versions':len(same)})
        else:kept.extend({**r,'stored_versions':len(same),'versions_disagree':True} for r in same)
    return kept


class FinancialTools:
    def __init__(self,tools):
        self.tools=tools;self.graph=tools.graph
        tools.register('list_macro_series',{},[],self.series,sources=['MacroObservation'])
        tools.register('get_financial_observations',{'targets':SELECTOR,'kind':{'enum':['actual','segment','guidance','consensus']},
             'fiscal_years':{'type':'array','items':{'type':'integer','minimum':1900,'maximum':2100}},
             'metrics':{'type':'array','items':STRING},'fs_basis':STRING,'limit':LIMIT},['targets','kind'],self.observations,sources=['FinancialMetric', 'ReportedBusinessSegment', 'Disclosure', 'SourceEvent'])
        tools.register('get_macro_observations',{'start_date':STRING,'end_date':STRING,'series_ids':{'type':'array','items':STRING},'query':{'type':'string'},'limit':LIMIT},
            ['start_date','end_date'],self.macro,sources=['MacroObservation'])
        tools.register('compare_two_figures',{'dataset_ref':RESULT_REF,'earlier_id':STRING,'later_id':STRING},['dataset_ref','earlier_id','later_id'],self.compare,sources=['저장한 재무·매크로 관측'])
        tools.register('derive_fourth_quarter',{'dataset_ref':RESULT_REF,'annual_id':STRING,'nine_month_id':STRING},['dataset_ref','annual_id','nine_month_id'],self.derive,sources=['저장한 재무 관측'])
        tools.register('estimate_contract_profit',{'event_ref':REF,'company_ref':REF,'years_low':{'type':'number','exclusiveMinimum':0},'years_high':{'type':'number','exclusiveMinimum':0},
             'margin_low':{'type':'number'},'margin_high':{'type':'number'},'company_share':{'type':'number','exclusiveMinimum':0,'maximum':1},
             'etf_ref':REF,'holdings_date':STRING,'holding_weight':{'type':'number','minimum':0,'maximum':1}},
            ['event_ref','company_ref'],self.impact,sources=['SourceEvent', 'EventMeasurement', 'FinancialMetric'])
        tools.register('compare_actual_to_expectation',{'actual_ref':RESULT_REF,'actual_id':STRING,'expectation_ref':RESULT_REF,'expectation_id':STRING},
            ['actual_ref','actual_id','expectation_ref','expectation_id'],self.expectation,sources=['저장한 실적과 기대 관측'])

    def impact(self,event_ref,company_ref,years_low=None,years_high=None,margin_low=None,margin_high=None,holding_weight=None,
               company_share=None,etf_ref=None,holdings_date=None):
        """What a contract adds to a company's yearly revenue and operating profit, as a range.

        Asked what an order means for earnings, answers said the effect was "limited" or "cannot be determined":
        the instructions require figures to come from a tool and no tool worked this out. The stored inputs are
        the contract value and period and the last full year's revenue and operating profit; the recognition
        period and the margin are assumptions, and each is named as stored or as supplied by the caller.
        """
        if event_ref['object_type']!='SourceEvent':raise ValueError('event_ref is a SourceEvent: the event_id of a list_events row')
        if company_ref['object_type']!='Company':
            # 3 of 22 calls passed the stock instead of its issuer; the issuer is one stored link away.
            issuers,_=self.tools.actors(self.graph.get([company_ref]))
            issuers=[a for a in issuers if a['object_type']=='Company']
            if len(issuers)!=1:raise ValueError('company_ref must be a Company, or an Equity with one issuing company')
            company_ref=issuers[0]
        found=[i['object'] for i in self.graph.get([event_ref])['items'] if i['status']=='resolved']
        if not found:raise ValueError('The event is not in this data at the cutoff')
        # One event is stored once per report, and the amount or the period is often in another report than the
        # one passed: 4 of 10 calls failed on that and two answers gave up on the estimate. Read every report of
        # this type about this company on this date, the same rows the event tools join.
        properties=found[0]['properties']
        same=self.graph.linked([company_ref],'Company_ParticipatesIn_SourceEvent',where='n.eventType=$type AND n.eventDate=date($day)',
            parameters={'type':properties.get('eventType'),'day':properties.get('eventDate')}) if properties.get('eventDate') else []
        reports=self.tools.events.enrich(list({o['object_id']:o for o in [*found,*(r['target'] for r in same)]}.values()))
        feature=lambda code,unit:[Decimal(m['properties']['value']) for report in reports for m in report['features']
            if m['properties'].get('metricCode')==code and m['properties'].get('unit')==unit and m['properties'].get('value') is not None]
        values=feature('CONTRACT_VALUE','KRW')
        if not values:
            raise ValueError('No report of this event states a won contract value, so there is nothing to scale. '
                'Pick an event row whose amounts list a won value (get_event_timeline lists the amounts of an event).')
        # The filing's own amount when a report carries it (news rounds 6,410억 9,538만원 to 6,400억원); else the amount most reports give.
        filed=[Decimal(m['properties']['value']) for report in reports for m in report['features'] if m['properties'].get('metricCode')=='CONTRACT_VALUE'
            and m['properties'].get('unit')=='KRW' and m['properties'].get('value') is not None and m['properties'].get('valueSource')=='DART']
        value=max(set(filed or values),key=(filed or values).count)
        other_values=sorted(set(values)-{value})
        days=set(feature('CONTRACT_DURATION','DAY'))
        stored_years=Decimal(days.pop())/Decimal(365) if len(days)==1 else None
        share=Decimal(str(company_share)) if company_share is not None else Decimal(1)
        if not 0<share<=1:raise ValueError('company_share is the part of the contract this company earns, above 0 and at most 1')
        value*=share
        if holding_weight is None and etf_ref is not None:
            # The weight was left out in most calls, so the answer had no fund-level size. Look it up from the fund.
            selection=self.tools.holdings(etf_ref,holdings_date or self.tools.store.cutoff[:10],'latest_on_or_before')[1]['selection']
            _,mapping=self.tools.actors(selection)
            issued={m['security']['object_id'] for m in mapping if m['actor']['object_id']==company_ref['object_id']}
            weights=[Decimal(str(i['weight_ratio'])) for i in selection['items'] if i['object_id'] in issued and i.get('weight_ratio') is not None]
            holding_weight=sum(weights,Decimal(0)) if weights else None
        low,high=(Decimal(str(y)) if y is not None else stored_years for y in (years_low,years_high))
        if low is None or high is None:
            raise ValueError('No contract period is stored for this report. Pass years_low and years_high: your assumption of how many years the amount is recognised over.')
        if not 0<low<=high:raise ValueError('years_low and years_high must be positive and ordered')
        rows=one_per_figure([r['target'] for r in self.graph.linked([company_ref],'FinancialMetric_ForCompany_Company','reverse',
            where="n.metric IN $m AND n.fiscalPeriod='FY' AND n.fsBasis='CFS'",parameters={'m':['revenue','operating_income']})],
            ('corpCode','fiscalYear','metric','periodKind','derivation'),'receivedAt')
        years={}
        for row in rows:
            p=row['properties']
            if p.get('unit')=='KRW' and p.get('value') is not None:years.setdefault(p['fiscalYear'],{})[p['metric']]=Decimal(p['value'])
        full=[y for y,v in years.items() if len(v)==2 and v['revenue']>0]
        if not full:raise ValueError('No full-year consolidated revenue and operating profit are stored for this company, so there is no base to compare with')
        year=max(full);revenue,profit=years[year]['revenue'],years[year]['operating_income']
        stored_margin=profit/revenue
        margins=[Decimal(str(m)) if m is not None else stored_margin for m in (margin_low,margin_high)]
        if not margins[0]<=margins[1]:raise ValueError('margin_low must not exceed margin_high')
        yearly=(value/high,value/low)                                   # recognised evenly: a longer period means less per year
        earned=(yearly[0]*margins[0],yearly[1]*margins[1])
        percent=lambda x:f'{x*100:.1f}%'
        round_won=lambda x:won_text((x/10**8).quantize(Decimal(1))*10**8) if abs(x)>=10**9 else won_text(x)   # an estimate is not exact to the won
        span=lambda pair,show:show(pair[0]) if pair[0]==pair[1] else show(pair[0])+' ~ '+show(pair[1])
        # Every value says its basis: an answer turned "0.7~1.5% a year" into "about 1% in the month ahead".
        estimate={'basis':'per year of recognition, for this company; one month is about a twelfth of a year',
            'contract_value_used':won_text(value)+(f' ({percent(share)} of the reported amount)' if share!=1 else ''),
            'yearly_revenue':span(yearly,round_won),'share_of_last_year_revenue':span(tuple(x/revenue for x in yearly),percent),
            'yearly_operating_profit':span(earned,round_won)}
        if profit>0:estimate['share_of_last_year_operating_profit']=span(tuple(x/profit for x in earned),percent)
        if holding_weight is not None and profit>0:
            weight=Decimal(str(holding_weight))
            estimate['weighted_by_fund_holding']=span(tuple(x/profit*weight for x in earned),percent)
            estimate['weighted_by_fund_holding_means']="the company's operating-profit uplift times its weight in the fund; a size of the earnings effect, not an expected price move"
        swing={'recognition_years':high/low,'operating_margin':margins[1]/margins[0] if margins[0]>0 else Decimal(1)}
        # The tool scaled a five-year tally of past awards that an answer took for a September contract. What the
        # reports say the stage is comes first, so an unsigned or unstaged amount is read as that.
        stages=sorted({r['event']['properties'].get('lifecycleStage') for r in reports if r['event']['properties'].get('lifecycleStage')})
        item={'contract_stage':(', '.join(stages) or 'not reported')+('' if 'DEFINITIVE_SIGNED' in stages else
                ': no report of this event says the contract is signed. Read the report before using this amount, and say the estimate is for an unsigned contract.'),
            'estimate':estimate,
            'most_sensitive_assumption':max(swing,key=swing.get) if max(swing.values())>1 else 'none varied: pass a low and a high value to see the range',
            **({'other_reported_amounts':[won_text(v) for v in other_values]} if other_values else {}),
            'confirmed_inputs':{'contract_value':won_text(value/share),'reports_of_this_event':len(reports),'last_full_year':year,'revenue':won_text(revenue),'operating_profit':won_text(profit),
                'operating_margin':percent(stored_margin),**({'holding_weight':percent(Decimal(str(holding_weight)))} if holding_weight is not None else {})},
            'assumptions':{'company_share':percent(share)+(' (the whole contract; pass company_share when it is shared with another company)' if company_share is None else ' (supplied by the caller)'),
                'recognition_years':span((low,high),lambda y:f'{y:.1f}')+(' (stored contract period)' if years_low is None and years_high is None else ' (supplied by the caller)'),
                'operating_margin':span(tuple(margins),percent)+(' (the company\'s last full-year margin)' if margin_low is None and margin_high is None else ' (supplied by the caller)'),
                'recognition':'even over the period; the first year and the month ahead recognise less than a full year'},
            'reading':'An estimate from the stated assumptions, not reported or forecast earnings. Say it as an estimate with its assumptions, and say which assumption would change it most.'}
        return self.tools.result([item],limit=1,scope={'dataset_kind':'contract_impact_estimate','event_ref':event_ref,'company_ref':company_ref})

    def observations(self,targets,kind,fiscal_years=None,metrics=None,fs_basis=None,limit=20):
        selection=self.tools.select(targets);actors,mapping=self.tools.actors(selection)
        companies=[a for a in actors if a['object_type']=='Company']
        if kind=='actual':
            conditions=[];params={}
            for key,value in [('fiscalYear',fiscal_years),('metric',metrics),('fsBasis',fs_basis)]:
                if value:
                    conditions.append('n.'+key+(' IN $' if isinstance(value,list) else '=$')+key);params[key]=value
            rows=self.graph.linked(companies,'FinancialMetric_ForCompany_Company','reverse',where=' AND '.join(conditions) or None,parameters=params)
            if not rows and params:
                # An empty answer to a filtered request is ambiguous: no data, or a value the data never uses
                # (operatingIncome for operating_income). The unfiltered rows settle which, so a wrong argument
                # comes back as an error naming the values that exist instead of reading as absence.
                stored=self.graph.linked(companies,'FinancialMetric_ForCompany_Company','reverse')
                for key,wanted in params.items():
                    present={row['target']['properties'].get(key) for row in stored}-{None}
                    unknown=[v for v in (wanted if isinstance(wanted,list) else [wanted]) if v not in present]
                    if stored and unknown:
                        raise ValueError(f'No stored row has {key} in {unknown}. Values stored for these companies: '
                            +', '.join(map(str,sorted(present,key=str)))+'. This is a wrong argument, not missing data; repeat with a stored value.')
            kept=one_per_figure([r['target'] for r in rows],('corpCode','fiscalYear','fiscalPeriod','metric','periodKind','fsBasis','derivation'),'receivedAt')
            source={r['target']['object_id']:r['source'] for r in rows}
            rows=[{'target':target,'source':source[target['object_id']]} for target in kept]
            items=[{**({'value_text':won_text(row['target']['properties'].get('value'))} if row['target']['properties'].get('unit')=='KRW' else {}),
                **row['target'],'company_ref':row['source']} for row in rows]
        elif kind=='segment':
            if fiscal_years or metrics or fs_basis:raise ValueError('Segment fiscal/accounting dimensions are not mapped; these filters cannot be honored')
            docs=self.graph.linked(companies,'Company_HasDisclosure_Disclosure')
            refs=[{k:r['target'][k] for k in ('object_type','object_id')} for r in docs]
            rows=self.graph.linked(refs,'ReportedBusinessSegment_DisclosedIn_Disclosure','reverse')
            items=[row['target'] for row in rows]
        elif kind in ('guidance','consensus'):
            if fiscal_years or metrics or fs_basis:raise ValueError('Comparable expectation fiscal/accounting dimensions are not yet mapped')
            if kind=='consensus':
                return self.tools.result([],selection=selection,scope={'dataset_kind':'financial_observations','kind':kind,
                    'availability':'unsupported','reason':'The graph contains no typed consensus observations. Source-event guidance cannot be relabeled consensus.'})
            events={}
            for row in self.graph.linked(companies,'Company_ParticipatesIn_SourceEvent',where="n.eventType='COMPANY.EARNINGS.GUIDANCE_CHANGE'"):
                events[row['target']['object_id']]=row['target']
            items=self.tools.events.enrich(list(events.values()))
            return self.tools.result(items,selection=selection,scope={'dataset_kind':'guidance_events','comparison_ready':False,
                'reason':'Event evidence lacks complete fiscal period/accounting/currency dimensions for comparable expected financials'},
                display_items=[self.tools.events.display(x) for x in items],limit=limit)
        else:raise ValueError('Unsupported financial kind')
        return self.tools.result(items,selection=selection,scope={'dataset_kind':'financial_observations','kind':kind,
            'actor_mapping':mapping,# An answer read stored_versions: 2 and wrote "only one version is stored".
            'stored_versions':'How many times the source figure was collected. The copies carry the same value and are shown as one row, '
                'unless versions_disagree is set: then every copy is a row and none may be dropped. It is not a count of restatements by the company.'},limit=limit,
            table='ids' if kind=='actual' else None)

    def macro(self,start_date,end_date,series_ids=None,query='',limit=20):
        if self.tools.check_date(start_date)>self.tools.check_date(end_date):raise ValueError('Invalid dates')
        items=self.graph.nodes('MacroObservation',query=query,filters={'seriesId':series_ids} if series_ids else {},
            where='n.observationDate>=date($start) AND n.observationDate<=date($end)',parameters={'start':start_date,'end':end_date})
        if not items and (query or series_ids):
            # Series are named in English ("USD/KRW exchange rate"), so a Korean word matched nothing and read as no data.
            stored,_=self.series()
            known={row['series_id'] for row in stored['items']}
            if query or any(s not in known for s in series_ids or []):
                raise ValueError('No macro series matches '+repr(query or series_ids)+'. Stored series: '
                    +'; '.join(f"{row['series_id']} ({row['name']}, {row['first_date']}..{row['last_date']})" for row in stored['items'])
                    +'. This is a wrong argument, not missing data; repeat with series_ids from this list.')
        items=one_per_figure(items,('seriesId','observationDate'),'availableAt')
        return self.tools.result(items,scope={'dataset_kind':'macro_observations','start_date':start_date,'end_date':end_date},limit=limit,table='ids')

    def observation(self,ref,identifier):
        data=self.tools.store.reference(ref,'dataset')
        rows=[x for x in data['items'] if x.get('object_id')==identifier]
        if len(rows)!=1:raise ValueError('Select one unambiguous observation from this stored dataset')
        return rows[0],data

    def series(self):
        rows=self.graph.query('MATCH (n:MacroObservation) WHERE n.availableAt<=datetime($cutoff) '
            'RETURN n.seriesId AS series_id,n.seriesName AS name,n.unit AS unit,n.sourceVendor AS provider,'
            'min(n.observationDate) AS first_date,max(n.observationDate) AS last_date ORDER BY series_id',
            {'cutoff':self.tools.store.cutoff},objects=['MacroObservation'])
        return self.tools.result(rows,scope={'dataset_kind':'available_macro_series'},limit=100)

    @staticmethod
    def comparable(a,b,keys):
        for key in keys:
            if a.get(key) is None or b.get(key) is None or a[key]!=b[key]:
                hint={'fiscalPeriod':' Cumulative figures compare only the same period of different years; to compare one quarter with the next, pick the rows whose periodKind is QUARTER.',
                      'periodKind':' One is a three-month figure and the other cumulative; pick two rows with the same periodKind.',
                      'fsBasis':' One is consolidated (CFS) and the other separate (OFS); pick two rows with the same fsBasis.'}.get(key,'')
                raise ValueError(f'The two observations differ in {key} ({a.get(key)} vs {b.get(key)}), so they cannot be compared.'+hint)
        if a.get('value') is None or b.get('value') is None:raise ValueError('Comparison value is missing')
        x,y=Decimal(a['value']),Decimal(b['value'])
        if not x.is_finite() or not y.is_finite():raise ValueError('Comparison values must be finite')
        return x,y

    def compare(self,dataset_ref,earlier_id,later_id):
        a,data=self.observation(dataset_ref,earlier_id);b,_=self.observation(dataset_ref,later_id)
        if a['object_type']!=b['object_type']:raise ValueError('Observation types differ')
        if a['object_type']=='FinancialMetric':
            keys=['corpCode','metric','unit','fsBasis','periodKind','derivation']
            # Three-month and six-month cumulative revenue are different durations.
            if a['properties'].get('periodKind') in ('CUMULATIVE','YTD'):
                keys.append('fiscalPeriod')
            before=a['properties'].get('periodEnd');after=b['properties'].get('periodEnd')
        elif a['object_type']=='MacroObservation':
            keys=['seriesId','unit'];before=a['properties'].get('observationDate');after=b['properties'].get('observationDate')
        else:raise ValueError('Unsupported comparison object')
        if not before or not after or before>=after:raise ValueError('Earlier/later observation dates must be ordered')
        x,y=self.comparable(a['properties'],b['properties'],keys)
        return self.tools.result([{'earlier_id':earlier_id,'later_id':later_id,'difference':str(y-x),
            **({'difference_text':won_text(y-x)} if a['properties']['unit']=='KRW' else {}),
            'change_ratio':str(y/x-1) if x>0 else None,'ratio_reason':None if x>0 else 'nonpositive_denominator',
            'unit':a['properties']['unit']}],selection=data.get('selection'),scope={'dataset_kind':'observation_comparison','input_ref':dataset_ref})

    def derive(self,dataset_ref,annual_id,nine_month_id):
        a,data=self.observation(dataset_ref,annual_id);b,_=self.observation(dataset_ref,nine_month_id)
        if a['object_type']!='FinancialMetric' or b['object_type']!='FinancialMetric':raise ValueError('FinancialMetric inputs required')
        p,q=a['properties'],b['properties']
        if p.get('metric') not in ('revenue','operating_income','net_income','REVENUE','OPERATING_INCOME','NET_INCOME'):
            raise ValueError('Only verified additive financial flow metrics may be differenced')
        if p.get('fiscalPeriod')!='FY' or q.get('fiscalPeriod') not in ('9M','Q3') or q.get('periodKind') not in ('YTD','CUMULATIVE'):
            raise ValueError('Verified full-year and cumulative nine-month periods required')
        x,y=self.comparable(p,q,['corpCode','metric','unit','fsBasis','fiscalYear'])
        if not p.get('rceptNo') or p.get('rceptNo')!=q.get('rceptNo'):
            raise ValueError('Cross-report revision compatibility is not established; choose compatible inputs')
        return self.tools.result([{'value':str(x-y),**({'value_text':won_text(x-y)} if p['unit']=='KRW' else {}),'metric':p['metric'],'unit':p['unit'],'fiscal_period':'Q4',
            'formula':'FY - 9M','input_ids':[annual_id,nine_month_id]}],selection=data.get('selection'),scope={'dataset_kind':'derived_financial','input_ref':dataset_ref})

    def expectation(self,actual_ref,actual_id,expectation_ref,expectation_id):
        a,data=self.observation(actual_ref,actual_id);e,expected=self.observation(expectation_ref,expectation_id)
        if expected['scope'].get('dataset_kind')!='expected_financial_observations':
            raise ValueError('No comparable expected-financial dataset; event amounts are not consensus')
        p,q=a['properties'],e['properties']
        if not p.get('availableAt') or not q.get('availableAt') or q['availableAt']>=p['availableAt']:
            raise ValueError('Expectation must be available strictly before the actual announcement')
        actual,estimate=self.comparable(p,q,['corpCode','metric','unit','currencyCode','fsBasis','fiscalYear','fiscalPeriod','periodKind'])
        return self.tools.result([{'difference':str(actual-estimate),**({'difference_text':won_text(actual-estimate)} if p['unit']=='KRW' else {}),'difference_ratio':str(actual/estimate-1) if estimate>0 else None,
            'ratio_reason':None if estimate>0 else 'nonpositive_expected_value','unit':p['unit'],
            'input_ids':[actual_id,expectation_id]}],selection=data.get('selection'),scope={'input_refs':[actual_ref,expectation_ref]})
