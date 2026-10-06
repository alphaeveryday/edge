"""Run the approved SDK loop against DeepSeek with committed tool callbacks."""

import asyncio
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, create_sdk_mcp_server, tool
from jsonschema import Draft202012Validator
import yaml

from edge_analysis_v2.tools.model_schema import agent_tool_schemas
from edge_analysis_v2.agent.work_session import WorkSession
from edge_analysis_v2.agent.work_context import WorkContext
from edge_analysis_v2.agent.work_tools import make_workspace_server
from edge_analysis_v2.tools.execution import ToolExecutionError


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
    for schema, visible in zip(schemas, agent_tool_schemas(schemas)):
        function = schema['function']
        name = function['name']
        validator = Draft202012Validator(function['parameters'])
        async def handler(arguments, tool_name=name, validator=validator):
            validator.validate(arguments)
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
                except ToolExecutionError as error:
                    return {'is_error': True, 'content': [{'type': 'text', 'text': str(error)}]}
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]}
        registered.append(tool(name, function['description'], visible['function']['parameters'])(handler))
        allowed.append('mcp__analysis__' + name)
    return create_sdk_mcp_server(name='analysis', version='1.0.0', tools=registered), allowed


async def run_model(*, initial: dict, prompt: str, schemas: list[dict], call,
                    output_schema: dict, artifacts: Path, key: str, model: str,
                    timeout_seconds: int | None = None, client_factory=ClaudeSDKClient, kind: str = 'outlook',
                    resume: bool = False) -> dict:
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
        timeout_seconds: Total deadline, 1..600 seconds; default outlook 600, movement 300.
        client_factory: SDK constructor; replaced by a fake for offline tests.
        kind: Analysis scope; movement must not expand into a forecast report.
        resume: Restore interrupted notes and TODOs with identical original observations.

    Returns:
        Validated final response. Meaning is reviewed separately by a reviewer.

    Raises:
        ValueError: Missing credentials, failed SDK result, or missing final JSON.
        TimeoutError: Conversation deadline exceeded.
        jsonschema.ValidationError: Final JSON violates the output contract.
    """
    if timeout_seconds is None:
        timeout_seconds = 600 if kind == 'outlook' else 300
    if not key or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
        raise ValueError('API key and a 1..600 second deadline are required')
    artifacts.mkdir(parents=True, exist_ok=True)
    if resume and json.loads((artifacts / 'input.json').read_text(encoding='utf-8')) != initial:
        raise ValueError('Resume requires the same original observations and cutoff')
    def encode(value):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str).replace(key, '[redacted]')
    server, allowed = make_server(schemas, call)
    with TemporaryDirectory(prefix='analysis-worker-') as directory:
        workspace = Path(directory)
        workspace_allowed = ['mcp__workspace__read_source', 'mcp__workspace__create_directory', 'mcp__workspace__update_tasks']
        skills = WorkSession(workspace, artifacts, kind, allowed + workspace_allowed)
        source_context = WorkContext(initial)
        prompt = skills.system_prompt(prompt)
        query_input = {'workspace_instructions': {'path': 'AGENTS.md',
                       'content': (workspace / 'AGENTS.md').read_text(encoding='utf-8')},
                       'input': source_context.initial}
        if resume:
            query_input['resumed_work'] = skills.restore()
        (artifacts / 'model_input.json').write_text(encode(query_input), encoding='utf-8')
        # DeepSeek's documented SDK model tag prevents the unknown-model 200K default.
        sdk_model = model if model.endswith('[1m]') else model + '[1m]'
        options = ClaudeAgentOptions(
            model=sdk_model, system_prompt=prompt, tools=['Skill', 'Read', 'Write', 'Edit'],
            allowed_tools=allowed + workspace_allowed + ['Read', 'Write', 'Edit'],
            skills=skills.names, plugins=[{'type':'local', 'path':str(skills.plugin)}],
            hooks={'PreToolUse':[HookMatcher(hooks=[skills.before])],
                   'PreCompact':[HookMatcher(hooks=[skills.precompact])],
                   'PostToolUse':[HookMatcher(hooks=[skills.after])]},
            mcp_servers={'analysis': server, 'workspace': make_workspace_server(source_context, skills)}, strict_mcp_config=True,
            output_format={'type': 'json_schema', 'schema': output_schema},
            permission_mode='dontAsk', setting_sources=[],
            settings=json.dumps({'autoCompactEnabled': True}),
            cwd=str(workspace),
            thinking={'type': 'enabled', 'budget_tokens': 8192}, effort='high',
            env={'TINYFISH_API_KEY':'', 'CLAUDE_CONFIG_DIR':str(workspace/'config'), 'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
                 'ANTHROPIC_AUTH_TOKEN': key, 'ANTHROPIC_API_KEY': '', 'CLAUDE_CODE_OAUTH_TOKEN': '',
                 'ANTHROPIC_MODEL': sdk_model, 'ANTHROPIC_DEFAULT_HAIKU_MODEL': sdk_model,
                 'ANTHROPIC_DEFAULT_SONNET_MODEL': sdk_model, 'ANTHROPIC_DEFAULT_OPUS_MODEL': sdk_model,
                 'DISABLE_AUTO_COMPACT': '0', 'DISABLE_COMPACT': '0',
                 'CLAUDE_CODE_DISABLE_1M_CONTEXT': '0',
                 'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000',
                 'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE': '80'})
        for name, value in [('input.json', initial), ('tool_schemas.json', agent_tool_schemas(schemas)),
                            ('output_schema.json', output_schema)]:
            (artifacts / name).write_text(encode(value), encoding='utf-8')
        (artifacts / 'system_prompt.txt').write_text(prompt.replace(key, '[redacted]'), encoding='utf-8')
        try:
            async with asyncio.timeout(timeout_seconds), client_factory(options=options) as client:
                await client.query(encode(query_input))
                while True:
                    final = None
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
                        final = value
                    if final is None:
                        raise ValueError('SDK ended without a final result')
                    continuation = skills.continuation()
                    if continuation is None:
                        (artifacts / 'response.json').write_text(encode(final), encoding='utf-8')
                        return final
                    await client.query(continuation)
        finally:
            if skills.status == 'running':
                skills.status = 'interrupted'
            try:
                skills.checkpoint()
            except (OSError, ValueError):
                # Saving notes must not replace the timeout, cancellation or model error being raised.
                pass
