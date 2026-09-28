"""Run the approved SDK loop against DeepSeek with committed tool callbacks."""

import asyncio
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool
from jsonschema import Draft202012Validator
import yaml


def load_prompt(path: Path) -> str:
    """Load a nonempty system_prompt from an explicit YAML file."""
    document = yaml.safe_load(path.read_text(encoding='utf-8'))
    prompt = document.get('system_prompt') if isinstance(document, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('system_prompt must be a nonempty string')
    return prompt


def make_server(schemas: list[dict], call):
    """Expose registered schemas and return exactly the committed callback result.

    Args:
        schemas: OpenAI-style nested function descriptors.
        call: Synchronous audited executor accepting name and arguments.

    Returns:
        SDK MCP server and its allowed tool names.
    """
    registered, allowed = [], []
    gate = asyncio.Lock()
    for schema in schemas:
        function = schema['function']
        name = function['name']
        async def handler(arguments, tool_name=name):
            async with gate:
                # Cancellation must not leave a database write racing publication failure.
                pending = asyncio.create_task(asyncio.to_thread(call, tool_name, arguments))
                try:
                    result = await asyncio.shield(pending)
                except asyncio.CancelledError:
                    try:
                        await pending
                    finally:
                        raise
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]}
        registered.append(tool(name, function['description'], function['parameters'])(handler))
        allowed.append('mcp__analysis__' + name)
    return create_sdk_mcp_server(name='analysis', version='1.0.0', tools=registered), allowed


async def run_model(*, initial: dict, prompt: str, schemas: list[dict], call,
                    output_schema: dict, artifacts: Path, key: str, model: str,
                    timeout_seconds: int = 300, client_factory=ClaudeSDKClient) -> dict:
    """Run one bounded conversation and validate the final JSON structure.

    Args:
        initial: Time-bounded source context and previous published analysis.
        prompt: Loaded system instruction text.
        schemas: Registered MCP function descriptors.
        call: Audited callback; output must already be committed.
        output_schema: Expected final response JSON schema.
        artifacts: Local execution folder for inspection.
        key: DeepSeek key, never persisted.
        model: DeepSeek model name.
        timeout_seconds: Total deadline, from 1 to 600 seconds.
        client_factory: SDK constructor; replaced by a fake for offline tests.

    Returns:
        Validated final response. Meaning is reviewed separately by a reviewer.

    Raises:
        ValueError: Missing credentials, failed SDK result, or missing final JSON.
        TimeoutError: Conversation deadline exceeded.
        jsonschema.ValidationError: Final JSON violates the output contract.
    """
    if not key or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
        raise ValueError('API key and a 1..600 second deadline are required')
    artifacts.mkdir(parents=True, exist_ok=True)
    def encode(value):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str).replace(key, '[redacted]')
    server, allowed = make_server(schemas, call)
    options = ClaudeAgentOptions(
        model=model, system_prompt=prompt, tools=[], allowed_tools=allowed,
        mcp_servers={'analysis': server}, strict_mcp_config=True,
        output_format={'type': 'json_schema', 'schema': output_schema},
        permission_mode='dontAsk', setting_sources=[],
        settings=json.dumps({'autoCompactEnabled': True}),
        cwd=str(artifacts.resolve()), max_turns=24,
        thinking={'type': 'enabled', 'budget_tokens': 8192}, effort='high',
        env={'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
             'ANTHROPIC_AUTH_TOKEN': key, 'ANTHROPIC_API_KEY': '', 'CLAUDE_CODE_OAUTH_TOKEN': '',
             'ANTHROPIC_MODEL': model, 'ANTHROPIC_DEFAULT_HAIKU_MODEL': model,
             'ANTHROPIC_DEFAULT_SONNET_MODEL': model, 'ANTHROPIC_DEFAULT_OPUS_MODEL': model,
             'DISABLE_AUTO_COMPACT': '0', 'DISABLE_COMPACT': '0'})
    for name, value in [('input.json', initial), ('tool_schemas.json', schemas),
                        ('output_schema.json', output_schema)]:
        (artifacts / name).write_text(encode(value), encoding='utf-8')
    (artifacts / 'system_prompt.txt').write_text(prompt.replace(key, '[redacted]'), encoding='utf-8')
    async with asyncio.timeout(timeout_seconds), client_factory(options=options) as client:
        await client.query(encode(initial))
        async for message in client.receive_response():
            event = json.loads(encode(asdict(message) if is_dataclass(message) else vars(message)))
            with (artifacts / 'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'message_type': type(message).__name__, 'message': event}, ensure_ascii=False) + '\n')
            if type(message).__name__ != 'ResultMessage':
                continue
            (artifacts / 'raw_response.txt').write_text(event.get('result') or '', encoding='utf-8')
            if event.get('is_error') or event.get('subtype') != 'success':
                raise ValueError('SDK did not produce a successful final result')
            value = event.get('structured_output')
            if value is None:
                value = json.loads(event.get('result') or '')
            Draft202012Validator(output_schema).validate(value)
            (artifacts / 'response.json').write_text(encode(value), encoding='utf-8')
            return value
    raise ValueError('SDK ended without a final result')
