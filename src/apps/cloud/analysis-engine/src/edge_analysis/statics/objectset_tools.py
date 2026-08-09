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
)
_BY_KIND = {binding.kind: binding for binding in _BINDINGS}


_LINKS = (
    _Link("ISSUER", "COMPANY_ENTITY", "instrument_id", "actor_id", "ISSUER",
          "v_equity_profile", "equity_profile", "instrument_id", "issuer_actor_id"),
    _Link("COMPANY_ENTITY", "ISSUER", "actor_id", "instrument_id", "ISSUED_SECURITY",
          "v_equity_profile", "equity_profile", "issuer_actor_id", "instrument_id"),
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
                 dataset_versions: dict[str, str] | None = None) -> None:
        if not _AS_OF.fullmatch(as_of):
            raise ValueError("as_of must be a local YYYY-MM-DDTHH:MM:SS timestamp")
        cutoff = datetime.fromisoformat(as_of)
        self._lake = lake
        self.as_of = cutoff.isoformat()
        self._as_of_date = cutoff.date().isoformat()
        self._versions = dict(dataset_versions or {})
        self._sets: dict[str, _Set] = {}
        self._columns: dict[str, tuple[tuple[str, str], ...]] = {}
        self._relations: dict[str, str] = {}
        self._dataset_relations: dict[str, str] = {}
        for binding in _BINDINGS:
            if relation := self._resolve_relation(binding.dataset, binding.view):
                self._relations[binding.kind] = relation
        self._kinds = tuple(sorted(self._relations))
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
        return [
            {"name": "objectset.create",
             "description": "Create an immutable set for one available object kind. The server pins the analysis clock.",
             "input_schema": _schema(
                 {"kind": {"type": "string", "enum": list(self._kinds)}}, ("kind",))},
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
        }
        try:
            if name not in operations:
                raise _PolicyError("TOOL_NOT_ALLOWED", "tool is not available")
            if not isinstance(arguments, dict):
                raise _PolicyError("INVALID_ARGUMENTS", "arguments must be an object")
            fn, allowed, required = operations[name]
            if unknown := sorted(set(arguments) - allowed):
                raise _PolicyError("INVALID_ARGUMENTS", "unknown arguments: " + ", ".join(unknown))
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

    def _create(self, kind: str) -> dict[str, Any]:
        if not isinstance(kind, str):
            raise _PolicyError("INVALID_ARGUMENTS", "kind must be a string")
        self._columns_for(kind)
        item = self._new(kind=kind, lineage=({"operation": "create", "kind": kind},))
        return self._envelope(item)

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

    def _render(self, item: _Set) -> tuple[str, list[Any]]:
        params: list[Any] = []
        binding = _BY_KIND[item.kind]
        target_relation = self._relation_sql(self._relations[item.kind])
        if item.link is None:
            source = f'SELECT * FROM {target_relation}'
            if binding.fixed_field:
                marks = ",".join("?" for _ in binding.fixed_values)
                source += f' WHERE "{binding.fixed_field}" IN ({marks})'
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
            edge_clamp = getattr(self._lake, "bound", {}).get(item.link.edge_dataset)
            if edge_clamp:
                source += f' WHERE edge."{edge_clamp}" <= ?'
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
           "MAX_INSPECT_ROWS", "ObjectSetRuntime"]
