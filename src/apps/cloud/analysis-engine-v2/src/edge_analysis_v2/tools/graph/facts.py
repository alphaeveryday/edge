"""Deterministic, allowlisted read queries against PuppyGraph, never a SQL fallback."""
import copy
import re
from contextlib import contextmanager
from datetime import date, datetime
from time import perf_counter


def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',value):
        raise ValueError('Invalid graph identifier')
    return '`'+value+'`'


def normalize(value):
    if isinstance(value,dict):return {k:normalize(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [normalize(v) for v in value]
    if hasattr(value,'iso_format'):return value.iso_format()
    if isinstance(value,(datetime,date)):return value.isoformat()
    return value


class GraphFacts:
    def __init__(self, run, catalog, cutoff, max_rows=10000):
        if catalog['modelChanged']:raise ValueError('Graph mapping must match the reviewed model')
        self.run=run;self.catalog=catalog;self.cutoff=cutoff;self.max_rows=max_rows
        self.objects={o['id']:o for o in catalog['objects']}
        self.links={r['id']:r for r in catalog['relations']}
        self.queries=[];self.totals={}

    def query(self, statement, parameters, *, objects=(), links=()):
        started=perf_counter()
        entry={'statement':statement,'parameters':parameters,'objects':list(objects),'links':list(links)}
        self.queries.append(entry)
        try:
            rows=normalize(self.run(statement,parameters))
            entry['row_count']=len(rows)
            if len(rows)>self.max_rows:raise ValueError('Dataset exceeds the explicit row bound; narrow dates or targets')
            return rows
        finally:entry['elapsed_ms']=round((perf_counter()-started)*1000,2)

    def properties(self, kind):
        if kind not in self.objects:raise ValueError('Unknown object type: '+kind)
        return {c['property']:c for c in self.objects[kind]['columns'] if c.get('property')}

    def total(self, kind):
        # One count per type and run: lets the agent judge coverage without probing with name variants.
        if kind not in self.totals:
            params={};clause=self.cutoff_clause(kind,'n',params)
            rows=self.query('MATCH (n:'+name(kind)+')'+(' WHERE '+clause if clause else '')+' RETURN count(n) AS total',params,objects=[kind])
            self.totals[kind]=rows[0]['total'] if rows else 0
        return self.totals[kind]

    def text_properties(self, kind):
        props=self.properties(kind)
        return [p for p in ('name','title','displayTitle','ticker','seriesName','metricCode') if p in props]

    def cutoff_clause(self, kind, alias, params):
        props=self.properties(kind)
        if 'availableAt' in props:
            # PuppyGraph accepts ISO text through datetime(); native Bolt temporal
            # parameters disconnect this deployed server rather than returning an error.
            params['cutoff']=self.cutoff
            return alias+'.availableAt <= datetime($cutoff)'
        return ''

    def nodes(self, kind, *, ids=None, filters=None, query='', where=None, parameters=None):
        props=self.properties(kind);conditions=[];params=dict(parameters or {})
        if ids is not None:
            if not ids:return []
            params['ids']=list(dict.fromkeys(ids));conditions.append('n.id IN $ids')
        if query:
            params['text']=query
            candidates=self.text_properties(kind)
            matches=['n.id=$text']+['n.'+name(p)+' CONTAINS $text' for p in candidates]
            if 'leadText' in props and 'leadObservedAt' in props:
                params['cutoff']=self.cutoff
                matches.append('(n.leadObservedAt<=datetime($cutoff) AND n.leadText CONTAINS $text)')
            conditions.append('('+' OR '.join(matches)+')')
        for i,(prop,value) in enumerate((filters or {}).items()):
            if prop not in props:raise ValueError('Unknown property: '+prop)
            if props[prop]['mappingStatus'] in ('unmapped','needsCorrection'):
                raise ValueError('Property has no verified mapping: '+prop)
            key='filter_'+str(i);params[key]=value
            conditions.append('n.'+name(prop)+(' IN $' if isinstance(value,list) else ' = $')+key)
        availability=self.cutoff_clause(kind,'n',params)
        if availability:conditions.append(availability)
        if where:conditions.append(where)
        statement='MATCH (n:'+name(kind)+')'+(' WHERE '+' AND '.join(conditions) if conditions else '')
        rows=self.query(statement+' RETURN n.id AS id,properties(n) AS properties ORDER BY n.id LIMIT '+str(self.max_rows+1),params,objects=[kind])
        return [self.object(kind,{**r['properties'],'id':r['id']}) for r in rows]

    def object(self, kind, properties):
        definition=self.objects[kind]
        properties=dict(properties)
        if 'leadText' in properties:
            observed=properties.get('leadObservedAt')
            if not observed or datetime.fromisoformat(observed.replace('Z','+00:00'))>datetime.fromisoformat(self.cutoff):
                properties['leadText']=None
        return {'object_type':kind,'object_id':properties['id'],
                'title':properties.get(definition['titleProperty']) or properties['id'], 'properties':properties}

    def get(self, refs):
        unique={};results=[]
        for ref in refs:
            if set(ref)!={'object_type','object_id'} or not isinstance(ref['object_id'],str) or not ref['object_id']:
                raise ValueError('Each object reference requires a concrete type and nonempty string ID')
            self.properties(ref['object_type'])
            unique[(ref['object_type'],ref['object_id'])]=ref
        by_type={}
        for kind,identifier in unique:by_type.setdefault(kind,[]).append(identifier)
        found={(o['object_type'],o['object_id']):o for kind,ids in by_type.items() for o in self.nodes(kind,ids=ids)}
        for key,ref in unique.items():
            results.append({**copy.deepcopy(ref),'status':'resolved' if key in found else 'not_found_at_cutoff',
                            'object':found.get(key)})
        return {'items':results,'requested_count':len(refs),'distinct_count':len(unique),
                'completeness':'complete' if len(found)==len(unique) else 'partial'}

    def linked(self, refs, relation, direction='forward', *, where=None, parameters=None):
        if relation not in self.links:raise ValueError('Unknown link type')
        if direction not in ('forward','reverse'):raise ValueError('Unknown link direction')
        link=self.links[relation]
        if link['physicalMapping']['kind']=='blocked':raise ValueError('Link mapping is not implemented')
        start,end=(link['source'],link['target']) if direction=='forward' else (link['target'],link['source'])
        if any(r['object_type']!=start for r in refs):raise ValueError('Reference type does not match link direction')
        params={**(parameters or {}),'ids':list(dict.fromkeys(r['object_id'] for r in refs))}
        if not params['ids']:return []
        pattern='(a:'+name(start)+')'+('-[r:'+name(relation)+']->' if direction=='forward' else '<-[r:'+name(relation)+']-')+'(n:'+name(end)+')'
        conditions=['a.id IN $ids']
        if where:conditions.append(where)
        for typ,alias in [(start,'a'),(end,'n')]:
            clause=self.cutoff_clause(typ,alias,params)
            if clause:conditions.append(clause)
        statement='MATCH '+pattern+' WHERE '+' AND '.join(conditions)
        rows=self.query(statement+' RETURN a.id AS sourceId,n.id AS id,properties(n) AS properties,properties(r) AS edge ORDER BY a.id,n.id,r.key_0 LIMIT '+str(self.max_rows+1),params,objects=[start,end],links=[relation])
        return [{'source':{'object_type':start,'object_id':r['sourceId']},'target':self.object(end,{**r['properties'],'id':r['id']}),
                 'relation':relation,'direction':direction,'link_properties':r['edge']} for r in rows]


@contextmanager
def open_reader(uri, username, password):
    """Yield run(statement, parameters) on one read-only session; the caller owns credentials."""
    from neo4j import GraphDatabase, Query
    with GraphDatabase.driver(uri,auth=(username,password),connection_timeout=15) as driver:
        with driver.session(default_access_mode='READ') as session:
            yield lambda statement,parameters:[r.data() for r in session.run(Query(statement,timeout=25),parameters)]
