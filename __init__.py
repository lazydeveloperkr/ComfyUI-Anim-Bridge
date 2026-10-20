import copy
import json
import pathlib
import hashlib
import re
import time
import uuid

from aiohttp import web, WSMsgType
import asyncio

from .bridge_protocol import PushBridge, trace
from .execution_events import install_execution_observer

import folder_paths
import nodes
from server import PromptServer

try:
    from comfy_api.latest import InputImpl, io
    from comfy_extras.nodes_audio import load as _load_audio_file
    from comfy_extras.nodes_minimax_h3 import (
        MiniMaxH3ReferenceToVideo as _StockMiniMaxH3ReferenceToVideo,
    )
except ImportError:
    # Keep the general Anim Bridge available on ComfyUI versions that do not
    # include MiniMax H3 yet. The two H3 nodes are registered only when the
    # corresponding stock node is present.
    InputImpl = None
    io = None
    _load_audio_file = None
    _StockMiniMaxH3ReferenceToVideo = None


def _completed_outputs(prompt_id):
    # map_function selects results under ComfyUI's queue lock without its
    # default deep copy of the executable graph and workflow metadata.
    return PromptServer.instance.prompt_queue.get_history(prompt_id, map_function=lambda entry: {
        'outputs': entry.get('outputs', {}),
        'status': {key: entry.get('status', {}).get(key) for key in ('completed', 'status_str')},
    })


_push = PushBridge(_completed_outputs)
install_execution_observer(PromptServer.instance, _push)
trace('server.loaded', bridgeVersion=13)

ANIM_IMAGE_REFERENCES = 'ANIM_IMAGE_REFERENCES'
ANIM_VIDEO_REFERENCES = 'ANIM_VIDEO_REFERENCES'
ANIM_AUDIO_REFERENCES = 'ANIM_AUDIO_REFERENCES'
ANIM_IMAGE_REFERENCE_CAPACITY = 100
MINIMAX_H3_REFERENCE_IMAGE_CAPACITY = 9
MINIMAX_H3_REFERENCE_VIDEO_CAPACITY = 3
MINIMAX_H3_REFERENCE_AUDIO_CAPACITY = 3
MINIMAX_H3_REFERENCE_VIDEO_FPS = 24
# Ordered reference inputs for Qwen generation; legacy roles remain readable.
# These identifiers describe order only, not image semantics.
# keyframe_reference is an earlier keyframe image of the same Storyboard
# Sequence, sent when Anim draws the next keyframe of that scene.
ANIM_IMAGE_INPUT_IDS = (
    'character',
    'character_2',
    'character_3',
    'outfit',
    'location',
    'keyframe_reference',
    'image',
    *('reference_' + str(index) for index in range(1, 9)),
)
# Fixed roles an Anim Text Input can take: keyframe generation's scene and
# appearance, plus the direct instruction used by image editing.
ANIM_TEXT_INPUT_IDS = (
    'scene',
    'character_appearance',
    'character_appearance_2',
    'character_appearance_3',
    'prompt',
)
# Up to three characters: character (the first), character_2, character_3,
# each with its own appearance text.
ANIM_APPEARANCE_IDS = (
    'character_appearance',
    'character_appearance_2',
    'character_appearance_3',
)
# Roles a run may leave out; a template sentence about one of them is
# dropped when it has no image or is not wired to the encoder.
ANIM_OPTIONAL_IMAGE_IDS = ('character_2', 'character_3', 'keyframe_reference')
ANIM_QWEN_PROMPT_TEMPLATE = '{scene}'
# Qwen-Image-2.1 expects latent sizes in multiples of 32.
ANIM_RESOLUTION_STEP = 32
ANIM_RESOLUTION_MIN = 256
ANIM_RESOLUTION_MAX = 4096


def _json(payload, status=200):
    # ComfyUI's own origin/CORS middleware applies to these routes. Do not
    # bypass the server's configured origin policy from the Bridge.
    return web.json_response(payload, status=status)


