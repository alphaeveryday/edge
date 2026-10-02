"""Check the native ERD against generated DBML and the listed v2 DDL migrations.

The DDL reader is deliberately scoped to these migrations, not a general SQL parser.
DBML independently checks table/column coverage and types; DDL supplies defaults,
nullability and keys omitted by the DBML exporter. No database credentials needed.
"""
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def normalized_type(value):
    return {"integer": "int", "timestamp with time zone": "timestamptz"}.get(value, value)


def validate(path):
    entities = json.loads(path.read_text(encoding="utf-8"))["collections"]
    tables = entities["tableEntities"]
    columns = entities["tableColumnEntities"]
    ddl = "\n".join((ROOT / "src/libs/schema/migrations-cloud" / name).read_text(encoding="utf-8")
                    for name in ("V202609281600__create_v2_analysis_storage.sql",
                                 "V202609282200__add_v2_factor_details.sql",
                                 "V202609301400__isolate_analysis_source.sql",
                                 "V202610021500__add_v2_execution_requests.sql"))
    expected, refs, uniques = {}, set(), set()
    for table, body in re.findall(r"CREATE TABLE (\w+) \((.*?)^\);", ddl, re.M | re.S):
        primary = re.search(r"PRIMARY KEY \(([^)]+)\)", body)
        primary = primary.group(1).replace(" ", "").split(",") if primary else []
        for name, kind, rest in re.findall(r"^    (\w+) (text\[\]|text|timestamptz|date|jsonb|integer|numeric)\b(.*)", body, re.M | re.I):
            kind = kind.lower()
            # A word boundary after text[] would exclude the closing brackets.
            if kind == "text" and rest.startswith("[]"):
                kind, rest = "text[]", rest[2:]
            pk = name in primary or "PRIMARY KEY" in rest
            default = re.search(r"DEFAULT ('[^']*'|\w+\(\))", rest)
            expected[f"{table}.{name}"] = (normalized_type(kind), pk or "NOT NULL" in rest,
                                            pk, default.group(1) if default else "")
            ref = re.search(r"REFERENCES (\w+)\((\w+)\)", rest)
            if ref:
                refs.add((f"{table}.{name}", f"{ref[1]}.{ref[2]}"))
            if re.search(r"\bUNIQUE\b", rest):
                uniques.add((table, (name,)))
        for key in re.findall(r"UNIQUE \(([^)]+)\)", body):
            uniques.add((table, tuple(key.replace(" ", "").split(","))))
    for table, name, kind in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+) (\w+);", ddl):
        expected[f"{table}.{name}"] = (normalized_type(kind), False, False, "")

    for table, name, kind, default in re.findall(r"ALTER TABLE (\w+)\s+ADD COLUMN (\w+) (TEXT) NOT NULL DEFAULT ('[^']*')", ddl):
        expected[f"{table}.{name}"] = (normalized_type(kind.lower()), True, False, default)

    actual = {key: (normalized_type(col["dataType"]), bool(col["options"] & 8),
                    bool(col["options"] & 2), col["default"])
              for key, col in columns.items()}
    errors = [f"{key}: ERD={actual.get(key)} DDL={expected.get(key)}"
              for key in sorted(actual.keys() | expected.keys()) if actual.get(key) != expected.get(key)]
    table_names = {t["name"] for t in tables.values()}
    if table_names != {key.split(".")[0] for key in expected}:
        errors.append("Native table coverage differs from DDL")
    actual_refs = {(child, parent) for rel in entities["relationshipEntities"].values()
                   for child, parent in zip(rel["end"]["columnIds"], rel["start"]["columnIds"], strict=True)}
    if actual_refs != refs:
        errors.append(f"FK columns differ: {actual_refs ^ refs}")
    actual_uniques = {(tables[index["tableId"]]["name"], tuple(
        columns[entities["indexColumnEntities"][key]["columnId"]]["name"]
        for key in index["indexColumnIds"]))
        for index in entities["indexEntities"].values() if index["unique"]}
    if actual_uniques != uniques:
        errors.append(f"Unique keys differ: {actual_uniques ^ uniques}")

    dbml = (ROOT / "src/libs/schema/generated/physical-erd.dbml").read_text(encoding="utf-8")
    generated = {}
    for table, body in re.findall(r'^Table "([^"]+)" \{(.*?)^}', dbml, re.M | re.S):
        if table in table_names:
            generated.update({f"{table}.{name}": normalized_type(kind)
                              for name, kind in re.findall(r'^  "([^"]+)" (\S+)', body, re.M)})
    if generated != {key: value[0] for key, value in expected.items()}:
        errors.append("Generated DBML column/type coverage differs from DDL")
    drawn = {f'{cell.get("parent")}.{cell.get("value").split(" : ")[0]}':
             normalized_type(cell.get("value").split(" : ")[1])
             for cell in ET.parse(HERE / "erd.drawio").iter("mxCell")
             if cell.get("parent") in table_names and " : " in cell.get("value", "")}
    if drawn != generated:
        errors.append("draw.io column/type coverage differs from DBML")
    svg_columns = {}
    for group in ET.parse(HERE / "erd.svg").iter("{http://www.w3.org/2000/svg}g"):
        table = group.get("id", "").removeprefix("table-")
        if table in table_names:
            for label in group.iter("{http://www.w3.org/2000/svg}text"):
                if " : " in (label.text or ""):
                    name, kind = label.text.split(" : ")
                    svg_columns[f"{table}.{name}"] = normalized_type(kind)
    if svg_columns != generated:
        errors.append("SVG column/type coverage differs from DBML")
    print(f"analysis-v2: {len(table_names)} tables, {len(expected)} columns, "
          f"{len(refs)} FKs, {len(uniques)} unique keys; {len(errors)} errors")
    return errors


if __name__ == "__main__":
    problems = validate(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "analysis-storage.erd")
    for problem in problems:
        print(problem)
    raise SystemExit(bool(problems))
