"""Graph tool surface: a response reaches the agent only after its evidence is stored."""
from datetime import date, datetime, timedelta, timezone
import json
from decimal import Decimal
from time import perf_counter

from jsonschema import Draft202012Validator

from edge_analysis_v2.tools.graph.facts import GraphFacts
from edge_analysis_v2.tools.graph.shape import overview
from edge_analysis_v2.tools.graph.store import EvidenceStore

from edge_analysis_v2.tools.graph import descriptions, shape

STRING = {'type': 'string', 'minLength': 1}
LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}
REF = {'type': 'object', 'properties': {'object_type': STRING, 'object_id': STRING},
       'required': ['object_type', 'object_id'], 'additionalProperties': False}
REFS = {'type': 'array', 'items': REF, 'minItems': 1, 'maxItems': 1000}
# path is accepted and not needed: the run id names the stored result and the receiving tool knows which part it reads.
RESULT_REF = {'type': 'object', 'properties': {'tool_run_id': STRING, 'path': {'enum': ['/selection', '/dataset']}},
              'required': ['tool_run_id'], 'additionalProperties': False}
# The securities or actors a tool works on: named objects, the constituents of a fund on a date, or a stored selection.
SELECTOR = {'oneOf': [
    {'type': 'object', 'properties': {'kind': {'const': 'objects'}, 'object_refs': REFS},
     'required': ['kind', 'object_refs'], 'additionalProperties': False},
    {'type': 'object', 'properties': {'kind': {'const': 'etf_holdings'}, 'etf_ref': REF, 'holdings_date': STRING,
                                      'date_policy': {'enum': ['exact', 'latest_on_or_before']}},
     'required': ['kind', 'etf_ref', 'holdings_date', 'date_policy'], 'additionalProperties': False},
    {'type': 'object', 'properties': {'kind': {'const': 'selection_ref'}, 'ref': RESULT_REF},
     'required': ['kind', 'ref'], 'additionalProperties': False}]}
BROAD = 100   # a search matching more rows than this returns their shape, not the rows