@PromptServer.instance.routes.get('/anim_bridge/v1/health')
async def anim_bridge_health(_request):
    return _json({'bridgeVersion': 13, 'status': 'ok', 'transport': 'websocket'})


@PromptServer.instance.routes.get('/anim_bridge/v1/workflows')
async def anim_bridge_workflows(_request):
    return _json({'workflows': _push.metadata()})


_SAMPLE_DIRECTORY = pathlib.Path(__file__).resolve().parent / 'workflows'
_SAMPLE_PREFIX = 'anim-sample/'
_SAMPLE_NAMES = ('qwen21_firstframe_anim.json', 'qwen21_text2image_anim.json',
                 'qwen21_image_text2image_anim.json', 'krea2_text2image_anim.json',
                 'krea2_image_text2image_anim.json')


def _image_samples():
    return {_SAMPLE_PREFIX + name: _SAMPLE_DIRECTORY / name for name in _SAMPLE_NAMES
            if (_SAMPLE_DIRECTORY / name).is_file()}


def _load_image_sample(identity):
    path = _image_samples().get(identity)
    return json.loads(path.read_text(encoding='utf-8')) if path else None


@PromptServer.instance.routes.get('/anim_bridge/v1/samples')
async def anim_bridge_samples(_request):
    return _json({'workflows': [{'id': identity, 'name': path.name}
                               for identity, path in _image_samples().items()]})


@PromptServer.instance.routes.get('/anim_bridge/v1/events')
async def anim_bridge_events(request):
    role = request.query.get('role')
    key = (request.query.get('session_id', ''), request.query.get('tab_id', ''))
    if role not in ('browser', 'anim') or (role == 'browser' and not all(key)):
        return _json({'error': 'A browser identity and role are required'}, 400)
    socket = web.WebSocketResponse(max_msg_size=1024 * 1024)
    await socket.prepare(request)
    trace('socket.connected', role=role, tabId=key[1])
    tasks = set()
    async def command(payload):
        try:
            result = await _push.command(socket, payload, _load_image_sample)
            await _push.send(socket, {'event': 'result', **result})
        except asyncio.TimeoutError:
            trace('command.failed', requestId=str(payload.get('requestId')), error='Browser command timed out after 30 seconds')
            await _push.send(socket, {'event': 'result', 'requestId': payload.get('requestId'),
                'error': 'The ComfyUI browser did not finish the request within 30 seconds. Check its connection; execution was not retried.'})
        except Exception as error:
            trace('command.failed', requestId=str(payload.get('requestId')), error=str(error))
            await _push.send(socket, {'event': 'result', 'requestId': payload.get('requestId'), 'error': str(error)})
    if role == 'browser':
        await _push.attach(key, socket)
    else:
        _push.clients.add(socket)
    await socket.send_json({'event': 'ready', 'bridgeVersion': 13})
    if role == 'anim':
        await socket.send_json({'event': 'workflows', 'workflows': _push.metadata()})
    try:
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            payload = None
            try:
                payload = json.loads(message.data)
                if not isinstance(payload, dict):
                    continue
                event = payload.get('event')
                if role == 'browser':
                    if event == 'metadata':
                        await _push.publish(key, socket, payload.get('workflows') or [])
                    elif event == 'result':
                        _push.result(socket, payload)
                    elif event == 'diagnostic':
                        stage = payload.get('stage')
                        if stage in ('command.received', 'command.started', 'tab.loading', 'tab.ready',
                                     'inputs.applying', 'inputs.applied', 'run.started', 'run.received',
                                     'command.finished', 'command.failed', 'command.cancelled'):
                            trace('browser.' + stage, tabId=key[1],
                                  requestId=str(payload.get('requestId') or ''),
                                  promptId=str(payload.get('promptId') or ''),
                                  error=str(payload.get('error') or ''))
                elif event == 'command':
                    if len(tasks) >= 32:
                        raise ValueError('Too many requests')
                    task = asyncio.create_task(command(payload))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                elif event == 'watch' and isinstance(payload.get('promptId'), str):
                    await _push.watch(socket, payload['promptId'])
                elif event == 'cancel':
                    pending = _push.pending.get(payload.get('requestId'))
                    if pending and pending[0] is socket:
                        pending[2].cancel()
            except (ValueError, TypeError) as error:
                trace('socket.invalid_message', role=role, error=str(error))
                await _push.send(socket, {'event': 'result', 'requestId': payload.get('requestId') if isinstance(payload, dict) else None, 'error': str(error)})
    finally:
        trace('socket.disconnected', role=role, tabId=key[1])
        if role == 'browser':
            await _push.detach(key, socket)
        else:
            await _push.disconnect(socket)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    return socket


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
                        'step': 1.0,
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


