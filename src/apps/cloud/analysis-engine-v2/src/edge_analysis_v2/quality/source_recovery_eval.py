"""Paid SDK probe: recover from an invalid source selector using the tool's guidance."""
import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from edge_analysis_v2.agent import runner
from edge_analysis_v2.agent.work_context import SourceInputError, WorkContext
from edge_analysis_v2.dashboard.jobs import read_settings


async def evaluate(folder, case, key, model):
    calls = []
    initial = {'news':[{'title':'Source-only headline 7F31'}],
               'prices':{'NOVA':{'columns':['close'], 'rows':[[43.5]]}}}
    source = 'news' if case == 'list' else 'prices'
    expected = 'Source-only headline 7F31' if case == 'list' else '43.5'

    class ObservedContext(WorkContext):
        def read(self, **args):
            try:
                result = super().read(**args)
            except SourceInputError as error:
                calls.append({'arguments':args, 'error':error.details})
                raise
            calls.append({'arguments':args, 'result':result})
            return result

    schema = {'type':'object','properties':{'answer':{'type':'string'}},
              'required':['answer'],'additionalProperties':False}
    prompt = (
        'This is a controlled tool recovery test, not an investment report. '
        'Register the question with workspace.update_tasks. '
        f'Your first read_source call must deliberately use source="{source}" and subject="WRONG_KEY". '
        'After that error, use only the tool description, catalog and error recovery guidance to obtain '
        + ('the first news title' if case == 'list' else 'the NOVA closing price')
        + '. Do not guess further selectors. Mark the task completed after reading the actual value. '
        'Return that exact value as the answer string. Do not use skills or external research tools.')
    try:
        with patch.object(runner, 'WorkContext', ObservedContext):
            result = await runner.run_model(initial=initial, prompt=prompt, schemas=[], call=lambda *a:None,
                output_schema=schema, artifacts=folder, key=key, model=model, timeout_seconds=120)
        errors = [call for call in calls if 'error' in call]
        checks = {
            'exact_value_read':result['answer'] == expected,
            'one_deliberate_error_only':len(errors) == 1 and calls[0]['arguments'].get('subject') == 'WRONG_KEY',
            'next_read_recovered':len(calls) >= 2 and 'result' in calls[1],
        }
        report = {'case':case, 'model':model, 'result':result, 'checks':checks, 'calls':calls}
        (folder/'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
        if not all(checks.values()):
            raise ValueError('Source recovery evaluation failed: ' + ', '.join(k for k,v in checks.items() if not v))
    finally:
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'source_calls.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--model', default='deepseek-flash')
    parser.add_argument('--case', choices=['list','dictionary'], required=True)
    args = parser.parse_args()
    asyncio.run(evaluate(args.artifacts, args.case, read_settings(args.env_file)['key'], args.model))


if __name__ == '__main__':
    main()
