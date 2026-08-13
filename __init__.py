import time
import uuid

from aiohttp import web

from server import PromptServer


_sessions = {}
_commands = {}
_stale_after_seconds = 15
_purge_after_seconds = 300


def _json(payload, status=200):
    # ComfyUI's own origin/CORS middleware applies to these routes. Do not
    # bypass the server's configured origin policy from the Bridge.
    return web.json_response(payload, status=status)


@PromptServer.instance.routes.get('/anim_bridge/v1/health')
async def anim_bridge_health(_request):
    return _json({'bridgeVersion': 1, 'status': 'ok'})


@PromptServer.instance.routes.post('/anim_bridge/v1/publish')
async def anim_bridge_publish(request):
    body = await request.json()
    session_id = str(body.get('sessionId') or '').strip()
    tab_id = str(body.get('tabId') or '').strip()
    if not session_id or not tab_id:
        return _json({'error': 'sessionId and tabId are required'}, 400)
    now = time.time()
    workflows = []
    for item in body.get('workflows') or []:
        workflow = dict(item)
        workflow['sessionId'] = session_id
        workflow['tabId'] = tab_id
        workflow['lastSeenAt'] = time.strftime(
            '%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)
        )
        workflow['isAvailable'] = True
        workflows.append(workflow)
    _sessions[(session_id, tab_id)] = {
        'lastSeen': now,
        'workflows': workflows,
    }
    completed = str(body.get('completedCommandId') or '').strip()
    if completed:
        _commands.pop(completed, None)
    return _json({'accepted': True})


@PromptServer.instance.routes.get('/anim_bridge/v1/workflows')
async def anim_bridge_workflows(_request):
    now = time.time()
    workflows = []
    expired_sessions = [
        key
        for key, session in _sessions.items()
        if now - session['lastSeen'] > _purge_after_seconds
    ]
    for key in expired_sessions:
        _sessions.pop(key, None)
    for session in _sessions.values():
        available = now - session['lastSeen'] <= _stale_after_seconds
        for item in session['workflows']:
            workflow = dict(item)
            workflow['isAvailable'] = available
            workflows.append(workflow)
    workflows.sort(key=lambda item: (not item.get('isAvailable', False), item.get('title', '')))
    return _json({'workflows': workflows})


@PromptServer.instance.routes.post('/anim_bridge/v1/refresh')
async def anim_bridge_refresh(request):
    body = await request.json()
    session_id = str(body.get('sessionId') or '').strip()
    tab_id = str(body.get('tabId') or '').strip()
    workflow_id = str(body.get('workflowId') or '').strip()
    if not session_id or not tab_id or not workflow_id:
        return _json({'error': 'sessionId, tabId, and workflowId are required'}, 400)
    session = _sessions.get((session_id, tab_id))
    if (
        session is None
        or time.time() - session['lastSeen'] > _stale_after_seconds
        or not any(
            item.get('workflowId') == workflow_id
            for item in session['workflows']
        )
    ):
        return _json({'error': 'The selected workflow tab is not available'}, 404)
    command_id = uuid.uuid4().hex
    _commands[command_id] = {
        'commandId': command_id,
        'sessionId': session_id,
        'tabId': tab_id,
        'workflowId': workflow_id,
        'createdAt': time.time(),
    }
    return _json({'commandId': command_id}, 202)


@PromptServer.instance.routes.get('/anim_bridge/v1/commands')
async def anim_bridge_commands(request):
    session_id = request.query.get('session_id', '')
    tab_id = request.query.get('tab_id', '')
    now = time.time()
    expired = [
        command_id
        for command_id, command in _commands.items()
        if now - command['createdAt'] > 30
    ]
    for command_id in expired:
        _commands.pop(command_id, None)
    commands = [
        command
        for command in _commands.values()
        if command['sessionId'] == session_id and command['tabId'] == tab_id
    ]
    return _json({'commands': commands})


WEB_DIRECTORY = './web'
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
