"""Read-only KIS coverage probe. Saves public responses, never credentials.

Run with the pipeline AWS profile, AWS_DEFAULT_REGION=ap-northeast-2 and the pipeline
source directory on PYTHONPATH. This is a single-page capability probe, not an
ingestion job: continuation is reported explicitly and never called complete.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

import boto3

from data_pipeline.sources.http import PoliteClient
from data_pipeline.sources.kis_auth import KisAuth, domain_for


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', default='20250101')
    parser.add_argument('--end', default='20261018')
    parser.add_argument('--secret-id', default='edge-dev-data-pipeline/kis/oauth')
    parser.add_argument('--kr-symbols', default='005930,069500,360750,379800,458730,0093A0')
    parser.add_argument('--us-symbols', default='VOO,IVV,SCHD,JEPI,NVDL')
    parser.add_argument('--product-type', default='')
    parser.add_argument('--rights-type', default='03')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        secret = json.loads(boto3.client('secretsmanager').get_secret_value(
            SecretId=args.secret_id)['SecretString'])
        token = KisAuth(secret['app_key'], secret['app_secret'], PoliteClient()).token()
        headers = {'authorization': f'Bearer {token}', 'appkey': secret['app_key'],
                   'appsecret': secret['app_secret'], 'custtype': 'P'}
        cases = []
        for symbol in filter(None, args.kr_symbols.split(',')):
            cases.append((f'kr-{symbol}', '/uapi/domestic-stock/v1/ksdinfo/dividend',
                          'HHKDB669102C0', dict(CTS='', GB1='0', F_DT=args.start,
                                               T_DT=args.end, SHT_CD=symbol, HIGH_GB='')))
        for symbol in filter(None, args.us_symbols.split(',')):
            cases.append((f'us-amount-{symbol}', '/uapi/overseas-price/v1/quotations/period-rights',
                          'CTRGT011R', dict(RGHT_TYPE_CD=args.rights_type, INQR_DVSN_CD='02',
                          INQR_STRT_DT=args.start, INQR_END_DT=args.end, PDNO=symbol,
                          PRDT_TYPE_CD=args.product_type, CTX_AREA_NK50='', CTX_AREA_FK50='')))
            cases.append((f'us-dates-{symbol}', '/uapi/overseas-price/v1/quotations/rights-by-ice',
                          'HHDFS78330900', dict(NCOD='US', SYMB=symbol,
                                               ST_YMD=args.start, ED_YMD=args.end)))
        failed = False
        client = PoliteClient(min_interval=1, timeout=25)
        for name, path, tr_id, params in cases:
            try:
                response_headers = {}
                response = client.request('GET', domain_for('prod') + path + '?' + urlencode(params),
                    headers={**headers, 'tr_id': tr_id}, response_headers=response_headers)
                body = json.loads(response)
                continuation = response_headers.get('tr_cont', '')
                evidence = dict(query=params, tr_id=tr_id, continuation=continuation, body=body)
                (args.output / f'{name}.json').write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
                rows = body.get('output1', body.get('output', []))
                ok = body.get('rt_cd') == '0'
                failed |= not ok
                print(json.dumps(dict(case=name, ok=ok, code=body.get('msg_cd'),
                    rows=len(rows) if isinstance(rows, list) else None,
                    continuation=continuation)), flush=True)
            except Exception as error:
                failed = True
                print(json.dumps(dict(case=name, error=type(error).__name__)), flush=True)
        return int(failed)
    except Exception as error:
        print(json.dumps(dict(error=type(error).__name__)), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
