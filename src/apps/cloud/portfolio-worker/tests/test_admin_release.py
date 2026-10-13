import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_release_requires_a_separate_private_build(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace())
    spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'deploy/release.py')
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    monkeypatch.setattr(release, 'ROOT', tmp_path / 'portfolio-worker')
    with pytest.raises(RuntimeError, match='private administrator UI'):
        release.admin_ui_directory()
    private = tmp_path / 'portfolio-ui/dist-admin'
    (private / 'assets').mkdir(parents=True)
    (private / 'admin.html').write_text('<html>private</html>')
    assert release.admin_ui_directory() == private
