"""Graph tool surface: a response reaches the agent only after its evidence is stored."""
from time import perf_counter

from jsonschema import Draft202012Validator

from edge_analysis_v2.tools.graph.facts import GraphFacts
from edge_analysis_v2.tools.graph.store import EvidenceStore

STRING = {'type': 'string', 'minLength': 1}
LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}
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
        self.register('get_result_page',
            'Read another page of a stored full dataset without rerunning the graph query or changing its cutoff.',
            {'dataset_ref': RESULT_REF, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': LIMIT},
            ['dataset_ref', 'offset'], self.page, sources=['이 분석에서 저장한 도구 결과'])

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
