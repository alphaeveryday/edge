#!/usr/bin/env python3
"""Validate domain documentation ERDs against the Flyway-derived Cloud DBML."""

from collections import Counter
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[4]
DBML_PATH = ROOT / "src/libs/schema/generated/physical-erd.dbml"
DOMAINS_PATH = ROOT / "docs/data-model/domains"


def parse_dbml(text: str) -> tuple[set[str], list[tuple[str, str]]]:
    tables = set(re.findall(r'^Table "([^"]+)"', text, re.MULTILINE))
    ref_pattern = re.compile(
        r'Ref:\s+"([^"]+)"(?:\."([^"]+)"|\.\(([^)]+)\))\s+>\s+'
        r'"([^"]+)"(?:\."([^"]+)"|\.\(([^)]+)\))'
    )
    refs = [(match.group(1), match.group(4)) for match in ref_pattern.finditer(text)]
    return tables, refs


def validate_domain(
    drawio_path: Path,
    dbml_refs: list[tuple[str, str]],
) -> tuple[set[str], list[str]]:
    domain = drawio_path.parent.name
    cells = {cell.get("id"): cell for cell in ET.parse(drawio_path).findall(".//mxCell")}
    tables_by_id = {
        cell_id: cell.get("value")
        for cell_id, cell in cells.items()
        if cell.get("vertex") == "1" and "swimlane" in cell.get("style", "")
    }
    tables = set(tables_by_id.values())

    actual_refs = Counter(
        (tables_by_id.get(cell.get("source")), tables_by_id.get(cell.get("target")))
        for cell in cells.values()
        if cell.get("edge") == "1"
        and cell.get("source") in tables_by_id
        and cell.get("target") in tables_by_id
    )
    expected_refs = Counter(
        (child, parent)
        for child, parent in dbml_refs
        if child in tables and parent in tables
    )

    errors = []
    missing_refs = expected_refs - actual_refs
    extra_refs = actual_refs - expected_refs
    if missing_refs:
        errors.append(f"{domain}: missing FK edges {dict(missing_refs)}")
    if extra_refs:
        errors.append(f"{domain}: non-DBML edges {dict(extra_refs)}")

    svg_path = drawio_path.with_suffix(".svg")
    if not svg_path.is_file():
        errors.append(f"{domain}: missing {svg_path.name}")
    else:
        svg = svg_path.read_text()
        missing_svg_tables = sorted(table for table in tables if table not in svg)
        if missing_svg_tables:
            errors.append(f"{domain}: SVG missing tables {missing_svg_tables}")

    print(f"{domain}: tables={len(tables)} FK edges={sum(actual_refs.values())}")
    return tables, errors


def main() -> int:
    dbml_tables, dbml_refs = parse_dbml(DBML_PATH.read_text())
    covered_tables = set()
    errors = []

    drawio_paths = sorted(DOMAINS_PATH.glob("*/erd.drawio"))
    if not drawio_paths:
        errors.append("no domain draw.io files found")

    for drawio_path in drawio_paths:
        tables, domain_errors = validate_domain(drawio_path, dbml_refs)
        covered_tables.update(tables)
        errors.extend(domain_errors)

    uncovered_tables = sorted(dbml_tables - covered_tables)
    unknown_tables = sorted(covered_tables - dbml_tables)
    if uncovered_tables:
        errors.append(f"Cloud tables absent from all domain ERDs: {uncovered_tables}")
    if unknown_tables:
        errors.append(f"domain ERDs contain tables absent from Cloud DBML: {unknown_tables}")

    print(f"Cloud table coverage: {len(dbml_tables) - len(uncovered_tables)}/{len(dbml_tables)}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
