"""Prepare short and medium window flow through the same audited calls used by the agent."""
from edge_analysis_v2.tools.execution import ToolExecutionError


def current_flow(call):
    """Expose both windows, their source IDs, and explicit failures; never infer missing values."""
    result = []
    for investor in ('foreign', 'institution'):
        for days in (5, 20):
            arguments = {'investor': investor, 'lookback_days': days}
            try:
                result.append(call('sum_weighted_net_flow', arguments))
            except ToolExecutionError as error:
                result.append(arguments | {'status': 'unavailable', 'error': str(error)})
    return result
