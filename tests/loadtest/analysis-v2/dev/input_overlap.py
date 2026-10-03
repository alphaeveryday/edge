"""끝난 배치의 분석 입력을 읽어 ETF 사이 중복과 시작 구간을 잰다 — 입력 공유의 효과를 어림하는 근거.

S3 관측 산출물(manifest·input.json·evidence.json)만 읽는다. 분석·DB 조회·태스크를 새로 시작하지 않는다.
input.json 은 에이전트 초기 입력이라 원천 전체가 아니다(뉴스는 일부만). 구성종목·거시·재무 공백은 온전하다.

    AWS_PROFILE=edge uv run --no-project --with boto3 python batch_run.py report <실행 이름>   # 먼저 results/<실행 이름>.json
    AWS_PROFILE=edge uv run --no-project --with boto3 python input_overlap.py <실행 이름>
"""
import argparse
import collections
import hashlib
import json
import statistics
from datetime import datetime

import boto3

from batch_run import BUCKET, REGION, RESULTS

s3 = boto3.client('s3', region_name=REGION)


def read(key):
    return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read())


def main(name):
    batch = json.loads((RESULTS/f'{name}.json').read_text())
    current, seen = collections.Counter(), collections.Counter()
    macros, per, gaps, starts = set(), {}, collections.Counter(), []
    for row in batch['rows']:
        attempt = next((a for a in row['attempts'] if a['status'] == 'SUCCEEDED'), None)
        if attempt is None:
            continue
        manifest = read(f"analysis-v2/runs/{attempt['analysis_id']}/manifest.json")
        data, evidence = read(manifest['files']['input.json']['key']), read(manifest['files']['evidence.json']['key'])
        # 재무 조회는 현재 보유종목만, 일봉·수급 조회는 최근 스냅샷(최대 40개)에 한 번이라도 든 종목 전체다
        members = {h['instrument_id'] for h in data['holdings']['holdings']}
        observed = {i['instrument_id'] for i in data['instruments']} - {row['etf_code']}
        current.update(members)
        seen.update(observed)
        macros.add(hashlib.sha256(json.dumps(data['macro'], sort_keys=True).encode()).hexdigest())
        for gap in data['source_gaps'].get('financials', []):
            gaps.update(gap.get('reasons', {}).values())
        # 시작 → 첫 툴 기록: 원천 읽기, writer 의 거시·재무 조회, 정의 등록, 첫 모델 응답까지 포함한 상한
        first = min((datetime.fromisoformat(r['started_at']) for r in evidence['tool_runs']), default=None)
        start = (first - datetime.fromisoformat(manifest['job']['started_at'])).total_seconds() if first else None
        per[row['etf_code']] = {'constituents': len(members), 'observed_instruments': len(observed),
                                'start_to_first_tool_s': round(start, 1) if first else None,
                                'tool_runs': len(evidence['tool_runs']),
                                'news_limit_reached': data['news_scope'].get('limit_reached')}
        if first:
            starts.append(start)
    if not per:
        raise SystemExit('성공한 분석이 없어 잴 입력이 없다')
    spread = lambda values: [round(min(values), 1), round(statistics.median(values), 1), round(max(values), 1)] if values else None
    total = sum(current.values())
    result = {'execution': name, 'analyses': len(per), 'constituents_sum': total, 'constituents_unique': len(current),
              'constituent_sizes_min_median_max': spread([v['constituents'] for v in per.values()]),
              'in_two_or_more_etfs': sum(c >= 2 for c in current.values()), 'top_shared': current.most_common(5),
              # 거시 5계열은 기준시각만 같으면 ETF 와 무관하다. 재무는 현재 보유종목마다 1회 호출이다.
              'research_calls_per_etf_sum': 5*len(per) + total, 'research_calls_shared': 5 + len(current),
              'observed_instruments_sum': sum(seen.values()), 'observed_instruments_unique': len(seen),
              'distinct_macro_payloads': len(macros), 'financial_gap_reasons': dict(gaps),
              'start_to_first_tool_s_min_median_max': spread(starts),
              'news_limit_reached': sum(bool(v['news_limit_reached']) for v in per.values()), 'per_etf': per}
    path = RESULTS/f'{name}.overlap.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'per_etf'}, ensure_ascii=False, indent=1))
    print('saved', path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name', help='batch_run.py report 가 남긴 실행 이름')
    main(parser.parse_args().name)
