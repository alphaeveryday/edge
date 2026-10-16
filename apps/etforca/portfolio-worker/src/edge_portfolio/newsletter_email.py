"""Inline, table-based email presentation with an equivalent plain-text edition."""
from html import escape
from urllib.parse import urlsplit

UNSUBSCRIBE = '{{amazonSESUnsubscribeUrl}}'
SITE = 'https://etforca.com/portfolio/'
FONT = "-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif"


def safe_url(value):
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else SITE
    except ValueError:
        return SITE


def shell(title, preheader, body, footer):
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f3f5f2;color:#202d29;font-family:{FONT};-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f5f2"><tr><td align="center" style="padding:24px 12px">
<!--[if mso]><table role="presentation" width="600"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-top:5px solid #126c54">
<tr><td style="padding:30px 24px 22px;border-bottom:1px solid #e4eae6"><a href="{SITE}" style="color:#126c54;font-size:19px;font-weight:800;letter-spacing:1px;text-decoration:none">ETF ORCA</a><span style="color:#66756e;font-size:13px"> &nbsp; 보유 종목 데일리</span></td></tr>
{body}
<tr><td style="padding:24px;background:#f8faf7;color:#66756e;font-size:12px;line-height:1.8">{footer}</td></tr>
</table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def confirmation(token):
    title = 'ETF Orca · 보유 종목 데일리 구독을 확인해 주세요'
    url = SITE + '#newsletter-confirm=' + token
    body = f'''<tr><td style="padding:32px 24px 40px">
<h1 style="margin:0 0 18px;font-size:26px;line-height:1.45">내 ETF의 하루,<br>핵심만 쉽게 읽어요.</h1>
<p style="margin:0 0 24px;font-size:16px;line-height:1.85">매일 보유 종목의 핵심 이슈와 가격이 움직인 이유를 짧게 정리해 드려요. 아래 버튼을 눌러 이메일 주소를 확인하면 구독이 시작돼요.</p>
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td bgcolor="#126c54" style="border-radius:6px"><a href="{escape(url, quote=True)}" style="display:inline-block;padding:16px 24px;color:#fff;font-size:16px;font-weight:700;text-decoration:none">이메일 확인하고 구독하기 →</a></td></tr></table>
<p style="margin:24px 0 0;font-size:13px;color:#66756e;line-height:1.8">링크는 메일 발송 후 48시간 동안 유효해요. 신청하지 않으셨다면 이 메일을 무시해 주세요.</p></td></tr>'''
    text = f'내 ETF의 하루, 핵심만 쉽게 읽어요.\n매일 보유 종목의 핵심 이슈와 가격 변동 이유를 짧게 정리해 드려요.\n\n이메일 확인하고 구독하기: {url}\n\n메일 발송 후 48시간 동안 유효해요. 신청하지 않았다면 이 메일을 무시해 주세요.'
    return title, shell(title, '확인 버튼을 누르면 보유 종목 데일리 구독이 시작돼요.', body, 'ETF Orca · 직접 신청한 이메일 주소를 확인하는 메일입니다.'), text


def daily(edition, cards):
    date = edition.strftime('%Y.%m.%d')
    title = f'[ETF Orca] {date} 내 ETF에 무슨 일이 있었을까요?'
    brief = cards[:3]
    headlines = ''.join(f'<p style="margin:0 0 10px;font-size:16px;line-height:1.65;word-break:keep-all;overflow-wrap:anywhere"><strong style="color:#126c54">{i+1:02d}</strong> &nbsp;{escape(c["headline"])}</p>' for i, c in enumerate(brief))
    body = f'''<tr><td style="padding:28px 24px 24px">
<p style="margin:0 0 12px;color:#66756e;font-size:13px">{date} &nbsp;·&nbsp; 보유 ETF {len(cards)}종목</p>
<h1 style="margin:0 0 12px;font-size:28px;line-height:1.4;letter-spacing:-1px">내 ETF의 하루를<br>가볍게 이해하는 시간</h1>
<p style="margin:0;color:#66756e;font-size:15px;line-height:1.8">핵심 이슈와 가격 변동의 배경을 모았어요.</p></td></tr>
<tr><td style="padding:0 24px 28px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:22px 18px;background:#edf5ef;border-radius:8px"><p style="margin:0 0 16px;color:#126c54;font-size:12px;font-weight:800;letter-spacing:1px">먼저 읽는 오늘의 핵심</p>{headlines}</td></tr></table></td></tr>'''
    plain = [title, '', '먼저 읽는 오늘의 핵심', *[f'{i+1}. {c["headline"]}' for i, c in enumerate(brief)]]
    for i, card in enumerate(cards):
        links = ''.join(f'<a href="{escape(safe_url(s["url"]), quote=True)}" style="display:inline-block;margin:8px 16px 0 0;color:#126c54;font-size:13px;text-decoration:underline">출처 {j+1} ↗</a>' for j, s in enumerate(card.get('sources', [])))
        details = f'''<p style="margin:0 0 6px;color:#66756e;font-size:12px;font-weight:700">가격은 왜 움직였나요?</p>
<p style="margin:0 0 22px;font-size:16px;line-height:1.85;word-break:keep-all;overflow-wrap:anywhere">{escape(card['why_moved'])}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:15px 16px;background:#f7f8f4;border-left:3px solid #9bb98b;font-size:14px;line-height:1.8"><strong>다음에 볼 것</strong><br>{escape(card['watch_next'])}</td></tr></table>
''' if card['why_moved'] else ''
        body += f'''<tr><td style="padding:28px 24px;border-top:1px solid #e4eae6">
<p style="margin:0 0 12px;color:#126c54;font-size:13px;font-weight:700">{i+1:02d} &nbsp; {escape(card['name'])}</p>
<h2 style="margin:0 0 20px;font-size:22px;line-height:1.5;letter-spacing:-0.5px;word-break:keep-all;overflow-wrap:anywhere">{escape(card['headline'])}</h2>
<p style="margin:0 0 6px;color:#66756e;font-size:12px;font-weight:700">무슨 일이 있었나요?</p>
<p style="margin:0 0 22px;font-size:16px;line-height:1.85;word-break:keep-all;overflow-wrap:anywhere">{escape(card['issue'])}</p>
{details}
<p style="margin:12px 0 0">{links}</p></td></tr>'''
        plain += ['', f'{i+1}. {card["name"]} — {card["headline"]}', '무슨 일이 있었나요? '+card['issue'],
                  '가격은 왜 움직였나요? '+card['why_moved'], '다음에 볼 것: '+card['watch_next'],
                  *[f'출처: {safe_url(s["url"])}' for s in card.get('sources', [])]]
    footer = f'''신청하신 보유 종목을 기준으로 보내드렸어요.<br>AI가 자료를 요약한 정보이며 매매 지시가 아닙니다. 가격 기준일과 전망 시점은 본문을 확인해 주세요.<br><br>
<a href="{SITE}" style="color:#126c54">ETF Orca에서 포트폴리오 보기</a> &nbsp;·&nbsp; <a href="{UNSUBSCRIBE}" style="color:#66756e">수신 거부</a>'''
    plain += ['', 'ETF Orca: '+SITE, '수신 거부: '+UNSUBSCRIBE, 'AI가 자료를 요약한 정보이며 매매 지시가 아닙니다.']
    return title, shell(title, ' · '.join(c['headline'] for c in brief), body, footer), '\n'.join(plain)
