"""The public package layout must not depend on retired source copies or modules."""
import importlib
from pathlib import Path

import edge_analysis_v2
from edge_analysis_v2.contracts.screen_validation import contract_info


def test_role_packages_have_no_legacy_root_modules_or_contract_copies():
    root = Path(edge_analysis_v2.__file__).parent
    assert {p.name for p in root.glob('*.py')} == {'__init__.py'}
    assert not (root/'contracts'/'sources').exists()
    assert all('snapshot' not in source for source in contract_info()['sources'])
    for name in ('analysis.service','agent.runner','tools.fixture_data','storage.publications',
                 'contracts.screen_validation','quality.audit','prompts.versions','dashboard.server'):
        importlib.import_module('edge_analysis_v2.'+name)
    assert (root/'dashboard/static/index.html').is_file()
    assert (root/'dashboard/static/prompts.js').is_file()
