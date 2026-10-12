import unittest
from types import SimpleNamespace
from datetime import date
from edge_analysis_v2.tools.graph.events import EventTools,feature_status


def measurement(value,**extra):
    return {'object_id':'measurement','properties':{'metricCode':'CONTRACT_VALUE','value':value,'unit':'KRW','periodBasis':'TOTAL',
        'valueSource':'PARSED','parseStatus':'ok','argumentGroup':1,**extra}}


CONDITION={'metric_code':'CONTRACT_VALUE','operator':'gte','value':'100','unit':'KRW','period_basis':'TOTAL'}


class EventFeatureTests(unittest.TestCase):
    def test_reported_date_is_not_advertised_as_verified_execution_date(self):
        schemas=[]
        EventTools(SimpleNamespace(graph=None,register=lambda *args,**kw:schemas.append(args)))
        date_input=schemas[1][1]['time_field']
        self.assertNotIn('occurred',date_input['enum'])
        event={'object_type':'SourceEvent','object_id':'e','title':'September filing',
            'properties':{'eventDate':'2026-09-23','lifecycleStage':'DEFINITIVE_SIGNED'}}
        shown=EventTools.display({'event':event,'participants':[],'features':[],'documents':[]})
        self.assertEqual(shown['reported_date'],'2026-09-23')
        self.assertNotIn('event_date',shown)   # the name read as the date the event happened
        self.assertIn('not the date a contract was signed',EventTools.SCOPE['reported_date'])
        self.assertIn('not a verified current',EventTools.SCOPE['stage'])

    def test_missing_invalid_units_and_approximate_values_are_unknown_not_zero(self):
        for rows in ([],[measurement(None)],[measurement('120',unit='USD')],
                     [measurement('120',periodBasis='UNKNOWN')],[measurement('120',parseStatus='approx_or_range')]):
            self.assertEqual(feature_status(rows,CONDITION),'unknown')

    def test_conflicting_values_are_not_resolved_by_repetition(self):
        self.assertEqual(feature_status([measurement('120')]*5+[measurement('80')],CONDITION),'unknown')
        self.assertEqual(feature_status([measurement('100')],CONDITION),'matched')
        self.assertEqual(feature_status([measurement('99.999999999999999999')],CONDITION),'not_matched')

    def test_role_in_one_group_and_amount_in_another_cannot_satisfy_combined_question(self):
        actor={'object_type':'Company','object_id':'a','title':'Company A'}
        event={'object_type':'SourceEvent','object_id':'e','title':'Event','properties':{}}
        graph=SimpleNamespace(query=lambda *a,**kw:[{'code':'CONTRACT_VALUE'}],
            linked=lambda refs,*a,**kw:[{'target':event}] if refs else [])
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,
            select=lambda _: {'items':[]},actors=lambda _:([{k:actor[k] for k in ('object_type','object_id')}],[]),
            result=lambda items,**kw:(items,kw))
        tool=EventTools(owner)
        tool.enrich=lambda _: [{'event':event,'participants':[{'actor':actor,'role':'SUPPLIER','argument_group':1}],
            'features':[measurement('200',argumentGroup=2)],'documents':[]}]
        result,_=tool.search({},'2026-10-01','2026-10-05','reported_event_date',
            participant_conditions=[{'role_codes':['SUPPLIER']}],feature_filters=[CONDITION])
        self.assertEqual(result[0]['match_status'],'unknown')

    def test_company_scope_does_not_accept_an_equity_as_participant_identity(self):
        actor={'object_type':'Company','object_id':'a'}
        event={'object_type':'SourceEvent','object_id':'e','properties':{}}
        graph=SimpleNamespace(query=lambda *a,**kw:[],linked=lambda refs,*a,**kw:[{'target':event}] if refs else [])
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,
            select=lambda _: {},actors=lambda _:([actor],[]),result=lambda *a,**kw:None)
        tool=EventTools(owner);tool.enrich=lambda _:[{'event':event,'participants':[],'features':[],'documents':[]}]
        with self.assertRaisesRegex(ValueError,'Actor'):
            tool.search({},'2026-10-01','2026-10-05','reported_event_date',participant_conditions=[{
                'role_codes':['SUPPLIER'],'actor_refs':[{'object_type':'Equity','object_id':'a'}]}])


