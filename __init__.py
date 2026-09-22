import copy
import json
import time
import uuid

from aiohttp import web

import nodes
from server import PromptServer

try:
    import folder_paths
    from comfy_api.latest import InputImpl, io
    from comfy_extras.nodes_audio import load as _load_audio_file
    from comfy_extras.nodes_minimax_h3 import (
        MiniMaxH3ReferenceToVideo as _StockMiniMaxH3ReferenceToVideo,
    )
except ImportError:
    # Keep the general Anim Bridge available on ComfyUI versions that do not
    # include MiniMax H3 yet. The two H3 nodes are registered only when the
    # corresponding stock node is present.
    folder_paths = None
    InputImpl = None
    io = None
    _load_audio_file = None
    _StockMiniMaxH3ReferenceToVideo = None


_sessions = {}
_commands = {}
_stale_after_seconds = 15
_purge_after_seconds = 300

ANIM_IMAGE_REFERENCES = 'ANIM_IMAGE_REFERENCES'
ANIM_VIDEO_REFERENCES = 'ANIM_VIDEO_REFERENCES'
ANIM_AUDIO_REFERENCES = 'ANIM_AUDIO_REFERENCES'
ANIM_IMAGE_REFERENCE_CAPACITY = 100
MINIMAX_H3_REFERENCE_IMAGE_CAPACITY = 9
MINIMAX_H3_REFERENCE_VIDEO_CAPACITY = 3
MINIMAX_H3_REFERENCE_AUDIO_CAPACITY = 3
MINIMAX_H3_REFERENCE_VIDEO_FPS = 24


def _json(payload, status=200):
    # ComfyUI's own origin/CORS middleware applies to these routes. Do not
    # bypass the server's configured origin policy from the Bridge.
    return web.json_response(payload, status=status)


@PromptServer.instance.routes.get('/anim_bridge/v1/health')
async def anim_bridge_health(_request):
    return _json({'bridgeVersion': 4, 'status': 'ok'})


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
    if not _workflow_is_open(session_id, tab_id, workflow_id):
        return _json({'error': 'The selected workflow tab is not available'}, 404)
    command_id = _queue_command(
        session_id,
        tab_id,
        workflow_id,
        command_type='refresh',
    )
    return _json({'commandId': command_id}, 202)


@PromptServer.instance.routes.post('/anim_bridge/v1/apply')
async def anim_bridge_apply(request):
    body = await request.json()
    session_id = str(body.get('sessionId') or '').strip()
    tab_id = str(body.get('tabId') or '').strip()
    workflow_id = str(body.get('workflowId') or '').strip()
    inputs = body.get('inputs')
    if not session_id or not tab_id or not workflow_id:
        return _json({'error': 'sessionId, tabId, and workflowId are required'}, 400)
    if not isinstance(inputs, dict) or not inputs:
        return _json({'error': 'inputs must be a non-empty object'}, 400)
    for input_id, value in inputs.items():
        if not isinstance(input_id, str) or '.' not in input_id:
            return _json({'error': f'Invalid input mapping: {input_id}'}, 400)
        if not isinstance(value, (str, list)) or (
            isinstance(value, list)
            and not all(isinstance(item, str) for item in value)
        ):
            return _json(
                {'error': f'Unsupported value for input mapping: {input_id}'},
                400,
            )
    if not _workflow_is_open(session_id, tab_id, workflow_id):
        return _json({'error': 'The selected workflow tab is not available'}, 404)
    command_id = _queue_command(
        session_id,
        tab_id,
        workflow_id,
        command_type='applyInputs',
        inputs=inputs,
    )
    return _json({'commandId': command_id}, 202)


def _workflow_is_open(session_id, tab_id, workflow_id):
    session = _sessions.get((session_id, tab_id))
    return (
        session is not None
        and time.time() - session['lastSeen'] <= _stale_after_seconds
        and any(
            item.get('workflowId') == workflow_id
            for item in session['workflows']
        )
    )


def _queue_command(
    session_id,
    tab_id,
    workflow_id,
    command_type,
    inputs=None,
):
    command_id = uuid.uuid4().hex
    command = {
        'commandId': command_id,
        'type': command_type,
        'sessionId': session_id,
        'tabId': tab_id,
        'workflowId': workflow_id,
        'createdAt': time.time(),
    }
    if inputs is not None:
        command['inputs'] = dict(inputs)
    _commands[command_id] = command
    return command_id


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


