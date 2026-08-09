"""Structured ObjectSet tools for the hypothesis agent.

The model composes immutable handles.  It never supplies relation names, query text,
or the analysis clock; those are resolved from the server-side catalog and the
runtime's pinned ``as_of`` value.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..observability import record

MAX_INSPECT_ROWS = 40
MAX_FIELDS = 20
MAX_FILTER_STRING = 500
MAX_INSPECT_BYTES = 32 * 1024
_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$")
_OPERATORS = ("eq", "ne", "lt", "lte", "gt", "gte", "in")


@dataclass(frozen=True, slots=True)
class NewsScope:
    """Server-owned ETF news discovery boundary."""

    etf_instrument_id: str
    start_date: str

    def __post_init__(self) -> None:
        if not self.etf_instrument_id or len(self.etf_instrument_id) > 200:
            raise ValueError("etf_instrument_id must be 1 to 200 characters")
        try:
            date.fromisoformat(self.start_date)
        except ValueError as exc:
            raise ValueError("start_date must be YYYY-MM-DD") from exc


@dataclass(frozen=True, slots=True)
class _Link:
    source_kind: str
    target_kind: str
    source_field: str
    target_field: str
    name: str
    edge_view: str
    edge_dataset: str
    edge_source_field: str
    edge_target_field: str


@dataclass(frozen=True, slots=True)
class _Binding:
    kind: str
    view: str
    dataset: str
    key: str
    fixed_field: str = ""
    fixed_values: tuple[str, ...] = ()


_BINDINGS = (
    _Binding("ISSUER", "v_instrument", "instrument", "instrument_id"),
    _Binding("COMPANY_ENTITY", "v_actor", "actor", "actor_id",
             "actor_type", ("COMPANY",)),
    _Binding("PRODUCT_OR_CONCEPT", "v_concept", "concept", "concept_id",
             "concept_type", ("PRODUCT_OR_CONCEPT",)),
    _Binding("AUTHORITY_OR_RULE", "v_concept", "concept", "concept_id",
             "concept_type", ("AUTHORITY_OR_RULE",)),
    _Binding("LOCATION_OR_HAZARD", "v_concept", "concept", "concept_id",
             "concept_type", ("LOCATION_OR_HAZARD",)),
    _Binding("INDEX_OR_EXCHANGE", "v_concept", "concept", "concept_id",
             "concept_type", ("INDEX_OR_EXCHANGE",)),
    _Binding("NEWS_THREAD", "v_event_thread", "event_thread", "thread_id"),
    _Binding("NEWS_EVENT", "v_source_event", "source_event", "source_event_id",
             "source_class", ("NEWS",)),
    _Binding("EVENT_ARGUMENT", "v_event_argument", "event_argument", "event_argument_id"),
    _Binding("EVENT_EVIDENCE", "v_event_evidence", "event_evidence", "evidence_id"),
)
_BY_KIND = {binding.kind: binding for binding in _BINDINGS}


_LINKS = (
    _Link("ISSUER", "COMPANY_ENTITY", "instrument_id", "actor_id", "ISSUER",
          "v_equity_profile", "equity_profile", "instrument_id", "issuer_actor_id"),
    _Link("COMPANY_ENTITY", "ISSUER", "actor_id", "instrument_id", "ISSUED_SECURITY",
          "v_equity_profile", "equity_profile", "issuer_actor_id", "instrument_id"),
    _Link("NEWS_THREAD", "NEWS_EVENT", "thread_id", "source_event_id", "EVENTS",
          "v_event_thread_link", "event_thread_link", "thread_id", "source_event_id"),
    _Link("NEWS_EVENT", "EVENT_ARGUMENT", "source_event_id", "event_argument_id", "ARGUMENTS",
          "v_event_argument", "event_argument", "source_event_id", "event_argument_id"),
    _Link("NEWS_EVENT", "EVENT_EVIDENCE", "source_event_id", "evidence_id", "EVIDENCE",
          "v_event_evidence", "event_evidence", "source_event_id", "evidence_id"),
)


@dataclass(frozen=True, slots=True)
class _Filter:
    field: str
    operator: str
    value: Any


@dataclass(frozen=True, slots=True)
class _Set:
    handle: str
    kind: str
    lineage: tuple[dict[str, Any], ...]
    parent: str = ""
    link: _Link | None = None
    filters: tuple[_Filter, ...] = field(default_factory=tuple)


class _PolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _schema(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False,
    }


class ObjectSetRuntime:
    """One analysis-run registry of immutable, point-in-time ObjectSet handles."""

    def __init__(self, lake, *, as_of: str,
                 dataset_versions: dict[str, str] | None = None,
                 news_scope: NewsScope | None = None) -> None:
        if not _AS_OF.fullmatch(as_of):
            raise ValueError("as_of must be a local YYYY-MM-DDTHH:MM:SS timestamp")
        cutoff = datetime.fromisoformat(as_of)
        if news_scope and date.fromisoformat(news_scope.start_date) > cutoff.date():
            raise ValueError("news scope start_date must not be after as_of")
        self._lake = lake
        self.as_of = cutoff.isoformat()
        self._as_of_date = cutoff.date().isoformat()
        self._versions = dict(dataset_versions or {})
        self._news_scope = news_scope
        self._sets: dict[str, _Set] = {}
        self._columns: dict[str, tuple[tuple[str, str], ...]] = {}
        self._relations: dict[str, str] = {}
        self._dataset_relations: dict[str, str] = {}
        from edge_ontology import load_process_registry, load_relations
        self._processes = load_process_registry()
        self._relation_vocabulary = load_relations()
        for binding in _BINDINGS:
            if relation := self._resolve_relation(binding.dataset, binding.view):
                self._relations[binding.kind] = relation
        self._kinds = tuple(sorted(self._relations))
        self._public_kinds = tuple(kind for kind in self._kinds if kind not in {
            "NEWS_THREAD", "NEWS_EVENT", "EVENT_ARGUMENT", "EVENT_EVIDENCE"})
        if not self._kinds:
            raise ValueError("no queryable object kinds are bound")

    @staticmethod
    def _relation_sql(name: str) -> str:
        return ".".join(f'"{part}"' for part in name.split("."))

    def _resolve_relation(self, dataset: str, preferred: str = "") -> str:
        if dataset in self._dataset_relations:
            return self._dataset_relations[dataset]
        for relation in (preferred or f"v_{dataset}", f"rdb.public.{dataset}"):
            try:
                self._lake.con.execute(
                    f"DESCRIBE SELECT * FROM {self._relation_sql(relation)}").fetchall()
            except Exception:  # noqa: BLE001 - unavailable binding is omitted from the menu
                continue
            self._dataset_relations[dataset] = relation
            return relation
        return ""

    def tool_specs(self) -> list[dict[str, Any]]:
        """JSON-safe model contract. Execution callables remain server-side."""
        handle = {"type": "string", "minLength": 16, "maxLength": 64}
        scalar = {"type": ["string", "number", "integer", "boolean", "null"]}
        specs = [
            {"name": "objectset.create",
             "description": "Create an immutable set for one available object kind. The server pins the analysis clock.",
             "input_schema": _schema(
                 {"kind": {"type": "string", "enum": list(self._public_kinds)}}, ("kind",))},
            {"name": "objectset.filter",
             "description": "Return a new set narrowed by one declared field comparison.",
             "input_schema": _schema({
                 "handle": handle,
                 "field": {"type": "string", "minLength": 1, "maxLength": 80},
                 "operator": {"type": "string", "enum": list(_OPERATORS)},
                 "value": {"anyOf": [scalar, {"type": "array", "items": scalar,
                                                "minItems": 1, "maxItems": 50}]},
             }, ("handle", "field", "operator", "value"))},
            {"name": "objectset.describe",
             "description": "Describe the object kind, fields, provenance, and point-in-time gaps for a handle.",
             "input_schema": _schema({"handle": handle}, ("handle",))},
            {"name": "objectset.list_affordances",
             "description": "List valid filter fields and relationship moves for this handle.",
             "input_schema": _schema({"handle": handle}, ("handle",))},
            {"name": "objectset.follow",
             "description": "Return a new set by following one relationship advertised for this handle.",
             "input_schema": _schema({
                 "handle": handle,
                 "relation": {"type": "string", "minLength": 1, "maxLength": 80},
             }, ("handle", "relation"))},
            {"name": "objectset.inspect",
             "description": f"Inspect at most {MAX_INSPECT_ROWS} objects from a handle.",
             "input_schema": _schema({
                 "handle": handle,
                 "fields": {"type": "array", "items": {"type": "string"},
                            "maxItems": MAX_FIELDS, "uniqueItems": True},
                 "limit": {"type": "integer", "minimum": 1, "maximum": MAX_INSPECT_ROWS},
             }, ("handle",))},
        ]
        limit = {"type": "integer", "minimum": 1, "maximum": MAX_INSPECT_ROWS}
        event_type = {"type": "string", "minLength": 1, "maxLength": 120}
        specs.extend([
            {"name": "news.find_threads",
             "description": "Find point-in-time news threads and return a reusable handle.",
             "input_schema": _schema({"event_type_code": event_type, "limit": limit}, ())},
            {"name": "news.get_thread",
             "description": "Get one news thread as a point-in-time handle.",
             "input_schema": _schema({
                 "thread_id": {"type": "string", "minLength": 1, "maxLength": 200},
             }, ("thread_id",))},
            {"name": "news.list_events",
             "description": "List news events belonging to a thread handle.",
             "input_schema": _schema({"handle": handle, "limit": limit}, ("handle",))},
            {"name": "news.get_event_arguments",
             "description": "List resolved and unresolved participants for an event handle.",
             "input_schema": _schema({"handle": handle, "limit": limit}, ("handle",))},
            {"name": "news.describe_event_schema",
             "description": "Describe allowed participant roles, cardinality, object kinds, and measures.",
             "input_schema": _schema({"event_type_code": event_type}, ("event_type_code",))},
            {"name": "news.follow_argument",
             "description": "Follow one resolved participant to its object set; retain unresolved text.",
             "input_schema": _schema({
                 "handle": handle,
                 "event_argument_id": {"type": "integer", "minimum": 1},
             }, ("handle", "event_argument_id"))},
            {"name": "news.get_event_evidence",
             "description": "List evidence attached to an event handle.",
             "input_schema": _schema({"handle": handle, "limit": limit}, ("handle",))},
        ])
        return specs

    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        """Validate an untrusted model call and return one consistent envelope."""
        operations = {
            "objectset.create": (self._create, {"kind"}, {"kind"}),
            "objectset.filter": (
                self._filter, {"handle", "field", "operator", "value"},
                {"handle", "field", "operator", "value"}),
            "objectset.describe": (self._describe, {"handle"}, {"handle"}),
            "objectset.list_affordances": (self._affordances, {"handle"}, {"handle"}),
            "objectset.follow": (
                self._follow, {"handle", "relation"}, {"handle", "relation"}),
            "objectset.inspect": (
                self._inspect, {"handle", "fields", "limit"}, {"handle"}),
            "news.find_threads": (
                self._find_threads, {"event_type_code", "limit"}, set()),
            "news.get_thread": (self._get_thread, {"thread_id"}, {"thread_id"}),
            "news.list_events": (self._list_events, {"handle", "limit"}, {"handle"}),
            "news.get_event_arguments": (
                self._get_event_arguments, {"handle", "limit"}, {"handle"}),
            "news.describe_event_schema": (
                self._describe_event_schema, {"event_type_code"}, {"event_type_code"}),
            "news.follow_argument": (
                self._follow_argument, {"handle", "event_argument_id"},
                {"handle", "event_argument_id"}),
            "news.get_event_evidence": (
                self._get_event_evidence, {"handle", "limit"}, {"handle"}),
        }
        try:
            if name not in operations:
                raise _PolicyError("TOOL_NOT_ALLOWED", "tool is not available")
            if not isinstance(arguments, dict):
                raise _PolicyError("INVALID_ARGUMENTS", "arguments must be an object")
            fn, allowed, required = operations[name]
            if unknown := sorted(set(arguments) - allowed):
                raise _PolicyError(
                    "INVALID_ARGUMENTS", "arguments contain unsupported fields")
            if missing := sorted(required - set(arguments)):
                raise _PolicyError("INVALID_ARGUMENTS", "missing arguments: " + ", ".join(missing))
            out = fn(**arguments)
            record("objectset.tool", tool=name, ok=True,
                   handle=str(out.get("handle", "")), lineage_id=str(out.get("lineage_id", "")))
            return out
        except _PolicyError as exc:
            record("objectset.tool", tool=name, ok=False, code=exc.code)
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001 - never expose engine/adapter internals to the model
            record("objectset.tool", tool=name, ok=False, code="EXECUTION_FAILED",
                   error_type=type(exc).__name__)
            return {"ok": False, "error": {
                "code": "EXECUTION_FAILED", "message": "object operation failed"}}

    def _columns_for(self, kind: str) -> tuple[tuple[str, str], ...]:
        if kind not in self._kinds:
            raise _PolicyError("KIND_NOT_ALLOWED", "object kind is not available")
        if kind not in self._columns:
            relation = self._relation_sql(self._relations[kind])
            rows = self._lake.con.execute(
                f'DESCRIBE SELECT * FROM {relation}').fetchall()
            self._columns[kind] = tuple((str(r[0]), str(r[1])) for r in rows)
        return self._columns[kind]

    def _new(self, *, kind: str, lineage: tuple[dict[str, Any], ...],
             parent: str = "", link: _Link | None = None,
             filters: tuple[_Filter, ...] = ()) -> _Set:
        recipe = {"as_of": self.as_of, "kind": kind, "lineage": lineage}
        handle = "os_" + hashlib.sha256(
            json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       default=str).encode("utf-8")).hexdigest()[:24]
        item = _Set(handle, kind, lineage, parent, link, filters)
        self._sets[handle] = item
        return item

    def _get(self, handle: str) -> _Set:
        if not isinstance(handle, str) or handle not in self._sets:
            raise _PolicyError("HANDLE_NOT_FOUND", "object set handle is not available")
        return self._sets[handle]

    def _get_kind(self, handle: str, expected: str) -> _Set:
        item = self._get(handle)
        if item.kind != expected:
            raise _PolicyError(
                "HANDLE_KIND_MISMATCH", f"handle must contain {expected} objects")
        return item

    def _create(self, kind: str) -> dict[str, Any]:
        if not isinstance(kind, str):
            raise _PolicyError("INVALID_ARGUMENTS", "kind must be a string")
        if kind not in self._public_kinds:
            raise _PolicyError("KIND_NOT_ALLOWED", "object kind is not publicly creatable")
        self._columns_for(kind)
        item = self._new(kind=kind, lineage=self._initial_lineage(kind))
        return self._envelope(item)

    def _initial_lineage(self, kind: str) -> tuple[dict[str, Any], ...]:
        lineage = ({"operation": "create", "kind": kind},)
        if kind == "NEWS_THREAD":
            scoped = () if self._news_scope is None else ({
                "operation": "scope_news",
                "start_date": self._news_scope.start_date,
                "end_at": self.as_of,
                "relationships": [
                    "etf_holding_snapshot", "event_argument", "source_event",
                    "event_evidence", "document_assertion", "document",
                    "event_thread_link",
                ],
            },)
            return (*lineage, *scoped,
                    {"operation": "knowledge_clamp",
                     "edge_dataset": "event_thread_link"},
                    {"operation": "knowledge_clamp", "edge_dataset": "source_event"})
        return lineage

    def _scope_relation(self, dataset: str) -> str:
        relation = self._resolve_relation(dataset, f"v_{dataset}")
        if not relation:
            raise _PolicyError(
                "RELATION_UNAVAILABLE", "scoped news relationship is not present")
        return self._relation_sql(relation)

    def _scope_event_predicate(self, event_alias: str) -> tuple[str, list[Any]]:
        scope = self._news_scope
        if scope is None:
            return "", []
        holding = self._scope_relation("etf_holding_snapshot")
        argument = self._scope_relation("event_argument")
        evidence = self._scope_relation("event_evidence")
        assertion = self._scope_relation("document_assertion")
        document = self._scope_relation("document")
        cutoff = self.as_of.replace("T", " ")
        predicate = f''' AND {event_alias}."event_date" >= ?
            AND {event_alias}."event_date" <= ?
            AND EXISTS (
              SELECT 1 FROM {argument} AS scope_argument
              WHERE scope_argument."source_event_id" = {event_alias}."source_event_id"
                AND scope_argument."entity_id" IN (
                  SELECT ?
                  UNION
                  SELECT holding."constituent_instrument_id" FROM {holding} AS holding
                  WHERE holding."etf_instrument_id" = ?
                    AND holding."available_at" <= ?
                    AND holding."trade_date" = (
                      SELECT max(snapshot."trade_date") FROM {holding} AS snapshot
                      WHERE snapshot."etf_instrument_id" = ?
                        AND snapshot."available_at" <= ?
                        AND snapshot."trade_date" <= ?)))
            AND EXISTS (
              SELECT 1 FROM {evidence} AS scope_evidence
              JOIN {assertion} AS scope_assertion
                ON scope_assertion."assertion_id" = scope_evidence."assertion_id"
              JOIN {document} AS scope_document
                ON scope_document."document_id" = scope_assertion."document_id"
              WHERE scope_evidence."source_event_id" = {event_alias}."source_event_id"
                AND scope_assertion."available_at" <= ?
                AND scope_document."available_at" <= ?)'''
        return predicate, [
            scope.start_date, self._as_of_date, scope.etf_instrument_id,
            scope.etf_instrument_id, cutoff, scope.etf_instrument_id, cutoff,
            self._as_of_date,
            cutoff, cutoff,
        ]

    def _document_event_predicate(self, event_alias: str) -> tuple[str, list[Any]]:
        evidence = self._scope_relation("event_evidence")
        assertion = self._scope_relation("document_assertion")
        document = self._scope_relation("document")
        cutoff = self.as_of.replace("T", " ")
        return f''' AND EXISTS (
            SELECT 1 FROM {evidence} AS grounded_evidence
            JOIN {assertion} AS grounded_assertion
              ON grounded_assertion."assertion_id" = grounded_evidence."assertion_id"
            JOIN {document} AS grounded_document
              ON grounded_document."document_id" = grounded_assertion."document_id"
            WHERE grounded_evidence."source_event_id" = {event_alias}."source_event_id"
              AND grounded_assertion."available_at" <= ?
              AND grounded_document."available_at" <= ?)''', [cutoff, cutoff]

    def _scope_counts(self, final_threads: int) -> dict[str, int]:
        scope = self._news_scope
        if scope is None:
            return {}
        holding = self._scope_relation("etf_holding_snapshot")
        argument = self._scope_relation("event_argument")
        event = self._scope_relation("source_event")
        thread_link = self._scope_relation("event_thread_link")
        cutoff = self.as_of.replace("T", " ")
        entity_sql = f'''SELECT count(DISTINCT entity_id) FROM (
            SELECT ? AS entity_id
            UNION ALL
            SELECT "constituent_instrument_id" FROM {holding}
            WHERE "etf_instrument_id" = ? AND "available_at" <= ? AND "trade_date" = (
              SELECT max("trade_date") FROM {holding}
              WHERE "etf_instrument_id" = ? AND "available_at" <= ? AND "trade_date" <= ?))'''
        entity_params = [scope.etf_instrument_id, scope.etf_instrument_id, cutoff,
                         scope.etf_instrument_id, cutoff, self._as_of_date]
        candidate_entities = int(self._lake.con.execute(entity_sql, entity_params).fetchone()[0])
        candidates_sql = f'''SELECT DISTINCT candidate."source_event_id"
            FROM {event} AS candidate JOIN {argument} AS arg
              ON arg."source_event_id" = candidate."source_event_id"
            WHERE candidate."source_class" = 'NEWS' AND arg."entity_id" IN (
              SELECT ? UNION SELECT "constituent_instrument_id" FROM {holding}
              WHERE "etf_instrument_id" = ? AND "available_at" <= ? AND "trade_date" = (
                SELECT max("trade_date") FROM {holding}
                WHERE "etf_instrument_id" = ? AND "available_at" <= ? AND "trade_date" <= ?))'''
        candidate_events = int(self._lake.con.execute(
            f"SELECT count(*) FROM ({candidates_sql}) AS candidate_events",
            entity_params).fetchone()[0])
        candidate_threads = int(self._lake.con.execute(
            f'''SELECT count(DISTINCT link."thread_id") FROM {thread_link} AS link
                JOIN ({candidates_sql}) AS candidate
                  ON candidate."source_event_id" = link."source_event_id"
                WHERE link."thread_id" IS NOT NULL''', entity_params).fetchone()[0])
        predicate, predicate_params = self._scope_event_predicate("pit_event")
        pit_sql = (f'''SELECT count(DISTINCT pit_event."source_event_id")
            FROM {event} AS pit_event WHERE pit_event."source_class" = 'NEWS'
              AND pit_event."event_status" = 'ACTIVE'
              AND pit_event."available_at" <= ?''' + predicate)
        pit_filtered_events = int(self._lake.con.execute(
            pit_sql, [cutoff, *predicate_params]).fetchone()[0])
        return {
            "candidate_entities": candidate_entities,
            "candidate_events": candidate_events,
            "candidate_threads": candidate_threads,
            "pit_filtered_events": pit_filtered_events,
            "final_threads": final_threads,
        }

    def _filter(self, handle: str, field: str, operator: str, value: Any) -> dict[str, Any]:
        item = self._get(handle)
        fields = {name for name, _ in self._columns_for(item.kind)}
        if not isinstance(field, str) or field not in fields:
            raise _PolicyError("FIELD_NOT_ALLOWED", "field is not available for this object kind")
        if operator not in _OPERATORS:
            raise _PolicyError("OPERATOR_NOT_ALLOWED", "filter operator is not available")
        if operator == "in":
            if not isinstance(value, list) or not value or len(value) > 50:
                raise _PolicyError("INVALID_ARGUMENTS", "in value must contain 1 to 50 items")
            if any(isinstance(v, (dict, list)) for v in value):
                raise _PolicyError("INVALID_ARGUMENTS", "in values must be scalar")
            if any(isinstance(v, str) and len(v) > MAX_FILTER_STRING for v in value):
                raise _PolicyError("INVALID_ARGUMENTS", "filter string is too long")
        elif isinstance(value, (dict, list)):
            raise _PolicyError("INVALID_ARGUMENTS", "filter value must be scalar")
        elif isinstance(value, str) and len(value) > MAX_FILTER_STRING:
            raise _PolicyError("INVALID_ARGUMENTS", "filter string is too long")
        step = {"operation": "filter", "field": field, "operator": operator, "value": value}
        new = self._new(kind=item.kind, lineage=(*item.lineage, step), parent=item.parent,
                        link=item.link, filters=(*item.filters, _Filter(field, operator, value)))
        return self._envelope(new)

    def _describe(self, handle: str) -> dict[str, Any]:
        item = self._get(handle)
        return {**self._envelope(item),
                "fields": [{"name": n, "type": t} for n, t in self._columns_for(item.kind)]}

    def _affordances(self, handle: str) -> dict[str, Any]:
        item = self._get(handle)
        relations = [{"name": link.name, "target_kind": link.target_kind}
                     for link in _LINKS if link.source_kind == item.kind
                     and link.target_kind in self._kinds]
        return {**self._envelope(item),
                "filter_fields": [n for n, _ in self._columns_for(item.kind)],
                "filter_operators": list(_OPERATORS), "relations": relations}

    def _follow(self, handle: str, relation: str) -> dict[str, Any]:
        item = self._get(handle)
        links = [link for link in _LINKS
                 if link.source_kind == item.kind and link.name == relation
                 and link.target_kind in self._kinds]
        if len(links) != 1:
            raise _PolicyError("RELATION_NOT_ALLOWED", "relationship is not available for this handle")
        link = links[0]
        source_fields = {n for n, _ in self._columns_for(item.kind)}
        target_fields = {n for n, _ in self._columns_for(link.target_kind)}
        if link.source_field not in source_fields or link.target_field not in target_fields:
            raise _PolicyError("RELATION_UNAVAILABLE", "relationship fields are not present")
        try:
            edge_relation = self._resolve_relation(link.edge_dataset, link.edge_view)
            if not edge_relation:
                raise LookupError(link.edge_dataset)
            edge_fields = {str(row[0]) for row in self._lake.con.execute(
                f'DESCRIBE SELECT * FROM {self._relation_sql(edge_relation)}').fetchall()}
        except Exception as exc:
            raise _PolicyError("RELATION_UNAVAILABLE", "relationship dataset is not present") from exc
        if link.edge_source_field not in edge_fields or link.edge_target_field not in edge_fields:
            raise _PolicyError("RELATION_UNAVAILABLE", "relationship fields are not present")
        step = {"operation": "follow", "relation": link.name,
                "from_kind": item.kind, "to_kind": link.target_kind,
                "edge_dataset": link.edge_dataset}
        new = self._new(kind=link.target_kind, lineage=(*item.lineage, step),
                        parent=item.handle, link=link)
        return self._envelope(new)

    def _inspect(self, handle: str, fields: list[str] | None = None,
                 limit: int = 20) -> dict[str, Any]:
        item = self._get(handle)
        available = [n for n, _ in self._columns_for(item.kind)]
        chosen = available if fields is None else fields
        if (not isinstance(chosen, list) or not chosen or len(chosen) > MAX_FIELDS
                or any(not isinstance(f, str) or f not in available for f in chosen)):
            raise _PolicyError("FIELD_NOT_ALLOWED", "inspect fields are not available")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_INSPECT_ROWS:
            raise _PolicyError("INVALID_ARGUMENTS", f"limit must be 1 to {MAX_INSPECT_ROWS}")
        source, params = self._render(item)
        cols = ", ".join(f'"{f}"' for f in chosen)
        cur = self._lake.con.execute(
            f'SELECT {cols} FROM ({source}) AS object_set '
            f'ORDER BY "{_BY_KIND[item.kind].key}" LIMIT ?', [*params, limit + 1])
        raw = cur.fetchall()
        objects: list[dict[str, Any]] = []
        used = 2
        byte_truncated = False
        for row in raw[:limit]:
            obj = {field: self._json_value(value) for field, value in zip(chosen, row)}
            size = len(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")) + 1
            if used + size > MAX_INSPECT_BYTES:
                if not objects:
                    raise _PolicyError(
                        "RESULT_TOO_LARGE", "one object exceeds the inspection byte limit; select fewer fields")
                byte_truncated = True
                break
            objects.append(obj)
            used += size
        truncated = len(raw) > limit or byte_truncated
        return {**self._envelope(item), "objects": objects,
                "count": len(objects), "truncated": truncated,
                "truncated_reason": ("byte limit" if byte_truncated else
                                     "row limit" if len(raw) > limit else "")}

    def _find_threads(self, event_type_code: str | None = None,
                      limit: int = 20) -> dict[str, Any]:
        item = self._new(
            kind="NEWS_THREAD",
            lineage=self._initial_lineage("NEWS_THREAD"),
        )
        if event_type_code is not None:
            self._event_type(event_type_code)
            item = self._sets[self._filter(
                item.handle, "event_type_code", "eq", event_type_code)["handle"]]
        inspected = self._inspect(
            item.handle, ["thread_id", "event_type_code", "opened_at"], limit)
        objects = inspected.pop("objects")
        inspected.pop("count")
        inspected.pop("truncated_reason")
        rendered, rendered_params = self._render(item)
        final_threads = int(self._lake.con.execute(
            f"SELECT count(*) FROM ({rendered}) AS final_threads",
            rendered_params).fetchone()[0])
        counts = self._scope_counts(final_threads)
        if counts:
            counts["delivered_threads"] = len(objects)
            record("news.scope", **counts, relationship_lineage=[
                "etf_holding_snapshot", "event_argument", "source_event",
                "event_evidence", "document_assertion", "document",
                "event_thread_link",
            ])
        return {**inspected, "threads": objects, **({"scope_counts": counts} if counts else {})}

    def _get_thread(self, thread_id: str) -> dict[str, Any]:
        if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 200:
            raise _PolicyError("INVALID_ARGUMENTS", "thread_id must be 1 to 200 characters")
        created = self._new(
            kind="NEWS_THREAD",
            lineage=self._initial_lineage("NEWS_THREAD"),
        )
        handle = self._filter(created.handle, "thread_id", "eq", thread_id)["handle"]
        inspected = self._inspect(
            handle, ["thread_id", "event_type_code", "opened_at"], 1)
        rows = inspected.pop("objects")
        inspected.pop("count")
        inspected.pop("truncated_reason")
        return {**inspected, "thread": rows[0] if rows else None}

    def _list_events(self, handle: str, limit: int = 20) -> dict[str, Any]:
        self._get_kind(handle, "NEWS_THREAD")
        event_handle = self._follow(handle, "EVENTS")["handle"]
        inspected = self._inspect(
            event_handle,
            ["source_event_id", "event_type_code", "available_at"], limit)
        rows = inspected.pop("objects")
        inspected.pop("count")
        inspected.pop("truncated_reason")
        return {**inspected, "events": rows}

    def _get_event_arguments(self, handle: str, limit: int = 20) -> dict[str, Any]:
        self._get_kind(handle, "NEWS_EVENT")
        argument_handle = self._follow(handle, "ARGUMENTS")["handle"]
        fields = [
            "event_argument_id", "source_event_id", "role_code", "slot",
            "mention_text", "entity_kind", "entity_id", "confidence",
        ]
        inspected = self._inspect(argument_handle, fields, limit)
        rows = inspected.pop("objects")
        inspected.pop("count")
        inspected.pop("truncated_reason")
        arguments = [
            {**{("object_kind" if key == "entity_kind" else key): value
                for key, value in row.items()},
             "resolved": row["entity_id"] is not None}
            for row in rows
        ]
        return {**inspected, "arguments": arguments}

    def _event_type(self, event_type_code: str):
        if (not isinstance(event_type_code, str) or not event_type_code
                or len(event_type_code) > 120):
            raise _PolicyError(
                "INVALID_ARGUMENTS", "event_type_code must be 1 to 120 characters")
        event_type = self._processes.get(event_type_code)
        if event_type is None:
            raise _PolicyError("EVENT_TYPE_NOT_ALLOWED", "event type is not in the ontology")
        return event_type

    def _describe_event_schema(self, event_type_code: str) -> dict[str, Any]:
        event_type = self._event_type(event_type_code)
        required = set(event_type.required_roles)
        arguments = []
        for role_code in (*event_type.required_roles, *event_type.optional_roles):
            object_kind = self._relation_vocabulary.kind_of(role_code)
            if object_kind is None:
                continue
            arguments.append({
                "role_code": role_code,
                "cardinality": "ONE_OR_MORE" if role_code in required else "ZERO_OR_MORE",
                "object_kind": object_kind,
                "slot": event_type.slot_of(role_code),
            })
        measures = [{
            "role_code": role_code,
            "cardinality": "ONE_OR_MORE" if attribute.required else "ZERO_OR_MORE",
            "dtype": attribute.dtype,
            "unit_family": attribute.unit_family,
            "basis": list(attribute.basis),
        } for role_code, attribute in event_type.quantities.items()]
        lineage = [{"operation": "describe_event_schema",
                    "event_type_code": event_type_code}]
        lineage_id = "lin_" + hashlib.sha256(json.dumps(
            lineage, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
        return {
            "ok": True,
            "as_of": self.as_of,
            "dataset": {"name": "ontology", "version": self._processes.version},
            "ontology_version": self._processes.version,
            "event_type_code": event_type_code,
            "arguments": arguments,
            "measures": measures,
            "pit": {"clamp": None, "gaps": []},
            "lineage_id": lineage_id,
            "lineage": lineage,
        }

    def _follow_argument(self, handle: str, event_argument_id: int) -> dict[str, Any]:
        self._get_kind(handle, "EVENT_ARGUMENT")
        if (not isinstance(event_argument_id, int) or isinstance(event_argument_id, bool)
                or event_argument_id < 1):
            raise _PolicyError("INVALID_ARGUMENTS", "event_argument_id must be a positive integer")
        narrowed = self._filter(
            handle, "event_argument_id", "eq", event_argument_id)["handle"]
        inspected = self._inspect(
            narrowed,
            ["event_argument_id", "source_event_id", "role_code", "entity_kind",
             "mention_text", "entity_id"],
            1,
        )
        if not inspected["objects"]:
            raise _PolicyError("ARGUMENT_NOT_FOUND", "argument is not in this handle")
        row = inspected["objects"][0]
        argument = {
            "event_argument_id": row["event_argument_id"],
            "source_event_id": row["source_event_id"],
            "role_code": row["role_code"],
            "object_kind": row["entity_kind"],
            "mention_text": row["mention_text"],
            "entity_id": row["entity_id"],
        }
        if row["entity_id"] is None:
            return {**self._envelope(self._sets[narrowed]), "resolved": False,
                    "reason": "UNRESOLVED_ARGUMENT", "argument": argument,
                    "objects": []}
        declared_kind = str(row["entity_kind"] or "")
        # Entity-resolution stores listed-company arguments in the instrument
        # namespace; COMPANY_ENTITY is the ontology role kind, not an actor_id.
        kind = "ISSUER" if declared_kind == "COMPANY_ENTITY" else declared_kind
        if kind not in self._kinds:
            raise _PolicyError("OBJECT_KIND_UNAVAILABLE", "resolved object kind is not available")
        target = self._new(
            kind=kind,
            lineage=(*self._sets[narrowed].lineage, {
                "operation": "follow_argument", "event_argument_id": event_argument_id,
                "to_kind": kind,
            }),
        )
        key = _BY_KIND[kind].key
        target_handle = self._filter(target.handle, key, "eq", row["entity_id"])["handle"]
        available = {name for name, _ in self._columns_for(kind)}
        fields = [key] + (["display_name"] if "display_name" in available else [])
        objects = self._inspect(target_handle, fields, 1)["objects"]
        return {**self._envelope(self._sets[target_handle]), "resolved": bool(objects),
                **({} if objects else {"reason": "TARGET_NOT_AVAILABLE"}),
                "argument": argument, "objects": objects}

    def _get_event_evidence(self, handle: str, limit: int = 20) -> dict[str, Any]:
        self._get_kind(handle, "NEWS_EVENT")
        evidence_handle = self._follow(handle, "EVIDENCE")["handle"]
        inspected = self._inspect(
            evidence_handle,
            ["evidence_id", "source_event_id", "evidence_type", "evidence_text",
             "link_confidence"],
            limit,
        )
        rows = inspected.pop("objects")
        inspected.pop("count")
        inspected.pop("truncated_reason")
        return {**inspected, "evidence": rows}

    def _render(self, item: _Set) -> tuple[str, list[Any]]:
        params: list[Any] = []
        binding = _BY_KIND[item.kind]
        target_relation = self._relation_sql(self._relations[item.kind])
        if item.link is None:
            if item.kind == "NEWS_THREAD":
                link_name = self._resolve_relation(
                    "event_thread_link", "v_event_thread_link")
                event_name = self._resolve_relation("source_event", "v_source_event")
                if not link_name or not event_name:
                    raise _PolicyError(
                        "RELATION_UNAVAILABLE", "news thread knowledge boundary is not present")
                link_relation = self._relation_sql(link_name)
                event_relation = self._relation_sql(event_name)
                source = (
                    f'SELECT DISTINCT target.* FROM {target_relation} AS target '
                    f'JOIN {link_relation} AS knowledge_link '
                    f'ON knowledge_link."thread_id" = target."thread_id" '
                    f'JOIN {event_relation} AS knowledge_event '
                    f'ON knowledge_event."source_event_id" = knowledge_link."source_event_id" '
                    f'WHERE knowledge_link."evaluated_at" <= ? '
                    f'AND knowledge_event."available_at" <= ? '
                    f'AND knowledge_event."source_class" = ? '
                    f'AND knowledge_event."event_status" = ?')
                params.extend((self.as_of.replace("T", " "),
                               self.as_of.replace("T", " "), "NEWS", "ACTIVE"))
                scope_predicate, scope_params = self._scope_event_predicate("knowledge_event")
                source += scope_predicate
                params.extend(scope_params)
                if self._news_scope is None:
                    document_predicate, document_params = self._document_event_predicate(
                        "knowledge_event")
                    source += document_predicate
                    params.extend(document_params)
            else:
                source = f'SELECT * FROM {target_relation}'
            if binding.fixed_field:
                marks = ",".join("?" for _ in binding.fixed_values)
                joiner = " AND" if item.kind == "NEWS_THREAD" else " WHERE"
                source += f'{joiner} "{binding.fixed_field}" IN ({marks})'
                params.extend(binding.fixed_values)
        else:
            parent = self._get(item.parent)
            parent_source, parent_params = self._render(parent)
            params.extend(parent_params)
            edge_relation = self._relation_sql(
                self._resolve_relation(item.link.edge_dataset, item.link.edge_view))
            source = (f'SELECT DISTINCT target.* FROM {target_relation} AS target '
                      f'JOIN {edge_relation} AS edge '
                      f'ON target."{item.link.target_field}" = edge."{item.link.edge_target_field}" '
                      f'JOIN ({parent_source}) AS source '
                      f'ON edge."{item.link.edge_source_field}" = source."{item.link.source_field}"')
            scoped_where = False
            if item.kind == "NEWS_EVENT":
                source += ' WHERE target."event_status" = ?'
                params.append("ACTIVE")
                if self._news_scope is not None:
                    scope_predicate, scope_params = self._scope_event_predicate("target")
                    source += scope_predicate
                    params.extend(scope_params)
                else:
                    document_predicate, document_params = self._document_event_predicate("target")
                    source += document_predicate
                    params.extend(document_params)
                scoped_where = True
            if item.kind == "EVENT_EVIDENCE":
                assertion = self._scope_relation("document_assertion")
                document = self._scope_relation("document")
                cutoff = self.as_of.replace("T", " ")
                source += f''' WHERE EXISTS (
                    SELECT 1 FROM {assertion} AS evidence_assertion
                    JOIN {document} AS evidence_document
                      ON evidence_document."document_id" = evidence_assertion."document_id"
                    WHERE evidence_assertion."assertion_id" = target."assertion_id"
                      AND evidence_assertion."available_at" <= ?
                      AND evidence_document."available_at" <= ?)'''
                params.extend((cutoff, cutoff))
                scoped_where = True
            edge_clamp = getattr(self._lake, "bound", {}).get(item.link.edge_dataset)
            if edge_clamp:
                source += (" AND" if scoped_where else " WHERE") + f' edge."{edge_clamp}" <= ?'
                params.append(self.as_of.replace("T", " ") if edge_clamp.endswith("_at")
                              else self._as_of_date)
            if binding.fixed_field:
                marks = ",".join("?" for _ in binding.fixed_values)
                source = (f'SELECT * FROM ({source}) AS typed '
                          f'WHERE "{binding.fixed_field}" IN ({marks})')
                params.extend(binding.fixed_values)
        clauses: list[str] = []
        clamp = getattr(self._lake, "bound", {}).get(binding.dataset)
        if clamp:
            clauses.append(f'"{clamp}" <= ?')
            params.append(self.as_of.replace("T", " ") if clamp.endswith("_at")
                          else self._as_of_date)
        op_sql = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
        for flt in item.filters:
            if flt.operator == "in":
                clauses.append(f'"{flt.field}" IN (' + ",".join("?" for _ in flt.value) + ")")
                params.extend(flt.value)
            else:
                clauses.append(f'"{flt.field}" {op_sql[flt.operator]} ?')
                params.append(flt.value)
        if clauses:
            source = f"SELECT * FROM ({source}) AS filtered WHERE " + " AND ".join(clauses)
        return source, params

    def _envelope(self, item: _Set) -> dict[str, Any]:
        binding = _BY_KIND[item.kind]
        clamp = getattr(self._lake, "bound", {}).get(binding.dataset)
        gaps: list[str] = []
        kinds = {str(step.get("kind") or step.get("to_kind")) for step in item.lineage}
        kinds.discard("None")
        for kind in sorted(kinds):
            dataset = _BY_KIND[kind].dataset
            if not getattr(self._lake, "bound", {}).get(dataset):
                gaps.append(f"NO_PIT_COLUMN:{dataset}")
            if not self._versions.get(dataset):
                gaps.append(f"NO_DATASET_VERSION:{dataset}")
        for step in item.lineage:
            if edge_dataset := step.get("edge_dataset"):
                if not getattr(self._lake, "bound", {}).get(edge_dataset):
                    gaps.append(f"NO_PIT_COLUMN:{edge_dataset}")
                if not self._versions.get(edge_dataset):
                    gaps.append(f"NO_DATASET_VERSION:{edge_dataset}")
        lineage_id = "lin_" + hashlib.sha256(json.dumps(
            item.lineage, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]
        return {"ok": True, "handle": item.handle, "kind": item.kind,
                "as_of": self.as_of,
                "dataset": {"name": binding.dataset,
                            "version": self._versions.get(binding.dataset)},
                "pit": {"clamp": clamp, "gaps": sorted(set(gaps))},
                "lineage_id": lineage_id, "lineage": list(item.lineage)}

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (type(None), bool, int, float, str)):
            return value
        return str(value)


__all__ = ["MAX_FIELDS", "MAX_FILTER_STRING", "MAX_INSPECT_BYTES",
           "MAX_INSPECT_ROWS", "NewsScope", "ObjectSetRuntime"]
