"""그날 발행된 전망 화면을 랜딩 버킷으로 내보낸다(ALPHA-1267).

etforca.com 의 종목 검색이 읽는 파일(outlook/<코드>.json)을 만든다. 전망은 분석 v2 조회 API 로만 읽는다 —
분석 DB·내부 코드에 기대지 않는다. 스케줄러가 매일 08:00 KST 에 부른다(전망 배치는 06:00 시작, 08:00 마감).

같은 화면으로 검색 엔진에 여는 종목 페이지(etf/<코드>)와 그 sitemap(etf/sitemap.xml)도 쓴다(ALPHA-1454).

수동 실행은 event 로 범위를 좁힌다: {"etf_codes": ["091160"], "date": "2026-10-12"}.
"""
import html
import json
import os
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
SITE = 'https://etforca.com'
PAGE = string.Template(Path(__file__).with_name('page.html').read_text(encoding='utf-8'))


def strip_internal(node):
    """분석 도구 실행 번호(tool_run_ids)를 뺀다 — 랜딩은 로그인 없이 열리고 이 값을 쓰지 않는다."""
    if isinstance(node, dict):
        return {key: strip_internal(value) for key, value in node.items() if key != 'tool_run_ids'}
    if isinstance(node, list):
        return [strip_internal(value) for value in node]
    return node


def render_page(code, name, screen):
    """검색 엔진에 여는 종목 페이지. 전망 화면에서 '오늘 일어난 일'(factors)과 참고 기사만 싣는다.

    방향·결론·요약·근거 판단 문장(outlook·conclusion·summary_card·detail 의 문장)은 공개 웹에 두지 않고 앱으로 넘긴다
    — 종목별 방향을 검색 결과에 매일 노출하지 않기로 했다. 문장은 LLM 이 쓴 글이라 전부 escape 하고,
    기사 주소는 http(s) 만 링크로 단다.
    """
    esc = html.escape
    facts = screen['factors']
    if not facts:
        raise ValueError('오늘 일어난 일(factors)이 비었다')
    links = {link['url']: link['title'] for item in screen['detail']['items'] for link in item.get('source_links', [])
             if link.get('url', '').startswith(('https://', 'http://'))}
    when = datetime.fromisoformat(screen['publication']['analysis_at']).astimezone(KST)
    # 설치를 종목 페이지별로 세도록 Play 링크에 꼬리표를 단다(랜딩은 ALPHA-1451)
    referrer = urllib.parse.urlencode({'utm_source': 'etforca.com', 'utm_medium': 'etf_page', 'utm_campaign': code})
    play = 'https://play.google.com/store/apps/details?' + urllib.parse.urlencode({'id': 'com.alphaeveryday.etforca', 'hl': 'ko', 'referrer': referrer})
    return PAGE.substitute(
        name=esc(name), code=esc(code), url=f'{SITE}/etf/{code}', description=esc(facts[0]['sentence']),
        when=f'{when.month}월 {when.day}일 {when:%H:%M}',
        facts='\n'.join(f'<li><b>{esc(fact["type"])}</b>{esc(fact["sentence"])}</li>' for fact in facts),
        news='\n'.join(f'<li><a href="{esc(url)}" target="_blank" rel="nofollow noopener">{esc(title)}</a></li>'
                       for url, title in list(links.items())[:8]),
        play=esc(play))


def sitemap(codes):
    """종목 페이지 주소 목록. 첫 페이지는 랜딩 배포의 sitemap.xml 이 맡고, robots.txt 가 둘 다 가리킨다."""
    urls = ''.join(f'<url><loc>{SITE}/etf/{code}</loc></url>' for code in codes)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'


