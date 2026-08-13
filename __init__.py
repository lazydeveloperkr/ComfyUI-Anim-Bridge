import json
import time
import uuid

from aiohttp import web

import nodes
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
    return _json({'bridgeVersion': 2, 'status': 'ok'})


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


class AnimPromptInput:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'prompt': (
                    'STRING',
                    {
                        'default': '',
                        'multiline': True,
                        'dynamicPrompts': True,
                    },
                ),
            },
        }

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('prompt',)
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = 'Receives the final video prompt selected in Anim.'

    def emit(self, prompt):
        return (prompt,)


class AnimImageReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'references_json': (
                    'STRING',
                    {
                        'default': '[]',
                        'multiline': True,
                    },
                ),
                'max_references': (
                    'INT',
                    {
                        'default': 4,
                        'min': 1,
                        'max': 100,
                        'step': 1,
                    },
                ),
            },
        }

    RETURN_TYPES = ('IMAGE', 'MASK', 'STRING')
    RETURN_NAMES = ('images', 'masks', 'file_names')
    OUTPUT_IS_LIST = (True, True, True)
    FUNCTION = 'load'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives Asset image references from Anim in array order and loads '
        'them from the ComfyUI input folder.'
    )

    def load(self, references_json, max_references):
        references = _parse_references(
            references_json,
            max_references,
            media_label='image',
        )

        images = []
        masks = []
        loader = nodes.LoadImage()
        for file_name in references:
            image, mask = loader.load_image(file_name)
            images.append(image)
            masks.append(mask)
        return (images, masks, references)


def _reference_input_types(default_capacity):
    return {
        'required': {
            'references_json': (
                'STRING',
                {
                    'default': '[]',
                    'multiline': True,
                },
            ),
            'max_references': (
                'INT',
                {
                    'default': default_capacity,
                    'min': 1,
                    'max': 100,
                    'step': 1,
                },
            ),
        },
    }


def _parse_references(references_json, max_references, media_label):
    try:
        references = json.loads(references_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(
            f'Anim {media_label.title()} References expected a JSON array of '
            'file names.'
        ) from error
    if not isinstance(references, list) or not all(
        isinstance(item, str) and item.strip() for item in references
    ):
        raise ValueError(
            f'Anim {media_label.title()} References expected a JSON array of '
            'file names.'
        )
    if len(references) > max_references:
        raise ValueError(
            f'Anim sent {len(references)} {media_label} references, but this '
            f'node allows {max_references}.'
        )
    if not references:
        raise ValueError(
            f'Anim sent no {media_label} references to this node.'
        )
    return references


class AnimVideoReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return _reference_input_types(default_capacity=1)

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('file_names',)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives Asset video file names from Anim in array order. Connect the '
        'output to the video loader contract used by this workflow.'
    )

    def emit(self, references_json, max_references):
        return (
            _parse_references(
                references_json,
                max_references,
                media_label='video',
            ),
        )


class AnimAudioReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return _reference_input_types(default_capacity=1)

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('file_names',)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives Asset audio file names from Anim in array order. Connect the '
        'output to the audio loader contract used by this workflow.'
    )

    def emit(self, references_json, max_references):
        return (
            _parse_references(
                references_json,
                max_references,
                media_label='audio',
            ),
        )


NODE_CLASS_MAPPINGS = {
    'AnimPromptInput': AnimPromptInput,
    'AnimImageReferences': AnimImageReferences,
    'AnimVideoReferences': AnimVideoReferences,
    'AnimAudioReferences': AnimAudioReferences,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    'AnimPromptInput': 'Anim Prompt Input',
    'AnimImageReferences': 'Anim Image References',
    'AnimVideoReferences': 'Anim Video References',
    'AnimAudioReferences': 'Anim Audio References',
}