class AnimDurationInput:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'duration': (
                    'FLOAT',
                    {
                        'default': 5.0,
                        'min': 0.0,
                        'max': 3600.0,
                        'step': 0.1,
                    },
                ),
            },
        }

    RETURN_TYPES = ('FLOAT',)
    RETURN_NAMES = ('duration',)
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives the clip duration, in seconds, selected in Anim.'
    )

    def emit(self, duration):
        return (float(duration),)


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
                        'default': ANIM_IMAGE_REFERENCE_CAPACITY,
                        'min': 1,
                        'max': 100,
                        'step': 1,
                    },
                ),
            },
        }

    RETURN_TYPES = ('IMAGE', 'MASK', 'STRING', ANIM_IMAGE_REFERENCES)
    RETURN_NAMES = ('images', 'masks', 'file_names', 'references')
    OUTPUT_IS_LIST = (True, True, True, False)
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
        return (images, masks, references, tuple(images))


def _validate_reference_bundle(references, media_label, capacity):
    if references is None:
        return None
    if not isinstance(references, (tuple, list)):
        raise TypeError(
            f'Anim MiniMax H3 requires {media_label} from the matching Anim '
            'References node.'
        )
    if not references:
        raise ValueError(f'Anim MiniMax H3 received no {media_label}.')
    if len(references) > capacity:
        raise ValueError(
            f'MiniMax H3 accepts at most {capacity} {media_label}, but '
            f'{len(references)} were supplied.'
        )
    return references


def _to_stock_minimax_h3_images(ref_images):
    references = _validate_reference_bundle(
        ref_images,
        'image references',
        MINIMAX_H3_REFERENCE_IMAGE_CAPACITY,
    )
    if references is None:
        return None
    stock_references = {}
    for index, image in enumerate(references):
        shape = getattr(image, 'shape', None)
        if shape is None or len(shape) != 4 or shape[0] != 1:
            raise ValueError(
                f'MiniMax H3 reference image {index + 1} must contain exactly '
                'one IMAGE tensor with shape [1, H, W, C].'
            )
        stock_references[f'ref_image_{index}'] = image
    return stock_references


def _load_minimax_h3_video_references(ref_videos):
    references = _validate_reference_bundle(
        ref_videos,
        'video references',
        MINIMAX_H3_REFERENCE_VIDEO_CAPACITY,
    )
    if references is None:
        return None, None
    stock_videos = {}
    stock_video_audios = {}
    for index, file_name in enumerate(references):
        path = folder_paths.get_annotated_filepath(file_name)
        components = InputImpl.VideoFromFile(path).get_components()
        frames = components.images
        if len(frames) == 0:
            raise ValueError(
                f'Anim video reference {index + 1} contains no video frames.'
            )
        source_fps = float(components.frame_rate)
        if source_fps <= 0:
            raise ValueError(
                f'Anim video reference {index + 1} has an invalid frame rate.'
            )
        if source_fps != MINIMAX_H3_REFERENCE_VIDEO_FPS:
            target_count = max(
                1,
                round(
                    len(frames)
                    * MINIMAX_H3_REFERENCE_VIDEO_FPS
                    / source_fps
                ),
            )
            frame_indexes = [
                min(
                    len(frames) - 1,
                    int(position * source_fps / MINIMAX_H3_REFERENCE_VIDEO_FPS),
                )
                for position in range(target_count)
            ]
            frames = frames[frame_indexes]
        stock_videos[f'ref_video_{index}'] = frames
        if components.audio is not None:
            stock_video_audios[f'ref_video_audio_{index}'] = components.audio
    return stock_videos, stock_video_audios


def _load_minimax_h3_audio_references(ref_audios):
    references = _validate_reference_bundle(
        ref_audios,
        'audio references',
        MINIMAX_H3_REFERENCE_AUDIO_CAPACITY,
    )
    if references is None:
        return None
    stock_audios = {}
    for index, file_name in enumerate(references):
        path = folder_paths.get_annotated_filepath(file_name)
        waveform, sample_rate = _load_audio_file(path)
        stock_audios[f'ref_audio_{index}'] = {
            'waveform': waveform.unsqueeze(0),
            'sample_rate': sample_rate,
        }
    return stock_audios


