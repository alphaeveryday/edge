import asyncio
import copy
from contextlib import suppress
import json
import logging
import os
import signal
import time
from threading import Event
from pathlib import Path
from typing import get_args

from pydantic import ValidationError
from pydantic_core import ErrorType
import httpx

from . import store
from .agent import run_analysis
from .contracts import AnalysisInput, FinalReport
from .followup import answer_question

log = logging.getLogger("portfolio-worker")
HEARTBEAT = Path('/tmp/portfolio-worker-ready')


def failure_codes(error):
    if isinstance(error, BaseExceptionGroup):
        return [code for child in error.exceptions for code in failure_codes(child)]
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429 and error.request.url.host == 'mcp.exa.ai':
        return ['EXA_RATE_LIMIT']
    prefix = str(error).split(':', 1)[0]
    allowed = {'EXA_FAILED', 'EMPTY_SOURCE', 'TOOL_NOT_ALLOWED', 'TOOL_LIMIT', 'INVALID_TOOL_ARGUMENT', 'OUTPUT_INVALID', 'MODEL_BUDGET', 'MODEL_CALL_BUDGET', 'MODEL_CONTEXT_BUDGET', 'MODEL_TRUNCATED'}
    if prefix == 'OUTPUT_INVALID':
        cause = error.__cause__
        if isinstance(cause, json.JSONDecodeError):
            return ['OUTPUT_INVALID', 'JSON_INVALID']
        if isinstance(cause, ValidationError):
            schema = FinalReport.model_json_schema()
            fields = {key for model in [schema, *schema.get('$defs', {}).values()] for key in model.get('properties', {})}
            codes = []
            for issue in cause.errors(include_input=False, include_context=False, include_url=False)[:6]:
                path = [part if part in fields else 'FIELD' for part in issue['loc'] if not isinstance(part, int)]
                kind = issue['type'] if issue['type'] in get_args(ErrorType) else 'VALIDATION'
                codes.append('_'.join(['SCHEMA', *path, kind]).upper())
            return ['OUTPUT_INVALID', *codes]
        reasons = {'must exactly match': 'ALLOCATION_MISMATCH', 'Call read_portfolio_history': 'HISTORY_NOT_READ',
                   'Evidence must select': 'EVIDENCE_INVALID', 'paragraphs must total': 'SECTION_LENGTH',
                   'each bullet must be one sentence': 'BULLET_FORMAT', 'No eligible candidate': 'NO_ELIGIBLE_CANDIDATE',
                   'require source evidence': 'EVIDENCE_REQUIRED', 'Market estimates must cite': 'FORECAST_EVIDENCE',
                   'account was not selected': 'ACCOUNT_NOT_SELECTED', 'cannot be allocated directly': 'ACCOUNT_MARKET',
                   'Account subtype does not support': 'ACCOUNT_SUBTYPE'}
        return ['OUTPUT_INVALID', next((code for text, code in reasons.items() if text in str(error)), 'OUTPUT_SCHEMA')]
    return [prefix if prefix in allowed else type(error).__name__]


