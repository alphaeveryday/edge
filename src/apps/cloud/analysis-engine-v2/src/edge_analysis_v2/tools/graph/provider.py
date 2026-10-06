"""Graph tool surface: a response reaches the agent only after its evidence is stored."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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
        self.register('get_etf_holdings',
            'Read every direct constituent of one ETF on one date, with the stored weights (0.05 means 5%). '
            'etf_ref.object_id is the id a lookup tool returned, so resolve the fund first. '
            'exact fails when that date has no snapshot; latest_on_or_before picks the nearest earlier snapshot and reports '
            'selected_date, which the answer must state. Weights are not normalized and the snapshot is not certified complete.',
            {'etf_ref': REF, 'holdings_date': {'type': 'string', 'format': 'date', 'description': 'YYYY-MM-DD, Korean market day'},
             'date_policy': {'enum': ['exact', 'latest_on_or_before']}, 'limit': LIMIT},
            ['etf_ref', 'holdings_date', 'date_policy'], self.holdings,
            sources=['ontology_view.etf_holding', 'ontology_view.equity', 'ontology_view.etf'])
        self.register('summarize_etf_holdings',
            'Add up stored holding weights from a get_etf_holdings result: all constituents, an explicit subset, or the largest N. '
            'Use this for any weight total, concentration or combined exposure stated in the answer instead of adding weights yourself. '
            'Pass selection_ref from the holdings result. No normalization and no inferred groups.',
            {'selection_ref': RESULT_REF, 'members': REFS, 'top_n': {'type': 'integer', 'minimum': 1, 'maximum': 1000}},
            ['selection_ref'], self.summarize_holdings, sources=['get_etf_holdings로 저장한 구성'])
        self.register('compare_holdings_dates',
            'Compare two get_etf_holdings results of the same ETF on an earlier and a later date: who was present on each date '
            'and how each stored weight changed. Pass both selection_refs. A weight change is not evidence of a trade or rebalancing.',
            {'earlier_ref': RESULT_REF, 'later_ref': RESULT_REF}, ['earlier_ref', 'later_ref'], self.compare_holdings,
            sources=['get_etf_holdings로 저장한 두 구성'])
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
            # Members stay in stored evidence; a later tool reads them through selection_ref.
            value['selection'] = {key: item for key, item in selection.items() if key != 'items'} | {'members': len(selection['items'])}
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
        rows = [{'requested_ticker': r['requested_ticker'], 'status': r['status'], 'object_type': r['object_type'],
                 'object_id': r['object_id'], 'name': r['object']['title'] if r['object'] else None,
                 'candidates': r['candidates'] if r['status'] == 'ambiguous' else []} for r in items]
        return self.result(items, selection=selection, limit=100, display_items=rows, scope={
            'dataset_kind': 'security_resolution', 'identity': 'exchange market plus exact ticker; no name guessing'})

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
        if object_types:
            # The overview already describes every link; a detail read repeats only their ids.
            links = [link['id'] for link in links]
        return {'objects': objects, 'links': links, 'cutoff': self.store.cutoff, 'fact_source': 'PuppyGraph',
                'supported_tools': list(self._handlers)}, None

    def search(self, object_type, query='', filters=None, limit=20):
        items = self.graph.nodes(object_type, query=query, filters=filters)
        return self.result(items, limit=limit,
            scope={'object_type': object_type, 'query': query, 'filters': filters or {}, 'complete_within_query': True,
                   'objects_of_this_type_in_graph': self.graph.total(object_type),
                   'match': 'substring of any matched property', 'matched_properties': ['id'] + self.graph.text_properties(object_type)})

    def linked(self, object_refs, link_type, direction='forward', limit=20):
        selection = self.graph.get(object_refs)
        scope = {'link_type': link_type, 'direction': direction, 'complete_within_query': True,
                 'start_objects_not_found': [i['object_id'] for i in selection['items'] if i['status'] != 'resolved']}
        return self.result(self.graph.linked(object_refs, link_type, direction), limit=limit, scope=scope)

    def check_date(self, value):
        """Reject a Korean market day that begins after the analysis cutoff."""
        parsed = date.fromisoformat(value)
        if parsed > datetime.fromisoformat(self.store.cutoff).astimezone(timezone(timedelta(hours=9))).date():
            raise ValueError('Requested date is after the analysis cutoff')
        return parsed

    def holdings(self, etf_ref, holdings_date, date_policy, limit=20):
        requested = self.check_date(holdings_date)
        if etf_ref['object_type'] != 'ETF':
            raise ValueError('ETF reference required')
        if self.graph.get([etf_ref])['completeness'] != 'complete':
            raise ValueError('ETF not found: object_id must be the id returned by resolve_securities or search_objects, not a ticker')
        selected = holdings_date
        if date_policy == 'latest_on_or_before':
            rows = self.graph.query(
                'MATCH (h:ETFHolding)-[:ETFHolding_ForETF_ETF]->(e:ETF) WHERE e.id=$etf AND h.tradeDate<=date($day) '
                'AND h.availableAt<=datetime($cutoff) RETURN max(h.tradeDate) AS day',
                {'etf': etf_ref['object_id'], 'day': holdings_date, 'cutoff': self.store.cutoff},
                objects=['ETF', 'ETFHolding'], links=['ETFHolding_ForETF_ETF'])
            selected = rows[0]['day'] if rows else None
        items = self.graph.nodes('ETFHolding', filters={'etfInstrumentId': etf_ref['object_id']},
                                 where='n.tradeDate=date($day)', parameters={'day': selected}) if selected else []
        if not items:
            raise ValueError('No holdings snapshot for this ETF on ' + holdings_date + ' under date_policy ' + date_policy
                             + ('; latest_on_or_before selects the nearest earlier snapshot' if date_policy == 'exact' else ''))
        ids = list(dict.fromkeys(o['properties']['constituentInstrumentId'] for o in items))
        securities = {}
        for kind in ('Equity', 'ETF'):
            for obj in self.graph.nodes(kind, ids=ids):
                securities.setdefault(obj['object_id'], []).append(obj)
        members = []
        for holding in items:
            prop = holding['properties']
            found = securities.get(prop['constituentInstrumentId'], [])
            one = found[0] if len(found) == 1 else None
            members.append({'object_type': one['object_type'] if one else None, 'object_id': prop['constituentInstrumentId'],
                            'object': one, 'status': 'resolved' if one else 'unresolved', 'holding_id': holding['object_id'],
                            'weight_ratio': prop.get('weightRatio')})
        selection = {'items': members, 'requested_date': holdings_date, 'selected_date': selected, 'date_policy': date_policy,
                     'staleness_days': (requested - date.fromisoformat(selected)).days,
                     'completeness': 'complete' if all(m['status'] == 'resolved' for m in members) else 'partial',
                     'scope': 'all available direct holding rows; no recursive expansion', 'etf_ref': etf_ref}
        rows = [{'name': m['object']['title'] if m['object'] else h['properties'].get('securityName'),
                 'ticker': h['properties'].get('securityTicker'), 'weight_ratio': m['weight_ratio'], 'status': m['status'],
                 'object_type': m['object_type'], 'object_id': m['object_id']} for m, h in zip(members, items)]
        return self.result(items, selection=selection, limit=limit, display_items=rows, scope={
            'source_snapshot_completeness': 'not_certified', 'historical_revisions': 'not_reconstructable', 'holdings_date': selected})

    def holdings_selection(self, ref):
        # A holdings result carries two references to the same run; either one identifies its selection.
        selection = self.store.reference({'tool_run_id': ref['tool_run_id'], 'path': '/selection'}, 'selection')
        if not selection.get('etf_ref') or not selection.get('selected_date'):
            raise ValueError('A dated ETF holdings selection from get_etf_holdings is required')
        return selection

    def summarize_holdings(self, selection_ref, members=None, top_n=None):
        selection = self.holdings_selection(selection_ref)
        rows = selection['items']
        if members is not None and top_n is not None:
            raise ValueError('Choose an explicit subset or top N, not both')
        if members is not None:
            requested = {(r['object_type'], r['object_id']) for r in members}
            if not requested <= {(r['object_type'], r['object_id']) for r in rows}:
                raise ValueError('A requested member is not in this saved holding snapshot')
            rows = [r for r in rows if (r['object_type'], r['object_id']) in requested]
        if top_n is not None:
            if any(r.get('weight_ratio') is None for r in rows):
                raise ValueError('Unknown weights prevent ranking the largest holdings')
            rows = sorted(rows, key=lambda r: (-Decimal(str(r['weight_ratio'])), r['object_id']))[:top_n]
        known = [Decimal(str(r['weight_ratio'])) for r in rows if r.get('weight_ratio') is not None]
        if any(not w.is_finite() or w < 0 for w in known):
            raise ValueError('Invalid holdings weights')
        total = sum(known, Decimal(0))
        summary = {'raw_weight_sum': str(total), 'weight_percent': str(total * 100), 'member_count': len(rows),
                   'members': [(r['object'] or {}).get('title') or r['object_id'] for r in rows],
                   'unknown_weights': len(rows) - len(known), 'status': 'calculated' if len(known) == len(rows) else 'partial'}
        return self.result([summary], limit=1, scope={'dataset_kind': 'holding_weight_summary', 'input_ref': selection_ref,
            'selected_date': selection['selected_date'],
            'interpretation': 'Sum of stored direct holding weights; not profit exposure, return attribution or certified full-fund completeness.'})

    def compare_holdings(self, earlier_ref, later_ref):
        earlier, later = self.holdings_selection(earlier_ref), self.holdings_selection(later_ref)
        if earlier['etf_ref'] != later['etf_ref']:
            raise ValueError('Date comparison requires the same ETF')
        if earlier['selected_date'] >= later['selected_date']:
            raise ValueError('Earlier and later actual dates must be ordered')
        left = {(r['object_type'], r['object_id']): r for r in earlier['items']}
        right = {(r['object_type'], r['object_id']): r for r in later['items']}
        rows = []
        for key in sorted(left.keys() | right.keys(), key=str):
            before, after = left.get(key), right.get(key)
            old = before.get('weight_ratio') if before else None
            new = after.get('weight_ratio') if after else None
            delta = Decimal(str(new)) - Decimal(str(old)) if old is not None and new is not None else None
            rows.append({'name': ((after or before).get('object') or {}).get('title') or key[1], 'object_type': key[0], 'object_id': key[1],
                         'earlier_present': before is not None, 'later_present': after is not None,
                         'earlier_weight': old, 'later_weight': new,
                         'percentage_point_change': str(delta * 100) if delta is not None else None})
        return self.result(rows, limit=100, scope={'dataset_kind': 'holding_date_comparison', 'input_refs': [earlier_ref, later_ref],
            'earlier_date': earlier['selected_date'], 'later_date': later['selected_date'],
            'interpretation': 'Presence within stored snapshots. Missing rows are not certified zero holdings. Weight differences do not identify manager trades.'})
