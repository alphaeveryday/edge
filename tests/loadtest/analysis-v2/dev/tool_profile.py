"""끝난 배치의 성공 분석마다 툴 감사 기록의 시각을 읽어 로컬 부하 탐침의 시간 프로필을 만든다.

S3 관측 산출물(manifest·evidence.json)만 읽는다. 분석·DB 조회·태스크를 새로 시작하지 않는다.
프로필 = 분석마다 [작업 시작 → 각 툴 기록 시작] 초 목록과 [작업 시작 → 작업 끝] 초. local/load900.py 가 그대로 재생한다.

    AWS_PROFILE=edge uv run --no-project --with boto3 python tool_profile.py <실행 이름>   # results/<실행 이름>.json 이 먼저 있어야 한다
"""
import argparse
import json
import statistics
from datetime import datetime, timezone

from input_overlap import read
from batch_run import RESULTS


def main(name):
    batch = json.loads((RESULTS/f'{name}.json').read_text())
    began = datetime.fromisoformat(batch['started'])
    ended = datetime.fromisoformat(batch['stopped']) if batch['stopped'] else datetime.now(timezone.utc)
    runs = []
    for row in batch['rows']:
        attempt = next((a for a in row['attempts'] if a['status'] == 'SUCCEEDED'
                        and began <= datetime.fromisoformat(a['started']) <= ended), None)
        if attempt is None:
            continue
        manifest = read(f"analysis-v2/runs/{attempt['analysis_id']}/manifest.json")
        evidence = read(manifest['files']['evidence.json']['key'])
        start = datetime.fromisoformat(manifest['job']['started_at'])
        finish = datetime.fromisoformat(manifest['job']['finished_at'])
        offsets = sorted((datetime.fromisoformat(r['started_at']) - start).total_seconds() for r in evidence['tool_runs'])
        runs.append({'etf': row['etf_code'], 'job_s': round((finish - start).total_seconds(), 2),
                     'tool_offsets_s': [round(x, 3) for x in offsets]})
    if not runs:
        raise SystemExit('성공한 분석이 없어 프로필이 없다')
    gaps = [b - a for r in runs for a, b in zip(r['tool_offsets_s'], r['tool_offsets_s'][1:])]
    q = lambda values, p: sorted(values)[min(len(values) - 1, int(len(values) * p))]
    summary = {'execution': name, 'analyses': len(runs),
               'tools_min_median_max': [min(len(r['tool_offsets_s']) for r in runs),
                                        statistics.median(len(r['tool_offsets_s']) for r in runs),
                                        max(len(r['tool_offsets_s']) for r in runs)],
               'job_s_min_median_max': [min(r['job_s'] for r in runs), statistics.median(r['job_s'] for r in runs),
                                        max(r['job_s'] for r in runs)],
               'first_tool_s_median': statistics.median(r['tool_offsets_s'][0] for r in runs if r['tool_offsets_s']),
               'gap_s_p10_p50_p90_max': [round(q(gaps, p), 3) for p in (0.1, 0.5, 0.9)] + [round(max(gaps), 1)],
               'gaps_under_1s_share': round(sum(g < 1 for g in gaps) / len(gaps), 3)}
    path = RESULTS/f'{name}.tool_profile.json'
    path.write_text(json.dumps(summary | {'runs': runs}, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print('saved', path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name', help='batch_run.py report 가 남긴 실행 이름')
    main(parser.parse_args().name)
