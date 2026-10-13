"""A benchmark score must name the release that produced it."""
import pytest

from edge_analysis_v2.quality.cq import release


def test_sources_changed_after_the_release_was_registered_are_refused_until_registered(tmp_path):
    # WHY: a score recorded under a release number must come from that release's code.
    source = tmp_path/'pkg'
    (source/'quality/cq').mkdir(parents=True)
    (source/'tools').mkdir()
    (source/'tools/tool.py').write_text('a = 1\n', encoding='utf8')
    (source/'quality/cq/measure.py').write_text('x = 1\n', encoding='utf8')
    manifest = tmp_path/'releases.json'
    assert release.bump('first', manifest, source)['version'] == 1
    (source/'tools/tool.py').write_bytes(b'a = 1\r\n')                                    # a Windows checkout is the same source
    (source/'quality/cq/measure.py').write_text('x = 2\n', encoding='utf8')         # measuring happens after the run
    (source/'dashboard.py').write_text('page = 1', encoding='utf8')                 # code a run never loads
    assert release.check(manifest, source)['version'] == 1
    (source/'tools/tool.py').write_text('a = 2\n', encoding='utf8')
    with pytest.raises(ValueError, match='Agent sources changed since release 1'):
        release.check(manifest, source)
    second = release.bump('tool returns two', manifest, source)
    assert second['version'] == 2 and [r['version'] for r in second['history']] == [1] and release.check(manifest, source)
    with pytest.raises(ValueError, match='Describe'):
        release.bump(' ', manifest, source)


def test_the_registered_history_is_readable_and_its_numbers_increase():
    # WHY: the manifest in this checkout is the record every stored score points to.
    value = release.release()
    assert value['version'] == value['history'][-1]['version'] + 1 and value['source_digest']
