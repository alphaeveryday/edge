"""Readable customer previews of server-assembled publication contracts."""

from datetime import datetime
from html import escape

from edge_analysis_v2.analysis.body_editor import KST
from edge_analysis_v2.storage.factors import METRICS
from edge_analysis_v2.contracts.publication_validation import FACTORS


# Display units only; stored calculation values remain untouched.
METRIC_LABELS = {
    'ma20_distance_pct': ('20일선 대비 · 현재가 반영', '%'),
    'ma60_direction': ('60일선 방향 · 현재가 반영', ''),
    'new_closing_high_count_20d': ('20일 종가 신고가', '회'),
    'distance_from_52w_closing_high_pct': ('52주 최고 종가 대비', '%'),
    'turnover_ratio_previous_day': ('거래대금(전일)', '배'),
    'atr14_pct': ('변동성(ATR)', '%'),
    'usd_krw': ('원·달러 환율', '원'),
    'commodity_return_20d_pct': ('원자재 20일 변화', '%'),
    'kr_treasury_10y_yield': ('한국 국고채 10년', '%'),
    'us_treasury_10y_yield': ('미국 국채 10년', '%'),
    'brent_spot_usd': ('브렌트유 현물', '달러/배럴'),
    'days_until_policy_decision': ('다음 금리 결정', '일 뒤'),
    'weighted_per': ('구성종목 PER 가중평균', '배'),
    'weighted_pbr': ('구성종목 PBR 가중평균', '배'),
    'weighted_per_band_5y_pct': ('구성종목 PER 가중평균 5년 밴드', '%'),
    'distribution_yield_12m_pct': ('ETF 최근 12개월 분배율', '%'),
    'weighted_institution_net_amount_20d': ('구성종목 기관 20일 가중 순매수', '억 원'),
    'weighted_foreign_net_amount_20d': ('구성종목 외국인 20일 가중 순매수', '억 원'),
    'weighted_institution_net_buy_streak': ('기관 연속 순매수', '일'),
    'weighted_foreign_net_buy_streak': ('외국인 연속 순매수', '일'),
    'etf_units_change_20d_pct': ('ETF 발행좌수 20일 변화', '%'),
}


def text(value):
    return escape(str(value)) if value is not None else ''


def display_time(value):
    """Preserve date-only observations; convert actual timestamps to KST."""
    if not value or len(value) == 10:
        return text(value)
    return datetime.fromisoformat(value).astimezone(KST).strftime('%Y-%m-%d %H:%M KST')


def panel(title, body, *, css='paper'):
    return f'<section class="panel {css}"><h2>{text(title)}</h2>{body}</section>'


def badge(value):
    return f'<span class="badge">{text(value)}</span>'


def paragraphs(items):
    result = []
    for item in items:
        sentiment = {'positive': '긍정 ↑', 'neutral': '중립 —', 'negative': '부정 ↓'}.get(item.get('sentiment'))
        result.append('<div class="entry">')
        if item.get('type'):
            result.append(badge(item['type']))
        if sentiment:
            result.append(badge(sentiment))
        result.append('<h3>'+text(item['title_keyword'])+'</h3>')
        if 'sentences' in item:
            result.append('<ul>')
            for bullet in item['sentences']:
                sentence = text(bullet['sentence'])
                result.append('<li>'+('<strong>'+sentence+'</strong>' if bullet['is_updated'] else sentence)+'</li>')
            result.append('</ul>')
        else:
            result.append('<p>'+text(item.get('sentence'))+'</p>')
        if item.get('source_as_of'):
            result.append('<p class="muted">자료 '+display_time(item['source_as_of'])+' 기준</p>')
        result.append('</div>')
    return ''.join(result)


