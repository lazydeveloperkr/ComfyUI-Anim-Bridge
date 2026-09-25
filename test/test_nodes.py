import asyncio
import importlib.util
import json
import pathlib
import sys
import types
import unittest


class _Routes:
    def get(self, _path):
        return lambda function: function

    def post(self, _path):
        return lambda function: function


class _LoadImage:
    @classmethod
    def IS_CHANGED(cls, file_name):
        return f'changed:{file_name}'

    def load_image(self, file_name):
        return (f'image:{file_name}', f'mask:{file_name}')


class _ComfyNode:
    pass


class _CustomType:
    def __init__(self, type_name):
        self.type_name = type_name

    def Input(self, input_id, **kwargs):
        return types.SimpleNamespace(
            id=input_id,
            type_name=self.type_name,
            **kwargs,
        )


class _StockMiniMaxH3ReferenceToVideo:
    RETURN_TYPES = ('CONDITIONING', 'LATENT')
    RETURN_NAMES = ('positive', 'LATENT')
    CATEGORY = 'model/conditioning/minimax'
    last_execute = None

    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'clip': ('CLIP',),
                'vae': ('VAE',),
                'audio_vae': ('VAE',),
                'prompt': ('STRING',),
                'width': ('INT',),
                'height': ('INT',),
                'length': ('INT',),
                'ref_image_size': (['match', 'max'],),
            },
            'optional': {
                'ref_images': ('AUTOGROW',),
                'ref_videos': ('AUTOGROW',),
                'ref_video_audios': ('AUTOGROW',),
                'ref_audios': ('AUTOGROW',),
            },
        }

    @classmethod
    def define_schema(cls):
        return types.SimpleNamespace(
            node_id='MiniMaxH3ReferenceToVideo',
            display_name='MiniMax H3 Reference to Video',
            description='',
            inputs=[
                types.SimpleNamespace(id=input_id)
                for input_id in (
                    'clip',
                    'vae',
                    'audio_vae',
                    'prompt',
                    'width',
                    'height',
                    'length',
                    'ref_image_size',
                    'ref_images',
                    'ref_videos',
                    'ref_video_audios',
                    'ref_audios',
                )
            ],
        )

    @classmethod
    def execute(cls, **kwargs):
        cls.last_execute = kwargs
        return ('positive', 'latent')


class _ImageTensor:
    def __init__(self, name):
        self.name = name
        self.shape = (1, 64, 96, 3)


class _Frames:
    def __init__(self, values):
        self.values = list(values)

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        if isinstance(index, list):
            return _Frames(self.values[item] for item in index)
        return self.values[index]


class _VideoFile:
    def __init__(self, path):
        self.path = path

    def get_components(self):
        frame_rate = 30 if 'first' in self.path else 24
        return types.SimpleNamespace(
            images=_Frames(range(frame_rate)),
            audio=f'video-audio:{self.path}',
            frame_rate=frame_rate,
        )


class _Waveform:
    def __init__(self, value):
        self.value = value

    def unsqueeze(self, axis):
        return ('batched-waveform', axis, self.value)