class GraphTools:
    """Register graph tools one at a time and audit every call.

    Args:
        run: Callable(statement, parameters) returning rows from one read-only graph session.
        catalog: Reviewed graph catalog from ``load_catalog``.
        directory: Folder that receives one evidence file per call.
        cutoff: Analysis time with an explicit timezone; later facts stay invisible.
        expose: Names of the tools the agent may see and call; every tool when omitted. A tool left out
            does not exist for the agent, so one tool's effect can be measured by adding it alone.

    Raises:
        ValueError: ``expose`` names a tool that is not defined.
    """

    data_source = 'graph'

    def __init__(self, run, catalog, directory, cutoff, expose=None, web=None):
        self._expose = None if expose is None else set(expose)
        self.web = web   # fetches the page behind a stored article link; None where no web credential is configured
        self.store = EvidenceStore(directory, cutoff)
        self.graph = GraphFacts(run, catalog, self.store.cutoff)
        self.schemas = []
        self.definitions = []
        self._handlers = {}
        self.defined = set()
        kinds = {'enum': list(self.graph.objects)}
        self.register('get_result_page',
            {'dataset_ref': RESULT_REF, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': LIMIT},
            ['dataset_ref', 'offset'], self.page, sources=['이 분석에서 저장한 도구 결과'])
        self.register('get_ontology_schema',
            {'object_types': {'type': 'array', 'items': kinds}}, [], self.schema, sources=['검토된 그래프 카탈로그'])
        self.register('search_objects',
            {'object_type': kinds, 'query': {'type': 'string', 'maxLength': 120}, 'filters': {'type': 'object'}, 'limit': LIMIT},
            ['object_type'], self.search, sources=['ontology_view의 해당 객체 뷰'])
        self.register('get_linked_objects',
            {'object_refs': REFS, 'link_type': {'enum': list(self.graph.links)}, 'direction': {'enum': ['forward', 'reverse']},
             'limit': LIMIT}, ['object_refs', 'link_type'], self.linked, sources=['ontology_view의 해당 관계 뷰'])
        self.register('get_etf_holdings',
            {'etf_ref': REF, 'holdings_date': {'type': 'string', 'format': 'date', 'description': 'YYYY-MM-DD, Korean market day'},
             'date_policy': {'enum': ['exact', 'latest_on_or_before']}, 'limit': LIMIT},
            ['etf_ref', 'holdings_date', 'date_policy'], self.holdings,
            sources=['ontology_view.etf_holding', 'ontology_view.equity', 'ontology_view.etf'])
        self.register('sum_holding_weights',
            {'selection_ref': RESULT_REF, 'members': REFS, 'top_n': {'type': 'integer', 'minimum': 1, 'maximum': 1000}},
            ['selection_ref'], self.summarize_holdings, sources=['get_etf_holdings로 저장한 구성'])
        self.register('compare_holdings_between_dates',
            {'earlier_ref': RESULT_REF, 'later_ref': RESULT_REF}, ['earlier_ref', 'later_ref'], self.compare_holdings,
            sources=['get_etf_holdings로 저장한 두 구성'])
        self.register('find_securities_by_ticker',
            {'tickers': {'type': 'array', 'items': STRING, 'minItems': 1, 'maxItems': 1000}, 'market_code': STRING,
             'security_type': {'enum': ['Equity', 'ETF', 'both']}},
            ['tickers', 'market_code'], self.find_securities_by_ticker, sources=['ontology_view.equity', 'ontology_view.etf'])
        self.register('get_objects',
            {'object_refs': REFS}, ['object_refs'], self.get, sources=['ontology_view의 해당 객체 뷰'])
        # Imported here because these modules take their argument schemas from this one.
        from edge_analysis_v2.tools.graph.events import EventTools
        from edge_analysis_v2.tools.graph.financial import FinancialTools
        from edge_analysis_v2.tools.graph.market import MarketTools
        self.events = EventTools(self)
        self.market = MarketTools(self)
        self.financial = FinancialTools(self)
        unknown = (self._expose or set()) - self.defined
        if unknown:
            raise ValueError('Unknown tools to expose: ' + ', '.join(sorted(unknown)))

    def register(self, name, properties, required, handler, *, sources, version='graph-v1'):
        """Expose one tool to the agent and to the administrator-facing definition store.

        The text the agent reads (the description and the argument descriptions) is in descriptions.py.

        Args:
            name: Public function name, unique within this surface.
            properties: JSON Schema properties of the arguments.
            required: Argument names that must be present.
            handler: Callable(**arguments) returning (public result, full dataset or None).
            sources: Object types (recorded as their views) or stored results this tool reads.
            version: Changes whenever the meaning of the response changes.
        """
        if name in self.defined:
            raise ValueError('Tool is already registered: ' + name)
        self.defined.add(name)
        if self._expose is not None and name not in self._expose:
            return
        sources = [self.graph.objects.get(s, {}).get('viewName', s) for s in sources]
        description = name
        if name in descriptions.TOOLS:
            description = descriptions.text(name)
            notes = descriptions.TOOLS[name]['args']
            missing = set(properties) - set(notes)
            if missing:
                raise ValueError(f'{name}: no description for arguments {sorted(missing)}')
            properties = {key: {**schema, 'description': notes[key]} for key, schema in properties.items()}
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

    def result(self, items, *, selection=None, scope=None, limit=20, display_items=None, search=False, table=None):
        """Split a complete dataset into the visible first page and the stored whole.

        Args:
            search: The rows are matches of a search the agent can narrow, not a set it asked for by identity.
            table: Show graph objects as columns and rows; 'ids' also keeps each row's object id, 'objects' its title too.
        """
        scope = {'cutoff': self.store.cutoff, 'source': 'PuppyGraph', **(scope or {})}
        if search and len(items) <= BROAD:
            # A search is told to narrow below BROAD rows and then read every row, so a set that small is shown
            # whole. Cut to the requested limit, 52 rows came back as 20 and the other 32 were never read.
            limit = BROAD
        if table and not (search and len(items) > BROAD):
            # A table row is short, so rows that were asked for are bounded by the response size alone:
            # 46 rate rows came back as 20, and 198 price rows as 100 of a page that had room for all.
            limit = len(items)
        visible = items if display_items is None else display_items
        columns = same = None
        if table and items:
            columns, visible, same = shape.table(visible, keep_id=table in ('ids', 'objects'), keep_title=table == 'objects')
        used = len(json.dumps(same, ensure_ascii=False, default=str)) if same else 0
        asked, limit = limit, shape.fit(visible, limit, used=used)
        summary = None
        if len(items) > limit:
            # The shape of the whole set travels with a cut page, so it takes its room from the rows.
            summary = overview(items if columns is not None else visible)
            limit = shape.fit(visible, limit, used=used + len(json.dumps(summary, ensure_ascii=False)))
        value = {'total_rows': len(items), 'page': {'complete': len(items) <= limit, 'next_offset': limit if len(items) > limit else None}}
        if len(items) > limit:
            # A first page of a large result was read as the whole answer. What the page leaves out comes
            # first, with the shape of the full set, so the next step is to narrow rather than to conclude.
            value['notice'] = (f'{limit} of {len(items)} rows are shown' + (', as many as one response holds' if limit < asked else '')
                + f'. The {len(items) - limit} not shown belong to the same answer set: '
                'do not describe, count or rule anything out from this page alone. overview summarises the whole set; '
                "narrow the request with this tool's filters, or read further pages with get_result_page.")
            value['overview'] = summary
            if search and len(items) > BROAD:
                # Saying what a page leaves out did not change what the agent did. A search too broad to
                # read returns no rows, so the next step has to be a narrower search it can read whole.
                limit = 0
                value['page']['next_offset'] = 0
                value['notice'] = (f'{len(items)} rows match, too many to read, so none are shown. Choose what the question needs from '
                    f'overview and call this tool again with narrower arguments until fewer than {BROAD} rows match; '
                    'then read every row. Reading this whole set page by page with get_result_page is possible but rarely the right step.')
        if columns is not None:
            value.update(columns=columns, rows=visible[:limit], same_for_all_rows=same, data_scope=scope)
        else:
            value.update(items=visible[:limit], data_scope=scope)
        if selection is not None:
            # Members stay in stored evidence; a later tool reads them through selection_ref.
            value['selection'] = {key: item for key, item in selection.items() if key != 'items'} | {'members': len(selection['items'])}
        dataset = {'items': items, 'scope': scope, 'selection': selection}
        if display_items is not None or columns is not None:
            dataset['display_items'] = visible
        if columns is not None:
            # A later page is rows of the same table, and reads wrong without what the first page said once.
            dataset.update(display_columns=columns, display_same=same)
        return value, dataset

    def page(self, dataset_ref, offset, limit=20):
        return self.store.page(dataset_ref, offset, limit), None

    def find_securities_by_ticker(self, tickers, market_code, security_type='both'):
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
        scope = {'object_type': object_type, 'query': query, 'filters': filters or {}, 'complete_within_query': True,
                 'objects_of_this_type_in_graph': self.graph.total(object_type),
                 'cutoff_applied': self.graph.time_bounded(object_type),
                 'match': 'substring of any matched property', 'matched_properties': ['id'] + self.graph.text_properties(object_type)}
        words = query.split()
        if not items and query:
            # After "한화" matched nothing, the same search was repeated with "한화오션" and "한화에어로스페이스".
            scope['nothing_matches'] = ('No stored object of this type has this text, in this letter case, in its name, title or ticker under '
                                        'these filters, so a longer text that includes it cannot match those either. An id matches only when given whole.')
        if not items and len(words) > 1:
            # A fund that is not stored was looked for 12 times under other spellings. Showing what each word
            # alone matches lets one call settle that nothing stored carries all of them.
            matches = {word: [o['title'] for o in self.graph.nodes(object_type, query=word, filters=filters)] for word in words}
            cut = {word: len(titles) for word, titles in matches.items() if len(titles) > 30}
            scope['nothing_has_the_whole_query'] = {
                'each_word_alone_matches': {word: titles[:30] for word, titles in matches.items()},
                'meaning': 'No stored object of this type has the whole query in its name, title or ticker under these filters. '
                           'Matching keeps letter case and spacing as stored. If the object you want is not among these, search one '
                           'distinctive word the way these stored names write it, and check the object type and the filters, '
                           'before saying it is absent. Do not cycle through further variants after that.'}
            if cut:
                # A list cut at 30 hid the 31st match while the text above said it was not stored.
                scope['nothing_has_the_whole_query'].update(
                    lists_cut_at_30={word: {'matches': total} for word, total in cut.items()},
                    meaning='No stored object of this type has the whole query in its name, title or ticker under these filters. A list cut at 30 does not '
                            'show every match of that word, so an object missing from it may still be stored: search a word '
                            'whose list is complete, or that word with a filter.')
        return self.result(items, limit=limit, search=True, scope=scope, table='objects')

    def linked(self, object_refs, link_type, direction=None, limit=20):
        link = self.graph.links[link_type]
        if direction is None or link['source'] != link['target']:
            # The start objects say which end of the relation they are. Asking the caller as well failed 5 calls,
            # and 2 more after it became optional, so it only decides a relation between two objects of one type.
            direction = 'reverse' if link['source'] != link['target'] and {r['object_type'] for r in object_refs} == {link['target']} else 'forward'
        selection = self.graph.get(object_refs)
        scope = {'link_type': link_type, 'direction': direction, 'complete_within_query': True,
                 'cutoff_applied': self.graph.time_bounded(link['target'] if direction == 'forward' else link['source']),
                 'start_objects_not_found': [i['object_id'] for i in selection['items'] if i['status'] != 'resolved']}
        items = self.graph.linked(object_refs, link_type, direction)
        # The linked object with where it was reached from and what the link says, as one table row. Each row
        # used to carry the whole target object and the link's own keys: 64 rows did not fit one response.
        rows = [{**row['target'], 'properties': {**row['target']['properties'], **row['link_properties']}, 'source_id': row['source']['object_id']}
                for row in items]
        return self.result(items, limit=limit, scope=scope, display_items=rows, table='objects')

    def get(self, object_refs):
        selection = self.graph.get(object_refs)
        # Each object as one table row; one that was not found keeps its id and status.
        rows = [{**(item.get('object') or {k: item[k] for k in ('object_type', 'object_id')} | {'properties': {}}), 'status': item['status']}
                for item in selection['items']]
        return self.result(selection['items'], selection=selection, limit=100, display_items=rows, table='objects')

    def select(self, targets):
        """Resolve a SELECTOR argument to its members."""
        if targets['kind'] == 'objects':
            return self.graph.get(targets['object_refs'])
        if targets['kind'] == 'selection_ref':
            return self.store.reference(targets['ref'], 'selection')
        return self.holdings(targets['etf_ref'], targets['holdings_date'], targets['date_policy'])[1]['selection']

    def actors(self, selection):
        """Companies and organizations behind a selection: named directly, or the issuers of its equities."""
        actors, mapping, equities = {}, [], []
        for item in selection['items']:
            if item['status'] != 'resolved':
                continue
            ref = {k: item[k] for k in ('object_type', 'object_id')}
            if ref['object_type'] in ('Company', 'Organization'):
                actors[(ref['object_type'], ref['object_id'])] = ref
            elif ref['object_type'] == 'Equity':
                equities.append(ref)
        for row in self.graph.linked(equities, 'Company_Issues_Equity', 'reverse'):
            ref = {k: row['target'][k] for k in ('object_type', 'object_id')}
            actors[(ref['object_type'], ref['object_id'])] = ref
            mapping.append({'security': row['source'], 'actor': ref})
        return list(actors.values()), mapping

    def time_bounds(self, start_date, end_date):
        """Korean market days as a half-open instant range."""
        start, end = self.check_date(start_date), self.check_date(end_date)
        if start > end:
            raise ValueError('Invalid dates')
        return {'start_at': start.isoformat() + 'T00:00:00+09:00', 'end_at': (end + timedelta(days=1)).isoformat() + 'T00:00:00+09:00'}

    def cutoff_day(self):
        """The Korean day of the cutoff. The cutoff is stored in UTC, whose date is a day earlier for nine hours of every day."""
        return datetime.fromisoformat(self.store.cutoff).astimezone(timezone(timedelta(hours=9))).date()

    def check_date(self, value):
        """Reject a Korean market day that begins after the analysis cutoff."""
        parsed = date.fromisoformat(value)
        if parsed > self.cutoff_day():
            raise ValueError('Requested date is after the analysis cutoff')
        return parsed

    def holdings(self, etf_ref, holdings_date, date_policy, limit=20):
        requested = self.check_date(holdings_date)
        if etf_ref['object_type'] != 'ETF':
            raise ValueError('ETF reference required')
        if self.graph.get([etf_ref])['completeness'] != 'complete':
            raise ValueError('ETF not found: object_id must be the id returned by find_securities_by_ticker or search_objects, not a ticker')
        selected = holdings_date = requested.isoformat()
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
        return self.result(items, selection=selection, limit=limit, display_items=rows, table=True, scope={
            'source_snapshot_completeness': 'not_certified', 'historical_revisions': 'not_reconstructable', 'holdings_date': selected})

    def holdings_selection(self, ref):
        # A holdings result carries two references to the same run; either one identifies its selection.
        selection = self.store.reference(ref, 'selection')
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
