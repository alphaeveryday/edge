"""Publishing skill edits must not leave one document updated and the other missing."""
import importlib.util
from pathlib import Path

import pytest


def test_missing_source_preserves_existing_package_and_complete_sources_copy_exactly(tmp_path):
    path = Path(__file__).parents[1]/'scripts/sync_skills.py'
    spec = importlib.util.spec_from_file_location('sync_skills', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, target = tmp_path/'source', tmp_path/'target'
    for name in module.NAMES:
        (target/name).mkdir(parents=True)
        (target/name/'SKILL.md').write_bytes(b'previous')
    (source/module.NAMES[0]).mkdir(parents=True)
    (source/module.NAMES[0]/'SKILL.md').write_bytes(b'new first')
    with pytest.raises(FileNotFoundError):
        module.sync(source, target)
    assert all((target/name/'SKILL.md').read_bytes() == b'previous' for name in module.NAMES)
    (source/module.NAMES[1]).mkdir()
    (source/module.NAMES[1]/'SKILL.md').write_bytes(b'new second')
    module.sync(source, target)
    assert all((target/name/'SKILL.md').read_bytes() == (source/name/'SKILL.md').read_bytes() for name in module.NAMES)
