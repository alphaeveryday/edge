import pytest
from edge_analysis_v2.prompts.versions import PromptVersions, PromptConflict


def test_version_comparison_is_read_only_and_identifies_changed_lines(tmp_path):
    source = tmp_path/'prompts'
    source.mkdir()
    (source/'outlook.yaml').write_text('system_prompt: |\n  keep\n  old\n', encoding='utf-8')
    store = PromptVersions(source, tmp_path/'history')
    old = store.read('outlook')
    active = store.save('outlook','system_prompt: |\n  keep\n  new\n  added\n',old['version'],'edit')
    result = store.compare('outlook',old['version'])
    assert result['removed'] == 1 and result['added'] == 2
    assert ''.join(r['old']['text'] for r in result['rows'] if r['old']) == old['yaml']
    assert ''.join(r['new']['text'] for r in result['rows'] if r['new']) == active['yaml']
    assert result['rows'][0]['kind'] == 'equal'
    assert store.read('outlook') == active
    assert store.compare('outlook',active['version'])['added'] == 0
    with pytest.raises(ValueError):
        store.compare('outlook','missing')


def test_save_restore_and_stale_editor_never_overwrites_newer_prompt(tmp_path):
    source = tmp_path/'prompts'
    source.mkdir()
    (source/'outlook.yaml').write_text('system_prompt: |\n  original\n', encoding='utf-8')
    store = PromptVersions(source, tmp_path/'history')
    first = store.read('outlook')
    second = store.save('outlook', 'system_prompt: |\n  changed\n', first['version'], 'edit')
    assert store.read('outlook')['system_prompt'] == 'changed\n'
    with pytest.raises(PromptConflict):
        store.save('outlook', first['yaml'], first['version'], 'stale')
    restored = store.save('outlook', first['yaml'], second['version'], 'restore')
    assert restored['version'] == first['version']
    assert len(store.read('outlook')['history']) == 3
    assert (source/'outlook.yaml').read_text(encoding='utf-8') == first['yaml']


@pytest.mark.parametrize('text', ['system_prompt: ""', 'system_prompt: 1', 'system_prompt: a\nsystem_prompt: b', 'system_prompt: okay\nextra: hidden', '!!python/object:evil {}'])
def test_invalid_yaml_cannot_replace_active_prompt(tmp_path, text):
    source = tmp_path/'prompts'
    source.mkdir()
    path = source/'outlook.yaml'
    path.write_text('system_prompt: original', encoding='utf-8')
    store = PromptVersions(source, tmp_path/'history')
    first = store.read('outlook')
    with pytest.raises(ValueError):
        store.save('outlook', text, first['version'], '')
    assert store.read('outlook')['version'] == first['version']
    with pytest.raises(ValueError):
        store.read('../outlook')