class AnimSequenceOutput:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'filename_prefix': (
                    'STRING',
                    {
                        'default': 'ComfyUI',
                    },
                ),
            },
        }

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('filename_prefix',)
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives the output file name prefix Anim builds from the Sequence '
        'number and name, such as S001_Opening. Connect it to the '
        'filename_prefix input of the existing save node; this node does not '
        'save files.'
    )

    def emit(self, filename_prefix):
        # Anim sends an already sanitized prefix. An empty value falls back to
        # the stock save-node default instead of producing a nameless file.
        return (str(filename_prefix).strip() or 'ComfyUI',)


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


class AnimTextInput:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'text_id': (
                    list(ANIM_TEXT_INPUT_IDS),
                    {'default': ANIM_TEXT_INPUT_IDS[0]},
                ),
                'text': (
                    'STRING',
                    {
                        'default': '',
                        'multiline': True,
                    },
                ),
            },
        }

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('text',)
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives one text from Anim for a fixed role: scene (per Sequence) '
        'character_appearance (per character Asset), or prompt (image edit).'
    )

    def emit(self, text_id, text):
        if text_id not in ANIM_TEXT_INPUT_IDS:
            raise ValueError(
                f'Anim Text Input has an unknown text_id "{text_id}". '
                f'Use one of: {", ".join(ANIM_TEXT_INPUT_IDS)}.'
            )
        return (str(text),)


def _qwen_image_tokens(graph, encoder_id):
    """Maps each Anim Image Input role wired to a Qwen encoder to <imageN>."""
    tokens = {}
    encoder = graph.get(str(encoder_id)) or {}
    for input_name, connection in (encoder.get('inputs') or {}).items():
        if not input_name.startswith('images.image_'):
            continue
        slot = input_name[len('images.image_'):]
        if not slot.isdigit() or not isinstance(connection, (list, tuple)):
            continue
        source = graph.get(str(connection[0])) or {}
        if source.get('class_type') != 'AnimImageInput':
            continue
        role = (source.get('inputs') or {}).get('image_id')
        if role in ANIM_IMAGE_INPUT_IDS:
            tokens[role] = f'<image{slot}>'
    if any(role.startswith('reference_') for role in tokens):
        active = []
        for name, connection in (encoder.get('inputs') or {}).items():
            if not name.startswith('images.image_') or not isinstance(connection, (list, tuple)):
                continue
            source = graph.get(str(connection[0])) or {}
            inputs = source.get('inputs') or {}
            role = inputs.get('image_id')
            if role in tokens and str(inputs.get('image') or '').strip():
                active.append((int(name.rsplit('_', 1)[-1]), role))
        return {role: f'<image{index + 1}>' for index, (_, role) in enumerate(sorted(active))}
    return tokens


