"""그날 발행된 전망 화면을 랜딩 버킷으로 내보낸다(ALPHA-1267).

etforca.com 의 종목 검색이 읽는 파일(outlook/<코드>.json)을 만든다. 전망은 분석 v2 조회 API 로만 읽는다 —
분석 DB·내부 코드에 기대지 않는다. 스케줄러가 매일 08:00 KST 에 부른다(전망 배치는 06:00 시작, 08:00 마감).

수동 실행은 event 로 범위를 좁힌다: {"etf_codes": ["091160"], "date": "2026-10-12"}.
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def strip_internal(node):
    """분석 도구 실행 번호(tool_run_ids)를 뺀다 — 랜딩은 로그인 없이 열리고 이 값을 쓰지 않는다."""
    if isinstance(node, dict):
        return {key: strip_internal(value) for key, value in node.items() if key != 'tool_run_ids'}
    if isinstance(node, list):
        return [strip_internal(value) for value in node]
    return node


def export(codes, day, fetch, put, pause=lambda: None):
    """종목마다 그날 전망을 읽어 저장한다. 그날 발행본이 없는 종목은 건너뛰고 앞선 파일을 둔다.

    한 종목의 조회·저장 오류는 나머지를 막지 않는다 — 끝까지 돈 뒤 실패로 끝내 알람이 울리게 한다.
    하나도 쓰지 못해도 실패다 — 조회 경로가 달라져도 응답은 같은 404 라, 0건을 성공으로 두면
    랜딩이 낡은 전망을 계속 보여 줘도 아무도 모른다.
    """
    missing, failed = [], {}
    for code in codes:
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
        # 조회 1회를 5초로 끊는다 — 전 종목이 다 느려도 (5 + 간격 0.25)초 × 148종이 함수 제한 900초 안에 든다.
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=dict(signed.headers)), timeout=5) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:  # 그날 발행본 없음
                return None
            raise

    def put(code, body):
        # no-cache: 랜딩 CDN 의 기본 캐시가 24시간이라, 없으면 어제 전망이 하루 더 보인다.
        s3.put_object(Bucket=bucket, Key=f'outlook/{code}.json', Body=body.encode('utf-8'),
                      ContentType='application/json; charset=utf-8', CacheControl='no-cache')

    return export(event.get('etf_codes') or json.loads(os.environ['ETF_CODES']),
                  event.get('date') or datetime.now(KST).date().isoformat(),
                  fetch, put, pause=lambda: time.sleep(0.25))  # 조회 API 한도가 초당 5건이다
