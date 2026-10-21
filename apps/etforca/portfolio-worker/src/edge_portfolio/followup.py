"""Revisit an owned report through the same research and constraint checks."""
from copy import deepcopy

from pydantic import Field

from .agent import run_analysis
from .contracts import AnalysisInput


class FollowupInput(AnalysisInput):
    # Two independently bounded customer messages; the public analysis limit stays 1,000.
    question: str = Field(max_length=2100)
    funding_questions: list[str] = Field(exclude=True, min_length=2, max_length=2)


async def answer_question(question, report, key, *, on_stage=None, on_event=None,
                          on_checkpoint=None, revision_id=None, model_call=None, evidence=None, market=None):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
        raise ValueError('INVALID_FOLLOWUP_QUESTION')
    original = AnalysisInput.model_validate(report['input'], context={'persisted_input': True})
    payload = original.model_dump(mode='json')
    payload['question'] = original.question + '\n\n후속 질문: ' + question
    payload['funding_questions'] = [original.question, question]
    request = FollowupInput.model_validate(payload, context={'persisted_input': True})
    revised = await run_analysis(request, key, on_stage or (lambda role: None),
        model_call=model_call, evidence=evidence, market=market, on_event=on_event,
        on_checkpoint=on_checkpoint, prior_report=deepcopy(report), followup_question=question)
    return {'answer': revised['sections']['opinion'], 'sources': revised['sources'],
            'as_of': revised['as_of'], 'parent_as_of': report.get('as_of'),
            'status': revised['completion']['status'], 'revision_id': revision_id,
            'revision': revised, 'elapsed_seconds': revised['elapsed_seconds'],
            'diagnostics': revised['diagnostics']}