def _load_bridge():
    aiohttp_module = types.ModuleType('aiohttp')
    aiohttp_module.web = types.SimpleNamespace(
        json_response=lambda payload, status=200: (payload, status),
    )
    nodes_module = types.ModuleType('nodes')
    nodes_module.LoadImage = _LoadImage
    comfy_api_module = types.ModuleType('comfy_api')
    comfy_api_latest_module = types.ModuleType('comfy_api.latest')
    comfy_api_latest_module.io = types.SimpleNamespace(
        ComfyNode=_ComfyNode,
        Custom=_CustomType,
    )
    comfy_api_latest_module.InputImpl = types.SimpleNamespace(
        VideoFromFile=_VideoFile,
    )
    comfy_extras_module = types.ModuleType('comfy_extras')
    audio_module = types.ModuleType('comfy_extras.nodes_audio')
    audio_module.load = lambda path: (_Waveform(path), 48000)
    minimax_module = types.ModuleType('comfy_extras.nodes_minimax_h3')
    minimax_module.MiniMaxH3ReferenceToVideo = (
        _StockMiniMaxH3ReferenceToVideo
    )
    server_module = types.ModuleType('server')
    server_module.PromptServer = type(
        'PromptServer',
        (),
        {'instance': types.SimpleNamespace(routes=_Routes())},
    )
    folder_paths_module = types.ModuleType('folder_paths')
    folder_paths_module.get_annotated_filepath = (
        lambda file_name: f'/input/{file_name}'
    )
    folder_paths_module.exists_annotated_filepath = (
        lambda file_name: not file_name.startswith('missing')
    )
    sys.modules['aiohttp'] = aiohttp_module
    sys.modules['nodes'] = nodes_module
    sys.modules['server'] = server_module
    sys.modules['comfy_api'] = comfy_api_module
    sys.modules['comfy_api.latest'] = comfy_api_latest_module
    sys.modules['comfy_extras'] = comfy_extras_module
    sys.modules['comfy_extras.nodes_audio'] = audio_module
    sys.modules['comfy_extras.nodes_minimax_h3'] = minimax_module
    sys.modules['folder_paths'] = folder_paths_module
    path = pathlib.Path(__file__).parents[1] / '__init__.py'
    spec = importlib.util.spec_from_file_location('anim_bridge_test_module', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge()


class _JsonRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class AnimBridgeNodeTest(unittest.TestCase):
    def setUp(self):
        bridge._sessions.clear()
        bridge._commands.clear()

    def test_apply_route_queues_visible_widget_assignments(self):
        bridge._sessions[('session', 'tab')] = {
            'lastSeen': bridge.time.time(),
            'workflows': [{'workflowId': 'workflow'}],
        }

        payload, status = asyncio.run(
            bridge.anim_bridge_apply(
                _JsonRequest(
                    {
                        'sessionId': 'session',
                        'tabId': 'tab',
                        'workflowId': 'workflow',
                        'inputs': {
                            '1.prompt': 'Visible prompt',
                            '2.image_paths': 'first.png\nlast.png',
                        },
                    }
                )
            )
        )

        self.assertEqual(status, 202)
        command = bridge._commands[payload['commandId']]
        self.assertEqual(command['type'], 'applyInputs')
        self.assertEqual(command['inputs']['1.prompt'], 'Visible prompt')
        self.assertEqual(
            command['inputs']['2.image_paths'],
            'first.png\nlast.png',
        )

    def test_apply_route_rejects_invalid_input_values(self):
        payload, status = asyncio.run(
            bridge.anim_bridge_apply(
                _JsonRequest(
                    {
                        'sessionId': 'session',
                        'tabId': 'tab',
                        'workflowId': 'workflow',
                        'inputs': {'1.prompt': {'not': 'a widget value'}},
                    }
                )
            )
        )

        self.assertEqual(status, 400)
        self.assertIn('Unsupported value', payload['error'])

    def test_prompt_input_returns_the_string(self):
        self.assertEqual(bridge.AnimPromptInput().emit('prompt'), ('prompt',))

    def test_duration_input_returns_a_float(self):
        self.assertEqual(bridge.AnimDurationInput().emit(5.0), (5.0,))
        self.assertEqual(bridge.AnimDurationInput().emit('5.5'), (5.5,))

    def test_duration_input_declares_a_float_widget(self):
        inputs = bridge.AnimDurationInput.INPUT_TYPES()['required']

        self.assertEqual(inputs['duration'][0], 'FLOAT')
        self.assertEqual(inputs['duration'][1]['default'], 5.0)
        self.assertEqual(inputs['duration'][1]['step'], 1.0)

    def test_sequence_output_returns_the_filename_prefix(self):
        self.assertEqual(
            bridge.AnimSequenceOutput().emit('S003_거실_공놀이'),
            ('S003_거실_공놀이',),
        )

    def test_sequence_output_falls_back_to_the_stock_prefix(self):
        self.assertEqual(bridge.AnimSequenceOutput().emit('  '), ('ComfyUI',))

    def test_sequence_output_declares_a_string_widget(self):
        inputs = bridge.AnimSequenceOutput.INPUT_TYPES()['required']

        self.assertEqual(list(inputs), ['filename_prefix'])
        self.assertEqual(inputs['filename_prefix'][0], 'STRING')
        self.assertEqual(bridge.AnimSequenceOutput.RETURN_TYPES, ('STRING',))

    def test_image_references_preserve_array_order(self):
        result = bridge.AnimImageReferences().load(
            'one.png\ntwo.png',
            2,
        )
        self.assertEqual(result[0], ['image:one.png', 'image:two.png'])
        self.assertEqual(result[1], ['mask:one.png', 'mask:two.png'])
        self.assertEqual(result[2], ['one.png', 'two.png'])
        self.assertEqual(result[3], ('image:one.png', 'image:two.png'))
        self.assertEqual(
            bridge.AnimImageReferences.RETURN_TYPES[-1],
            'ANIM_IMAGE_REFERENCES',
        )

    def test_image_reference_node_declares_hidden_paths_and_configurable_capacity(self):
        inputs = bridge.AnimImageReferences.INPUT_TYPES()['required']

        self.assertTrue(inputs['image_paths'][1]['hidden'])
        self.assertEqual(inputs['max_references'][1]['default'], 100)
        self.assertEqual(inputs['max_references'][1]['max'], 100)

        with self.assertRaisesRegex(ValueError, 'allows 1'):
            bridge.AnimImageReferences().load('one.png\ntwo.png', 1)

    def test_minimax_h3_wrapper_preserves_stock_connections_and_image_order(self):
        first = _ImageTensor('first')
        second = _ImageTensor('second')
        ref_videos = ('first.mp4', 'second.mp4')
        ref_audios = ('voice.wav', 'music.wav')

        result = bridge.AnimMiniMaxH3ReferenceToVideo.execute(
            clip='clip',
            vae='video-vae',
            audio_vae='audio-vae',
            prompt='prompt',
            width=1344,
            height=768,
            length=124,
            ref_image_size='max',
            ref_images=(first, second),
            ref_videos=ref_videos,
            ref_audios=ref_audios,
        )

        self.assertEqual(result, ('positive', 'latent'))
        forwarded = _StockMiniMaxH3ReferenceToVideo.last_execute
        self.assertEqual(
            list(forwarded['ref_images']),
            ['ref_image_0', 'ref_image_1'],
        )
        self.assertIs(forwarded['ref_images']['ref_image_0'], first)
        self.assertIs(forwarded['ref_images']['ref_image_1'], second)
        self.assertEqual(
            list(forwarded['ref_videos']),
            ['ref_video_0', 'ref_video_1'],
        )
        self.assertEqual(len(forwarded['ref_videos']['ref_video_0']), 24)
        self.assertEqual(len(forwarded['ref_videos']['ref_video_1']), 24)
        self.assertEqual(
            list(forwarded['ref_video_audios']),
            ['ref_video_audio_0', 'ref_video_audio_1'],
        )
        self.assertEqual(
            list(forwarded['ref_audios']),
            ['ref_audio_0', 'ref_audio_1'],
        )
        self.assertEqual(
            forwarded['ref_audios']['ref_audio_0']['sample_rate'],
            48000,
        )
        self.assertEqual(forwarded['prompt'], 'prompt')
        self.assertEqual(forwarded['width'], 1344)
        self.assertEqual(forwarded['height'], 768)
        self.assertEqual(forwarded['length'], 124)
        self.assertEqual(forwarded['ref_image_size'], 'max')

    def test_minimax_h3_wrapper_enforces_the_model_limit_of_9(self):
        images = tuple(_ImageTensor(str(index)) for index in range(10))

        with self.assertRaisesRegex(ValueError, 'at most 9'):
            bridge.AnimMiniMaxH3ReferenceToVideo.execute(
                clip='clip',
                vae='video-vae',
                audio_vae='audio-vae',
                prompt='prompt',
                width=1344,
                height=768,
                length=124,
                ref_images=images,
            )

    def test_minimax_h3_wrapper_enforces_video_and_audio_limits(self):
        with self.assertRaisesRegex(ValueError, 'at most 3 video references'):
            bridge.AnimMiniMaxH3ReferenceToVideo.execute(
                clip='clip',
                vae='video-vae',
                audio_vae='audio-vae',
                prompt='prompt',
                width=1344,
                height=768,
                length=124,
                ref_videos=tuple(f'video-{index}.mp4' for index in range(4)),
            )

        with self.assertRaisesRegex(ValueError, 'at most 3 audio references'):
            bridge.AnimMiniMaxH3ReferenceToVideo.execute(
                clip='clip',
                vae='video-vae',
                audio_vae='audio-vae',
                prompt='prompt',
                width=1344,
                height=768,
                length=124,
                ref_audios=tuple(f'audio-{index}.wav' for index in range(4)),
            )

    def test_minimax_h3_wrapper_replaces_only_the_stock_image_input(self):
        schema = bridge.AnimMiniMaxH3ReferenceToVideo.define_schema()
        inputs = {input_spec.id: input_spec for input_spec in schema.inputs}

        self.assertEqual(schema.node_id, 'AnimMiniMaxH3ReferenceToVideo')
        self.assertEqual(inputs['ref_images'].type_name, 'ANIM_IMAGE_REFERENCES')
        self.assertEqual(inputs['ref_videos'].type_name, 'ANIM_VIDEO_REFERENCES')
        self.assertEqual(inputs['ref_audios'].type_name, 'ANIM_AUDIO_REFERENCES')
        self.assertIn('ref_video_audios', inputs)

    def test_video_and_audio_references_preserve_array_order(self):
        payload = json.dumps(['first.mp4', 'second.mp4'])
        self.assertEqual(
            bridge.AnimVideoReferences().emit(payload, 2),
            (
                ['first.mp4', 'second.mp4'],
                ('first.mp4', 'second.mp4'),
            ),
        )
        audio_payload = json.dumps(['music.wav', 'voice.wav'])
        self.assertEqual(
            bridge.AnimAudioReferences().emit(audio_payload, 2),
            (
                ['music.wav', 'voice.wav'],
                ('music.wav', 'voice.wav'),
            ),
        )
        self.assertEqual(
            bridge.AnimVideoReferences.RETURN_TYPES[-1],
            'ANIM_VIDEO_REFERENCES',
        )
        self.assertEqual(
            bridge.AnimAudioReferences.RETURN_TYPES[-1],
            'ANIM_AUDIO_REFERENCES',
        )

    def test_minimax_h3_adds_no_model_specific_reference_loader(self):
        self.assertNotIn(
            'AnimMiniMaxH3ReferenceImageLoader',
            bridge.NODE_CLASS_MAPPINGS,
        )
        self.assertIn(
            'AnimMiniMaxH3ReferenceToVideo',
            bridge.NODE_CLASS_MAPPINGS,
        )

    def test_duration_input_is_registered(self):
        self.assertIs(
            bridge.NODE_CLASS_MAPPINGS['AnimDurationInput'],
            bridge.AnimDurationInput,
        )
        self.assertEqual(
            bridge.NODE_DISPLAY_NAME_MAPPINGS['AnimDurationInput'],
            'Anim Duration Input',
        )

    def test_sequence_output_is_registered(self):
        self.assertIs(
            bridge.NODE_CLASS_MAPPINGS['AnimSequenceOutput'],
            bridge.AnimSequenceOutput,
        )
        self.assertEqual(
            bridge.NODE_DISPLAY_NAME_MAPPINGS['AnimSequenceOutput'],
            'Anim Sequence Output',
        )

    def test_reference_capacity_is_enforced(self):
        with self.assertRaisesRegex(ValueError, 'allows 1'):
            bridge.AnimVideoReferences().emit(
                json.dumps(['first.mp4', 'second.mp4']),
                1,
            )

    def test_image_input_loads_the_file_for_its_role(self):
        self.assertEqual(
            bridge.AnimImageInput().load('outfit', ' outfit.webp '),
            ('image:outfit.webp', 'mask:outfit.webp'),
        )

    def test_image_input_offers_only_the_fixed_roles(self):
        image_id = bridge.AnimImageInput.INPUT_TYPES()['required']['image_id']
        self.assertEqual(image_id[0], ['character', 'outfit', 'location'])
        self.assertEqual(image_id[1]['default'], 'character')

    def test_image_input_rejects_an_unknown_role(self):
        with self.assertRaisesRegex(ValueError, 'unknown image_id "face"'):
            bridge.AnimImageInput().load('face', 'face.png')

    def test_image_input_rejects_an_empty_image(self):
        with self.assertRaisesRegex(ValueError, 'no location image'):
            bridge.AnimImageInput().load('location', '  ')

    def test_image_input_rejects_a_missing_file(self):
        with self.assertRaisesRegex(ValueError, 'not in the ComfyUI input'):
            bridge.AnimImageInput().load('character', 'missing.png')

    def test_image_input_reruns_when_the_file_changes(self):
        self.assertEqual(
            bridge.AnimImageInput.IS_CHANGED('character', 'char.png'),
            'changed:char.png',
        )
        self.assertEqual(bridge.AnimImageInput.IS_CHANGED('character', ''), '')

    def test_resolution_input_returns_width_and_height(self):
        self.assertEqual(
            bridge.AnimResolutionInput().emit(1152, 2048),
            (1152, 2048),
        )

    def test_resolution_input_declares_32_pixel_steps_and_2k_default(self):
        required = bridge.AnimResolutionInput.INPUT_TYPES()['required']
        self.assertEqual(required['width'][1]['default'], 2048)
        self.assertEqual(required['height'][1]['default'], 1152)
        self.assertEqual(required['width'][1]['step'], 32)
        self.assertEqual(required['height'][1]['step'], 32)

    def test_resolution_input_rejects_sizes_off_the_32_pixel_grid(self):
        with self.assertRaisesRegex(ValueError, 'height 1080 is not a multiple'):
            bridge.AnimResolutionInput().emit(1920, 1080)

    def test_resolution_input_rejects_sizes_out_of_range(self):
        with self.assertRaisesRegex(ValueError, 'width 8192 is outside'):
            bridge.AnimResolutionInput().emit(8192, 1152)

    def test_image_and_resolution_inputs_are_registered(self):
        self.assertIs(
            bridge.NODE_CLASS_MAPPINGS['AnimImageInput'],
            bridge.AnimImageInput,
        )
        self.assertIs(
            bridge.NODE_CLASS_MAPPINGS['AnimResolutionInput'],
            bridge.AnimResolutionInput,
        )
        self.assertEqual(
            bridge.NODE_DISPLAY_NAME_MAPPINGS['AnimImageInput'],
            'Anim Image Input',
        )
        self.assertEqual(
            bridge.NODE_DISPLAY_NAME_MAPPINGS['AnimResolutionInput'],
            'Anim Resolution Input',
        )

    def test_health_reports_bridge_version_5(self):
        payload, _status = asyncio.run(bridge.anim_bridge_health(None))
        self.assertEqual(payload['bridgeVersion'], 5)


if __name__ == '__main__':
    unittest.main()
