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


if __name__ == '__main__':
    unittest.main()