def factor_details(screens):
    parts = []
    for kind in FACTORS:
        screen = screens.get(kind)
        if screen is None:
            continue
        body = badge(screen['sticker'])+'<h3>'+text(screen['headline'])+'</h3>'
        if kind == '이슈':
            body += paragraphs(screen['items'])
        else:
            body += '<p class="muted">분석 '+display_time(screen.get('analysis_at'))+'</p>'
            lookup = {m['key']: m for m in screen['metrics'] if m['value'] is not None}
            cards = []
            for key in METRICS[kind]:
                if key not in lookup:
                    continue
                metric = lookup[key]
                label, unit = METRIC_LABELS[key]
                value = metric['value']
                if isinstance(value, (int, float)):
                    if unit == '억 원':
                        value /= 100_000_000
                    value = f'{value:,.0f}' if unit in ('회', '일', '일 뒤') else f'{value:,.1f}'
                subject = metric.get('subject') if key in ('commodity_return_20d_pct', 'days_until_policy_decision') else None
                label = (subject+' · ' if subject else '')+label
                css = 'metric featured' if not cards else 'metric'
                cards.append(f'<div class="{css}"><h3>{text(label)}</h3><strong class="metric-value">'
                             +text(value)+text(unit)+'</strong><p class="muted">'
                             +display_time(metric['observed_at'])+' 기준</p></div>')
            body += '<div class="metrics">'+''.join(cards)+'</div>' if cards else '<p>표시할 지표 데이터가 없어요</p>'
            if kind == '차트':
                body += '<p class="muted">원천 종가 기준 · 수정주가·배당 포함 수익률이 아닙니다.</p>'
        parts.append('<div id="factor-'+str(FACTORS.index(kind))+'">'+panel(kind, body)+'</div>')
    return ''.join(parts) or '<p class="empty">이 발행본의 요인 상세가 없습니다.</p>'


def render_screen(kind, screen, feature='all'):
    """Render only customer fields; evidence stays in the separate audit view."""
    parts = []
    publication = screen.get('publication', {})
    if publication:
        parts.append('<p class="muted">'+text(publication.get('etf_code'))+' · 발행 '
                     +display_time(publication.get('published_at'))
                     +(' · '+text(publication['forecast_period']) if publication.get('forecast_period') else '')+'</p>')
    if feature == 'factor_details':
        return ''.join(parts) + factor_details(screen)
    if kind == 'movement':
        if not screen.get('items'):
            return ''.join(parts)+'<p class="empty">표시할 오늘 움직임이 없습니다.</p>'
        if feature in ('all', 'summary'):
            parts.append(panel('왜 움직였을까?', '<p>'+text(screen['summary'])+'</p><p class="muted">관련 이슈 '
                               +str(len(screen['items']))+'개</p><button data-feature="detail">자세히 보기</button>'))
        if feature in ('all', 'detail'):
            parts.append(panel('오늘의 가격변동 설명', paragraphs(screen['items'])))
        return ''.join(parts)
    if feature in ('all', 'summary') and screen.get('summary_card'):
        card = screen['summary_card']
        body = badge(screen['outlook']['direction'])+'<p>'+text(card['summary'])+'</p>'
        body += '<div class="factor-strip">'+''.join('<button data-factor="'+text(f['type'])+'">'
                +text(f['type']+' · '+f['sticker'])+'</button>' for f in screen.get('factors', []))+'</div>'
        body += '<p><button data-feature="detail">자세히 보기</button></p>'
        parts.append(panel(screen.get('detail', {}).get('title') or card['title'], body))
    if feature in ('all', 'detail') and screen.get('detail'):
        detail = screen['detail']
        updates = detail.get('updates', {})
        if updates.get('items'):
            body = '<p class="muted">'+text(updates.get('date'))+'</p>'
            for item in updates['items']:
                label = {'added': '추가', 'modified': '수정', 'deleted': '삭제'}[item['change_type']]
                body += '<h3>'+label+' · '+text(item['title_keyword'])+'</h3>'
                if item['sentence']:
                    body += '<p>'+text(item['sentence'])+'</p>'
            parts.append(panel('오늘 업데이트', body))
        parts.append(panel(detail.get('title', '전망 상세'), paragraphs(detail['items'])))
    if feature in ('all', 'factors') and 'factors' in screen:
        body = ''.join('<div class="entry"><h3><button data-factor="'+text(f['type'])+'">'
                       +text(f['type'])+' '+text(f['sticker'])+' ↗</button></h3><p>'
                       +text(f['sentence'])+'</p></div>' for f in screen['factors'])
        parts.append(panel('5가지 기준 모두 보기', body))
    if feature in ('all', 'conclusion') and screen.get('conclusion'):
        conclusion = screen['conclusion']
        body = ''
        for field, label in (('supports', '도움'), ('burdens', '부담')):
            if conclusion[field]:
                body += '<h3>'+label+'</h3>'+''.join(badge(item['label']) for item in conclusion[field])
        body += '<p>'+text(conclusion['sentence'])+'</p>'
        if conclusion.get('change_condition'):
            body += '<h3>판단이 달라질 조건</h3><p>'+text(conclusion['change_condition'])+'</p>'
        parts.append(panel(conclusion['title'], body))
    return ''.join(parts)
