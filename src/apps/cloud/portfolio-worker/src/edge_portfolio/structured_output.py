"""DeepSeek strict function output, followed by the existing business validators."""
from copy import deepcopy


def strict_schema(schema):
    definitions = schema.get('$defs', {})
    def expand(node):
        if isinstance(node, list):
            return [expand(child) for child in node]
        if isinstance(node, dict):
            if '$ref' in node:
                return expand({**definitions[node['$ref'].removeprefix('#/$defs/')],
                               **{key: value for key, value in node.items() if key != '$ref'}})
            return {key: expand(value) for key, value in node.items() if key != '$defs'}
        return node
    # Inline Pydantic's $defs: DeepSeek documents $def and rejects some referenced anyOf branches.
    result = expand(schema)
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            for key in ('minItems', 'maxItems', 'minLength', 'maxLength', 'default', 'title'):
                node.pop(key, None)
            if node.get('format') == 'date':
                node.pop('format')
                node['pattern'] = r'^\d{4}-\d{2}-\d{2}$'
            if node.get('type') == 'object':
                node['required'] = list(node.get('properties', {}))
                node['additionalProperties'] = False
            for name in ('properties', '$defs'):
                for child in node.get(name, {}).values():
                    visit(child)
            for name in ('items', 'anyOf', 'allOf', 'oneOf'):
                if name in node:
                    visit(node[name])
    visit(result)
    return result


async def structured_call(client, key, payload, report_model):
    request = deepcopy(payload)
    request.pop('response_format', None)
    # DeepSeek rejects required/forced tool choice in thinking mode.
    request['thinking'] = {'type': 'disabled'}
    request.pop('reasoning_effort', None)
    request['parallel_tool_calls'] = False
    report = {'type': 'function', 'function': {'name': 'submit_report', 'strict': True,
        'description': 'Complete the current role by submitting its report. Use the research tools first when needed; never return the report as plain text.',
        'parameters': strict_schema(report_model.model_json_schema())}}
    if request.get('tool_choice') == 'none':
        request['tools'] = [report]
        request['tool_choice'] = {'type': 'function', 'function': {'name': 'submit_report'}}
    else:
        # Research tools retain their optional arguments; only report submission needs a strict shape.
        request['tools'] = [*request.get('tools', []), report]
        request['tool_choice'] = 'required'
    response = await client.post('https://api.deepseek.com/beta/chat/completions',
        headers={'Authorization': f'Bearer {key}'}, json=request)
    response.raise_for_status()
    result = response.json()
    choice = result['choices'][0]
    message = choice['message']
    calls = message.get('tool_calls', [])
    # Let the existing agent record and retry truncation before parsing any arguments.
    if choice.get('finish_reason') == 'length':
        return result
    if not calls:
        raise ValueError('STRUCTURED_OUTPUT_REQUIRED')
    submitted = [call for call in calls if call['function']['name'] == 'submit_report']
    if submitted:
        if len(calls) != 1:
            raise ValueError('STRUCTURED_OUTPUT_MIXED_CALLS')
        message['content'] = submitted[0]['function']['arguments']
        message.pop('tool_calls')
        choice['finish_reason'] = 'stop'
    return result