async def work_once(stopping=None):
    job = store.claim()
    HEARTBEAT.touch()
    if not job:
        return False
    if stopping is not None and stopping.is_set():
        store.release_claim(job['id'], job['started_at'])
        log.info('job=%s released unstarted claim during shutdown', job['id'])
        return False
    started = time.monotonic()
    diagnostics = {'events': [], 'termination_reason': 'RUNNING'}
    partial = {}
    pending = {}
    changed = asyncio.Event()

    def save_checkpoint(result):
        pending.update(result)
        changed.set()

    async def write_checkpoints():
        while True:
            await changed.wait()
            changed.clear()
            snapshot = copy.deepcopy(pending)
            try:
                await asyncio.to_thread(store.checkpoint, job['id'], snapshot)
            except Exception as error:
                log.warning('job=%s checkpoint_failed code=%s', job['id'], type(error).__name__)

    def on_event(event):
        diagnostics['events'].append(event)
        if event['event'] == 'run_started':
            diagnostics.update({key: event[key] for key in ('model', 'prompt_version', 'code_version', 'limits')})
        if event['event'] == 'model_started':
            diagnostics['model_calls'] = event['call']
        if 'counters' in event:
            diagnostics.update(event['counters'])
        save_checkpoint({'diagnostics': diagnostics})

    def on_checkpoint(result):
        partial.clear()
        partial.update(result)
        save_checkpoint({'partial_report': partial})
    checkpoint_writer = asyncio.create_task(write_checkpoints())
    try:
        if job["kind"] == "chat":
            store.stage(job["id"], "opinion")
            parent = store.read(job["parent_id"], job["owner"])
            saved = parent.get('result') or {}
            report = saved.get('partial_report', {}) if parent['state'] == 'failed' else saved
            report = {**report, 'input': parent['input'], 'parent_state': parent['state']}
            result = await answer_question(job["input"]["question"], report, os.environ["DEEPSEEK_API_KEY"],
                on_stage=lambda role: store.stage(job['id'], role), on_event=on_event,
                on_checkpoint=on_checkpoint, revision_id=job['id'])
        else:
            result = await run_analysis(AnalysisInput.model_validate(job["input"], context={'persisted_input': True}), os.environ["DEEPSEEK_API_KEY"],
                                        lambda role: store.stage(job["id"], role), on_event=on_event, on_checkpoint=on_checkpoint)
        store.finish(job["id"], result=result)
        log.info("job=%s status=succeeded seconds=%s", job["id"], result.get("elapsed_seconds"))
    except Exception as error:
        codes = failure_codes(error)
        failure = 'SEARCH_RATE_LIMIT' if 'EXA_RATE_LIMIT' in codes else "TIMED_OUT" if any(code in {'TimeoutError', 'ReadTimeout', 'ConnectTimeout', 'WriteTimeout', 'PoolTimeout'} for code in codes) else "ANALYSIS_FAILED"
        diagnostics.update({'termination_reason': failure, 'failure_codes': codes})
        store.finish(job["id"], failure=failure, result={'diagnostics': diagnostics, 'partial_report': partial,
            'elapsed_seconds': round(time.monotonic() - started, 2)})
        log.warning("job=%s status=failed codes=%s", job["id"], codes)
    finally:
        checkpoint_writer.cancel()
        with suppress(asyncio.CancelledError):
            await checkpoint_writer
    return True


async def dashboard_loop(stopping):
    from .dashboard_work import work_once as dashboard_once
    from .dashboard_quotes import collection_loop
    async def loop(kind):
        while not stopping.is_set():
            try:
                await dashboard_once(stopping, kind=kind)
            except Exception as error:
                log.warning('dashboard loop failed kind=%s code=%s; retrying', kind, type(error).__name__)
            await asyncio.sleep(1)
    await asyncio.gather(loop('valuation'), loop('backtest'), collection_loop(stopping))


async def main():
    HEARTBEAT.unlink(missing_ok=True)
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    store.initialize()
    stopping = Event()

    def request_stop(number, frame):
        stopping.set()
        log.info('worker draining signal=%s; finishing the current job', number)

    handlers = {number: signal.signal(number, request_stop) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        # Quotes must progress even while the customer analysis queue stays busy.
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(asyncio.to_thread(lambda: asyncio.run(dashboard_loop(stopping))))
            try:
                while not stopping.is_set():
                    worked = await work_once(stopping)
                    if not worked and not stopping.is_set():
                        from .newsletter_work import work_once as newsletter_once
                        await newsletter_once(stopping)
                        HEARTBEAT.touch()
                    await asyncio.sleep(0 if worked else 1)
            finally:
                stopping.set()
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
        HEARTBEAT.unlink(missing_ok=True)
        log.info('worker stopped')


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
