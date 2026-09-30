"""Agent-facing tool descriptors, separate from server validation rules."""
from copy import deepcopy

DATA_SELECTORS = {'instrument_id', 'news_ids', 'thread_id', 'as_of_date',
                  'trade_date', 'start_observation', 'end_observation'}


def agent_tool_schemas(schemas):
    """Omit repeated data choices while preserving small semantic enums.

    Args:
        schemas: Original function descriptors retained for server validation.

    Returns:
        Independent descriptors; data selectors retain their types and bounds.
    """
    visible = deepcopy(schemas)
    for schema in visible:
        properties = schema['function']['parameters'].get('properties', {})
        for name in DATA_SELECTORS & properties.keys():
            rule = properties[name]
            rule.pop('enum', None)
            if isinstance(rule.get('items'), dict):
                rule['items'].pop('enum', None)
    return visible
