import asyncio
import logging
import os
from pathlib import Path

from . import store
from .agent import run_analysis
from .contracts import AnalysisInput
from .followup import answer_question

log = logging.getLogger("portfolio-worker")
HEARTBEAT = Path('/tmp/portfolio-worker-ready')


def failure_codes(error):
    if isinstance(error, BaseExceptionGroup):
        return [code for child in error.exceptions for code in failure_codes(child)]
    prefix = str(error).split(':', 1)[0]
    allowed = {'EXA_FAILED', 'EMPTY_SOURCE', 'TOOL_NOT_ALLOWED', 'TOOL_LIMIT', 'INVALID_TOOL_ARGUMENT', 'OUTPUT_INVALID', 'MODEL_BUDGET', 'MODEL_TRUNCATED'}
    return [prefix if prefix in allowed else type(error).__name__]


async def work_once():
    job = store.claim()
    HEARTBEAT.touch()
    if not job:
        return False
    try:
        if job["kind"] == "chat":
            store.stage(job["id"], "opinion")
            parent = store.read(job["parent_id"], job["owner"])
            result = await answer_question(job["input"]["question"], parent["result"], os.environ["DEEPSEEK_API_KEY"])
        else:
            result = await run_analysis(AnalysisInput.model_validate(job["input"]), os.environ["DEEPSEEK_API_KEY"],
                                        lambda role: store.stage(job["id"], role))
        store.finish(job["id"], result=result)
        log.info("job=%s status=succeeded seconds=%s", job["id"], result.get("elapsed_seconds"))
    except Exception as error:
        failure = "TIMED_OUT" if isinstance(error, TimeoutError) else "ANALYSIS_FAILED"
        store.finish(job["id"], failure=failure)
        log.warning("job=%s status=failed codes=%s", job["id"], failure_codes(error))
    return True


async def main():
    HEARTBEAT.unlink(missing_ok=True)
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    store.initialize()
    try:
        while True:
            worked = await work_once()
            if not worked:
                await asyncio.sleep(1)
    finally:
        HEARTBEAT.unlink(missing_ok=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
