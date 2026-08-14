import copy
import hashlib
import json
import time
import uuid

from aiohttp import web

import nodes
from server import PromptServer

try:
    from comfy_api.latest import io
    from comfy_extras.nodes_minimax_h3 import (
        MiniMaxH3ReferenceToVideo as _StockMiniMaxH3ReferenceToVideo,
    )
except ImportError:
    # Keep the general Anim Bridge available on ComfyUI versions that do not
    # include MiniMax H3 yet. The two H3 nodes are registered only when the
    # corresponding stock node is present.
    io = None
    _StockMiniMaxH3ReferenceToVideo = None


_sessions = {}
_commands = {}
_stale_after_seconds = 15
_purge_after_seconds = 300

ANIM_MINIMAX_H3_REFERENCE_IMAGES = 'ANIM_MINIMAX_H3_REFERENCE_IMAGES'
ANIM_IMAGE_REFERENCE_CAPACITY = 100
MINIMAX_H3_REFERENCE_IMAGE_CAPACITY = 9


def _json(payload, status=200):
    # ComfyUI's own origin/CORS middleware applies to these routes. Do not
    # bypass the server's configured origin policy from the Bridge.
    return web.json_response(payload, status=status)


@PromptServer.instance.routes.get('/anim_bridge/v1/health')
async def anim_bridge_health(_request):
    return _json({'bridgeVersion': 3, 'status': 'ok'})


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
                'image_paths': (
                    'STRING',
                    {
                        'default': '',
                        'multiline': True,
                        'hidden': True,
                    },
                ),
                'max_references': (
                    'INT',
                    {
                        'default': 9,
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

    def load(self, image_paths, max_references):
        references = _parse_path_lines(
            image_paths,
            max_references,
        )

        images = []
        masks = []
        loader = nodes.LoadImage()
        for file_name in references:
            image, mask = loader.load_image(file_name)
            images.append(image)
            masks.append(mask)
        return (images, masks, references)


class AnimMiniMaxH3ReferenceImageLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'image_paths': (
                    'STRING',
                    {
                        'default': '',
                        'multiline': True,
                        'hidden': True,
                    },
                ),
            },
        }

    RETURN_TYPES = (ANIM_MINIMAX_H3_REFERENCE_IMAGES, 'IMAGE')
    RETURN_NAMES = ('ref_images', 'image_list')
    OUTPUT_IS_LIST = (False, True)
    FUNCTION = 'load_reference_images'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives up to 100 ordered images from Anim. Connect ref_images to '
        'Anim MiniMax H3 Reference to Video, which enforces the model limit.'
    )

    @classmethod
    def VALIDATE_INPUTS(cls, image_paths):
        try:
            _parse_path_lines(image_paths, ANIM_IMAGE_REFERENCE_CAPACITY)
        except ValueError as error:
            return str(error)
        return True

    @classmethod
    def IS_CHANGED(cls, image_paths):
        references = _parse_path_lines(
            image_paths,
            ANIM_IMAGE_REFERENCE_CAPACITY,
        )
        hasher = hashlib.sha256()
        for file_name in references:
            hasher.update(file_name.encode('utf-8', 'surrogatepass'))
            hasher.update(b'\0')
            if hasattr(nodes.LoadImage, 'IS_CHANGED'):
                changed = nodes.LoadImage.IS_CHANGED(file_name)
                hasher.update(repr(changed).encode('utf-8', 'surrogatepass'))
            hasher.update(b'\0')
        return hasher.hexdigest()

    def load_reference_images(self, image_paths):
        references = _parse_path_lines(
            image_paths,
            ANIM_IMAGE_REFERENCE_CAPACITY,
        )
        loader = nodes.LoadImage()
        images = [loader.load_image(file_name)[0] for file_name in references]
        return (tuple(images), images)


def _to_stock_minimax_h3_references(ref_images):
    if ref_images is None:
        return None
    if not isinstance(ref_images, (tuple, list)):
        raise TypeError(
            'Anim MiniMax H3 Reference to Video requires ref_images from '
            'Anim MiniMax H3 Reference Image Loader.'
        )
    if not ref_images:
        raise ValueError('Anim MiniMax H3 received no reference images.')
    if len(ref_images) > MINIMAX_H3_REFERENCE_IMAGE_CAPACITY:
        raise ValueError(
            'MiniMax H3 accepts at most '
            f'{MINIMAX_H3_REFERENCE_IMAGE_CAPACITY} reference images, but '
            f'{len(ref_images)} were supplied.'
        )

    stock_references = {}
    for index, image in enumerate(ref_images):
        shape = getattr(image, 'shape', None)
        if shape is None or len(shape) != 4 or shape[0] != 1:
            raise ValueError(
                f'MiniMax H3 reference image {index + 1} must contain exactly '
                'one IMAGE tensor with shape [1, H, W, C].'
            )
        stock_references[f'ref_image_{index}'] = image
    return stock_references


