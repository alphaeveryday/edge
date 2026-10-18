"""Magazine-style letter: a fixed masthead and colophon around the production layout H and the experiment layouts A-G.

Inline styles on presentation tables with an equivalent plain-text edition. Gmail drops web fonts,
so the hierarchy must hold in system Korean serif/sans faces alone.
"""
import logging
from html import escape
from datetime import date
from urllib.parse import urlsplit

log = logging.getLogger('portfolio-newsletter-email')
UNSUBSCRIBE = '{{amazonSESUnsubscribeUrl}}'
SITE = 'https://etforca.com/portfolio/'
# Hosted with the landing page (app-landing/assets): Gmail blocks data-URI images. Ink tile, so it reads in dark mode too.
LOGO = 'https://etforca.com/assets/newsletter-logo.png'
SERIF = "'Noto Serif KR','Nanum Myeongjo',AppleMyungjo,Batang,Georgia,serif"
SANS = "-apple-system,'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif"
PAPER, DESK, INK, BODY, MUTED, HAIR, BRASS = '#fbfaf6', '#efece4', '#17150f', '#2b2822', '#6b665b', '#e0dbcf', '#86652c'
UP, DOWN = '#c8322a', '#2453b8'  # Korean market convention: rise red, fall blue. Always paired with ▲▼.
FORMATS = {'H': ('보유 이유 브리핑', '내 보유 이유에 중요한 것만'), 'A': ('손익 리포트', '오늘 내 계좌는 어땠나'), 'B': ('원인 노트', '왜 움직였나'),
           'C': ('새 소식', '오늘 새로 나온 일'), 'D': ('판단 점검', '보유 이유는 그대로인가'),
           'E': ('결정 노트', '지금 결정이 필요한가'), 'F': ('변화 감지', '중요한 변화만 골라'),
           'G': ('목적별 브리핑', '내 목적에서 본 오늘')}
# Production edition. Lives here, not in newsletter_agent, so the 128 MB sender never imports the research stack.
DEFAULT_FORMAT = 'H'
PURPOSES = {'long_term': '장기 적립', 'mid_term': '중기 보유', 'short_term': '단기 매매'}
LEAN = {'약화': 0, '강화': 1, '유지': 2}
VERDICTS = {'악화': '⚠️', '견고': '🛡️', '강화': '💪'}  # H
COVER = {'악화': 0, '강화': 1, '견고': 2}  # H cover priority: a weakened thesis first, then news that strengthened one
EVENT_ICONS = {'실적': '🧾', '물가': '📊', '금리': '🏛️', '정책': '📜', '제품': '🧪', '기타': '📅'}  # when no logo resolved
# One line to close each letter, picked by date: re-renders stay identical. Eight lines so no weekday is stuck with
# one, and every line reads right at an 8 AM send on any day, holidays included.
CHEERS = ('오늘도 힘내세요. 내 기준을 지키는 하루면 충분해요.', '흔들리는 날에도 기준을 지키는 당신, 충분히 잘하고 있어요.',
          '좋은 투자는 매일 조금씩 쌓여요. 오늘 하루도 응원할게요.', '숫자보다 내 기준을 믿는 하루가 되길 바라요.',
          '급할 것 없어요. 오늘도 차분하게, 힘내세요.', '어제도 수고 많으셨어요. 오늘도 좋은 하루 보내세요.',
          '잠깐의 확인이면 충분해요. 나머지 시간은 나를 위해 쓰세요.', '시장은 매일 바뀌어도 원칙은 그대로예요. 오늘도 힘내세요.')
P = f'margin:0;font-family:{SANS};word-break:keep-all;overflow-wrap:anywhere'


def safe_url(value):
    if UNSUBSCRIBE in value:
        return SITE
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else SITE
    except ValueError:
        return SITE


def scrub(value):
    """Remove the SES marker until none is left: one pass would rebuild a nested '{{amazonSESUnsub' + marker + 'scribeUrl}}'."""
    while UNSUBSCRIBE in value:
        value = value.replace(UNSUBSCRIBE, '')
    return value


def e(value):
    return escape(str(value))


def day(value):
    value = date.fromisoformat(value) if isinstance(value, str) else value
    return f'{value.month}월 {value.day}일'


def move(pct):
    """Colour plus arrow so the sign survives colour blindness and dark-mode inversion."""
    if pct is None:
        return '<span style="color:#6b665b">–</span>', '–'
    arrow, colour = ('▲', UP) if pct > 0 else ('▼', DOWN) if pct < 0 else ('–', MUTED)
    label = f'{arrow} {abs(pct):.2f}%'
    return f'<span style="color:{colour};white-space:nowrap">{label}</span>', label


def row(inner, pad='0 32px'):
    return f'<tr><td class="px" style="padding:{pad}">{inner}</td></tr>'


def label(text, colour=INK):
    return f'<p style="{P};margin:0 0 6px;color:{colour};font-size:12px;font-weight:700;letter-spacing:.6px">{e(text)}</p>'


def para(text, size=16, colour=BODY, bottom=20):
    return f'<p style="{P};margin:0 0 {bottom}px;color:{colour};font-size:{size}px;line-height:1.75">{e(text)}</p>'


def rule(weight=1, colour=HAIR, space=0):
    return f'<div style="border-top:{weight}px solid {colour};margin:{space}px 0;font-size:0;line-height:0">&nbsp;</div>'


class Letter:
    def __init__(self):
        self.rows, self.plain = [], []

    def add(self, html, *text):
        self.rows.append(html)
        self.plain.extend(text)


def quote_of(card):
    return card.get('quote') or {}