def _empty_qwen_roles(graph, encoder_id):
    """Roles whose Anim Image Input on the Qwen encoder received no image."""
    empty = set()
    encoder = graph.get(str(encoder_id)) or {}
    for input_name, connection in (encoder.get('inputs') or {}).items():
        if not input_name.startswith('images.image_'):
            continue
        if not isinstance(connection, (list, tuple)) or not connection:
            continue
        source = graph.get(str(connection[0])) or {}
        if source.get('class_type') != 'AnimImageInput':
            continue
        inputs = source.get('inputs') or {}
        if not str(inputs.get('image') or '').strip():
            empty.add(inputs.get('image_id'))
    return empty


def _drop_role_sentences(text, role):
    """Removes each sentence that points at {role}, with its leading space."""
    return re.sub(r'[^.]*\{' + re.escape(role) + r'\}[^.]*\.?', '', text)


def _downstream_qwen_encoders(graph, node_id):
    return [
        encoder_id
        for encoder_id, node in graph.items()
        if node.get('class_type') == 'TextEncodeQwenImage21'
        and any(
            isinstance(connection, (list, tuple))
            and connection
            and str(connection[0]) == str(node_id)
            for connection in (node.get('inputs') or {}).values()
        )
    ]


class AnimQwenPromptCompose:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'scene': ('STRING', {'forceInput': True}),
                'template': (
                    'STRING',
                    {
                        'default': ANIM_QWEN_PROMPT_TEMPLATE,
                        'multiline': True,
                    },
                ),
            },
            'optional': {
                'character_appearance': ('STRING', {'forceInput': True}),
                'character_appearance_2': ('STRING', {'forceInput': True}),
                'character_appearance_3': ('STRING', {'forceInput': True}),
            },
            'hidden': {
                'prompt': 'PROMPT',
                'unique_id': 'UNIQUE_ID',
            },
        }

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('prompt',)
    FUNCTION = 'compose'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Combines the scene and character appearance texts into one '
        'Qwen-Image-2.1 prompt. {character}, {character_2}, {character_3}, '
        '{outfit}, {location}, and {keyframe_reference} in the template '
        'become the <imageN> token of the matching Anim Image Input on the '
        'connected Qwen encoder. A sentence about a second or third '
        'character, or about the neighboring keyframe, is dropped when that '
        'image is empty or not wired.'
    )

    def compose(
        self,
        scene,
        template,
        character_appearance=None,
        character_appearance_2=None,
        character_appearance_3=None,
        prompt=None,
        unique_id=None,
    ):
        scene = str(scene or '').strip()
        if not scene:
            raise ValueError('Anim sent no scene text to compose.')
        text = str(template)
        appearances = zip(
            ANIM_APPEARANCE_IDS,
            (character_appearance, character_appearance_2, character_appearance_3),
        )
        for name, value in appearances:
            appearance = str(value or '').strip()
            if appearance:
                text = text.replace('{' + name + '}', appearance)
            else:
                # Drop the whole appearance sentence rather than leaving ": ."
                text = _drop_role_sentences(text, name)
        text = text.replace('{scene}', scene)

        graph = prompt if isinstance(prompt, dict) else {}
        encoders = _downstream_qwen_encoders(graph, unique_id)
        tokens = (
            _qwen_image_tokens(graph, encoders[0])
            if len(encoders) == 1
            else {}
        )
        empty = (
            _empty_qwen_roles(graph, encoders[0])
            if len(encoders) == 1
            else set()
        )
        # A role Anim left empty (such as keyframe_reference for the first
        # frame of a scene) sends no image, so its sentence is dropped too.
        for role in empty:
            text = _drop_role_sentences(text, role)
        # An optional role this workflow does not wire at all is left out too.
        for role in ANIM_OPTIONAL_IMAGE_IDS:
            if role not in tokens:
                text = _drop_role_sentences(text, role)
        for role in ANIM_IMAGE_INPUT_IDS:
            placeholder = '{' + role + '}'
            if placeholder not in text:
                continue
            if role not in tokens:
                raise ValueError(
                    f'The prompt template uses {placeholder}, but no {role} '
                    'Anim Image Input is wired to the Qwen encoder that '
                    'receives this prompt.'
                )
            text = text.replace(placeholder, tokens[role])
        return (text.strip(),)


