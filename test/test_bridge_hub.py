import asyncio
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('bridge_protocol', pathlib.Path(__file__).parents[1] / 'bridge_protocol.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

GRAPH = {
    '1': {'class_type': 'LoadImage', 'inputs': {'image': 'a.png'}},
    '2': {'class_type': 'AnimPromptInput', 'inputs': {'prompt': ''}},
    '3': {'class_type': 'AnimDurationInput', 'inputs': {'duration': 5.0}},
    '4': {'class_type': 'AnimSequenceOutput', 'inputs': {'filename_prefix': 'x'}},
    '5': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0], 'filename_prefix': ['4', 0]}},
    '6': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 512, 'batch_size': 1}},
}


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


def publication(**extra):
    return {'sessionId': 's', 'tabId': 't', 'clientId': 'c',
            'workflows': [{'workflowId': 'w', 'title': 'W', 'isActive': True},
                          {'workflowId': 'other', 'title': 'Other'}],
            'snapshot': {'workflowId': 'w', 'apiGraph': GRAPH, 'revision': 'r1',
                         'inputs': [{'nodeId': '2', 'inputName': 'prompt'}], 'outputNodeIds': ['5']},
            **extra}


def command(kind, **extra):
    return {'commandId': 'c1', 'type': kind, 'sessionId': 's', 'tabId': 't', 'workflowId': 'w', **extra}


class BridgeHubTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connected = {'c'}
        self.hub = module.BridgeHub(is_connected=lambda client_id: client_id in self.connected)
        self.hub.publish(publication())
        self.anim = Socket()
        self.hub.clients.add(self.anim)
        self.submitted = []

    async def submit(self, prompt, client_id):
        self.submitted.append((prompt, client_id))
        return 'prompt-1'

    async def test_inspection_answers_from_the_published_graph_without_any_tab_round_trip(self):
        result = await self.hub.command(self.anim, command('refresh'), self.submit)
        snapshot = result['snapshot']
        self.assertEqual((snapshot['sessionId'], snapshot['tabId'], snapshot['workflowId']), ('s', 't', 'w'))
        self.assertEqual(snapshot['apiGraph'], GRAPH)
        self.assertEqual(snapshot['revision'], 'r1')
        self.assertEqual(snapshot['outputNodeIds'], ['5'])
        snapshot['apiGraph']['1']['inputs']['image'] = 'changed'
        self.assertEqual(GRAPH['1']['inputs']['image'], 'a.png', 'callers get a copy')

    async def test_metadata_lists_open_workflows_and_hides_graphs(self):
        rows = self.hub.metadata()
        self.assertEqual([row['workflowId'] for row in rows], ['w', 'other'])
        self.assertTrue(all('apiGraph' not in row for row in rows))
        self.assertEqual(rows[0]['protocolVersion'], '2.0')

    async def test_apply_reads_back_values_as_comfyui_stores_them(self):
        result = await self.hub.command(self.anim, command('applyInputs', inputs={
            '2.prompt': 'hello', '3.duration': '7', '6.width': '640', '4.filename_prefix': 'S001'}), self.submit)
        self.assertEqual(result['values'], {'2.prompt': 'hello', '3.duration': 7.0, '6.width': 640,
                                            '4.filename_prefix': 'S001'})
        self.assertIsInstance(result['values']['6.width'], int)
        self.assertEqual(self.submitted, [], 'applying never runs the workflow')

    async def test_run_queues_the_published_graph_with_inputs_and_tracks_the_prompt(self):
        result = await self.hub.command(self.anim, command('queuePrompt', inputs={
            '2.prompt': 'go', '4.filename_prefix': 'S001_Smoke', '1.image': ['a.png', 'b.png']}), self.submit)
        self.assertEqual(result, {'promptId': 'prompt-1'})
        prompt, client_id = self.submitted[0]
        self.assertEqual(client_id, 'c', 'the tab sees progress for runs Anim started')
        self.assertEqual(prompt['2']['inputs']['prompt'], 'go')
        self.assertEqual(prompt['1']['inputs']['image'], {'__value__': ['a.png', 'b.png']})
        self.assertEqual(prompt['5']['inputs']['images'], ['1', 0])
        self.assertEqual(GRAPH['2']['inputs']['prompt'], '', 'the published graph is not changed')
        self.assertIn('prompt-1', self.hub.subscriptions[self.anim])

    async def test_inputs_that_cannot_be_set_fail_with_a_message(self):
        for inputs, message in (({'9.prompt': 'x'}, 'not in this workflow'),
                                ({'5.filename_prefix': 'x'}, 'connected to another node'),
                                ({'3.duration': 'long'}, 'not a number')):
            with self.assertRaisesRegex(module.BridgeError, message):
                await self.hub.command(self.anim, command('applyInputs', inputs=inputs), self.submit)

    async def test_workflow_never_shown_in_this_tab_explains_how_to_share_it(self):
        with self.assertRaisesRegex(module.BridgeError, 'Click its tab in ComfyUI'):
            await self.hub.command(self.anim, command('refresh', workflowId='other'), self.submit)

    async def test_closed_or_disconnected_tab_is_not_listed_or_used(self):
        self.connected.clear()
        self.assertEqual(self.hub.metadata(), [])
        with self.assertRaisesRegex(module.BridgeError, 'not open'):
            await self.hub.command(self.anim, command('refresh'), self.submit)
        self.connected.add('c'); self.hub.publish(publication())
        self.hub.unpublish({'sessionId': 's', 'tabId': 't'})
        self.assertEqual(self.hub.metadata(), [])

    async def test_closing_a_workflow_drops_its_graph(self):
        self.hub.publish(publication(workflows=[{'workflowId': 'other'}], snapshot=None))
        self.hub.publish(publication(snapshot=None))
        with self.assertRaisesRegex(module.BridgeError, 'Click its tab'):
            await self.hub.command(self.anim, command('refresh'), self.submit)

    async def test_switching_tabs_keeps_the_graph_of_the_previous_active_workflow(self):
        self.hub.publish(publication(snapshot={'workflowId': 'other', 'apiGraph': {'9': {}}}))
        result = await self.hub.command(self.anim, command('refresh'), self.submit)
        self.assertEqual(result['snapshot']['apiGraph'], GRAPH)

    async def test_status_reaches_only_the_client_that_asked(self):
        other = Socket(); self.hub.clients.add(other)
        await self.hub.command(self.anim, command('queuePrompt', inputs={}), self.submit)
        await self.hub.execution('executing', {'prompt_id': 'prompt-1', 'node': None, 'outputs': 'huge'})
        self.assertEqual(self.anim.messages, [{'event': 'execution', 'type': 'executing',
                                               'data': {'prompt_id': 'prompt-1', 'node': None}}])
        self.assertEqual(other.messages, [])
        await self.hub.execution('executed', {'prompt_id': 'prompt-1'})
        await self.hub.execution('status', {'status': {}})
        self.assertEqual(len(self.anim.messages), 1)

    async def test_status_produced_before_the_queue_response_is_not_lost(self):
        async def submit(prompt, client_id):
            await self.hub.execution('execution_start', {'prompt_id': 'fast'})
            return 'fast'
        await self.hub.command(self.anim, command('queuePrompt', inputs={}), submit)
        self.assertEqual(self.anim.messages[0]['data'], {'prompt_id': 'fast'})
        self.assertFalse(self.hub.queueing)

    async def test_rejected_run_reports_and_tracks_nothing(self):
        async def submit(prompt, client_id):
            raise module.BridgeError('ComfyUI rejected the workflow: missing node')
        with self.assertRaisesRegex(module.BridgeError, 'rejected'):
            await self.hub.command(self.anim, command('queuePrompt', inputs={}), submit)
        self.assertFalse(self.hub.subscriptions)
        self.assertFalse(self.hub.queueing)

    async def test_installed_samples_explain_they_must_be_opened_in_comfyui(self):
        with self.assertRaisesRegex(module.BridgeError, 'Open the sample in ComfyUI'):
            await self.hub.command(self.anim, command('loadSample'), self.submit)

    async def test_invalid_commands_are_rejected_before_anything_runs(self):
        for inputs in ({'1.prompt': {'bad': 1}}, {'bad': 'v'}, {'1.images': [42]}):
            with self.assertRaises(module.BridgeError):
                await self.hub.command(self.anim, command('queuePrompt', inputs=inputs), self.submit)
        with self.assertRaises(module.BridgeError):
            await self.hub.command(self.anim, command('applyInputs', inputs={}), self.submit)
        self.assertEqual(self.submitted, [])

    async def test_watch_limits_and_forget(self):
        await self.hub.track_one(self.anim, 'p', True)
        await self.hub.track_one(self.anim, 'p', False)
        self.assertFalse(self.hub.subscriptions)
        with self.assertRaises(module.BridgeError):
            await self.hub.track(self.anim, [str(n) for n in range(65)])
        await self.hub.track(self.anim, ['p'])
        self.hub.forget(self.anim)
        self.assertFalse(self.hub.subscriptions)
        self.assertNotIn(self.anim, self.hub.clients)

    async def test_tab_identity_is_required(self):
        with self.assertRaises(module.BridgeError):
            self.hub.publish({'workflows': []})


if __name__ == '__main__':
    unittest.main()
