import json
import pathlib
import unittest


class Krea2ImageEditWorkflowTest(unittest.TestCase):
    def setUp(self):
        path = (
            pathlib.Path(__file__).parents[1]
            / 'workflows'
            / 'krea2_image_edit_anim.json'
        )
        self.workflow = json.loads(path.read_text(encoding='utf-8'))
        self.nodes = {node['id']: node for node in self.workflow['nodes']}

    def test_exposes_required_source_optional_reference_and_prompt(self):
        source = self.nodes[72]
        reference = self.nodes[90]
        prompt = self.nodes[111]
        self.assertEqual(source['type'], 'AnimImageInput')
        self.assertEqual(source['widgets_values'][0], 'image')
        self.assertEqual(reference['type'], 'AnimOptionalImageInput')
        self.assertEqual(reference['widgets_values'][0], 'reference_image')
        self.assertEqual(reference['mode'], 0)
        self.assertEqual(prompt['type'], 'AnimTextInput')
        self.assertEqual(prompt['widgets_values'][0], 'prompt')
        self.assertNotEqual(prompt['widgets_values'][1].strip(), '')

        roles = [
            node['widgets_values'][0]
            for node in self.nodes.values()
            if node['type'] in {
                'AnimImageInput',
                'AnimOptionalImageInput',
                'AnimTextInput',
            }
        ]
        self.assertEqual(roles, ['image', 'reference_image', 'prompt'])

    def test_wires_the_source_and_prompt_to_krea2_edit(self):
        links = {link[0]: link for link in self.workflow['links']}
        self.assertEqual(links[28][1:5], [111, 0, 84, 1])
        self.assertEqual(links[12][1:5], [72, 0, 84, 2])
        self.assertEqual(links[18][1:5], [90, 0, 84, 3])
        self.assertEqual(self.nodes[84]['type'], 'Krea2EditGroundedEncode')
        self.assertEqual(self.nodes[79]['type'], 'Krea2EditModelPatch')
        self.assertEqual(self.nodes[73]['type'], 'VAEEncode')
        self.assertEqual(self.nodes[92]['type'], 'AnimOptionalVAEEncode')
        self.assertEqual(links[29][1:5], [112, 0, 29, 1])


class Krea2GroundedEncodeValuesTest(unittest.TestCase):
    """Krea2 Edit Grounded Encode keeps three widget values, prompt first,
    even when its prompt is linked. A missing prompt value shifts
    grounding_px to '' and ComfyUI fails to read it as an INT."""

    def test_every_grounded_encode_has_an_int_grounding_px(self):
        folder = pathlib.Path(__file__).parents[1] / 'workflows'
        checked = 0
        for path in sorted(folder.glob('*.json')):
            workflow = json.loads(path.read_text(encoding='utf-8'))
            for node in workflow.get('nodes', []):
                if node.get('type') != 'Krea2EditGroundedEncode':
                    continue
                values = node['widgets_values']
                with self.subTest(workflow=path.name, node=node['id']):
                    self.assertEqual(len(values), 3)
                    self.assertIsInstance(values[1], int)
                checked += 1
        self.assertGreater(checked, 0)


FIXTURES = pathlib.Path(__file__).parent / 'fixtures'


class BridgeSmokeFixtureTest(unittest.TestCase):
    """The smoke workflows must stay CPU-only editor files that ComfyUI can open."""

    STOCK = {'LoadImage', 'SaveImage', 'StringConcatenate', 'StringSubstring'}
    ANIM = {'AnimTextInput', 'AnimPromptInput', 'AnimDurationInput', 'AnimSequenceOutput'}

    def load(self, name):
        return json.loads((FIXTURES / 'workflows' / name).read_text(encoding='utf-8'))

    def test_fixtures_are_editor_workflows_with_consistent_links(self):
        for path in (FIXTURES / 'workflows').glob('*.json'):
            workflow = json.loads(path.read_text(encoding='utf-8'))
            # Saved from the ComfyUI editor, not an API prompt.
            self.assertIn('nodes', workflow, path.name)
            self.assertIn('links', workflow, path.name)
            nodes = {node['id']: node for node in workflow['nodes']}
            for link_id, source, _, target, slot, _type in workflow['links']:
                self.assertIn(source, nodes, path.name)
                self.assertEqual(nodes[target]['inputs'][slot]['link'], link_id, path.name)

    def test_fixtures_need_no_model_or_gpu(self):
        allowed = self.STOCK | self.ANIM | {'AnimBridgeTestDelay'}
        for path in (FIXTURES / 'workflows').glob('*.json'):
            types = {node['type'] for node in self.load(path.name)['nodes']}
            self.assertLessEqual(types, allowed, path.name)
            self.assertIn('LoadImage', types)
            self.assertIn('SaveImage', types)

    def test_image_and_storyboard_inputs_reach_the_saved_file_name(self):
        image = {node['type']: node for node in self.load('bridge_cpu_smoke.json')['nodes']}
        self.assertEqual(image['AnimTextInput']['widgets_values'][0], 'prompt')
        storyboard = {node['type'] for node in self.load('bridge_storyboard_cpu_smoke.json')['nodes']}
        self.assertTrue({'AnimPromptInput', 'AnimDurationInput', 'AnimSequenceOutput'} <= storyboard)
        delay = {node['type']: node for node in self.load('bridge_storyboard_delay_smoke.json')['nodes']}
        self.assertEqual(delay['AnimBridgeTestDelay']['widgets_values'], [60])

    def test_smoke_assets_ship_as_tests_not_as_user_samples(self):
        self.assertTrue((FIXTURES / 'assets' / 'bridge_smoke.png').read_bytes().startswith(b'\x89PNG'))
        init = (pathlib.Path(__file__).parents[1] / '__init__.py').read_text(encoding='utf-8')
        self.assertNotIn('bridge_cpu_smoke', init)
        self.assertNotIn('AnimBridgeTestDelay', init)


if __name__ == '__main__':
    unittest.main()

class GenericGenerationWorkflowsTest(unittest.TestCase):
    def test_four_templates_have_valid_links_and_anim_inputs(self):
        folder = pathlib.Path(__file__).parents[1] / 'workflows'
        for model, mode, count in [('qwen21','text2image',0), ('qwen21','image_text2image',8), ('krea2','text2image',0), ('krea2','image_text2image',2)]:
            with self.subTest(model=model, mode=mode):
                workflow = json.loads((folder / f'{model}_{mode}_anim.json').read_text())
                nodes = {n['id']: n for n in workflow['nodes']}
                images = [n for n in nodes.values() if n['type'] in ['AnimImageInput', 'AnimOptionalImageInput']]
                self.assertEqual([n['widgets_values'][0] for n in images], [f'reference_{i+1}' for i in range(count)])
                self.assertTrue(any(n['type']=='AnimTextInput' and n['widgets_values'][0]=='scene' for n in nodes.values()))
                self.assertTrue(any(n['type']=='AnimResolutionInput' for n in nodes.values()))
                self.assertTrue(any(n['type']=='AnimSequenceOutput' for n in nodes.values()))
                for link in workflow['links']:
                    self.assertIn(link[0], nodes[link[1]]['outputs'][link[2]]['links'])
                    self.assertEqual(nodes[link[3]]['inputs'][link[4]]['link'], link[0])
                if mode=='text2image':
                    self.assertFalse(any(n['type']=='Krea2EditModelPatch' for n in nodes.values()))