class AnimImageInput:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'image_id': (
                    list(ANIM_IMAGE_INPUT_IDS),
                    {'default': 'reference_1'},
                ),
                'image': (
                    'STRING',
                    {
                        'default': '',
                    },
                ),
            },
        }

    RETURN_TYPES = ('IMAGE', 'MASK')
    RETURN_NAMES = ('image', 'mask')
    FUNCTION = 'load'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives one ordered reference image from Anim. Choose reference_1, '
        'reference_2, and so on to match the reference list in Anim. Empty '
        'inputs output no image. Legacy role IDs remain available for '
        'existing edit workflows. Files load from the ComfyUI input folder.'
    )

    @classmethod
    def IS_CHANGED(cls, image_id, image):
        # Re-run when the same file name is overwritten with new content.
        file_name = str(image or '').strip()
        if not file_name:
            return ''
        return nodes.LoadImage.IS_CHANGED(file_name)

    def load(self, image_id, image):
        if image_id not in ANIM_IMAGE_INPUT_IDS:
            raise ValueError(
                f'Anim Image Input has an unknown image_id "{image_id}". '
                f'Use one of: {", ".join(ANIM_IMAGE_INPUT_IDS)}.'
            )
        file_name = str(image or '').strip()
        if not file_name:
            # No image for this role in this run, such as keyframe_reference
            # for the first frame of a scene. Downstream nodes get None.
            return (None, None)
        if not folder_paths.exists_annotated_filepath(file_name):
            raise ValueError(
                f'The {image_id} image "{file_name}" is not in the ComfyUI '
                'input folder.'
            )
        return nodes.LoadImage().load_image(file_name)


class AnimOptionalImageInput:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'image_id': (
                    ['reference_image'],
                    {'default': 'reference_image'},
                ),
                'image': ('STRING', {'default': ''}),
            },
        }

    RETURN_TYPES = ('IMAGE', 'MASK')
    RETURN_NAMES = ('image', 'mask')
    FUNCTION = 'load'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives one optional extra reference for an Anim image edit. An '
        'empty value returns no image; the required image being edited uses '
        'Anim Image Input instead.'
    )

    @classmethod
    def IS_CHANGED(cls, image_id, image):
        return AnimImageInput.IS_CHANGED(image_id, image)

    def load(self, image_id, image):
        if image_id != 'reference_image':
            raise ValueError(
                'Anim Optional Image Input only accepts image_id '
                '"reference_image".'
            )
        file_name = str(image or '').strip()
        if not file_name:
            return (None, None)
        if not folder_paths.exists_annotated_filepath(file_name):
            raise ValueError(
                f'The {image_id} image "{file_name}" is not in the ComfyUI '
                'input folder.'
            )
        return nodes.LoadImage().load_image(file_name)


class AnimOptionalVAEEncode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'pixels': ('IMAGE',),
                'vae': ('VAE',),
            },
        }

    RETURN_TYPES = ('LATENT',)
    FUNCTION = 'encode'
    CATEGORY = 'Anim/Workflow'
    DESCRIPTION = (
        'Encodes an optional image. It returns no latent when an image-edit '
        'request has no source image.'
    )

    def encode(self, vae, pixels=None):
        if pixels is None:
            return (None,)
        return nodes.VAEEncode().encode(vae, pixels)


