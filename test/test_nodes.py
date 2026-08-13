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
    def load_image(self, file_name):
        return (f'image:{file_name}', f'mask:{file_name}')


def _load_bridge():
    aiohttp_module = types.ModuleType('aiohttp')
    aiohttp_module.web = types.SimpleNamespace(
        json_response=lambda payload, status=200: (payload, status),
    )
    nodes_module = types.ModuleType('nodes')
    nodes_module.LoadImage = _LoadImage
    server_module = types.ModuleType('server')
    server_module.PromptServer = type(
        'PromptServer',
        (),
        {'instance': types.SimpleNamespace(routes=_Routes())},
    )
    sys.modules['aiohttp'] = aiohttp_module
    sys.modules['nodes'] = nodes_module
    sys.modules['server'] = server_module
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
