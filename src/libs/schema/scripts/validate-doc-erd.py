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


def parse_svg_relations(
    svg_path: Path,
    tables_by_id: dict[str, str],
) -> tuple[Counter, list[str]]:
    root = ET.parse(svg_path).getroot()
    elements = list(root.iter())
    table_bounds = {}
    for group in elements:
        group_id = group.get("id", "")
        if not group_id.startswith("table-"):
            continue
        rect = next(
            (
                child
                for child in group
                if child.tag.endswith("rect") and child.get("class") == "table"
            ),
            None,
        )
        if rect is not None:
            table_bounds[group_id.removeprefix("table-")] = tuple(
                float(rect.get(attribute, "0"))
                for attribute in ("x", "y", "width", "height")
            )

    drawio_groups = {
        element.get("data-cell-id"): element
        for element in elements
        if element.get("data-cell-id")
    }
    for table_id, table_name in tables_by_id.items():
        group = drawio_groups.get(table_id)
        if group is None:
            continue
        outline_paths = [
            element for element in group.iter() if element.tag.endswith("path")
        ][:2]
        numbers = [
            float(value)
            for path in outline_paths
            for value in re.findall(r"-?\d+(?:\.\d+)?", path.get("d", ""))
        ]
        points = list(zip(numbers[::2], numbers[1::2]))
        if points:
            xs, ys = zip(*points)
            table_bounds[table_name] = (
                min(xs),
                min(ys),
                max(xs) - min(xs),
                max(ys) - min(ys),
            )

    paths = [
        element
        for element in elements
        if element.tag.endswith("path") and element.get("class") == "relation"
    ]
    labels = [
        "".join(element.itertext())
        for element in elements
        if element.tag.endswith("text") and element.get("class") == "cardinality"
    ]
    errors = []
    if len(labels) != len(paths) * 2:
        errors.append(
            f"SVG has {len(paths)} relationship paths but {len(labels)} cardinality labels"
        )
        return Counter(), errors

    def endpoint_table(point: tuple[float, float], tolerance: float = 0.01) -> str | None:
        px, py = point
        matches = []
        for table, (x, y, width, height) in table_bounds.items():
            on_horizontal = x - tolerance <= px <= x + width + tolerance and (
                abs(py - y) <= tolerance or abs(py - y - height) <= tolerance
            )
            on_vertical = y - tolerance <= py <= y + height + tolerance and (
                abs(px - x) <= tolerance or abs(px - x - width) <= tolerance
            )
            if on_horizontal or on_vertical:
                matches.append(table)
        return matches[0] if len(matches) == 1 else None

    relations = Counter()
    for index, path in enumerate(paths):
        numbers = [
            float(value)
            for value in re.findall(r"-?\d+(?:\.\d+)?", path.get("d", ""))
        ]
        points = list(zip(numbers[::2], numbers[1::2]))
        if len(points) < 2:
            errors.append(f"SVG relationship {index + 1} has no usable path")
            continue
        child = endpoint_table(points[0])
        parent = endpoint_table(points[-1])
        if child is None or parent is None:
            errors.append(
                f"SVG relationship {index + 1} does not terminate on exactly one table"
            )
            continue
        relations[(child, parent, labels[index * 2], labels[index * 2 + 1])] += 1

    if paths:
        return relations, errors

    for edge_id, edge in sorted(
        (
            item
            for item in drawio_groups.items()
            if re.fullmatch(r"relation-\d+", item[0])
        ),
        key=lambda item: int(item[0].removeprefix("relation-")),
    ):
        edge_paths = [element for element in edge.iter() if element.tag.endswith("path")]
        if not edge_paths:
            errors.append(f"SVG {edge_id} has no usable path")
            continue
        line_numbers = [
            float(value)
            for value in re.findall(r"-?\d+(?:\.\d+)?", edge_paths[0].get("d", ""))
        ]
        line_points = list(zip(line_numbers[::2], line_numbers[1::2]))
        if len(line_points) < 2:
            errors.append(f"SVG {edge_id} has no usable path")
            continue
        arrow_numbers = (
            [
                float(value)
                for value in re.findall(
                    r"-?\d+(?:\.\d+)?", edge_paths[1].get("d", "")
                )
            ]
            if len(edge_paths) > 1
            else []
        )
        child = endpoint_table(line_points[0], 2)
        parent = endpoint_table(
            (arrow_numbers[0], arrow_numbers[1]) if len(arrow_numbers) >= 2 else line_points[-1],
            2,
        )
        child_group = drawio_groups.get(f"{edge_id}-child")
        parent_group = drawio_groups.get(f"{edge_id}-parent")
        child_label = (
            "".join(child_group.itertext()).strip() if child_group is not None else ""
        )
        parent_label = (
            "".join(parent_group.itertext()).strip() if parent_group is not None else ""
        )
        if child is None or parent is None:
            errors.append(f"SVG {edge_id} does not terminate on exactly one table")
            continue
        relations[(child, parent, child_label, parent_label)] += 1
    return relations, errors


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
        drawio_relations = Counter()
        for cell in cells.values():
            if (
                cell.get("edge") != "1"
                or cell.get("source") not in tables_by_id
                or cell.get("target") not in tables_by_id
            ):
                continue
            edge_id = cell.get("id")
            child_label = cells.get(f"{edge_id}-child")
            parent_label = cells.get(f"{edge_id}-parent")
            if child_label is None or parent_label is None:
                errors.append(f"{domain}: {edge_id} is missing cardinality labels")
                continue
            drawio_relations[
                (
                    tables_by_id[cell.get("source")],
                    tables_by_id[cell.get("target")],
                    child_label.get("value", ""),
                    parent_label.get("value", ""),
                )
            ] += 1

        svg_relations, svg_errors = parse_svg_relations(svg_path, tables_by_id)
        errors.extend(f"{domain}: {error}" for error in svg_errors)
        missing_svg_relations = drawio_relations - svg_relations
        extra_svg_relations = svg_relations - drawio_relations
        if missing_svg_relations:
            errors.append(f"{domain}: SVG missing relationships {dict(missing_svg_relations)}")
        if extra_svg_relations:
            errors.append(f"{domain}: SVG has stale relationships {dict(extra_svg_relations)}")

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