if io is not None and _StockMiniMaxH3ReferenceToVideo is not None:
    class AnimMiniMaxH3ReferenceToVideo(io.ComfyNode):
        RETURN_TYPES = tuple(_StockMiniMaxH3ReferenceToVideo.RETURN_TYPES)
        RETURN_NAMES = tuple(_StockMiniMaxH3ReferenceToVideo.RETURN_NAMES)
        FUNCTION = 'EXECUTE_NORMALIZED'
        CATEGORY = _StockMiniMaxH3ReferenceToVideo.CATEGORY
        DESCRIPTION = (
            'MiniMax H3 reference-to-video using the shared ordered Anim '
            'Image, Video, and Audio References nodes.'
        )

        @classmethod
        def define_schema(cls):
            schema = _StockMiniMaxH3ReferenceToVideo.define_schema()
            schema.node_id = 'AnimMiniMaxH3ReferenceToVideo'
            schema.display_name = 'Anim MiniMax H3 Reference to Video'
            schema.description = cls.DESCRIPTION
            schema.inputs = list(schema.inputs)
            replacements = {
                'ref_images': (
                    ANIM_IMAGE_REFERENCES,
                    'Anim Image References output. MiniMax H3 accepts up to 9.',
                ),
                'ref_videos': (
                    ANIM_VIDEO_REFERENCES,
                    'Anim Video References output. MiniMax H3 accepts up to 3.',
                ),
                'ref_audios': (
                    ANIM_AUDIO_REFERENCES,
                    'Anim Audio References output. MiniMax H3 accepts up to 3.',
                ),
            }
            replaced = set()
            for index, input_spec in enumerate(schema.inputs):
                replacement = replacements.get(input_spec.id)
                if replacement is None:
                    continue
                reference_type, tooltip = replacement
                schema.inputs[index] = io.Custom(reference_type).Input(
                    input_spec.id,
                    optional=True,
                    tooltip=tooltip,
                )
                replaced.add(input_spec.id)
            if replaced != set(replacements):
                raise RuntimeError(
                    'The stock MiniMax H3 reference inputs have changed.'
                )
            return schema

        @classmethod
        def INPUT_TYPES(cls):
            input_types = copy.deepcopy(
                _StockMiniMaxH3ReferenceToVideo.INPUT_TYPES(),
            )
            optional = input_types.setdefault('optional', {})
            optional['ref_images'] = (ANIM_IMAGE_REFERENCES, {})
            optional['ref_videos'] = (ANIM_VIDEO_REFERENCES, {})
            optional['ref_audios'] = (ANIM_AUDIO_REFERENCES, {})
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
            stock_videos, derived_video_audios = (
                _load_minimax_h3_video_references(ref_videos)
            )
            stock_video_audios = dict(derived_video_audios or {})
            stock_video_audios.update(ref_video_audios or {})
            return _StockMiniMaxH3ReferenceToVideo.execute(
                clip=clip,
                vae=vae,
                audio_vae=audio_vae,
                prompt=prompt,
                width=width,
                height=height,
                length=length,
                ref_image_size=ref_image_size,
                ref_images=_to_stock_minimax_h3_images(ref_images),
                ref_videos=stock_videos,
                ref_video_audios=stock_video_audios or None,
                ref_audios=_load_minimax_h3_audio_references(ref_audios),
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
        return _reference_input_types(default_capacity=100)

    RETURN_TYPES = ('STRING', ANIM_VIDEO_REFERENCES)
    RETURN_NAMES = ('file_names', 'references')
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives Asset video file names from Anim in array order. Connect the '
        'output to the video loader contract used by this workflow.'
    )

    def emit(self, references_json, max_references):
        references = _parse_references(
            references_json,
            max_references,
            media_label='video',
        )
        return (references, tuple(references))


class AnimAudioReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return _reference_input_types(default_capacity=100)

    RETURN_TYPES = ('STRING', ANIM_AUDIO_REFERENCES)
    RETURN_NAMES = ('file_names', 'references')
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives Asset audio file names from Anim in array order. Connect the '
        'output to the audio loader contract used by this workflow.'
    )

    def emit(self, references_json, max_references):
        references = _parse_references(
            references_json,
            max_references,
            media_label='audio',
        )
        return (references, tuple(references))


NODE_CLASS_MAPPINGS = {
    'AnimPromptInput': AnimPromptInput,
    'AnimDurationInput': AnimDurationInput,
    'AnimImageReferences': AnimImageReferences,
    'AnimVideoReferences': AnimVideoReferences,
    'AnimAudioReferences': AnimAudioReferences,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    'AnimPromptInput': 'Anim Prompt Input',
    'AnimDurationInput': 'Anim Duration Input',
    'AnimImageReferences': 'Anim Image References',
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