class AnimResolutionInput:
    @classmethod
    def INPUT_TYPES(cls):
        size = {
            'min': ANIM_RESOLUTION_MIN,
            'max': ANIM_RESOLUTION_MAX,
            'step': ANIM_RESOLUTION_STEP,
        }
        return {
            'required': {
                'width': ('INT', {'default': 2048, **size}),
                'height': ('INT', {'default': 1152, **size}),
            },
        }

    RETURN_TYPES = ('INT', 'INT')
    RETURN_NAMES = ('width', 'height')
    FUNCTION = 'emit'
    CATEGORY = 'Anim/Inputs'
    DESCRIPTION = (
        'Receives the output image width and height selected in Anim. '
        'Connect both outputs to the latent size of the workflow, such as '
        'Empty Latent Image.'
    )

    def emit(self, width, height):
        return (
            _resolution_side('width', width),
            _resolution_side('height', height),
        )


def _resolution_side(name, value):
    side = int(value)
    if not ANIM_RESOLUTION_MIN <= side <= ANIM_RESOLUTION_MAX:
        raise ValueError(
            f'Anim Resolution Input {name} {side} is outside '
            f'{ANIM_RESOLUTION_MIN}-{ANIM_RESOLUTION_MAX}.'
        )
    if side % ANIM_RESOLUTION_STEP:
        # Never resize silently: the image would not match what Anim asked for.
        raise ValueError(
            f'Anim Resolution Input {name} {side} is not a multiple of '
            f'{ANIM_RESOLUTION_STEP}.'
        )
    return side


def _validate_reference_bundle(references, media_label, capacity):
    if references is None:
        return None
    if not isinstance(references, (tuple, list)):
        raise TypeError(
            f'Anim MiniMax H3 requires {media_label} from the matching Anim '
            'References node.'
        )
    if not references:
        # Anim Video/Audio References send an empty bundle for a Sequence
        # without such references; the stock node treats None as "none".
        return None
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


def _parse_references(
    references_json,
    max_references,
    media_label,
    allow_empty=False,
):
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
    if not references and not allow_empty:
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
        'output to the video loader contract used by this workflow. A Sequence '
        'without motion or video references sends an empty array, which yields '
        'no references instead of an error.'
    )

    def emit(self, references_json, max_references):
        # Most Sequences have no reference video, so video is optional too.
        references = _parse_references(
            references_json,
            max_references,
            media_label='video',
            allow_empty=True,
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
        'output to the audio loader contract used by this workflow. A Sequence '
        'without voice or sound references sends an empty array, which yields '
        'no references instead of an error.'
    )

    def emit(self, references_json, max_references):
        # Many Sequences have no dialogue or voice timbre, so audio is optional.
        references = _parse_references(
            references_json,
            max_references,
            media_label='audio',
            allow_empty=True,
        )
        return (references, tuple(references))


NODE_CLASS_MAPPINGS = {
    'AnimPromptInput': AnimPromptInput,
    'AnimDurationInput': AnimDurationInput,
    'AnimSequenceOutput': AnimSequenceOutput,
    'AnimTextInput': AnimTextInput,
    'AnimQwenPromptCompose': AnimQwenPromptCompose,
    'AnimImageInput': AnimImageInput,
    'AnimOptionalImageInput': AnimOptionalImageInput,
    'AnimOptionalVAEEncode': AnimOptionalVAEEncode,
    'AnimResolutionInput': AnimResolutionInput,
    'AnimImageReferences': AnimImageReferences,
    'AnimVideoReferences': AnimVideoReferences,
    'AnimAudioReferences': AnimAudioReferences,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    'AnimPromptInput': 'Anim Prompt Input',
    'AnimDurationInput': 'Anim Duration Input',
    'AnimSequenceOutput': 'Anim Sequence Output',
    'AnimTextInput': 'Anim Text Input',
    'AnimQwenPromptCompose': 'Anim Qwen Prompt Compose',
    'AnimImageInput': 'Anim Image Input',
    'AnimOptionalImageInput': 'Anim Optional Image Input',
    'AnimOptionalVAEEncode': 'Anim Optional VAE Encode',
    'AnimResolutionInput': 'Anim Resolution Input',
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