def button(url, text, primary=False):
    colour, fill = (PAPER, INK) if primary else (INK, PAPER)
    return (f'<a href="{escape(safe_url(url), quote=True)}" style="display:inline-block;margin:0 6px 8px 0;padding:{"14px 22px" if primary else "8px 12px"};'
            f'background:{fill};border:1px solid {INK if primary else HAIR};color:{colour};font-family:{SANS};font-size:{15 if primary else 12}px;'
            f'font-weight:700;text-decoration:none;white-space:nowrap">{e(text)}</a>')


def source_buttons(card):
    urls = list(dict.fromkeys(safe_url(source['url']) for source in card.get('sources', [])))
    html = ''.join(button(url, '출처 · ' + (urlsplit(url).hostname or '').removeprefix('www.')) for url in urls)
    return (f'<p style="margin:6px 0 0;line-height:1">{html}</p>' if html else ''), [f'출처: {url}' for url in urls]


def alert(card):
    """Price exception is arithmetic, not model judgement: today's move beyond twice the typical daily move."""
    quote = quote_of(card)
    sigma, change = quote.get('sigma_pct'), quote.get('change_pct')
    # A flat history (sigma 0) makes any real move exceptional; no history means no judgement.
    return sigma is not None and bool(change) and abs(change) >= 2 * sigma


def brand():
    """Logo tile plus the wordmark; with images blocked the alt text and wordmark still name the sender."""
    return (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="padding-right:12px">'
            f'<img src="{LOGO}" width="40" height="40" alt="ETF ORCA" style="display:block;border:0;outline:none;text-decoration:none"></td>'
            f'<td style="font-family:{SERIF};font-size:23px;font-weight:700;letter-spacing:6px;color:{INK}">ETF ORCA</td></tr></table>')


def issued(at):
    if at is None:
        return ''
    return f" · {'오전' if at.hour < 12 else '오후'} {(at.hour - 1) % 12 + 1}:{at.minute:02d} 발행"


