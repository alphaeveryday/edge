"""공개 데이터 생성 규칙."""
from datetime import datetime, timezone
import json
from pathlib import Path

from edge_truth_agent import site
from edge_truth_agent.cases import load_dir

SEED = Path(__file__).resolve().parents[1] / "seed"
NOW = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)


def test_public_files_hide_private_snapshot_paths_and_internal_flags():
    # 비공개 버킷 경로가 공개 JSON 에 실리면 내부 저장 구조가 드러난다.
    cases = load_dir(SEED)
    cases[0]["sources"][0]["snapshot"] = "snapshots/abc.json"
    files = site.build(cases, NOW)
    body = b"".join(files.values()).decode("utf-8")
    assert "snapshots/abc.json" not in body
    assert '"locked"' not in body


def test_index_has_one_summary_per_case_with_types():
    files = site.build(load_dir(SEED), NOW)
    index = json.loads(files["index.json"])
    assert [c["id"] for c in index["cases"]] == sorted(p.stem for p in SEED.glob("*.json"))
    geumyang = next(c for c in index["cases"] if c["id"] == "geumyang-monlaa")
    assert geumyang["types"] == ["fabricated"] and geumyang["resolvedAt"] == "2026-10-07"
    assert set(files) == {"index.json"} | {f"cases/{c['id']}.json" for c in index["cases"]}


def test_build_refuses_nan_instead_of_publishing_unreadable_json():
    import math
    import pytest
    cases = load_dir(SEED)
    cases[0]["impact"] = math.nan
    with pytest.raises(ValueError):
        site.build(cases, NOW)


def test_build_is_deterministic():
    # 매시 실행에서 내용이 같으면 같은 바이트여야 바뀐 파일만 다시 올릴 수 있다.
    cases = load_dir(SEED)
    assert site.build(cases, NOW) == site.build(list(reversed(cases)), NOW)
