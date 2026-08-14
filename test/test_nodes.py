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
    comfy_extras_module = types.ModuleType('comfy_extras')
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
    sys.modules['aiohttp'] = aiohttp_module
    sys.modules['nodes'] = nodes_module
    sys.modules['server'] = server_module
    sys.modules['comfy_api'] = comfy_api_module
    sys.modules['comfy_api.latest'] = comfy_api_latest_module
    sys.modules['comfy_extras'] = comfy_extras_module
    sys.modules['comfy_extras.nodes_minimax_h3'] = minimax_module
    path = pathlib.Path(__file__).parents[1] / '__init__.py'
    spec = importlib.util.spec_from_file_location('anim_bridge_test_module', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge()


class AnimBridgeNodeTest(unittest.TestCase):
    def test_prompt_input_returns_the_string(self):
        self.assertEqual(bridge.AnimPromptInput().emit('prompt'), ('prompt',))

    def test_image_references_preserve_array_order(self):
        result = bridge.AnimImageReferences().load(
            'one.png\ntwo.png',
            2,
        )
        self.assertEqual(result[0], ['image:one.png', 'image:two.png'])
        self.assertEqual(result[1], ['mask:one.png', 'mask:two.png'])
        self.assertEqual(result[2], ['one.png', 'two.png'])

    def test_image_reference_node_declares_hidden_paths_and_configurable_capacity(self):
        inputs = bridge.AnimImageReferences.INPUT_TYPES()['required']

        self.assertTrue(inputs['image_paths'][1]['hidden'])
        self.assertEqual(inputs['max_references'][1]['default'], 9)
        self.assertEqual(inputs['max_references'][1]['max'], 100)

        with self.assertRaisesRegex(ValueError, 'allows 1'):
            bridge.AnimImageReferences().load('one.png\ntwo.png', 1)

    def test_minimax_h3_loader_accepts_100_network_images_in_order(self):
        paths = '\n'.join(f'image-{index}.png' for index in range(100))

        self.assertTrue(
            bridge.AnimMiniMaxH3ReferenceImageLoader.VALIDATE_INPUTS(paths),
        )
        bundle, image_list = (
            bridge.AnimMiniMaxH3ReferenceImageLoader().load_reference_images(
                paths,
            )
        )

        self.assertEqual(len(bundle), 100)
        self.assertEqual(bundle[0], 'image:image-0.png')
        self.assertEqual(bundle[-1], 'image:image-99.png')
        self.assertEqual(list(bundle), image_list)
        self.assertEqual(
            bridge.AnimMiniMaxH3ReferenceImageLoader.RETURN_TYPES,
            ('ANIM_MINIMAX_H3_REFERENCE_IMAGES', 'IMAGE'),
        )

    def test_minimax_h3_loader_rejects_more_than_100_network_images(self):
        paths = '\n'.join(f'image-{index}.png' for index in range(101))

        with self.assertRaisesRegex(ValueError, 'allows 100'):
            bridge.AnimMiniMaxH3ReferenceImageLoader().load_reference_images(
                paths,
            )

    def test_minimax_h3_wrapper_preserves_stock_connections_and_image_order(self):
        first = _ImageTensor('first')
        second = _ImageTensor('second')
        ref_videos = {'ref_video_0': object()}
        ref_video_audios = {'ref_video_audio_0': object()}
        ref_audios = {'ref_audio_0': object()}

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
            ref_video_audios=ref_video_audios,
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
        self.assertIs(forwarded['ref_videos'], ref_videos)
        self.assertIs(forwarded['ref_video_audios'], ref_video_audios)
        self.assertIs(forwarded['ref_audios'], ref_audios)
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

    def test_minimax_h3_wrapper_replaces_only_the_stock_image_input(self):
        schema = bridge.AnimMiniMaxH3ReferenceToVideo.define_schema()
        inputs = {input_spec.id: input_spec for input_spec in schema.inputs}

        self.assertEqual(schema.node_id, 'AnimMiniMaxH3ReferenceToVideo')
        self.assertEqual(
            inputs['ref_images'].type_name,
            'ANIM_MINIMAX_H3_REFERENCE_IMAGES',
        )
        self.assertIn('ref_videos', inputs)
        self.assertIn('ref_video_audios', inputs)
        self.assertIn('ref_audios', inputs)

    def test_video_and_audio_references_preserve_array_order(self):
        payload = json.dumps(['first.mp4', 'second.mp4'])
        self.assertEqual(
            bridge.AnimVideoReferences().emit(payload, 2),
            (['first.mp4', 'second.mp4'],),
        )
        audio_payload = json.dumps(['music.wav', 'voice.wav'])
        self.assertEqual(
            bridge.AnimAudioReferences().emit(audio_payload, 2),
            (['music.wav', 'voice.wav'],),
        )

    def test_reference_capacity_is_enforced(self):
        with self.assertRaisesRegex(ValueError, 'allows 1'):
            bridge.AnimVideoReferences().emit(
                json.dumps(['first.mp4', 'second.mp4']),
                1,
            )


if __name__ == '__main__':
    unittest.main()