class EventRowTests(unittest.TestCase):
    def test_what_the_source_said_besides_the_companies_is_read_from_the_title(self):
        # WHY: the customer and the effective date of a real contract existed only in the title string, so answers
        # named no customer and gave the disclosure date (09-23) as the contract date (08-19).
        from edge_analysis_v2.tools.graph.events import arguments
        title=('COMPANY.CONTRACT.SIGNING / 현대로템 [SUPPLIER]; 대만 타오위안 브라운라인 E&M 공급 사업 [CONTRACT_OBJECT]; '
               '타오위안시정부 철도공정국 (Department of Rapid Transit Systems, Taoyuan) [CUSTOMER]; 2026-08-19 [EFFECTIVE_DATE] / 2026-09-23')
        found={a['role']:a['text'] for a in arguments(title)}
        self.assertEqual(found['EFFECTIVE_DATE'],'2026-08-19');self.assertIn('철도공정국',found['CUSTOMER'])
        self.assertEqual(arguments('COMPANY.CONTRACT.SIGNING / 현대로템 [SUPPLIER] / 2026-09-27'),[{'role':'SUPPLIER','text':'현대로템'}])
        self.assertEqual(arguments('no structure'),[])
        event={'object_id':'e','title':title,'properties':{'eventDate':'2026-09-23'}}
        amount={'object_id':'m','properties':{'metricCode':'CONTRACT_VALUE','value':'344500969560.00000000','unit':'KRW','periodBasis':'TOTAL'}}
        days={'object_id':'d','properties':{'metricCode':'CONTRACT_DURATION','value':'2968.00000000','unit':'DAY'}}
        shown=EventTools.display({'event':event,'participants':[],'features':[amount,days],'documents':[]})
        self.assertEqual(shown['features'][0]['value_text'],'3,445억 96만 9,560원');self.assertNotIn('value_text',shown['features'][1])
        self.assertEqual(shown['arguments'][-1],{'role':'EFFECTIVE_DATE','text':'2026-08-19'})

    def test_an_event_type_that_is_not_stored_is_a_wrong_argument_not_an_absence(self):
        # WHY: CONTRACT.SIGNED returned 0 rows while 21 COMPANY.CONTRACT.SIGNING events existed.
        graph=SimpleNamespace(query=lambda *a,**kw:[{'code':'COMPANY.CONTRACT.SIGNING'},{'code':'COMPANY.PRODUCT.LAUNCH'}],linked=lambda *a,**kw:[])
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,select=lambda _:{'items':[]},
            actors=lambda _:([],[]),result=lambda items,**kw:(items,kw))
        tool=EventTools(owner)
        with self.assertRaisesRegex(ValueError,'wrong argument.*repeat with a stored type'):
            tool.search({},'2026-09-01','2026-09-30','reported_event_date',event_types=['CONTRACT.SIGNED'])
        items,_=tool.search({},'2026-09-01','2026-09-30','reported_event_date',event_types=['COMPANY.CONTRACT.SIGNING'])
        self.assertEqual(items,[])

    def test_a_search_says_what_it_looked_through_so_nothing_found_can_be_stated(self):
        # WHY: asked whether a company issued shares, runs found no such event, never learned a share-issue type
        # exists, and searched news and filings by keyword 6 to 11 times to make sure.
        launch={'object_id':'e1','title':'COMPANY.PRODUCT.LAUNCH / A [ISSUER] / 2026-09-02','properties':{'eventType':'COMPANY.PRODUCT.LAUNCH','eventDate':'2026-09-02'}}
        def linked(refs,link,direction='forward',where=None,parameters=None):
            if not refs or 'ParticipatesIn' not in link or direction!='forward':return []
            return [] if where and 'eventType IN' in where else [{'target':launch}]
        graph=SimpleNamespace(query=lambda *a,**kw:[{'code':c} for c in ('COMPANY.CAPITAL.EQUITY_ISSUANCE','COMPANY.PRODUCT.LAUNCH')],linked=linked)
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,select=lambda _:{'items':[]},
            actors=lambda _:([{'object_type':'Company','object_id':'c'}],[]),result=lambda items,**kw:(items,kw))
        tool=EventTools(owner)
        _,broad=tool.search({},'2026-09-01','2026-09-30','reported_event_date')
        self.assertEqual(broad['scope']['coverage']['stored_types_with_no_event_here'],['COMPANY.CAPITAL.EQUITY_ISSUANCE'])
        items,narrow=tool.search({},'2026-09-01','2026-09-30','reported_event_date',event_types=['COMPANY.CAPITAL.EQUITY_ISSUANCE'])
        self.assertEqual(items,[])
        self.assertEqual({k:narrow['scope']['coverage'][k] for k in ('events_of_these_companies_in_this_period','of_the_requested_types')},
            {'events_of_these_companies_in_this_period':1,'of_the_requested_types':0})

    def test_reports_of_one_event_come_back_as_one_row_with_what_they_disagree_on(self):
        # WHY: one contract reported by 36 articles came back as 36 rows; a company's month passed 100 rows and
        # one run split September into single days with 30 searches to read it.
        actor={'object_type':'Company','object_id':'a','title':'한화오션'}
        def report(n,customer,value,date_='2026-09-03'):
            title=f'COMPANY.CONTRACT.SIGNING / 한화오션 [SUPPLIER]; {customer} [CUSTOMER] / {date_}'
            return {'event':{'object_type':'SourceEvent','object_id':f'e{n}','title':title,'properties':{'eventType':'COMPANY.CONTRACT.SIGNING','eventDate':date_,'availableAt':f'2026-09-03T0{n}:00'}},
                'participants':[{'actor':actor,'role':'SUPPLIER','argument_group':None}],
                'features':[{'object_id':f'm{n}','properties':{'metricCode':'CONTRACT_VALUE','value':value,'unit':'KRW','periodBasis':'TOTAL'}}] if value else [],
                'documents':[{'document':{'object_type':'NewsArticle','object_id':f'd{n}','title':f'기사 {n}','properties':{'leadText':'본문'}},'link_properties':{}}]}
        reports=[report(1,'양밍해운','1552700000000'),report(2,'대만 양밍해운','1552700000000'),report(3,'양밍해운',None),
                 report(4,'양밍해운','1500000000000'),report(5,'양밍해운','1552700000000','2026-09-04')]
        graph=SimpleNamespace(query=lambda *a,**kw:[{'code':'COMPANY.CONTRACT.SIGNING'}],
            linked=lambda refs,*a,**kw:[{'target':r['event']} for r in reports] if refs else [])
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,select=lambda _:{'items':[]},
            actors=lambda _:([{'object_type':'Company','object_id':'a'}],[]),result=lambda items,**kw:(items,kw))
        tool=EventTools(owner);tool.enrich=lambda _:[dict(r) for r in reports]
        rows,sent=tool.search({},'2026-09-01','2026-09-30','reported_event_date')
        self.assertEqual([len(r['reports']) for r in rows],[4,1]);self.assertEqual(sent['scope']['stored_reports'],5)
        row=sent['display_items'][0]
        self.assertEqual((row['reports'],row['reported_date'],row['first_available_at']),(4,'2026-09-03','2026-09-03T01:00'))
        self.assertNotIn('stages',row)   # nothing to say is not said
        self.assertEqual([(a['value_text'],a['reports']) for a in row['amounts']],[('1조 5,527억원',2),('1조 5,000억원',1)])
        self.assertEqual(row['arguments']['CUSTOMER'],[{'text':'양밍해운','reports':3},{'text':'대만 양밍해운','reports':1}])
        self.assertEqual(len(row['documents']),1);self.assertEqual(len(row['participants']),1)
        self.assertEqual([sorted(d) for d in row['documents']],[['document_ref','title']])
        self.assertNotIn('value',row['amounts'][0]);self.assertNotIn('SUPPLIER',row['arguments'])   # said once, as written

    def test_an_event_is_found_by_words_alone_and_its_reports_are_joined_by_the_stored_thread(self):
        # WHY: an event could be found only by company and period, and reports were joined by date although the data
        # already says which reports are one event.
        def report(n,title,day):
            return {'event':{'object_type':'SourceEvent','object_id':f'e{n}','title':title,'properties':{'eventType':'COMPANY.CONTRACT.SIGNING','eventDate':day}},
                'participants':[],'features':[],'documents':[{'document':{'object_type':'NewsArticle','object_id':f'd{n}','title':'기사','properties':{}},'link_properties':{}}]}
        reports=[report(1,'COMPANY.CONTRACT.SIGNING / A [SUPPLIER]; 크로아티아 천무 [CONTRACT_OBJECT] / 2026-09-04','2026-09-04'),
                 report(2,'COMPANY.CONTRACT.SIGNING / A [SUPPLIER]; 크로아티아 천무 [CONTRACT_OBJECT] / 2026-09-11','2026-09-11'),
                 report(3,'COMPANY.CONTRACT.SIGNING / A [SUPPLIER]; 폴란드 K9 [CONTRACT_OBJECT] / 2026-09-11','2026-09-11')]
        asked=[]
        def nodes(kind,query='',**kw):
            asked.append(query);return [r['event'] for r in reports]
        thread={'object_type':'EventThread','object_id':'t1','properties':{}}
        linked=lambda refs,link,*a,**kw:[{'source':{'object_id':'e1'},'target':thread,'link_properties':{'noveltyStatus':'FIRST_IN_THREAD'}},{'source':{'object_id':'e2'},'target':thread,'link_properties':{'noveltyStatus':'DUPLICATE_REBROADCAST'}}] if 'EventThread' in link else []
        graph=SimpleNamespace(query=lambda *a,**kw:[{'code':'COMPANY.CONTRACT.SIGNING'}],nodes=nodes,linked=linked)
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,result=lambda items,**kw:(items,kw))
        tool=EventTools(owner);tool.enrich=lambda found:[r for r in reports if r['event'] in found]
        rows,sent=tool.search(start_date='2026-09-01',end_date='2026-09-30',query='크로아티아 천무')
        self.assertEqual(asked,['크로아티아'])            # the graph is asked for the longest word; the rest are checked on what comes back
        self.assertEqual([len(r['reports']) for r in rows],[2])   # 폴란드 does not carry both words; the two reports share a thread
        row=sent['display_items'][0]
        self.assertEqual((row['grouped_by'],row['reported_date'],row['last_reported_date']),('stored thread','2026-09-04','2026-09-11'))
        self.assertEqual(row['reports_by_what_they_added'],{'FIRST_IN_THREAD':1,'DUPLICATE_REBROADCAST':1})
        with self.assertRaisesRegex(ValueError,'event_types, query, or both'):tool.search(start_date='2026-09-01',end_date='2026-09-30')

    def test_a_timeline_lists_the_reports_that_changed_the_event_and_counts_the_repeats(self):
        # WHY: one announcement and its rebroadcasts came back as one row each, all saying the same thing.
        def event(n,stage,at):
            return {'object_type':'SourceEvent','object_id':f'e{n}','title':'COMPANY.CONTRACT.SIGNING / A [SUPPLIER] / 2026-09-04',
                'properties':{'eventType':'COMPANY.CONTRACT.SIGNING','eventDate':'2026-09-04','lifecycleStage':stage,'availableAt':at}}
        stored={'e1':event(1,'ANNOUNCED','2026-09-04T01:00'),'e2':event(2,'ANNOUNCED','2026-09-04T02:00'),'e3':event(3,'ANNOUNCED','2026-09-04T03:00'),
                'e4':event(4,'DEFINITIVE_SIGNED','2026-09-11T01:00')}
        status={'e1':'FIRST_IN_THREAD','e2':'DUPLICATE_REBROADCAST','e3':'DUPLICATE_REBROADCAST','e4':'FOLLOW_UP_STAGE'}
        thread={'object_type':'EventThread','object_id':'t1','properties':{}}
        def linked(refs,link,direction='forward',**kw):
            if 'EventThread' not in link:return []
            if direction=='reverse':return [{'source':thread,'target':stored[i],'link_properties':{'noveltyStatus':status[i]}} for i in stored]
            return [{'source':r,'target':thread,'link_properties':{}} for r in refs]
        graph=SimpleNamespace(get=lambda refs:{'items':[{'object_id':r['object_id'],'status':'resolved','object':stored[r['object_id']]} for r in refs]},linked=linked)
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,result=lambda items,**kw:(items,kw))
        items,sent=EventTools(owner).history([{'object_type':'SourceEvent','object_id':'e3'}])
        self.assertEqual([(r['added'],r['stage'],r['repeated_by']) for r in sent['display_items']],
            [('FIRST_IN_THREAD','ANNOUNCED',2),('FOLLOW_UP_STAGE','DEFINITIVE_SIGNED',0)])

    def test_events_ordered_by_amount_put_the_largest_first(self):
        # WHY: rows were in date order; an answer read 44 of 66 and called a 6,400억 contract the largest while larger ones sat on the next page.
        def report(n,value):
            return {'event':{'object_type':'SourceEvent','object_id':f'e{n}','title':f'COMPANY.CONTRACT.SIGNING / A{n} [SUPPLIER] / 2026-09-{n:02d}',
                    'properties':{'eventType':'COMPANY.CONTRACT.SIGNING','eventDate':f'2026-09-{n:02d}'}},'participants':[],'documents':[],
                'features':[{'object_id':f'm{n}','properties':{'metricCode':'CONTRACT_VALUE','value':str(value),'unit':'KRW','periodBasis':'UNKNOWN'}}]}
        reports=[report(n,n*10**11) for n in range(1,8)]
        graph=SimpleNamespace(query=lambda *a,**kw:[{'code':'COMPANY.CONTRACT.SIGNING'}],nodes=lambda *a,**kw:[r['event'] for r in reports],linked=lambda *a,**kw:[])
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,result=lambda items,**kw:(items,kw))
        tool=EventTools(owner);tool.enrich=lambda found:[r for r in reports if r['event'] in found]
        rows,sent=tool.search(start_date='2026-09-01',end_date='2026-09-30',query='COMPANY',order_by='amount')
        self.assertEqual(sent['display_items'][0]['reported_date'],'2026-09-07')
        self.assertEqual(sent['display_items'][0]['amounts'][0]['periodBasis'],'not stated')

    def test_the_scan_is_one_line_per_event_shows_every_event_and_a_precedent_needs_no_company(self):
        # WHY: a row carried companies, amounts, wordings and a document (900 characters); a fund's month broke into
        # pages and answers were written from the first. A precedent could not be looked for without naming a company.
        def report(n):
            return {'event':{'object_type':'SourceEvent','object_id':f'e{n:03d}','title':f'COMPANY.CONTRACT.SIGNING / A [SUPPLIER]; 천무 {n} [CONTRACT_OBJECT] / 2026-09-01',
                    'properties':{'eventType':'COMPANY.CONTRACT.SIGNING','eventDate':'2026-09-01','lifecycleStage':'DEFINITIVE_SIGNED'}},
                'participants':[{'actor':{'object_type':'Company','object_id':f'c{n}','title':f'회사{n:03d}'},'role':'SUPPLIER','argument_group':0}],'documents':[],'features':[]}
        reports=[report(n) for n in range(150)]
        asked=[]
        def nodes(kind,query='',**kw):
            asked.append(query);return [r['event'] for r in reports]
        graph=SimpleNamespace(query=lambda *a,**kw:[{'code':'COMPANY.CONTRACT.SIGNING'}],nodes=nodes,linked=lambda *a,**kw:[r['event'] and {'target':r['event']} for r in reports])
        seen={}
        def result(items,**kw):
            seen.update(kw);return items,kw
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,result=result,store=SimpleNamespace(cutoff='2026-10-05T00:00:00+00:00'),
            select=lambda targets:{'items':[]},actors=lambda selection:([{'object_type':'Company','object_id':'c0'}],[]))
        tool=EventTools(owner);tool.enrich=lambda found:[r for r in reports if r['event'] in found]
        rows,sent=tool.listing({'kind':'objects','object_refs':[]},'2026-09-01','2026-09-30')
        self.assertEqual((len(rows),sent['table'],sent.get('search')),(150,True,None))     # all 150, as a table, not cut as a broad search
        self.assertEqual(sent['display_items'][0],{'company':'회사000','reported_date':'2026-09-01','event_type':'COMPANY.CONTRACT.SIGNING','what':'천무 0',
            'stages':'DEFINITIVE_SIGNED','reports':1,'event_id':'e000'})
        rows,sent=tool.similar(event_types=['COMPANY.CONTRACT.SIGNING'])
        self.assertEqual((asked[-1],sent['scope']['start_date'],sent['scope']['end_date'],sent['search']),('','2000-01-01','2026-10-05',True))
        with self.assertRaisesRegex(ValueError,'event_types, query, or both'):tool.similar()

    def test_an_article_is_read_in_full_from_the_id_of_a_listed_event(self):
        # WHY: the data holds an opening excerpt that stops mid-sentence, and reaching even that took three calls.
        document={'object_type':'NewsArticle','object_id':'d1','title':'천무 수출','properties':{'title':'천무 수출','publisher':'신문','publishedAt':'2026-09-04T01:00:00+00:00','sourceUri':'https://example.com/a'}}
        asked=[]
        def read(url,offset):
            asked.append((url,offset));return {'text':'본문 전체','total_chars':5,'next_offset':None}
        graph=SimpleNamespace(linked=lambda refs,link,direction:[{'target':document}] if link.startswith('NewsArticle') else [],
            get=lambda refs:{'items':[{'status':'resolved','object':document}]})
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,result=lambda items,**kw:(items,kw),web=SimpleNamespace(read=read))
        tool=EventTools(owner)
        items,sent=tool.article(event_id='e1')
        self.assertEqual((asked,items[0]['text'],items[0]['publisher'],sent['scope']['other_documents_of_this_event']),([('https://example.com/a',0)],'본문 전체','신문',0))
        self.assertEqual(tool.article(document_ref={'object_type':'NewsArticle','object_id':'d1'},offset=3)[0][0]['offset'],3)
        with self.assertRaisesRegex(ValueError,'one of them'):tool.article()
        owner.web=None
        with self.assertRaisesRegex(ValueError,'not available in this run'):tool.article(event_id='e1')

    def test_a_scan_too_large_for_one_response_counts_events_and_lists_none(self):
        # WHY: 474 events came back as 339 with two companies cut whole, and the answer spoke for all of them.
        def report(n):
            return {'event':{'object_type':'SourceEvent','object_id':f'e{n:04d}','title':'COMPANY.CONTRACT.SIGNING / A [SUPPLIER]; '+'가'*40+' [CONTRACT_OBJECT] / 2026-09-01',
                    'properties':{'eventType':'COMPANY.CONTRACT.SIGNING' if n%2 else 'COMPANY.LEGAL.LITIGATION','eventDate':'2026-09-01','lifecycleStage':'DEFINITIVE_SIGNED'}},
                'participants':[{'actor':{'object_type':'Company','object_id':f'c{n}','title':f'회사{n%3}'},'role':'SUPPLIER','argument_group':0}],'documents':[],'features':[]}
        reports=[report(n) for n in range(600)]
        graph=SimpleNamespace(query=lambda *a,**kw:[{'code':'COMPANY.CONTRACT.SIGNING'},{'code':'COMPANY.LEGAL.LITIGATION'}],linked=lambda *a,**kw:[{'target':r['event']} for r in reports])
        owner=SimpleNamespace(graph=graph,register=lambda *a,**kw:None,check_date=date.fromisoformat,result=lambda items,**kw:(items,kw),
            select=lambda targets:{'items':[]},actors=lambda selection:([{'object_type':'Company','object_id':'c0'}],[]))
        tool=EventTools(owner);tool.enrich=lambda found:[r for r in reports if r['event'] in found]
        rows,sent=tool.listing({'kind':'objects','object_refs':[]},'2026-09-01','2026-09-30')
        self.assertEqual((len(rows),sum(r['events'] for r in rows),sent['scope']['events_not_listed']),(6,600,600))
        self.assertEqual(rows[0],{'company':'회사0','event_type':'COMPANY.CONTRACT.SIGNING','events':100})