def masthead(letter, edition, at):
    letter.add(row(f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td>{brand()}</td>
<td align="right" style="font-family:{SANS};font-size:11px;letter-spacing:1px;color:{MUTED}">보유 ETF 데일리</td></tr></table>
{rule(2, INK, 0)}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid {INK}"><tr>
<td style="padding:9px 0;font-family:{SANS};font-size:12px;color:{BODY}">{edition.year}년 {day(edition)} {'월화수목금토일'[edition.weekday()]}요일{issued(at)}</td></tr></table>''', '30px 32px 0'),
               f'ETF ORCA 보유 ETF 데일리 — {edition.isoformat()}{issued(at)}', '')


def cover(letter, headline, dek):
    letter.add(row(f'''<h1 class="hl" style="margin:0 0 16px;font-family:{SERIF};font-size:31px;line-height:1.36;letter-spacing:-.6px;color:{INK};font-weight:700;word-break:keep-all;overflow-wrap:anywhere">{e(headline)}</h1>
<p style="{P};color:{BODY};font-size:17px;line-height:1.7">{e(dek)}</p>''', '34px 32px 32px'),
               headline, dek, '')


def ledger(letter, cards, weights, big):
    """Holdings table. Contribution (weight × move) only when the reader gave weights; never invent them."""
    usable = [card for card in cards if quote_of(card)]
    if not usable:
        return
    # A portfolio figure needs every holding's weight and quote; a partial sum would pass for the whole.
    known = bool(weights) and all(card['instrument_id'] in weights and quote_of(card) for card in cards)
    head = ''
    if big and known:
        total = sum(weights[c['instrument_id']] * quote_of(c)['change_pct'] for c in usable)
        week = [quote_of(c).get('week_pct') for c in usable]
        week_total = sum(weights[c['instrument_id']] * w for c, w in zip(usable, week)) if None not in week else None
        head = f'''{label('포트폴리오 하루')}<p style="margin:0 0 4px;font-family:{SERIF};font-size:46px;line-height:1.15;font-weight:700">{move(total)[0]}</p>
<p style="{P};margin:0 0 22px;color:{MUTED};font-size:13px">비중 가중 하루 수익률{' &nbsp;·&nbsp; 최근 5거래일 ' + move(week_total)[0] if week_total is not None else ''} &nbsp;·&nbsp; 매입가 기준 손익이 아니에요</p>'''
        letter.plain += [f'포트폴리오 하루 {move(total)[1]}' + (f' · 최근 5거래일 {move(week_total)[1]}' if week_total is not None else '')]
    elif big:
        head = para('보유 비중을 알려주시면 계좌 전체의 하루 손익과 종목별 기여도를 계산해 드려요.', 14, MUTED)
    widest = max((abs(weights[c['instrument_id']] * quote_of(c)['change_pct']) for c in usable), default=0) if known else 0
    lines = ''
    for card in usable:
        quote = quote_of(card)
        bar = ''
        if known and big and widest:
            contribution = weights[card['instrument_id']] * quote['change_pct']
            width = max(2, round(abs(contribution) / widest * 100))
            bar = f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:7px"><tr>
<td width="{width}%" style="height:6px;background:{UP if contribution > 0 else DOWN};font-size:0;line-height:0">&nbsp;</td><td style="font-size:0;line-height:0">&nbsp;</td></tr></table>
<p style="{P};margin:5px 0 0;color:{MUTED};font-size:12px">기여도 {contribution:+.2f}%p</p>'''
            letter.plain.append(f"  {card['name']} 비중 {weights[card['instrument_id']]:.0%} · 기여도 {contribution:+.2f}%p")
        share = f"<br><span style=\"color:{MUTED};font-size:12px;font-weight:400\">비중 {weights[card['instrument_id']]:.0%}</span>" if known else ''
        lines += f'''<tr><td style="padding:12px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:15px;font-weight:700;color:{INK};word-break:keep-all">{e(card['name'])}{share}{bar}</td>
<td align="right" valign="top" style="padding:12px 0 12px 12px;border-top:1px solid {HAIR};font-family:{SANS};font-size:15px;font-weight:700">{move(quote['change_pct'])[0]}</td>
<td align="right" valign="top" style="padding:12px 0 12px 12px;border-top:1px solid {HAIR};font-family:{SANS};font-size:13px">{move(quote.get('week_pct'))[0]}</td></tr>'''
        letter.plain.append(f"{card['name']}: 하루 {move(quote['change_pct'])[1]} · 5거래일 {move(quote.get('week_pct'))[1]}")
    letter.add(row(f'''{rule(1, INK)}<div style="height:18px"></div>{head}<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:0 0 8px"></td><td align="right" style="padding:0 0 8px;font-family:{SANS};font-size:11px;color:{MUTED}">하루</td><td align="right" style="padding:0 0 8px 12px;font-family:{SANS};font-size:11px;color:{MUTED};white-space:nowrap">5거래일</td></tr>
{lines}</table>''', '0 32px 34px'), '')


def section(name, text, colour=INK):
    return label(name, colour) + para(text)


def story(letter, number, card, body, text, lead=False, brief=False):
    """The lead story's headline and dek are already the cover, so the story opens straight into its body.
    A brief keeps only headline and dek so long letters stay under the clipping budget."""
    sources_html, sources_text = ('', []) if brief else source_buttons(card)
    change = quote_of(card).get('change_pct')
    heading = f'''<h2 style="margin:18px 0 10px;font-family:{SERIF};font-size:23px;line-height:1.42;letter-spacing:-.4px;color:{INK};font-weight:700;word-break:keep-all;overflow-wrap:anywhere">{e(card['headline'])}</h2>
<p style="{P};margin:0 0 22px;color:{INK};font-size:16px;line-height:1.75;font-weight:700">{e(card.get('dek', ''))}</p>'''
    letter.add(row(f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td width="44" valign="top" style="padding-top:2px;font-family:{SERIF};font-size:22px;color:{BRASS};font-weight:700">{number:02d}</td>
<td valign="top" style="border-top:1px solid {INK};padding-top:10px"><p style="{P};color:{INK};font-size:14px;font-weight:700">{e(card['name'])} &nbsp;{move(change)[0] if change is not None else ''}</p></td></tr></table>
{'<div style="height:22px"></div>' if lead else heading}
{body}{sources_html}''', '0 32px 40px'),
               f'{number:02d}. {card["name"]}' + (f' {move(change)[1]}' if change is not None else '') + ('' if lead else f' — {card["headline"]}'),
               *([] if lead else [card.get('dek', '')]), *text, *sources_text, '')


LEGACY = (('issue', '무슨 일이 있었나'), ('why_moved', '왜 움직였나'), ('watch_next', '다음에 볼 것'))


def notice(letter, number, card, brief=False):
    """Unavailable holdings, or content cached under another format (e.g. written before a deploy): show what exists."""
    kept = [] if brief else [(name, card[key]) for key, name in LEGACY if card.get(key)]
    story(letter, number, {**card, 'dek': card.get('dek') or ('' if kept else card.get('issue', ''))},
          ''.join(section(name, text) for name, text in kept), [f'{name}: {text}' for name, text in kept], brief=brief)


def pull(text, caption):
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:2px 0 24px"><tr>
<td style="border-left:3px solid {BRASS};padding:4px 0 4px 18px"><p style="{P};margin:0 0 6px;color:{BRASS};font-size:12px;font-weight:700;letter-spacing:.6px">{e(caption)}</p>
<p style="margin:0;font-family:{SERIF};font-size:19px;line-height:1.6;color:{INK};word-break:keep-all">{e(text)}</p></td></tr></table>'''


def listing(items, mark, colour):
    return ''.join(f'''<tr><td width="22" valign="top" style="padding:0 0 10px;font-family:{SANS};font-size:16px;font-weight:700;color:{colour}">{mark}</td>
<td style="padding:0 0 10px;font-family:{SANS};font-size:15px;line-height:1.7;color:{BODY};word-break:keep-all">{e(item)}</td></tr>''' for item in items)


def write_a(number, card, purpose):
    return (section('왜 움직였나', card['why_moved']) + section('새로 알게 된 것', card['new_info']) + pull(card['judgment_impact'], '판단에 주는 의미'),
            [f"왜 움직였나: {card['why_moved']}", f"새로 알게 된 것: {card['new_info']}", f"판단에 주는 의미: {card['judgment_impact']}"])


def write_b(number, card, purpose):
    factors = changes_table(card['factors'])
    body = (label('변동 요인 후보') + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;border-bottom:1px solid {HAIR}">{factors}</table>'
            + section('새로 나온 정보', card['new_info']) + section('보유 판단과의 관계', card['thesis_relation']))
    return body, ['변동 요인 후보:', *[f"  [{f['certainty']}] {f['label']} — {f['detail']}" for f in card['factors']],
                  f"새로 나온 정보: {card['new_info']}", f"보유 판단과의 관계: {card['thesis_relation']}"]


def write_c(number, card, purpose):
    body, text = '', []
    for i, event in enumerate(card['events']):
        body += f'''<p style="{P};margin:0 0 4px;color:{BRASS};font-size:12px;font-weight:700">사건 {number}-{i + 1} &nbsp;·&nbsp; 중요도 {e(event['importance'])}</p>
<h3 style="margin:0 0 10px;font-family:{SERIF};font-size:19px;line-height:1.5;color:{INK};word-break:keep-all">{e(event['title'])}</h3>
{para(event['what'], 15)}{label('왜 중요한가')}{para(event['why_it_matters'], 15)}{label('가격과의 관계')}{para(event['price_link'], 15, bottom=24)}'''
        text += [f"[사건 {number}-{i + 1} · 중요도 {event['importance']}] {event['title']}", event['what'],
                 f"왜 중요한가: {event['why_it_matters']}", f"가격과의 관계: {event['price_link']}"]
    return body + pull(card['judgment_impact'], '판단에 주는 의미'), text + [f"판단에 주는 의미: {card['judgment_impact']}"]


def write_d(number, card, purpose):
    cells = ''.join(f'''<td width="33%" align="center" style="padding:10px 4px;font-family:{SANS};font-size:13px;{'background:' + INK + ';color:' + PAPER + ';font-weight:700' if card['verdict'] == name else 'color:' + MUTED + ';border:1px solid ' + HAIR}">{name}</td>'''
                    for name in ('약화', '유지', '강화'))
    body = (pull(card['thesis'], '보유 이유 (검증할 가설)') + label('오늘의 판정')
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px"><tr>{cells}</tr></table>'
            + para(card['verdict_reason']) + label('가설을 지지하는 사실')
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px">{listing(card["supporting"], "+", UP)}</table>'
            + label('반대 증거와 위험') + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px">{listing(card["opposing"], "−", DOWN)}</table>'
            + section('판정에 쓰인 새 사건', card['new_event']))
    return body, [f"보유 이유: {card['thesis']}", f"판정: {card['verdict']} — {card['verdict_reason']}",
                  *[f'  + {x}' for x in card['supporting']], *[f'  − {x}' for x in card['opposing']], f"새 사건: {card['new_event']}"]


def write_e(number, card, purpose):
    verdict = '결정이 필요해요' if card['decision_needed'] else '오늘은 결정하지 않아도 돼요'
    options = ''.join(f'''<tr><td width="88" valign="top" style="padding:14px 10px 14px 0;border-top:1px solid {HAIR};font-family:{SERIF};font-size:16px;font-weight:700;color:{INK};word-break:keep-all">{e(o['action'])}</td>
<td style="padding:14px 0;border-top:1px solid {HAIR}"><p style="{P};color:{BODY};font-size:15px;line-height:1.7"><b style="color:{INK}">근거</b> &nbsp;{e(o['rationale'])}</p>
<p style="{P};margin:6px 0 0;color:{BODY};font-size:15px;line-height:1.7"><b style="color:{INK}">위험</b> &nbsp;{e(o['risk'])}</p></td></tr>''' for o in card['options'])
    body = (f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;background:{DESK}"><tr><td style="padding:16px 18px">
<p style="margin:0 0 6px;font-family:{SERIF};font-size:19px;font-weight:700;color:{INK}">{verdict}</p><p style="{P};color:{BODY};font-size:15px;line-height:1.7">{e(card['decision_reason'])}</p></td></tr></table>'''
            + label('선택지별 근거와 위험') + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;border-bottom:1px solid {HAIR}">{options}</table>'
            + label('결정 전에 아직 모르는 것') + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 8px">{listing(card["unknowns"], "?", BRASS)}</table>')
    return body, [f"{verdict} — {card['decision_reason']}", *[f"  {o['action']} · 근거: {o['rationale']} · 위험: {o['risk']}" for o in card['options']],
                  *[f'  ? {x}' for x in card['unknowns']]]


def write_f(number, card, purpose):
    note = f'가격 경보: 하루 변동이 평소 변동 폭(표준편차 {quote_of(card)["sigma_pct"]:.2f}%)의 2배를 넘었어요.' if alert(card) else ''
    body = (pull(card['signal'], '판정') + section('무엇이 달라졌나', card['explanation']) + section('투자 조건에 미치는 영향', card['condition_impact'])
            + (para(note, 13, MUTED) if note else ''))
    return body, [f"판정: {card['signal']}", card['explanation'], f"투자 조건에 미치는 영향: {card['condition_impact']}", *([note] if note else [])]


def write_g(number, card, purpose):
    view = card[purpose]
    others = ''.join(f'''<p style="{P};margin:0 0 8px;color:{MUTED};font-size:13px;line-height:1.65"><b>{PURPOSES[key]}</b> &nbsp;{e(card[key]['state'])}</p>''' for key in PURPOSES if key != purpose)
    body = (label(f'{PURPOSES[purpose]} 관점의 지금') + para(view['state']) + section('새로 나온 정보', card['new_info'])
            + f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 22px;border-top:1px solid {HAIR};border-bottom:1px solid {HAIR}"><tr>
<td width="50%" valign="top" style="padding:14px 12px 14px 0"><p style="{P};margin:0 0 4px;color:{INK};font-size:12px;font-weight:700">전략 유지 조건</p><p style="{P};color:{BODY};font-size:14px;line-height:1.7">{e(view['keep_if'])}</p></td>
<td width="50%" valign="top" style="padding:14px 0 14px 12px;border-left:1px solid {HAIR}"><p style="{P};margin:0 0 4px;color:{INK};font-size:12px;font-weight:700">전략 변경 신호</p><p style="{P};color:{BODY};font-size:14px;line-height:1.7">{e(view['change_if'])}</p></td></tr></table>'''
            + label('다른 목적이라면', MUTED) + others)
    return body, [f"{PURPOSES[purpose]} 관점: {view['state']}", f"새로 나온 정보: {card['new_info']}", f"유지 조건: {view['keep_if']}",
                  f"변경 신호: {view['change_if']}", *[f"  {PURPOSES[k]}: {card[k]['state']}" for k in PURPOSES if k != purpose]]


def changes_table(factors):
    return ''.join(f'''<tr><td width="46" valign="top" style="padding:13px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:12px;font-weight:700;color:{INK if f['certainty'] == '확인' else BRASS}">{e(f['certainty'])}</td>
<td style="padding:13px 0;border-top:1px solid {HAIR}"><p style="{P};color:{INK};font-size:15px;font-weight:700">{e(f['label'])}</p><p style="{P};margin:4px 0 0;color:{BODY};font-size:15px;line-height:1.7">{e(f['detail'])}</p></td></tr>''' for f in factors)


def chip(verdict):
    return (f'<span style="display:inline-block;padding:5px 10px;border:1px solid {INK};font-family:{SANS};font-size:13px;font-weight:700;'
            f'color:{INK};white-space:nowrap">{VERDICTS[verdict]} {e(verdict)}</span>')


def image_url(value):
    return value if value and safe_url(value) == value else None


def news(title, items, mark):
    """One row per item; a small thumbnail only when the worker resolved the article's own picture."""
    if not items:
        return '', []
    rows = ''
    for item in items:
        image = image_url(item.get('image'))
        thumb = (f'<td width="104" valign="top" style="padding:13px 12px 13px 0;border-top:1px solid {HAIR}">'
                 f'<img src="{escape(image, quote=True)}" width="96" alt="" style="display:block;width:96px;height:auto;border:0"></td>') if image else ''
        rows += f'''<tr>{thumb}<td valign="top" style="padding:13px 0;border-top:1px solid {HAIR}"><p style="{P};color:{INK};font-size:15px;font-weight:700;line-height:1.55">{mark} {e(item['title'])} <span style="color:{INK if item['certainty'] == '확인' else BRASS};font-size:12px">· {e(item['certainty'])}</span></p>
<p style="{P};margin:4px 0 0;color:{BODY};font-size:15px;line-height:1.7">{e(item['detail'])}</p></td></tr>'''
    html = label(title) + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;border-bottom:1px solid {HAIR}">{rows}</table>'
    return html, [f'{title}:', *[f"  {mark} [{item['certainty']}] {item['title']} — {item['detail']}" for item in items]]


def write_h(number, card, purpose):
    change = quote_of(card).get('change_pct') or 0
    moved = '오늘 오른 이유' if change > 0 else '오늘 떨어진 이유' if change < 0 else '오늘의 움직임'
    note = '하루 변동이 평소 변동 폭의 2배를 넘었어요.' if alert(card) else ''
    good, good_text = news('새로운 긍정적 소식', card['positives'], '＋')
    bad, bad_text = news('새로운 부정적 소식', card['negatives'], '－')
    body = (pull(card['thesis'], '내 보유 이유')
            + f'<p style="{P};margin:0 0 30px;color:{INK};font-size:15px;font-weight:700">오늘 판정 &nbsp;{chip(card["verdict"])}</p>'
            + label('오늘의 이유 브리핑', BRASS) + rule(1, INK) + '<div style="height:14px"></div>'
            + section(moved, card['move_reason']) + good + bad + section('내 투자 판단에 주는 영향', card['impact'])
            + (para(note, 13, MUTED) if note else ''))
    return body, [f"내 보유 이유: {card['thesis']}", f"오늘 판정: {VERDICTS[card['verdict']]} {card['verdict']}", '오늘의 이유 브리핑',
                  f"{moved}: {card['move_reason']}", *good_text, *bad_text, f"내 투자 판단에 주는 영향: {card['impact']}", *([note] if note else [])]


def calendar(letter, edition, cards):
    """Upcoming events across holdings, deduplicated and in date order; past dates never show."""
    seen = {}
    for card in cards:
        for event in card['events']:
            if event['date'] >= edition.isoformat():
                # Titles come from separate model runs: '3분기 실적 발표' and '3분기 실적발표' by the same org are one event,
                # while the same title by two companies stays two rows.
                key = (event['date'], ''.join(event['title'].split()), ''.join(event['org'].split()))
                seen.setdefault(key, (event, []))[1].append(card['name'])
    if not seen:
        return
    lines, text = '', []
    for _, (event, names) in sorted(seen.items())[:8]:
        icon = image_url(event.get('icon'))
        mark = (f'<img src="{escape(icon, quote=True)}" width="18" height="18" alt="" style="display:inline-block;vertical-align:-4px;border:0;margin-right:6px">'
                if icon else f'<span style="margin-right:6px">{EVENT_ICONS[event["kind"]]}</span>')
        lines += f'''<tr><td width="74" valign="top" style="padding:12px 8px 12px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:13px;font-weight:700;color:{INK};white-space:nowrap">{e(event['when'])}</td>
<td valign="top" style="padding:12px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:14px;line-height:1.6;color:{INK};word-break:keep-all">{mark}<b>{e(event['title'])}</b><br><span style="color:{MUTED};font-size:12px">{e(event['org'])} · {e(', '.join(dict.fromkeys(names)))}</span></td></tr>'''
        text.append(f"  {event['when']} · {event['title']} ({event['org']}) — {', '.join(dict.fromkeys(names))}")
    letter.add(row(label('앞으로 예정된 주요 이벤트') + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid {HAIR}">{lines}</table>', '0 32px 38px'),
               '앞으로 예정된 주요 이벤트:', *text, '')


WRITERS = {'H': write_h, 'A': write_a, 'B': write_b, 'C': write_c, 'D': write_d, 'E': write_e, 'F': write_f, 'G': write_g}
NEXT = {'B': ('다음에 확인할 것', lambda c: c['watch_next']), 'D': ('판단이 바뀌는 조건', lambda c: c['change_trigger']),
        'F': ('다음에 볼 지점', lambda c: c['watch_next'])}


def flags(card, fmt):
    if fmt == 'H' and card['verdict'] == '악화':
        return True  # A weakened thesis is never 'no change', whatever the importance flag says.
    return card['significant' if fmt == 'F' else 'important'] or alert(card)


def lead(cards, fmt):
    def size(card):
        return abs(quote_of(card).get('change_pct') or 0)
    if fmt in 'FH':
        # Same definition as the stamp: a 2-sigma move is news even when the model calls the day quiet.
        flagged = [c for c in cards if flags(c, fmt)]
        return max(flagged, key=lambda c: (-COVER[c['verdict']], size(c)) if fmt == 'H' else size(c)) if flagged else None
    rank = {'D': lambda c: (-LEAN[c['verdict']], size(c)), 'E': lambda c: (c['decision_needed'], size(c)),
            'C': lambda c: (-min(['높음', '보통', '낮음'].index(x['importance']) for x in c['events']), size(c))}.get(fmt, lambda c: size(c))
    return max(cards, key=rank) if cards else None


def shell(title, preheader, rows, footer):
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{escape(title)}</title>
<style>@media (max-width:620px){{.px{{padding-left:20px!important;padding-right:20px!important}}.hl{{font-size:26px!important}}}}</style></head>
<body style="margin:0;padding:0;background:{DESK};color:{BODY};font-family:{SANS};-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{DESK}"><tr><td align="center" style="padding:24px 10px">
<!--[if mso]><table role="presentation" width="600"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:{PAPER}">
{''.join(rows)}
<tr><td class="px" style="padding:0 32px 34px">{footer}</td></tr>
</table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def colophon(body, action=''):
    return f'''{rule(2, INK)}<p style="margin:16px 0 14px;font-family:{SERIF};font-size:15px;font-weight:700;letter-spacing:3px;color:{INK}">ETF ORCA</p>
{action}<p style="{P};margin-top:8px;color:{MUTED};font-size:12px;line-height:1.85">{body}</p>'''


def confirmation(token):
    title = 'ETF Orca · 보유 종목 데일리 구독을 확인해 주세요'
    url = SITE + '#newsletter-confirm=' + token
    rows = [row(f'''{brand()}<div style="height:12px"></div>{rule(2, INK)}
<p style="{P};margin:34px 0 14px;color:{BRASS};font-size:12px;font-weight:700;letter-spacing:1px">구독 확인</p>
<h1 class="hl" style="margin:0 0 18px;font-family:{SERIF};font-size:30px;line-height:1.4;color:{INK};word-break:keep-all">내 ETF의 하루,<br>핵심만 읽어요.</h1>
{para('매일 보유 종목의 핵심 이슈와 가격이 움직인 이유를 짧게 정리해 드려요. 아래 버튼을 눌러 이메일 주소를 확인하면 구독이 시작돼요.', 16, BODY, 26)}
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td bgcolor="{INK}"><a href="{escape(url, quote=True)}" style="display:inline-block;padding:16px 26px;color:{PAPER};font-family:{SANS};font-size:16px;font-weight:700;text-decoration:none">이메일 확인하고 구독하기 →</a></td></tr></table>
{para('링크는 메일 발송 후 48시간 동안 유효해요. 이전에 수신을 거부했다면, 이전 뉴스레터 하단의 ‘수신 거부’ 링크에서 수신 설정을 다시 허용해 주세요. 신청하지 않으셨다면 이 메일을 무시해 주세요.', 13, MUTED, 0).replace('margin:0 0 0px', 'margin:26px 0 0')}''', '30px 32px 36px')]
    text = f'내 ETF의 하루, 핵심만 읽어요.\n매일 보유 종목의 핵심 이슈와 가격 변동 이유를 짧게 정리해 드려요.\n\n이메일 확인하고 구독하기: {url}\n\n메일 발송 후 48시간 동안 유효해요. 이전에 수신을 거부했다면 이전 뉴스레터 하단의 수신 거부 링크에서 수신 설정을 다시 허용해 주세요. 신청하지 않았다면 이 메일을 무시해 주세요.'
    return title, shell(title, '확인 버튼을 누르면 보유 종목 데일리 구독이 시작돼요.', rows, colophon('직접 신청한 이메일 주소를 확인하는 메일입니다.')), text


# Gmail clips HTML beyond ~102KB and hides the footer: the disclaimer and the unsubscribe link go with it.
BUDGET = 95_000


def daily(edition, cards, fmt=DEFAULT_FORMAT, *, weights=None, purpose='long_term', issued_at=None):
    """weights: instrument_id → portfolio share (A only, when the reader gave one). purpose: G's reading lens.
    issued_at: the send time shown in the masthead.

    Stories print in full in importance order until the letter would pass BUDGET; the rest become briefs
    (headline and dek), the way a magazine runs the remainder as short items."""
    for full in range(len(cards), -1, -1):
        rendered = compose(edition, cards, fmt, weights, purpose, full, issued_at)
        if len(rendered[1].encode()) <= BUDGET:
            return rendered
    log.warning('newsletter_over_budget bytes=%s holdings=%s', len(rendered[1].encode()), len(cards))
    return rendered


def fits(card, fmt):
    """Same format and the current shape: H cards cached before the briefing redesign fall back to a notice."""
    if card.get('unavailable') or card.get('format') != fmt:
        return False
    return fmt != 'H' or ('move_reason' in card and card.get('verdict') in VERDICTS)


def compose(edition, cards, fmt, weights, purpose, full, at):
    ready = [card for card in cards if fits(card, fmt)]
    top = lead(ready, fmt)
    missing = sum(bool(c.get('unavailable')) for c in cards)
    if fmt in 'FH' and top is None and ready:
        headline = '오늘은 보유 이유를 흔드는 변화가 없어요' if fmt == 'H' else '오늘은 중요한 변화가 없어요'
        dek = (f'자료를 확인한 {len(ready)}종목은 등락이 평소 범위 안이고 보유 이유를 흔드는 새 사건이 없었어요.'
               + (f' {missing}종목은 오늘 자료를 확보하지 못했어요.' if missing else ''))
    elif top:
        headline, dek = top['headline'], top['dek']
    elif other := next((c for c in cards if not c.get('unavailable')), None):
        headline, dek = other['headline'], other.get('dek') or other.get('issue', '')
    else:
        headline, dek = '오늘의 소식을 준비하지 못했어요', '최신 자료를 확보하지 못했어요. 다음 발행에서 다시 확인할게요.'
    order = sorted(ready, key=lambda c: c is not top) + [c for c in cards if c not in ready]
    letter = Letter()
    masthead(letter, edition, at)
    cover(letter, headline, dek)
    calm = []
    if fmt == 'H':
        # Only what touches the thesis gets a story; everything else is one line.
        calm = [c for c in ready if not flags(c, fmt)]
        order = sorted([c for c in ready if c not in calm], key=lambda c: c is not top) + [c for c in cards if c not in ready]
        if any(quote_of(c) for c in cards):
            letter.add(row(label('포트폴리오 보고'), '0 32px 4px'), '포트폴리오 보고:')
            ledger(letter, cards, weights, big=bool(weights))
        reported = len(ready) - len(calm)
        letter.add(row(f'''{rule(1, INK)}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:18px 0 0"><tr>
<td width="80" valign="top" style="font-family:{SERIF};font-size:52px;line-height:1;font-weight:700;color:{INK}">{reported}</td>
<td valign="top"><p style="{P};margin:4px 0 4px;color:{INK};font-size:17px;font-weight:700">오늘 주요 보고 종목</p><p style="{P};color:{MUTED};font-size:13px;line-height:1.7">보유 {len(cards)}종목 중{' · 나머지는 한 줄로 줄였어요' if calm else ''}</p></td></tr></table>''', '0 32px 34px'),
                   f'오늘 주요 보고 종목 {reported}개 · 보유 {len(cards)}종목 중', '')
    if fmt == 'F':
        flagged = [c for c in ready if c['significant'] or alert(c)]
        letter.add(row(f'''{rule(1, INK)}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:18px 0 0"><tr>
<td width="96" valign="top" style="font-family:{SERIF};font-size:58px;line-height:1;font-weight:700;color:{INK}">{len(flagged)}</td>
<td valign="top"><p style="{P};margin:4px 0 4px;color:{INK};font-size:17px;font-weight:700">건의 중요 변화</p><p style="{P};color:{MUTED};font-size:13px;line-height:1.7">보유 {len(cards)}종목 중 {len(ready)}종목 점검{' · 변화 없는 종목은 한 줄로 줄였어요' if len(flagged) < len(ready) else ''}</p></td></tr></table>''', '0 32px 34px'),
                   f'중요 변화 {len(flagged)}건 · 보유 {len(cards)}종목 중 {len(ready)}종목 점검', '')
        quiet = [c for c in ready if c not in flagged]
        if quiet:
            lines = ''.join(f'''<tr><td style="padding:12px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:14px;font-weight:700;color:{INK};word-break:keep-all" width="40%">{e(c['name'])}</td>
<td style="padding:12px 0 12px 12px;border-top:1px solid {HAIR};font-family:{SANS};font-size:14px;line-height:1.65;color:{BODY};word-break:keep-all">{move(quote_of(c).get('change_pct'))[0]} &nbsp;{e(c['signal'])}</td></tr>''' for c in quiet)
            letter.add(row(label('변화 없음') + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid {HAIR}">{lines}</table>', '0 32px 38px'),
                       '변화 없음:', *[f"  {c['name']} {move(quote_of(c).get('change_pct'))[1]} — {c['signal']}" for c in quiet], '')
        order = sorted(flagged, key=lambda c: c is not top) + [c for c in cards if c not in ready]
    if fmt in 'AB':
        ledger(letter, order, weights, big=fmt == 'A')
    if fmt == 'C':
        items = [(c, ev) for c in ready for ev in c['events']]
        lines = ''.join(f'''<tr><td width="30" valign="top" style="padding:10px 0;border-top:1px solid {HAIR};font-family:{SERIF};font-size:16px;color:{BRASS};font-weight:700">{i + 1}</td>
<td style="padding:10px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:15px;line-height:1.6;color:{INK};word-break:keep-all">{e(ev['title'])}<br><span style="color:{MUTED};font-size:12px">{e(c['name'])} · 중요도 {e(ev['importance'])}</span></td></tr>''' for i, (c, ev) in enumerate(items))
        if items:
            letter.add(row(f'{rule(1, INK)}<div style="height:16px"></div>' + label('오늘 읽을 사건') + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid {HAIR}">{lines}</table>', '0 32px 38px'),
                       '오늘 읽을 사건:', *[f"  {i + 1}. {ev['title']} ({c['name']})" for i, (c, ev) in enumerate(items)], '')
    if fmt == 'G':
        lens = ' &nbsp;/&nbsp; '.join(f'<b style="color:{INK};border-bottom:2px solid {INK}">{name}</b>' if key == purpose else f'<span style="color:{MUTED}">{name}</span>' for key, name in PURPOSES.items())
        letter.add(row(f'''{rule(1, INK)}<p style="{P};margin:16px 0 6px;font-size:15px">{lens}</p>
<p style="{P};margin:0 0 12px;color:{MUTED};font-size:12px">이 호는 <b>{PURPOSES[purpose]}</b> 관점으로 편집했어요.</p>{button(SITE, '목적 바꾸기')}''', '0 32px 38px'),
                       f'읽는 관점: {PURPOSES[purpose]}', '')
    if fmt == 'E':
        needed = sum(c['decision_needed'] for c in ready)
        letter.add(row(f'''{rule(1, INK)}<p style="margin:18px 0 6px;font-family:{SERIF};font-size:22px;font-weight:700;color:{INK}">오늘 결정이 필요한 종목 {needed}개</p>
<p style="{P};color:{MUTED};font-size:13px;line-height:1.7">세 선택지를 같은 무게로 비교해요. 어느 쪽도 권하지 않으며, 개인의 자금 사정과 목표는 반영하지 못했어요.</p>''', '0 32px 38px'),
                       f'오늘 결정이 필요한 종목 {needed}개 — 선택지 비교이며 매매 권유가 아닙니다.', '')
    number = 0
    for card in order:
        number += 1
        if card in ready and number <= full:
            body, text = WRITERS[fmt](number, card, purpose)
            story(letter, number, card, body, text, lead=card is top)
        elif card in ready:
            if number == full + 1:
                letter.add(row(label('나머지 종목은 제목과 요약만 실었어요', MUTED) + para('메일이 길어 잘리지 않도록 줄였어요.', 13, MUTED), '0 32px 20px'),
                           '나머지 종목은 제목과 요약만 실었어요.', '')
            story(letter, number, card, '', [], lead=card is top, brief=True)
        else:
            # Notices must shrink too, or cached legacy cards could push the letter past Gmail's clip.
            notice(letter, number, card, brief=number > full)
    if calm:
        lines = ''.join(f'''<tr><td style="padding:12px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:14px;font-weight:700;color:{INK};word-break:keep-all" width="40%">{e(c['name'])} &nbsp;{move(quote_of(c).get('change_pct'))[0]}</td>
<td style="padding:12px 0 12px 12px;border-top:1px solid {HAIR};font-family:{SANS};font-size:14px;line-height:1.65;color:{BODY};word-break:keep-all">{VERDICTS[c['verdict']]} {e(c['verdict'])} · {e(c['impact'])}</td></tr>''' for c in calm)
        letter.add(row(label('변화 없음') + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid {HAIR}">{lines}</table>', '0 32px 38px'),
                   '변화 없음:', *[f"  {c['name']} {move(quote_of(c).get('change_pct'))[1]} — {VERDICTS[c['verdict']]} {c['verdict']} · {c['impact']}" for c in calm], '')
    if fmt == 'H':
        calendar(letter, edition, ready)
    if fmt in NEXT and any(c in ready for c in order):
        title, pick = NEXT[fmt]
        lines = ''.join(f'''<tr><td width="34%" valign="top" style="padding:11px 10px 11px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:13px;font-weight:700;color:{INK};word-break:keep-all">{e(c['name'])}</td>
<td style="padding:11px 0;border-top:1px solid {HAIR};font-family:{SANS};font-size:14px;line-height:1.65;color:{BODY};word-break:keep-all">{e(pick(c))}</td></tr>''' for c in order if c in ready)
        letter.add(row(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{DESK}"><tr><td style="padding:18px 18px 8px">{label(title)}<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{lines}</table></td></tr></table>', '0 32px 36px'),
                   f'{title}:', *[f"  {c['name']}: {pick(c)}" for c in order if c in ready], '')
    cheer = CHEERS[edition.toordinal() % len(CHEERS)]
    letter.add(row(f'<p style="margin:0;font-family:{SERIF};font-size:18px;line-height:1.7;color:{INK};word-break:keep-all">{e(cheer)}</p>', '4px 32px 34px'), cheer, '')
    title = f'[ETF Orca] {day(edition)} · {headline}'
    footer = colophon(f'''‘확인’은 자료로 확인한 사실, ‘추정’은 시점과 방향이 맞는 요인 후보예요. AI가 공개 자료를 조사·요약한 정보이며 투자 권유나 매매 지시가 아닙니다. 투자 판단과 그 결과의 책임은 투자자 본인에게 있어요.<br><br>
<a href="{UNSUBSCRIBE}" style="color:{MUTED}">수신 거부</a>''', button(SITE, '내 포트폴리오 보기', primary=True))
    # SES expands this marker after rendering. Only our footer may contain it, never external text/URLs.
    rows = [scrub(part) for part in letter.rows]
    plain = [scrub(line) for line in letter.plain]
    # Model text reaches the Subject header now: never let a line break into it.
    title, preheader = (' '.join(scrub(value).split()) for value in (title, dek))
    plain += ['내 포트폴리오 보기: ' + SITE, '수신 거부: ' + UNSUBSCRIBE, 'AI가 공개 자료를 조사·요약한 정보이며 투자 권유나 매매 지시가 아닙니다.']
    return title, shell(title, preheader, rows, footer), '\n'.join(plain)
