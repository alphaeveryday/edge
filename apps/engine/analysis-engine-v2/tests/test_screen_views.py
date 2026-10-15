"""Customer previews must preserve publication meaning, not expose audit internals."""

from edge_analysis_v2.dashboard.views.analysis import render_screen


def test_empty_movement_hides_summary_and_detail_entry():
    page = render_screen('movement', {'summary': None, 'items': []})
    assert '왜 움직였을까?' not in page
    assert '관련 이슈' not in page


def test_summary_preview_uses_analysis_title_without_rewriting_agent_summary():
    page = render_screen('outlook', {'outlook': {'direction':'하락'},
        'summary_card': {'title':'방향 문구', 'summary':'요약 본문'},
        'detail': {'title':'분석 제목', 'items':[]}}, 'summary')
    assert '<h2>분석 제목</h2>' in page
    assert '방향 문구' not in page and '요약 본문' in page


def test_movement_keeps_original_item_time_and_hides_evidence_ids():
    page = render_screen('movement', {'summary': '<script>bad</script>', 'items': [
        {'type': '수급', 'title_keyword': '기관', 'sentence': '설명', 'sentiment': 'negative',
         'source_as_of': '2026-09-21T09:30:00+09:00', 'tool_run_ids': ['secret-run']}],
        'publication': {'published_at': '2026-09-21T11:00:00+09:00', 'etf_code': 'ETF'}})
    assert '09:30' in page and '11:00' in page
    assert 'secret-run' not in page and '<script>' not in page
    assert '관련 이슈 1개' in page and '부정' in page
    summary = render_screen('movement', {'summary':'요약', 'items':[{'title_keyword':'사건', 'sentence':'설명'}]}, 'summary')
    assert 'data-feature="detail"' in summary


def test_outlook_renders_changes_emphasis_and_optional_conclusion_without_ids():
    page = render_screen('outlook', {'detail': {'title': '본문', 'items': [
        {'id': 'hidden-topic', 'title_keyword': '논점', 'sentences': [
            {'sentence': '새 내용', 'is_updated': True}], 'tool_run_ids': ['secret-run']}],
        'updates': {'date': '2026-09-21', 'items': [
            {'change_type': 'deleted', 'title_keyword': '삭제 전 제목', 'sentence': None}]}},
        'conclusion': {'title': '판단', 'supports': [{'label': '도움'}], 'burdens': [],
                       'sentence': '결론', 'change_condition': '달라질 조건'}})
    assert '<strong>새 내용</strong>' in page
    assert '삭제 · 삭제 전 제목' in page and 'None' not in page
    assert '달라질 조건' in page and '도움' in page
    assert 'hidden-topic' not in page and 'secret-run' not in page


def test_metrics_keep_zero_drop_null_preserve_date_and_never_infer_sticker():
    page = render_screen('outlook', {'수급': {'type': '수급', 'sticker': '하락',
        'headline': '하락 쪽이에요', 'analysis_at': '2026-09-21T08:30:00+09:00', 'metrics': [
            {'key': 'weighted_institution_net_amount_20d', 'value': None, 'observed_at': '2026-09-18'},
            {'key': 'weighted_foreign_net_amount_20d', 'value': 0, 'observed_at': '2026-09-18'},
            {'key': 'weighted_foreign_net_buy_streak', 'value': 5, 'observed_at': '2026-09-18'}]}}, 'factor_details')
    assert '0.0억 원' in page and '5일' in page
    assert '2026-09-18 기준' in page and '15:30' not in page
    assert '하락 쪽이에요' in page and 'metric featured' in page
    assert '구성종목 기관 20일' not in page


def test_missing_metrics_and_empty_updates_are_not_fabricated():
    assert '오늘 업데이트' not in render_screen('outlook', {'detail': {'items': [], 'updates': {'items': []}}})
    page = render_screen('outlook', {'차트': {'type': '차트', 'sticker': '상승',
        'headline': '상승 쪽이에요', 'metrics': []}}, 'factor_details')
    assert '표시할 지표 데이터가 없어요' in page


def test_factor_controls_open_details_for_the_same_parent_publication():
    page = render_screen('outlook', {'factors': [
        {'type': '차트', 'sticker': '상승', 'sentence': '설명'}]}, 'factors')
    assert 'data-factor="차트"' in page
