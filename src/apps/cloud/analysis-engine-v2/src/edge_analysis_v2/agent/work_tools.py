"""Read-only observation paging and directory creation for the current workspace."""
import json
from claude_agent_sdk import create_sdk_mcp_server, tool
from edge_analysis_v2.agent.work_context import SourceInputError


def make_workspace_server(context, session):
    def response(value):
        return {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}

    source_rule = {'type':'string', 'description':'Choose an exact source name from available_sources.'}
    if context.sources:
        source_rule['enum'] = list(context.sources)

    @tool('read_source', 'Read a page of initial observations from available_sources. Only source is required. Omit subject for lists; use it only for a listed dictionary key. On invalid_argument, use retry_arguments or allowed_values instead of guessing. Empty rows mean an empty source/page, not a tool failure. Exploration only; not a final evidence ID.',
          {'type':'object', 'properties':{'source':source_rule,
           'subject':{'type':'string','default':'','description':'Optional dictionary key; omit for list sources.'},
           'offset':{'type':'integer','minimum':0,'default':0},
           'limit':{'type':'integer','minimum':1,'maximum':100,'default':50}},
           'required':['source'], 'additionalProperties':False})
    async def read_source(args):
        try:
            return response(context.read(**args))
        except SourceInputError as error:
            return response({'error': error.details}) | {'is_error': True}

    @tool('create_directory', 'Create a directory under notes/ for Markdown working notes.', {'path': str})
    async def create_directory(args):
        try:
            path = session.note_path(args['path'], create=True, directory=True)
            return response({'path': path.relative_to(session.workspace).as_posix()})
        except (ValueError, OSError) as error:
            return response({'error': str(error)}) | {'is_error': True}

    @tool('update_tasks', 'Register questions before using analysis tools. Replace the nonempty work list, retaining important unanswered questions; complete only actually resolved tasks.',
          {'type':'object', 'properties':{'todos':{'type':'array', 'minItems':1, 'items':{'type':'object',
           'properties':{'content':{'type':'string'}, 'status':{'type':'string','enum':['pending','in_progress','completed']}},
           'required':['content','status'], 'additionalProperties':False}}}, 'required':['todos'], 'additionalProperties':False})
    async def update_tasks(args):
        try:
            session.update_tasks(args['todos'])
            return response({'todos': session.todos})
        except ValueError as error:
            return response({'error': str(error)}) | {'is_error': True}

    return create_sdk_mcp_server(name='workspace', version='1.0.0', tools=[read_source, create_directory, update_tasks])
