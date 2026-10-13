"""Batch discovery of document context and security classifications through graph links."""
from decimal import Decimal
from edge_analysis_v2.tools.graph.provider import SELECTOR,STRING,LIMIT,RESULT_REF
from edge_analysis_v2.tools.graph.shape import number,won_text

FACTORS={'industry':'SecurityIndustryClassification','exchange_classification':'ExchangeSecurityClassification',
         'listing':'SecurityListingSnapshot','market_capitalization':'MarketCapitalization'}


class ContextTools:
    def __init__(self,tools):
        self.tools=tools;self.graph=tools.graph
        tools.register('get_security_classifications',{'targets':SELECTOR,'factors':{'type':'array','items':{'enum':list(FACTORS)},'minItems':1},'limit':LIMIT},['targets','factors'],self.factors,sources=['SecurityIndustryClassification', 'ExchangeSecurityClassification', 'SecurityListingSnapshot', 'MarketCapitalization'])
        tools.register('get_instrument_factors',{'targets':SELECTOR,'start_date':STRING,'end_date':STRING,
             'factors':{'type':'array','items':{'enum':['chart','flow','valuation','macro']},'minItems':1,'uniqueItems':True},'limit':LIMIT},
            ['targets','start_date','end_date','factors'],self.instrument_factors,sources=['DailyBar', 'DailyInvestorFlow', 'FinancialMetric', 'MacroObservation'])
        tools.register('compare_etf_overlap',{'left_ref':RESULT_REF,'right_ref':RESULT_REF,'group_by':{'enum':['security','company']}},['left_ref','right_ref','group_by'],self.exposure,sources=['get_etf_holdings로 저장한 두 구성', 'Company'])
        tools.register('sum_investor_flows',{'dataset_ref':RESULT_REF,'field':{'enum':['netQtyIndividual','netValIndividual','netQtyForeign','netValForeign','netQtyInstitutionTotal','netValInstitutionTotal']}},
            ['dataset_ref','field'],self.flow_totals,sources=['get_flow_observations로 저장한 수급'])
        tools.register('get_previous_reports',{'etf_ref':{'type':'object','properties':{'object_type':{'const':'ETF'},'object_id':STRING},'required':['object_type','object_id'],'additionalProperties':False},
             'before':STRING,'kind':{'enum':['outlook','movement','both']},'limit':LIMIT},['etf_ref','before'],self.previous,sources=['ETFOutlookReport', 'ETFPriceExplanation'])

    def instrument_factors(self,targets,start_date,end_date,factors,limit=20):
        selection=self.tools.select(targets);items=[];scopes={}
        for factor in factors:
            if factor=='chart':result,dataset=self.tools.market.prices(targets,start_date,end_date)
            elif factor=='flow':result,dataset=self.tools.market.flows(targets,start_date,end_date,'daily')
            elif factor=='valuation':
                result,dataset=self.tools.financial.observations(targets,'actual',metrics=['eps_basic','bps','bps_total_shares'],limit=limit)
            else:result,dataset=self.tools.financial.macro(start_date,end_date,limit=limit)
            scopes[factor]=result['data_scope']
            items.extend({'factor':factor,'observation':row} for row in dataset['items'])
        # 709 gathered observations came back as whole objects, 623 characters each, and 64 were read.
        return self.tools.result(items,selection=selection,scope={'dataset_kind':'factor_inputs','factor_scopes':scopes,
            'derived_factor_scores':'not calculated; this tool gathers inputs','shared_macro':'one query, not repeated per security'},limit=limit,
            display_items=[{**i['observation'],'factor':i['factor']} for i in items],table=True)

    def previous(self,etf_ref,before,kind='both',limit=20):
        from datetime import datetime
        stamp=datetime.fromisoformat(before.replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('before must include a timezone')
        # Nothing after the cutoff is readable, so a later bound means the cutoff; refusing it cost a retry each time.
        cutoff=datetime.fromisoformat(self.tools.store.cutoff)
        if stamp>cutoff:stamp=cutoff;before=self.tools.store.cutoff
        selection=self.graph.get([etf_ref]);items=[]
        kinds=['ETFOutlookReport','ETFPriceExplanation'] if kind=='both' else [{'outlook':'ETFOutlookReport','movement':'ETFPriceExplanation'}[kind]]
        for report_type in kinds:
            for row in self.graph.linked([etf_ref],report_type+'_ForETF_ETF','reverse',
                    where='n.analysisAt<datetime($before) AND n.availableAt<datetime($before)',parameters={'before':before}):
                items.append(row['target'])
        items.sort(key=lambda o:(o['properties'].get('analysisAt',''),o['object_id']),reverse=True)
        return self.tools.result(items,selection=selection,limit=limit,scope={'dataset_kind':'previous_analyses',
            'historical_evidence_resolution':'unavailable','interpretation':'Previous model interpretations, not observed facts','before':before},table='ids')

    def factors(self,targets,factors,limit=20):
        selection=self.tools.select(targets);output=[]
        for kind in ('Equity','ETF'):
            refs=[{k:r[k] for k in ('object_type','object_id')} for r in selection['items'] if r['status']=='resolved' and r['object_type']==kind]
            for factor in factors:
                object_type=FACTORS[factor];relation=object_type+'_ForSecurity_'+kind
                if relation not in self.graph.links:
                    # Some classification objects expose a differently named declared link.
                    matches=[r['id'] for r in self.graph.links.values() if r['source']==object_type and r['target']==kind]
                    if len(matches)!=1:raise ValueError('No unique declared factor path: '+factor+' / '+kind)
                    relation=matches[0]
                for row in self.graph.linked(refs,relation,'reverse'):
                    output.append({'security_ref':row['source'],'factor':factor,'observation':row['target']})
        if set(factors)<={'industry','exchange_classification'}:
            # A 201-holding fund came back as 399 rows of 19 columns, 10 of them empty in every row: 44,000 characters,
            # two pages. Both runs wrote the sector split from the first page. One row per security holds both
            # classifications, and every security is shown.
            merged={}
            for o in output:merged.setdefault(o['security_ref']['object_id'],{'security_ref':o['security_ref'],'observations':[]})['observations'].append(o)
            weight={i['object_id']:i.get('weight_ratio') for i in selection['items']}
            def row(item):
                found={o['factor']:o['observation']['properties'] for o in item['observations']}
                first=item['observations'][0]['observation']['properties'];a=found.get('industry',{});b=found.get('exchange_classification',{})
                return {'security':first.get('securityName'),'ticker':first.get('securityTicker'),
                    **({'weight_percent':round(weight[item['security_ref']['object_id']]*100,2)} if weight.get(item['security_ref']['object_id']) is not None else {}),
                    'sector':a.get('sectorName'),'industry':a.get('industryName'),'exchange_large':b.get('largeName'),'exchange_medium':b.get('mediumName')}
            items=list(merged.values())
            return self.tools.result(items,selection=selection,limit=max(limit,len(items)),display_items=[row(i) for i in items],table=True,
                scope={'dataset_kind':'security_classifications','requested_factors':factors,
                    'columns_mean':'weight_percent is the stored weight in the fund. sector and industry are the FMP classification; exchange_large and exchange_medium are the exchange (KIS) classification. '
                        'Name a security\'s sector from these columns, not from what the company is known for. null means no stored classification.',
                    'securities_without_any_classification':len([i for i in selection['items'] if i['status']=='resolved'])-len(items),
                    'classification_dates':'snapshot/collection dates are not certified historical effective dates'})
        # A fund's 198 classifications came back 903 characters each, and the answer was written from the first 45.
        return self.tools.result(output,selection=selection,scope={'dataset_kind':'instrument_factors','requested_factors':factors,
            'classification_dates':'snapshot/collection dates are not certified historical effective dates'},limit=limit,
            display_items=[{**o['observation'],'factor':o['factor']} for o in output],table=True)

    def exposure(self,left_ref,right_ref,group_by):
        left=self.tools.store.reference(left_ref,'selection');right=self.tools.store.reference(right_ref,'selection')
        for selection in (left,right):
            if 'etf_ref' not in selection:raise ValueError('ETF holdings selections required')
        if left.get('selected_date')!=right.get('selected_date') or not left.get('selected_date'):
            raise ValueError('Both holdings selections must have the same actual date')
        def group(selection):
            weights={};unknown=[]
            issuer_map={}
            if group_by=='company':
                _,mapping=self.tools.actors(selection)
                issuer_map={m['security']['object_id']:m['actor']['object_id'] for m in mapping}
            for item in selection['items']:
                if group_by=='security':key=(item['object_type'],item['object_id'])
                else:
                    key=('Company',issuer_map.get(item['object_id'])) if item['object_type']=='Equity' else (None,None)
                weight=number(item.get('weight_ratio'))
                if item['status']!='resolved' or key[1] is None or weight is None or weight<0:
                    unknown.append(item['object_id']);continue
                weights[key]=weights.get(key,Decimal(0))+weight
                held=item.get('object') or {}
                names[key]=held.get('title') if group_by=='security' else held.get('properties',{}).get('issuerName')
            return weights,unknown
        names={};a,ua=group(left);b,ub=group(right)
        # Rows came as bare identifiers in identifier order: the few shared holdings were buried among two hundred
        # and could not be named without another lookup. Shared holdings lead, each with its name.
        rows=[{'name':names.get(k),'object_type':k[0],'object_id':k[1],'left_weight':str(a.get(k,0)),'right_weight':str(b.get(k,0)),
            'overlap_weight':str(min(a.get(k,Decimal(0)),b.get(k,Decimal(0))))} for k in a.keys()|b.keys()]
        rows.sort(key=lambda r:(-Decimal(r['overlap_weight']),-Decimal(r['left_weight'])-Decimal(r['right_weight']),r['object_id']))
        result,dataset=self.tools.result(rows,scope={'dataset_kind':'exposure_comparison','group_by':group_by,
            'date':left['selected_date'],'input_refs':[left_ref,right_ref],'unresolved_left':ua,'unresolved_right':ub,
            'overlap_weight':str(sum((min(a.get(k,Decimal(0)),b.get(k,Decimal(0))) for k in a.keys()|b.keys()),Decimal(0))),
            'shared_count':sum(1 for k in a.keys()&b.keys()),'left_count':len(a),'right_count':len(b),
            'interpretation':'raw weight overlap; incomplete holdings cannot establish full fund diversification'},table=True)
        return result,dataset

    def flow_totals(self,dataset_ref,field):
        data=self.tools.store.reference(dataset_ref,'dataset')
        if data['scope'].get('dataset_kind')!='flow_observations' or data['scope'].get('frequency')!='daily':
            raise ValueError('Only daily finalized flow datasets may be summed')
        rows=[]
        for member in data['selection']['items']:
            observations=[o for o in data['items'] if o['properties'].get('instrumentId')==member['object_id']]
            values=[v for v in (number(o['properties'].get(field)) for o in observations) if v is not None]
            currency=(member.get('object') or {}).get('properties',{}).get('currencyCode')
            unit='shares_or_units' if field.startswith('netQty') else currency
            dates=sorted(o['properties']['tradeDate'] for o in observations if o['properties'].get('tradeDate'))
            total=sum(values,Decimal(0)) if values and unit else None
            rows.append({'object_id':member['object_id'],'field':field,'value':str(total) if total is not None else None,
                'requested_start_date':data['scope'].get('start_date'),'requested_end_date':data['scope'].get('end_date'),
                'first_observation_date':dates[0] if dates else None,'last_observation_date':dates[-1] if dates else None,
                'value_text':won_text(total) if unit=='KRW' else None,
                'unit':unit,'observed_rows':len(observations),'missing_values':len(observations)-len(values),
                'status':'available_observations_only' if values and unit else 'unavailable',
                'input_ids':[o['object_id'] for o in observations]})
        return self.tools.result(rows,selection=data['selection'],scope={'dataset_kind':'flow_totals','input_ref':dataset_ref,
            'date_completeness':'Trading-calendar completeness is not certified; sums cover available observations only'},table=True)
