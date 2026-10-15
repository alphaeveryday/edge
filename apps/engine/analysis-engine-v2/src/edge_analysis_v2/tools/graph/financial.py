"""Comparable financial and macro evidence; report absent semantic dimensions explicitly."""
from decimal import Decimal
from edge_analysis_v2.tools.graph.provider import SELECTOR,STRING,LIMIT,RESULT_REF,REF
from edge_analysis_v2.tools.graph.shape import number,won_text


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
        # "10" and "10.0" are one value; a value that is not a number is compared as written.
        written=lambda r:(lambda v:str(r['properties'].get('value')) if v is None else v)(number(r['properties'].get('value')))
        if len({written(r) for r in same})==1:kept.append({**same[-1],'stored_versions':len(same)})
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
        tools.register('estimate_contract_profit',{'event_ref':REF,'company_ref':REF,'years_low':{'type':'number','minimum':0.01,'maximum':100},'years_high':{'type':'number','minimum':0.01,'maximum':100},
             'margin_low':{'type':'number','minimum':-1,'maximum':1},'margin_high':{'type':'number','minimum':-1,'maximum':1},'company_share':{'type':'number','exclusiveMinimum':0,'maximum':1},
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
        # NaN passes a schema's minimum and maximum, and then raised inside the arithmetic past the tool record.
        if any(v is not None and number(v) is None for v in (years_low,years_high,margin_low,margin_high,holding_weight,company_share)):
            raise ValueError('years, margins, company_share and holding_weight must be finite numbers')
        # With a weight passed by hand the fund was not read at all, so a fund that does not exist or a date after the cutoff went through.
        if holding_weight is not None and (etf_ref is not None or holdings_date is not None):
            raise ValueError('Pass holding_weight, or etf_ref to have the weight read from the fund, not both.')
        if holdings_date is not None and etf_ref is None:raise ValueError('holdings_date is the date of the holdings of etf_ref; pass etf_ref with it.')
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
        def parties(report):
            # One company can have several roles in one event; keeping the last one read let a customer through as a partner.
            found={}
            for p in report.get('participants') or []:found.setdefault((p['actor']['object_type'],p['actor']['object_id']),set()).add(p.get('role'))
            return found
        asked=next(r for r in reports if r['event']['object_id']==event_ref['object_id']);roles=parties(asked)
        mine=('Company',company_ref['object_id'])
        # The contract of one company was scaled against the accounts of another, and of its customer.
        if roles and mine not in roles:raise ValueError('company_ref is not a participant of this event. Pass the company that earns the contract, from the participants of the event row.')
        if 'CUSTOMER' in roles.get(mine,()) and 'SUPPLIER' not in roles[mine]:raise ValueError('company_ref is the customer of this contract. Pass the company that earns it.')
        # No participant is stored for some events. The estimate is still made, and says that the company's part is the caller's word.
        unconfirmed=None if roles else 'This event stores no participants, so the data does not confirm that this company earns the contract. Say that the company is taken from the report text.'
        # Two contracts of one company on one day are two events of one type and date, and the amount and period of
        # one are not the other's. A report is this contract when the data puts it in the thread of the event asked
        # for, or when it names one of its counterparties. A report that shows neither is not read: a missing
        # counterparty link is not a sign of the same contract.
        refs=[{'object_type':'SourceEvent','object_id':r['event']['object_id']} for r in reports]
        thread={r['source']['object_id']:r['target']['object_id'] for r in self.graph.linked(refs,'SourceEvent_InEventThread_EventThread')
            if r['target'].get('object_type')=='EventThread'}
        same_thread=lambda r:thread.get(event_ref['object_id']) is not None and thread.get(r['event']['object_id'])==thread[event_ref['object_id']]
        other_thread=lambda r:None not in (thread.get(event_ref['object_id']),thread.get(r['event']['object_id'])) and not same_thread(r)
        others=set(roles)-{mine};total=len(reports)
        reports=[r for r in reports if r is asked or same_thread(r) or (not other_thread(r) and others&set(parties(r)))]
        # A stored amount that is not a finite number, or that the pipeline marks as not read from the text
        # (the same marks the event filters treat as unknown), is an amount the report does not state.
        read=lambda m:m['properties'].get('valueSource')!='UNRESOLVED' and m['properties'].get('parseStatus') in (None,'ok')
        measures=lambda code,unit:[m for report in reports for m in report['features']
            if m['properties'].get('metricCode')==code and m['properties'].get('unit')==unit and read(m) and (number(m['properties'].get('value')) or 0)>0]
        feature=lambda code,unit:[number(m['properties']['value']) for m in measures(code,unit)]
        # An amount a year is not a contract total: 100억 a year over five years came back as 20억 a year.
        amounts=[m for m in measures('CONTRACT_VALUE','KRW') if m['properties'].get('periodBasis')!='ANNUAL']
        values=[number(m['properties']['value']) for m in amounts]
        if not values and len(amounts)<len(measures('CONTRACT_VALUE','KRW')):
            yearly_amounts=sorted({number(m['properties']['value']) for m in measures('CONTRACT_VALUE','KRW')})
            raise ValueError('The reports of this event state an amount a year ('+', '.join(won_text(v) for v in yearly_amounts)+'), not a contract total, so there is no total to spread over a period. '
                'That amount is the yearly revenue as reported: compare it with the last full-year revenue from get_financial_observations.')
        if not values:
            raise ValueError('No report of this event states a won contract value, so there is nothing to scale. '
                'Pick an event row whose amounts list a won value (get_event_timeline lists the amounts of an event).')
        # The filing's own amount when a report carries it (news rounds 6,410억 9,538만원 to 6,400억원); else the amount most reports give.
        filed=[number(m['properties']['value']) for m in amounts if m['properties'].get('valueSource')=='DART']
        value=max(set(filed or values),key=(filed or values).count)
        other_values=sorted(set(values)-{value})
        days=set(feature('CONTRACT_DURATION','DAY'))
        stored_years=Decimal(days.pop())/Decimal(365) if len(days)==1 else None
        share=Decimal(str(company_share)) if company_share is not None else Decimal(1)
        if not 0<share<=1:raise ValueError('company_share is the part of the contract this company earns, above 0 and at most 1')
        value*=share
        supplied_weight=holding_weight is not None;weight_missing=None
        if holding_weight is None and etf_ref is not None:
            # The weight was left out in most calls, so the answer had no fund-level size. Look it up from the fund.
            day=holdings_date or self.tools.cutoff_day().isoformat()
            selection=self.tools.holdings(etf_ref,day,'latest_on_or_before')[1]['selection']
            _,mapping=self.tools.actors(selection)
            issued={m['security']['object_id'] for m in mapping if m['actor']['object_id']==company_ref['object_id']}
            weights=[w if w is not None and w>=0 else None for w in (number(i.get('weight_ratio')) for i in selection['items'] if i['object_id'] in issued)]
            # Common and preferred shares held at 10% and an unknown weight are not a 10% holding of the company.
            holding_weight=sum(weights,Decimal(0)) if weights and None not in weights else None
            # Left out without a word, a fund that does not hold the company read the same as one whose weight is unknown.
            if holding_weight is None:
                weight_missing=(f'The fund holds no security issued by this company in its holdings on or before {day}' if not weights else
                    f'The fund holds the company on or before {day}, but a weight is not stored for every security of it')+', so no fund-weighted figure is given.'
        low,high=(Decimal(str(y)) if y is not None else stored_years for y in (years_low,years_high))
        if low is None or high is None:
            raise ValueError('No contract period is stored for this report. Pass years_low and years_high: your assumption of how many years the amount is recognised over.')
        if not 0<low<=high:raise ValueError('years_low and years_high must be positive and ordered')
        rows=one_per_figure([r['target'] for r in self.graph.linked([company_ref],'FinancialMetric_ForCompany_Company','reverse',
            where="n.metric IN $m AND n.fiscalPeriod='FY' AND n.fsBasis='CFS'",parameters={'m':['revenue','operating_income']})],
            ('corpCode','fiscalYear','metric','periodKind','derivation'),'receivedAt')
        years={}
        for row in rows:
            p=row['properties'];figure=number(p.get('value'))
            if p.get('unit')=='KRW' and figure is not None:years.setdefault(p['fiscalYear'],{}).setdefault(p['metric'],set()).add(figure)
        full=[y for y,v in years.items() if len(v)==2 and max(v['revenue'])>0]
        if not full:raise ValueError('No full-year consolidated revenue and operating profit are stored for this company, so there is no base to compare with')
        year=max(full)
        # Stored versions that disagree are all kept as rows; the last one read was then taken as the base without a word.
        unsettled=[metric for metric,figures in years[year].items() if len(figures)>1]
        if unsettled:raise ValueError(f'The stored versions of {year} full-year {" and ".join(sorted(unsettled))} disagree, so there is no single base to compare with. '
            'Read them with get_financial_observations and say which version you use; no estimate is made from an unsettled base.')
        (revenue,),(profit,)=years[year]['revenue'],years[year]['operating_income']
        stored_margin=profit/revenue
        margins=[Decimal(str(m)) if m is not None else stored_margin for m in (margin_low,margin_high)]
        if not margins[0]<=margins[1]:raise ValueError('margin_low must not exceed margin_high')
        yearly=(value/high,value/low)                                   # recognised evenly: a longer period means less per year
        corners=[y*m for y in yearly for m in margins]                  # a loss-making margin puts the long period at the high end
        earned=(min(corners),max(corners))
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
        # Width against the larger end: a ratio of the ends called a margin of 0% to 20% "not varied".
        widest=max(abs(margins[0]),abs(margins[1]))
        swing={'recognition_years':(high-low)/high,'operating_margin':(margins[1]-margins[0])/widest if widest else Decimal(0)}
        # The tool scaled a five-year tally of past awards that an answer took for a September contract. What the
        # reports say the stage is comes first, so an unsigned or unstaged amount is read as that.
        stages=sorted({r['event']['properties'].get('lifecycleStage') for r in reports if r['event']['properties'].get('lifecycleStage')})
        item={'contract_stage':(', '.join(stages) or 'not reported')+('' if 'DEFINITIVE_SIGNED' in stages else
                ': no report of this event says the contract is signed. Read the report before using this amount, and say the estimate is for an unsigned contract.'),
            'estimate':estimate,
            'most_sensitive_assumption':max(swing,key=swing.get) if max(swing.values())>0 else 'none varied: pass a low and a high value to see the range',
            **({'fund_weight_not_found':weight_missing} if weight_missing else {}),
            **({'company_not_confirmed':unconfirmed} if unconfirmed else {}),
            **({'same_day_reports_of_other_contracts_not_used':total-len(reports)} if total>len(reports) else {}),
            **({'other_reported_amounts':[won_text(v) for v in other_values]} if other_values else {}),
            'confirmed_inputs':{'contract_value':won_text(value/share),'reports_of_this_event':len(reports),'last_full_year':year,'revenue':won_text(revenue),'operating_profit':won_text(profit),
                'operating_margin':percent(stored_margin),**({'holding_weight':percent(holding_weight)} if holding_weight is not None and not supplied_weight else {})},
            'assumptions':{'company_share':percent(share)+(' (the whole contract; pass company_share when it is shared with another company)' if company_share is None else ' (supplied by the caller)'),
                'recognition_years':span((low,high),lambda y:f'{y:.1f}')+(' (stored contract period)' if years_low is None and years_high is None else ' (supplied by the caller)'),
                'operating_margin':span(tuple(margins),percent)+(' (the company\'s last full-year margin)' if margin_low is None and margin_high is None else ' (supplied by the caller)'),
                'recognition':'even over the period; the first year and the month ahead recognise less than a full year',
                **({'holding_weight':percent(Decimal(str(holding_weight)))+' (supplied by the caller, not read from the fund)'} if supplied_weight else {})},
            'reading':'An estimate from the stated assumptions, not reported or forecast earnings. Say it as an estimate with its assumptions, and say which assumption would change it most.'}
        return self.tools.result([item],limit=1,scope={'dataset_kind':'contract_impact_estimate','event_ref':event_ref,'company_ref':company_ref})

    def observations(self,targets,kind,fiscal_years=None,metrics=None,fs_basis=None,limit=20):
        selection=self.tools.select(targets);actors,mapping=self.tools.actors(selection)
        companies=[a for a in actors if a['object_type']=='Company']
        # Financial figures hang on companies. A fund named as an object, or a share with no stored issuer, leads to
        # none, and the request then came back as a company with no figures.
        if not companies:
            raise ValueError('None of the targets is a company or a share with a stored issuing company, so no financial figure can be looked up for them. '
                'For a fund, pass targets {"kind":"etf_holdings",...} to look at its constituents. This is a wrong argument, not missing data.')
        if kind=='actual':
            conditions=[];params={}
            for key,value in [('fiscalYear',fiscal_years),('metric',metrics),('fsBasis',fs_basis)]:
                if value:
                    conditions.append('n.'+key+(' IN $' if isinstance(value,list) else '=$')+key);params[key]=value
            rows=self.graph.linked(companies,'FinancialMetric_ForCompany_Company','reverse',where=' AND '.join(conditions) or None,parameters=params)
            asked={key:wanted if isinstance(wanted,list) else [wanted] for key,wanted in params.items()}
            # Beside a name that has rows, a wrong name was dropped without a word: revenue came back and operatingIncome was not missed.
            if any(v not in {row['target']['properties'].get(key) for row in rows} for key,wanted in asked.items() for v in wanted):
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
            items=[{**({'value_text':won_text(row['target']['properties']['value'])} if row['target']['properties'].get('unit')=='KRW'
                    and number(row['target']['properties'].get('value')) is not None else {}),
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
        scope={'dataset_kind':'macro_observations','start_date':start_date,'end_date':end_date}
        present={item['properties'].get('seriesId') for item in items}
        if (not items and query) or any(s not in present for s in series_ids or []):
            # Series are named in English ("USD/KRW exchange rate"), so a Korean word matched nothing and read as no data.
            stored=self.series()[1]   # every stored series: the response shows one page of them, and a series past it was called a wrong argument
            known={row['series_id'] for row in stored['items']}
            named=[row for row in stored['items'] if query and not items and (query in row['series_id'] or query in (row['name'] or ''))]
            if (query and not items and not named) or any(s not in known for s in series_ids or []):
                raise ValueError('No macro series matches '+repr(query or series_ids)+'. Stored series: '
                    +'; '.join(f"{row['series_id']} ({row['name']}, {row['first_date']}..{row['last_date']})" for row in stored['items'])
                    +'. This is a wrong argument, not missing data; repeat with series_ids from this list.')
            # The series is stored and the range holds none of it: a weekend, or days before the series begins.
            asked=[row for row in stored['items'] if row in named or (row['series_id'] in (series_ids or []) and row['series_id'] not in present)]
            scope['no_observation_in_range']=('These series are stored but have no observation between the two dates: '
                +'; '.join(f"{row['series_id']} ({row['first_date']}..{row['last_date']})" for row in asked))
        items=one_per_figure(items,('seriesId','observationDate'),'availableAt')
        return self.tools.result(items,scope=scope,limit=limit,table='ids')

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
        x,y=number(a['value']),number(b['value'])
        if x is None or y is None:raise ValueError('Comparison values must be finite numbers')
        return x,y

    def compare(self,dataset_ref,earlier_id,later_id):
        a,data=self.observation(dataset_ref,earlier_id);b,_=self.observation(dataset_ref,later_id)
        if a['object_type']!=b['object_type']:raise ValueError('Observation types differ')
        if a['object_type']=='FinancialMetric':
            # How a figure was arrived at is not a dimension of it: asking for the same derivation refused every
            # change from a reported third quarter to the fourth quarter, which is stored as full year minus nine months.
            keys=['corpCode','metric','unit','fsBasis','periodKind']
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
            **({'derivations':[a['properties'].get('derivation'),b['properties'].get('derivation')]}
                if a['properties'].get('derivation')!=b['properties'].get('derivation') else {}),
            'unit':a['properties']['unit']}],selection=data.get('selection'),scope={'dataset_kind':'observation_comparison','input_ref':dataset_ref})

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
