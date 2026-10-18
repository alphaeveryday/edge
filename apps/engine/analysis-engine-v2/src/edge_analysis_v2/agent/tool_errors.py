"""Keep caller mistakes repairable; infrastructure failure terminates the conversation."""
import json
import logging

from edge_analysis_v2.tools.execution import ToolExecutionError

LOG = logging.getLogger(__name__)


def input_error(message, *, tool_run_id=None, code="invalid_arguments", retryable=True):
    error = {'code': code, 'message': message, 'retryable': retryable}
    if tool_run_id:
        error['tool_run_id'] = tool_run_id
    return error_response(error)


def error_response(error):
    return {'is_error': True, 'content': [{'type': 'text', 'text': json.dumps({'error': error}, ensure_ascii=False)}]}


class ToolFailureState:
    """A fatal callback cannot become a publication even if the SDK catches its exception."""

    def __init__(self):
        self.error = None

    def fail(self, error):
        if self.error is None:
            self.error = {'code': 'tool_execution_failed', 'retryable': False,
                          'message': 'Tool execution failed. This analysis must stop; retrying arguments cannot repair infrastructure.'}
            if isinstance(error, ToolExecutionError):
                self.error['tool_run_id'] = error.tool_run_id
            # Never log exception text: driver errors can contain credentials or source data.
            LOG.error('Fatal tool boundary failure type=%s', type(error).__name__)
        return error_response(self.error)

    def blocked(self):
        return error_response(self.error) if self.error else None

    def check(self):
        if self.error:
            raise RuntimeError(self.error['message'])
