"""Agent-facing tool descriptors, separate from server validation rules."""
from copy import deepcopy

DATA_SELECTORS = {'instrument_id', 'news_ids', 'thread_id', 'as_of_date',
                  'trade_date', 'start_observation', 'end_observation'}


def _describe_sizes(node):
    """Turn maxLength/maxItems into description text.

    The SDK checks the visible schema itself and answers with the first failure only, echoing the
    text and naming no location. Left to the server, every overflow comes back at once by location.
    """
    if isinstance(node, list):
        for child in node:
            _describe_sizes(child)
    if not isinstance(node, dict):
        return
    for keyword, unit in (('maxLength', '자'), ('maxItems', '개')):
        if keyword in node:
            node['description'] = (node.get('description', '') + f' 최대 {node.pop(keyword)}{unit}.').strip()
    for child in node.values():
        _describe_sizes(child)


def agent_tool_schemas(schemas):
    """Omit repeated data choices while preserving small semantic enums.

    Size limits stay visible as description text and are enforced by the server schema.

    Args:
        schemas: Original function descriptors retained for server validation.

    Returns:
        Independent descriptors; data selectors retain their types and bounds.
    """
    visible = deepcopy(schemas)
    for schema in visible:
        _describe_sizes(schema['function']['parameters'])
        properties = schema['function']['parameters'].get('properties', {})
        for name in DATA_SELECTORS & properties.keys():
            rule = properties[name]
            rule.pop('enum', None)
            if isinstance(rule.get('items'), dict):
                rule['items'].pop('enum', None)
    return visible
