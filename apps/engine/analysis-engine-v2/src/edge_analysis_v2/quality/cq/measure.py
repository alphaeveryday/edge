"""What the agent did, counted from saved runs. No model and no judge: every figure comes from the run records.

    python -m edge_analysis_v2.quality.cq.measure <runs folder> [release ...]

An intent hypothesis for a tool change is written before the run and read off these counts after it.
"""
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from edge_analysis_v2.quality.cq.evaluation import read, records
from edge_analysis_v2.tools.graph.provider import BROAD

SEARCHES = ('find_similar_events', 'search_objects')
NUMBER = r'\d[\d,]*(?:\.\d+)?'
AMOUNT = re.compile(rf'(?<![\d.,])(?:({NUMBER})\s*조)?\s*(?:({NUMBER})\s*억)?\s*(?:({NUMBER})\s*만)?\s*원?')
RAW = re.compile(r'(?<![\w.])-?\d{7,}(?:\.\d+)?(?![\w])')
UNITS = (Decimal(10)**12, Decimal(10)**8, Decimal(10)**4)


def answered(runs_dir, releases=()):
    """Answered runs as (report, calls in order), limited to the named releases if any."""
    for path in sorted(Path(runs_dir).glob('*/benchmark.json')):
        report = read(path)
        if report.get('status') == 'answered' and (not releases or (report.get('agent_version') or {}).get('release') in releases):
            yield report, records(path.parent)


def followups(calls):
    """What followed each partial, broad, empty or failed result within one run.

    - partial: a result that did not fit on its page; followed = a later page of it was read, or the same
      tool was called again with different arguments
    - broad: a search matching more than BROAD rows; narrower = a later call of that tool matched fewer rows
    - empty / errors: retried = the same tool was called again with different arguments
    """
    s = defaultdict(int, calls=len(calls))
    for i, call in enumerate(calls):
        result, later = call['response'].get('result') or {}, calls[i + 1:]
        again = any(c['tool'] == call['tool'] and c['arguments'] != call['arguments'] for c in later)
        if call['tool'] != 'get_result_page' and isinstance(result.get('page'), dict) and result['page'].get('complete') is False:
            paged = any(c['tool'] == 'get_result_page'
                        and (c['arguments'].get('dataset_ref') or {}).get('tool_run_id') == call['response']['tool_run_id'] for c in later)
            s['partial'] += 1
            s['paged'] += paged
            s['followed'] += paged or again
        if call['tool'] in SEARCHES and result.get('total_rows', 0) > BROAD:
            smaller = [c for c in later if c['tool'] == call['tool'] and not c.get('error')
                       and 0 < (c['response'].get('result') or {}).get('total_rows', 0) < result['total_rows']]
            s['broad'] += 1
            s['broad_then_narrower'] += bool(smaller)
            s['broad_then_readable'] += any(c['response']['result']['total_rows'] <= BROAD for c in smaller)
        if not call.get('error') and result.get('total_rows') == 0:
            s['empty'] += 1
            s['empty_retried'] += again
        if call.get('error'):
            s['errors'] += 1
            s['errors_retried'] += again
            s['wrong_argument'] += 'wrong argument' in str(call['error'])
    return s


def amounts(text):
    """(won, tolerance, wording) for each 조/억 amount; the tolerance is one unit of the last digit written."""
    found = []
    for match in AMOUNT.finditer(text):
        parts = match.groups()
        if not (parts[0] or parts[1]):
            continue
        won, step = Decimal(0), None
        for part, unit in zip(parts, UNITS):
            if part:
                digits = part.replace(',', '')
                won += Decimal(digits)*unit
                step = unit/(Decimal(10)**len(digits.partition('.')[2]))
        found.append((won, step, match.group().strip()))
    return found


def check_amounts(answer, returned):
    """Classify each won amount the answer wrote against every number the run's tools returned.

    matched: a returned number is within the precision written. magnitude: only ten times or a tenth of one
    matches (14.47억 written as 145억). unmatched: neither; the answer's own sums land here too, so read the list.
    """
    returned = returned.replace('兆', '조').replace('億', '억')   # headlines write the units in hanja
    numbers = [abs(Decimal(m.group())) for m in RAW.finditer(returned)] + [won for won, _, _ in amounts(returned)]
    rows = []
    for won, step, wording in amounts(answer):
        near = lambda scale: any(abs(n*scale - won) <= step for n in numbers)
        rows.append(('matched' if near(1) else 'magnitude' if near(10) or near(Decimal('0.1')) else 'unmatched', wording))
    return rows


def main(runs_dir, releases):
    totals, listed = defaultdict(lambda: defaultdict(int)), []
    for report, calls in answered(runs_dir, releases):
        s = totals[report['agent_version']['release']]
        s['runs'] += 1
        for key, value in followups(calls).items():
            s[key] += value
        returned = ''.join(json.dumps(c['response'], ensure_ascii=False) for c in calls)
        for kind, wording in check_amounts(json.dumps(report['response'], ensure_ascii=False), returned):
            s['amounts'] += 1
            s[kind] += 1
            if kind != 'matched':
                listed.append(f"  release {report['agent_version']['release']} {report['case'][:4]} {kind}: {wording}")
    for number in sorted(totals):
        s = totals[number]
        share = lambda a, b: f"{s[a]}/{s[b]}" + (f" ({100*s[a]/s[b]:.0f}%)" if s[b] else '')
        print(f"release {number}: runs {s['runs']}, calls {s['calls']}, partial results followed up {share('followed', 'partial')} "
              f"[paged {s['paged']}], broad searches then narrower {share('broad_then_narrower', 'broad')}, "
              f"then readable {share('broad_then_readable', 'broad')}, empty retried {share('empty_retried', 'empty')}, "
              f"errors retried {share('errors_retried', 'errors')}, wrong-argument errors {s['wrong_argument']}; "
              f"won amounts {s['amounts']}: matched {s['matched']}, magnitude {s['magnitude']}, unmatched {s['unmatched']}")
    print('\n'.join(listed))


if __name__ == '__main__':
    main(sys.argv[1], tuple(int(v) for v in sys.argv[2:]))
