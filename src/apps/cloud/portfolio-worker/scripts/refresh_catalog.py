"""Build a dated ETF-only directory from KRX and Nasdaq Trader, never outlooks."""
import csv
import io
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'
KRX = 'https://data.krx.co.kr'


def krx_catalog():
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    def call(path, body=None):
        req = urllib.request.Request(KRX + path, data=urllib.parse.urlencode(body).encode() if body else None,
            headers={'User-Agent': UA, 'Referer': KRX + '/contents/MDC/mdiLoader/index.cmd?menuId=MDC0201030104'})
        return opener.open(req, timeout=25).read()
    call('/contents/MDC/COMS/client/view/login.jsp?site=mdc')
    result = json.loads(call('/contents/MDC/COMS/client/MDCCOMS001D1.cmd',
        {'mbrNm': '', 'telNo': '', 'di': '', 'certType': '', 'mbrId': os.environ['KRX_ID'], 'pw': os.environ['KRX_PS']}))
    if result.get('_error_code') != 'CD001':
        raise RuntimeError('KRX login not granted; no session override attempted')
    result = json.loads(call('/comm/bldAttendant/getJsonData.cmd',
        {'bld': 'dbms/MDC/STAT/standard/MDCSTAT04601', 'locale': 'ko_KR', 'share': '1', 'csvxls_isNo': 'false'}))
    rows = result.get('output', [])
    if len(rows) < 500:
        raise RuntimeError('Incomplete KRX ETF directory')
    return [{'id': 'KR:' + r['ISU_SRT_CD'], 'market': 'KR', 'ticker': r['ISU_SRT_CD'],
             'code': r['ISU_SRT_CD'], 'name': r['ISU_ABBRV'], 'isin': r['ISU_CD'], 'kind': 'etf'} for r in rows]


def nasdaq_catalog():
    rows, sources = [], []
    for filename, symbol in [('nasdaqlisted.txt', 'Symbol'), ('otherlisted.txt', 'ACT Symbol')]:
        url = 'https://www.nasdaqtrader.com/dynamic/SymDir/' + filename
        text = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30).read().decode('utf-8-sig')
        if 'File Creation Time:' not in text:
            raise RuntimeError('Incomplete Nasdaq directory')
        sources.append({'url': url, 'file_created': next(line.split('|')[0] for line in text.splitlines() if line.startswith('File Creation Time:'))})
        for row in csv.DictReader(io.StringIO(text), delimiter='|'):
            if row.get('ETF') != 'Y' or row.get('Test Issue') != 'N':
                continue
            ticker = row[symbol]
            rows.append({'id': 'US:' + ticker, 'market': 'US', 'ticker': ticker, 'code': ticker, 'name': row['Security Name'], 'kind': 'etf'})
    if len(rows) < 1000 or len({r['id'] for r in rows}) != len(rows):
        raise RuntimeError('Incomplete or duplicate US ETF directory')
    return rows, sources


def main():
    kr = krx_catalog()
    us, sources = nasdaq_catalog()
    data = {'fetched_at': datetime.now(timezone.utc).isoformat(),
            'sources': [{'url': KRX + '/contents/MDC/mdiLoader/index.cmd?menuId=MDC0201030104', 'note': 'ETF 전종목 기본정보; 제공 응답에 별도 기준일 없음'}] + sources,
            'counts': {'KR': len(kr), 'US': len(us)}, 'instruments': kr + us}
    path = ROOT / 'src' / 'edge_portfolio' / 'data' / 'etf_catalog.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf8')
    print(json.dumps({'counts': data['counts'], 'fetched_at': data['fetched_at']}))


if __name__ == '__main__':
    main()