def export(codes, day, fetch, put, pause=lambda: None, out_of_time=lambda: False):
    """종목마다 그날 전망을 읽어 저장한다. 그날 발행본이 없는 종목은 건너뛰고 앞선 파일을 둔다.

    한 종목의 조회·저장 오류는 나머지를 막지 않는다 — 끝까지 돈 뒤 실패로 끝내 알람이 울리게 한다.
    하나도 쓰지 못해도 실패다 — 조회 경로가 달라져도 응답은 같은 404 라, 0건을 성공으로 두면
    랜딩이 낡은 전망을 계속 보여 줘도 아무도 모른다.
    """
    missing, failed = [], {}
    for code in codes:
        if out_of_time():  # 강제 종료되면 결과 줄도 못 남긴다. 남은 종목을 실패로 적고 스스로 끝낸다
            failed[code] = '함수 제한 시간이 모자라 조회하지 못함'
            continue
        try:
            screen = fetch(code, day)
            if screen is None:
                missing.append(code)
            else:
                put(code, json.dumps(strip_internal(screen), ensure_ascii=False))
        except Exception as error:  # 종목 단위로 가둔다. 사유는 아래 결과 줄에 남는다
            failed[code] = f'{type(error).__name__}: {error}'
        pause()
    result = {'date': day, 'written': len(codes) - len(missing) - len(failed), 'missing': missing, 'failed': failed}
    print(json.dumps(result, ensure_ascii=False))
    if failed or not result['written']:
        raise RuntimeError(f"{day} 전망 내보내기 실패: 저장 {result['written']}종, 오류 {len(failed)}종, 발행본 없음 {len(missing)}종")
    return result


def handler(event, context):
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    session = boto3.session.Session()
    s3 = session.client('s3')
    api_url, bucket = os.environ['ANALYSIS_API_URL'], os.environ['LANDING_BUCKET']

    def fetch(code, day):
        url = f'{api_url}/v2/etfs/{code}/outlook?date={day}'
        signed = AWSRequest(method='GET', url=url)
        SigV4Auth(session.get_credentials(), 'execute-api', session.region_name).add_auth(signed)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=dict(signed.headers)), timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:  # 그날 발행본 없음
                return None
            raise

    names = json.loads(Path(__file__).with_name('etf_names.json').read_text(encoding='utf-8'))

    def put(code, body):
        # no-cache: 랜딩 CDN 의 기본 캐시가 24시간이라, 없으면 어제 전망이 하루 더 보인다.
        s3.put_object(Bucket=bucket, Key=f'outlook/{code}.json', Body=body.encode('utf-8'),
                      ContentType='application/json; charset=utf-8', CacheControl='no-cache')
        # 검색용 페이지는 JSON 뒤에 쓴다 — 페이지가 실패해도 랜딩 검색은 갱신되고, 실패는 그 종목의 오류로 남는다.
        # 확장자 없는 키다(CloudFront 에 주소 변환이 없다. truth/ 와 같은 방식).
        s3.put_object(Bucket=bucket, Key=f'etf/{code}', Body=render_page(code, names.get(code, code), json.loads(body)).encode('utf-8'),
                      ContentType='text/html; charset=utf-8', CacheControl='no-cache')

    # sitemap 은 수동 실행으로 좁힌 범위가 아니라 전체 목록으로 쓴다 — 좁히면 나머지 종목이 검색 엔진에서 빠진다
    all_codes = json.loads(os.environ['ETF_CODES'])
    s3.put_object(Bucket=bucket, Key='etf/sitemap.xml', Body=sitemap(all_codes).encode('utf-8'),
                  ContentType='application/xml', CacheControl='no-cache')

    return export(event.get('etf_codes') or all_codes,
                  event.get('date') or datetime.now(KST).date().isoformat(),
                  fetch, put, pause=lambda: time.sleep(0.25),  # 조회 API 한도가 초당 5건이다
                  # 조회 제한(30초)과 저장을 한 번 더 감당할 시간이 남았을 때만 다음 종목으로 간다
                  out_of_time=lambda: context.get_remaining_time_in_millis() < 60_000)