if io is not None and _StockMiniMaxH3ReferenceToVideo is not None:
    class AnimMiniMaxH3ReferenceToVideo(io.ComfyNode):
        RETURN_TYPES = tuple(_StockMiniMaxH3ReferenceToVideo.RETURN_TYPES)
        RETURN_NAMES = tuple(_StockMiniMaxH3ReferenceToVideo.RETURN_NAMES)
        FUNCTION = 'EXECUTE_NORMALIZED'
        CATEGORY = _StockMiniMaxH3ReferenceToVideo.CATEGORY
        DESCRIPTION = (
            'MiniMax H3 reference-to-video using the ordered image bundle '
            'received by Anim. All stock video and audio reference inputs are '
            'preserved.'
        )

        @classmethod
        def define_schema(cls):
            schema = _StockMiniMaxH3ReferenceToVideo.define_schema()
            schema.node_id = 'AnimMiniMaxH3ReferenceToVideo'
            schema.display_name = 'Anim MiniMax H3 Reference to Video'
            schema.description = cls.DESCRIPTION
            schema.inputs = list(schema.inputs)
            for index, input_spec in enumerate(schema.inputs):
                if input_spec.id == 'ref_images':
                    schema.inputs[index] = io.Custom(
                        ANIM_MINIMAX_H3_REFERENCE_IMAGES,
                    ).Input(
                        'ref_images',
                        optional=True,
                        tooltip=(
                            'Ordered images from Anim MiniMax H3 Reference '
                            'Image Loader. MiniMax H3 accepts up to 9.'
                        ),
                    )
                    break
            else:
                raise RuntimeError(
                    'The stock MiniMax H3 node no longer exposes ref_images.'
                )
            return schema

        @classmethod
        def INPUT_TYPES(cls):
            input_types = copy.deepcopy(
                _StockMiniMaxH3ReferenceToVideo.INPUT_TYPES(),
            )
            input_types.setdefault('optional', {})['ref_images'] = (
                ANIM_MINIMAX_H3_REFERENCE_IMAGES,
                {
                    'tooltip': (
                        'Ordered images from Anim MiniMax H3 Reference Image '
                        'Loader. MiniMax H3 accepts up to 9.'
                    ),
                },
            )
            return input_types

        @classmethod
        def execute(
            cls,
            clip,
            vae,
            audio_vae,
            prompt,
            width,
            height,
            length,
            ref_image_size='match',
            ref_images=None,
            ref_videos=None,
            ref_video_audios=None,
            ref_audios=None,
        ):
            return _StockMiniMaxH3ReferenceToVideo.execute(
                clip=clip,
                vae=vae,
                audio_vae=audio_vae,
                prompt=prompt,
                width=width,
                height=height,
                length=length,
                ref_image_size=ref_image_size,
                ref_images=_to_stock_minimax_h3_references(ref_images),
                ref_videos=ref_videos,
                ref_video_audios=ref_video_audios,
                ref_audios=ref_audios,
            )


def _parse_path_lines(image_paths, max_references):
    if not isinstance(image_paths, str):
        raise ValueError(
            'Anim Image References expected one image file name per line.'
        )
    references = [
        line.strip()
        for line in image_paths.splitlines()
        if line.strip()
    ]
    if len(references) > max_references:
        raise ValueError(
            f'Anim sent {len(references)} image references, but this node '
            f'allows {max_references}.'
        )
    if not references:
        raise ValueError('Anim sent no image references to this node.')
    return references


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
    'AnimMiniMaxH3ReferenceImageLoader': AnimMiniMaxH3ReferenceImageLoader,
    'AnimVideoReferences': AnimVideoReferences,
    'AnimAudioReferences': AnimAudioReferences,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    'AnimPromptInput': 'Anim Prompt Input',
    'AnimImageReferences': 'Anim Image References',
    'AnimMiniMaxH3ReferenceImageLoader': (
        'Anim MiniMax H3 Reference Image Loader'
    ),
    'AnimVideoReferences': 'Anim Video References',
    'AnimAudioReferences': 'Anim Audio References',
}

if io is not None and _StockMiniMaxH3ReferenceToVideo is not None:
    NODE_CLASS_MAPPINGS['AnimMiniMaxH3ReferenceToVideo'] = (
        AnimMiniMaxH3ReferenceToVideo
    )
    NODE_DISPLAY_NAME_MAPPINGS['AnimMiniMaxH3ReferenceToVideo'] = (
        'Anim MiniMax H3 Reference to Video'
    )
