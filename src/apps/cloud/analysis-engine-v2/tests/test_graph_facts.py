import unittest
from edge_analysis_v2.tools.graph.facts import GraphFacts


def catalog():
    return {'modelChanged':False,'objects':[{'id':t,'titleProperty':'name','columns':[
        {'property':p,'mappingStatus':'ready'} for p in ('id','name','availableAt')]} for t in ('Company','Equity')],
        'relations':[{'id':'Issues','source':'Company','target':'Equity','physicalMapping':{'kind':'object_fk'}}]}


class GraphFactsTests(unittest.TestCase):
    def test_excerpt_search_and_return_do_not_leak_later_or_undated_content(self):
        design=catalog();obj=design['objects'][0]
        obj['columns'] += [{'property':p,'mappingStatus':'ready'} for p in ('leadText','leadObservedAt')]
        calls=[]
        def run(q,p):
            calls.append((q,p))
            return [{'id':str(i),'properties':{'leadText':'later fact','leadObservedAt':stamp}}
                    for i,stamp in enumerate((None,'2026-10-06T00:00:00+00:00','2026-10-04T00:00:00+00:00'))]
        graph=GraphFacts(run,design,'2026-10-05T00:00:00+00:00')
        rows=graph.nodes('Company',query='fact')
        self.assertIn('n.leadObservedAt<=datetime($cutoff)',calls[0][0])
        self.assertEqual([r['properties']['leadText'] for r in rows],[None,None,'later fact'])

    def test_mixed_identity_and_duplicates_do_not_merge_companies_with_securities(self):
        calls=[]
        def run(q,p):
            calls.append((q,p));return [{'id':'same','properties':{'name':'Found'}}]
        graph=GraphFacts(run,catalog(),'2026-10-05T00:00:00+00:00')
        result=graph.get([{'object_type':t,'object_id':'same'} for t in ('Company','Equity','Company')])
        self.assertEqual(result['requested_count'],3)
        self.assertEqual(result['distinct_count'],2)
        self.assertEqual(len(calls),2)
        self.assertTrue(all('availableAt <= datetime($cutoff)' in q for q,_ in calls))
        self.assertTrue(all(isinstance(p['cutoff'],str) for _,p in calls))

    def test_unknown_filters_and_large_results_fail_instead_of_silently_truncating(self):
        graph=GraphFacts(lambda q,p:[{},{}],catalog(),'2026-10-05T00:00:00+00:00',max_rows=1)
        with self.assertRaises(ValueError):graph.nodes('Company',filters={'madeUp':1})
        with self.assertRaisesRegex(ValueError,'row bound'):graph.nodes('Company')

    def test_multiple_roles_survive_with_original_source_identity(self):
        rows=[{'sourceId':'company','id':'stock','properties':{},'edge':{'key_0':key}} for key in ('first','second')]
        graph=GraphFacts(lambda q,p:rows,catalog(),'2026-10-05T00:00:00+00:00')
        result=graph.linked([{'object_type':'Company','object_id':'company'}],'Issues')
        self.assertEqual(len(result),2)
        self.assertEqual({x['link_properties']['key_0'] for x in result},{'first','second'})
        with self.assertRaises(ValueError):graph.linked([{'object_type':'Equity','object_id':'stock'}],'Issues')
