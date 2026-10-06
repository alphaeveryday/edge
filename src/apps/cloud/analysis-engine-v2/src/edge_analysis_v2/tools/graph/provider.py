"""Graph tool surface: a response reaches the agent only after its evidence is stored."""
from time import perf_counter

from jsonschema import Draft202012Validator

from edge_analysis_v2.tools.graph.facts import GraphFacts
from edge_analysis_v2.tools.graph.store import EvidenceStore

STRING = {'type': 'string', 'minLength': 1}
LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}
REF = {'type': 'object', 'properties': {'object_type': STRING, 'object_id': STRING},
       'required': ['object_type', 'object_id'], 'additionalProperties': False}
REFS = {'type': 'array', 'items': REF, 'minItems': 1, 'maxItems': 1000}
RESULT_REF = {'type': 'object', 'properties': {'tool_run_id': STRING, 'path': {'enum': ['/selection', '/dataset']}},
              'required': ['tool_run_id', 'path'], 'additionalProperties': False}


class GraphTools:
    """Register graph tools one at a time and audit every call.

    Args:
        run: Callable(statement, parameters) returning rows from one read-only graph session.
        catalog: Reviewed graph catalog from ``load_catalog``.
        directory: Folder that receives one evidence file per call.
        cutoff: Analysis time with an explicit timezone; later facts stay invisible.
    """

    data_source = 'graph'

    def __init__(self, run, catalog, directory, cutoff):
        self.store = EvidenceStore(directory, cutoff)
        self.graph = GraphFacts(run, catalog, self.store.cutoff)
        self.schemas = []
        self.definitions = []
        self._handlers = {}
        kinds = {'enum': list(self.graph.objects)}
        self.register('get_result_page',
            'Read another page of a stored full dataset without rerunning the graph query or changing its cutoff.',
            {'dataset_ref': RESULT_REF, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': LIMIT},
            ['dataset_ref', 'offset'], self.page, sources=['이 분석에서 저장한 도구 결과'])
        self.register('get_ontology_schema',
            'Discover graph object types, properties and links; definitions are not observed facts. '
            'Without object_types it lists every type and link without properties; name the types you will query to get their properties.',
            {'object_types': {'type': 'array', 'items': kinds}}, [], self.schema, sources=['검토된 그래프 카탈로그'])
        self.register('search_objects',
            'Find objects of one type by part of their name, title or ticker. One call returns every match as a stored dataset '
            'plus a first page; total_rows and matched_properties say what was covered, so do not repeat the search with variants. '
            'A security is Equity or ETF; an issuer is Company. filters need exact property values from get_ontology_schema.',
            {'object_type': kinds, 'query': {'type': 'string', 'maxLength': 120}, 'filters': {'type': 'object'}, 'limit': LIMIT},
            ['object_type'], self.search, sources=['ontology_view의 해당 객체 뷰'])
        self.register('get_linked_objects',
            'Follow one declared relation from a batch of objects to everything linked to them, e.g. a company to all securities '
            'it issued. A link id reads Source_Relation_Target; direction reverse starts from the Target type. Use this instead '
            'of searching by name variants when the question is about a relation.',
            {'object_refs': REFS, 'link_type': {'enum': list(self.graph.links)}, 'direction': {'enum': ['forward', 'reverse']},
             'limit': LIMIT}, ['object_refs', 'link_type'], self.linked, sources=['ontology_view의 해당 관계 뷰'])
        self.register('resolve_securities',
            'Resolve a list of exchange tickers in one batch before querying their prices, flows or events. '
            'Use tickers given in the question, never remembered ones. An object another tool already returned is already '
            'resolved from the same data; calling this to confirm it adds nothing. '
            'For Korean stocks use market_code XKRX. Return a reusable selection; preserve missing or ambiguous codes. '
            'Prefer this to one search per ticker.',
            {'tickers': {'type': 'array', 'items': STRING, 'minItems': 1, 'maxItems': 1000}, 'market_code': STRING,
             'security_type': {'enum': ['Equity', 'ETF', 'both']}},
            ['tickers', 'market_code'], self.resolve_securities, sources=['ontology_view.equity', 'ontology_view.etf'])

    def register(self, name, description, properties, required, handler, *, sources, version='graph-v1'):
        """Expose one tool to the agent and to the administrator-facing definition store.

        Args:
            name: Public function name, unique within this surface.
            description: Text the agent reads when choosing a tool.
            properties: JSON Schema properties of the arguments.
            required: Argument names that must be present.
            handler: Callable(**arguments) returning (public result, full dataset or None).
            sources: Views or stored results this tool reads.
            version: Changes whenever the meaning of the response changes.
        """
        if name in self._handlers:
            raise ValueError('Tool is already registered: ' + name)
        parameters = {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
        self.schemas.append({'type': 'function', 'function': {'name': name, 'description': description, 'parameters': parameters}})
        self.definitions.append(dict(tool_id=name + ':' + version, function_name=name, version=version,
                                     description=description, source_names=list(sources)))
        self._handlers[name] = (Draft202012Validator(parameters), handler)

    def call(self, name, arguments):
        """Execute once and return the stored response.

        Raises:
            ValueError: Unknown tool.
            jsonschema.ValidationError: Arguments violate the registered schema.
            Exception: Graph connectivity or evidence persistence failed; never reported as missing data.
        """
        started = perf_counter()
        begin = len(self.graph.queries)
        if name not in self._handlers:
            raise ValueError('Unknown tool: ' + name)
        validator, handler = self._handlers[name]
        validator.validate(arguments)
        error = dataset = None
        try:
            result, dataset = handler(**arguments)
        except (ValueError, KeyError) as exc:
            # A request the data cannot answer is evidence too; the agent may cite it as a limitation.
            error = str(exc)
            result = {'status': 'invalid_or_unavailable', 'reason': error}
        return self.store.commit(name, arguments, result, elapsed_ms=round((perf_counter() - started) * 1000, 2),
                                 queries=self.graph.queries[begin:], error=error, dataset=dataset)

    def result(self, items, *, selection=None, scope=None, limit=20, display_items=None):
        """Split a complete dataset into the visible first page and the stored whole."""
        scope = {'cutoff': self.store.cutoff, 'source': 'PuppyGraph', **(scope or {})}
        visible = items if display_items is None else display_items
        value = {'items': visible[:limit], 'data_scope': scope, 'total_rows': len(items),
                 'page': {'complete': len(items) <= limit, 'next_offset': limit if len(items) > limit else None}}
        if selection is not None:
            value['selection'] = selection
        dataset = {'items': items, 'scope': scope, 'selection': selection}
        if display_items is not None:
            dataset['display_items'] = display_items
        return value, dataset

    def page(self, dataset_ref, offset, limit=20):
        return self.store.page(dataset_ref, offset, limit), None

    def resolve_securities(self, tickers, market_code, security_type='both'):
        kinds = ['Equity', 'ETF'] if security_type == 'both' else [security_type]
        requested = list(dict.fromkeys(tickers))
        found = {}
        for kind in kinds:
            for obj in self.graph.nodes(kind, filters={'ticker': requested, 'marketCode': market_code}):
                found.setdefault(obj['properties']['ticker'], []).append(obj)
        items = []
        for ticker in requested:
            candidates = found.get(ticker, [])
            obj = candidates[0] if len(candidates) == 1 else None
            items.append({'requested_ticker': ticker, 'requested_market': market_code,
                'object_type': obj['object_type'] if obj else None, 'object_id': obj['object_id'] if obj else None,
                'status': 'resolved' if obj else 'ambiguous' if candidates else 'not_found_at_cutoff', 'object': obj,
                'candidates': [{k: c[k] for k in ('object_type', 'object_id', 'title')} for c in candidates]})
        selection = {'items': items, 'requested_count': len(tickers), 'distinct_count': len(items),
                     'completeness': 'complete' if all(r['status'] == 'resolved' for r in items) else 'partial'}
        return self.result(items, selection=selection, limit=100, scope={'dataset_kind': 'security_resolution',
            'identity': 'exchange market plus exact ticker; no name guessing'})

    def schema(self, object_types=None):
        selected = object_types or list(self.graph.objects)
        for kind in selected:
            self.graph.properties(kind)
        objects = [{'object_type': kind, 'description': self.graph.objects[kind]['description']} for kind in selected]
        if object_types:
            for entry in objects:
                entry['properties'] = [{key: column.get(key) for key in ('property', 'type', 'description', 'mappingStatus')}
                                       for column in self.graph.objects[entry['object_type']]['columns'] if column.get('property')]
        links = [{'id': link['id'], 'source': link['source'], 'target': link['target'], 'description': link['description'],
                  'inverse': (link.get('inverse') or {}).get('apiName')}
                 for link in self.graph.links.values() if link['source'] in selected or link['target'] in selected]
        return {'objects': objects, 'links': links, 'cutoff': self.store.cutoff, 'fact_source': 'PuppyGraph',
                'supported_tools': list(self._handlers)}, None

    def search(self, object_type, query='', filters=None, limit=20):
        return self.result(self.graph.nodes(object_type, query=query, filters=filters), limit=limit,
            scope={'object_type': object_type, 'query': query, 'filters': filters or {}, 'complete_within_query': True,
                   'match': 'substring of any matched property', 'matched_properties': ['id'] + self.graph.text_properties(object_type)})

    def linked(self, object_refs, link_type, direction='forward', limit=20):
        selection = self.graph.get(object_refs)
        return self.result(self.graph.linked(object_refs, link_type, direction), selection=selection, limit=limit,
                           scope={'link_type': link_type, 'direction': direction, 'complete_within_query': True})
