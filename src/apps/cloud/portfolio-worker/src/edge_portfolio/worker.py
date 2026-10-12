import asyncio
import logging
import os

from . import store
from .agent import run_analysis
from .contracts import AnalysisInput
from .followup import answer_question

log = logging.getLogger("portfolio-worker")


async def work_once():
    job = store.claim()
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
        log.warning("job=%s status=failed type=%s", job["id"], type(error).__name__)
    return True


async def main():
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    store.initialize()
    while True:
        if not await work_once():
            await asyncio.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
