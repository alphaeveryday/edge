"""Newsletter jobs share the existing analysis worker and its restricted tool harness."""
import logging
import os

from . import newsletter_store
from .newsletter_agent import write_card

log = logging.getLogger('portfolio-newsletter')


async def work_once(stopping=None):
    job = newsletter_store.claim_content()
    if not job:
        return False
    if stopping is not None and stopping.is_set():
        newsletter_store.release_content(job)
        return False
    try:
        result = await write_card({'instrument_id': job['instrument_id'], 'name': job['name']},
                                  job['edition'], os.environ['DEEPSEEK_API_KEY'])
        saved = newsletter_store.finish_content(job, value=result)
        log.info('instrument=%s edition=%s newsletter=%s', job['instrument_id'], job['edition'], 'ready' if saved else 'discarded')
    except Exception as error:
        # No raw prompts, account information, recipient addresses or provider response bodies in logs.
        saved = newsletter_store.finish_content(job, failure=type(error).__name__)
        log.warning('instrument=%s edition=%s newsletter=%s code=%s', job['instrument_id'], job['edition'], 'failed' if saved else 'discarded', type(error).__name__)
    return True
