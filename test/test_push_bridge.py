import asyncio
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('push_protocol', pathlib.Path(__file__).parents[1] / 'bridge_protocol.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Socket:
    def __init__(self):
        self.messages = []
        self.closed = False

    async def send_json(self, message):
        self.messages.append(message)

    async def close(self):
        self.closed = True


class PushProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge = module.PushBridge()
        self.browser = Socket()
        self.owner = Socket()
        await self.bridge.attach(('s', 't'), self.browser)
        await self.bridge.publish(('s', 't'), self.browser, [{'workflowId': 'w', 'title': 'W', 'apiGraph': {'huge': 1}}])

    def command(self, **extra):
        return {'commandId': 'c', 'type': 'refresh', 'sessionId': 's', 'tabId': 't', 'workflowId': 'w', **extra}

    async def test_invalid_assignments_are_rejected_before_dispatch(self):
        for inputs in ({}, {'1.prompt': {'bad': 1}}, {'bad': 'v'}, {'1.images': [42]}):
            with self.assertRaises(ValueError):
                await self.bridge.command(self.owner, self.command(type='applyInputs', inputs=inputs), lambda _: None)
        self.assertEqual([m for m in self.browser.messages if m['event'] == 'command'], [])

    async def test_metadata_is_whitelisted_and_disconnect_removes_availability(self):
        self.assertNotIn('apiGraph', self.bridge.metadata()[0])
        await self.bridge.detach(('s', 't'), self.browser)
        self.assertEqual(self.bridge.metadata(), [])

    async def test_correlated_result_has_no_retained_graph_or_result(self):
        task = asyncio.create_task(self.bridge.command(self.owner, self.command(), lambda _: None))
        await asyncio.sleep(0)
        self.bridge.result(Socket(), {'commandId': 'c', 'snapshot': {'wrong': True}})
        self.assertFalse(task.done())
        result = {'commandId': 'c', 'snapshot': {'apiGraph': {'selected': 1}}}
        self.bridge.result(self.browser, result)
        self.assertEqual(await task, result)
        self.assertEqual(self.bridge.pending, {})
        self.assertNotIn('apiGraph', self.bridge.metadata()[0])

    async def test_duplicate_command_is_rejected_and_disconnect_fails_waiter(self):
        task = asyncio.create_task(self.bridge.command(self.owner, self.command(), lambda _: None))
        await asyncio.sleep(0)
        with self.assertRaises(ValueError):
            await self.bridge.command(self.owner, self.command(), lambda _: None)
        await self.bridge.detach(('s', 't'), self.browser)
        with self.assertRaises(ConnectionError):
            await task
        self.assertFalse(self.bridge.pending)

    async def test_timeout_and_owner_cancellation_release_waiters(self):
        with self.assertRaises(asyncio.TimeoutError):
            await self.bridge.command(self.owner, self.command(), lambda _: None, timeout=.001)
        self.assertFalse(self.bridge.pending)
        task = asyncio.create_task(self.bridge.command(self.owner, self.command(), lambda _: None))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.bridge.pending)

    async def test_reconnect_does_not_let_old_connection_remove_new_metadata(self):
        replacement = Socket()
        await self.bridge.attach(('s', 't'), replacement)
        await self.bridge.publish(('s', 't'), replacement, [{'workflowId': 'new'}])
        await self.bridge.detach(('s', 't'), self.browser)
        self.assertEqual(self.bridge.metadata()[0]['workflowId'], 'new')
        await self.bridge.publish(('s', 't'), self.browser, [{'workflowId': 'old'}])
        self.assertEqual(self.bridge.metadata()[0]['workflowId'], 'new')

    async def test_multiple_tabs_and_selected_sample_only(self):
        second = Socket()
        await self.bridge.attach(('s', 't2'), second)
        await self.bridge.publish(('s', 't2'), second, [{'workflowId': 'other'}])
        self.assertEqual(len(self.bridge.metadata()), 2)
        read = []
        def sample(sample_id):
            read.append(sample_id)
            return {'nodes': []} if sample_id == 'anim-sample/one.json' else None
        task = asyncio.create_task(self.bridge.command(self.owner,
            self.command(type='loadSample', workflowId='anim-sample/one.json'), sample))
        await asyncio.sleep(0)
        self.assertEqual(len([m for m in second.messages if m['event'] == 'command']), 1)
        self.assertEqual(read, ['anim-sample/one.json'])
        self.bridge.result(second, {'commandId': 'c', 'snapshot': {}})
        await task

    async def test_execution_has_prompt_identity_and_no_output_payload(self):
        self.bridge.clients.add(self.owner)
        await self.bridge.track(self.owner, ['p'])
        await self.bridge.execution(self.browser, {'type': 'execution_success', 'data': {'prompt_id': 'p', 'outputs': 'huge'}})
        self.assertEqual(self.owner.messages, [{'event': 'execution', 'type': 'execution_success', 'data': {'prompt_id': 'p'}}])
        await self.bridge.execution(self.browser, {'type': 'progress', 'data': {}})
        self.assertEqual(len(self.owner.messages), 1)

    async def test_unsubscribed_and_unrelated_execution_is_dropped(self):
        self.bridge.clients.add(self.owner)
        await self.bridge.execution(self.browser, {'type':'executing', 'data': {'prompt_id':'p', 'node':None}})
        self.assertEqual(self.owner.messages, [])
        await self.bridge.track(self.owner, ['other'])
        await self.bridge.execution(self.browser, {'type':'execution_error', 'data': {'prompt_id':'p'}})
        self.assertEqual(self.owner.messages, [])
        await self.bridge.track_one(self.owner, 'p', True)
        await self.bridge.execution(self.browser, {'type':'execution_error', 'data': {'prompt_id':'p', 'traceback':['huge']}})
        self.assertEqual(self.owner.messages[-1]['data'], {'prompt_id':'p'})
        await self.bridge.track(self.owner, [])
        self.assertEqual(self.browser.messages[-1], {'event':'tracking', 'promptIds':[]})
        self.assertFalse(self.bridge.subscriptions)

    async def test_dirty_flag_survives_metadata_whitelist(self):
        await self.bridge.publish(('s','t'), self.browser, [{'workflowId':'w', 'isDirty':True, 'apiGraph':{'huge':1}}])
        self.assertTrue(self.bridge.metadata()[0]['isDirty'])
        self.assertNotIn('apiGraph', self.bridge.metadata()[0])

    async def test_queue_result_preserves_interest_until_explicit_unwatch(self):
        self.bridge.clients.add(self.owner)
        task = asyncio.create_task(self.bridge.command(self.owner, self.command(type='queuePrompt', inputs={}), lambda _:None))
        await asyncio.sleep(0)
        self.bridge.result(self.browser, {'commandId':'c', 'promptId':'p'})
        await task
        await self.bridge.execution(self.browser, {'type':'executing', 'data':{'prompt_id':'p', 'node':None}})
        self.assertEqual(len(self.owner.messages), 1)
        await self.bridge.track_one(self.owner, 'p', False)
        self.assertFalse(self.bridge.subscriptions)

    async def test_pending_queue_reserves_global_prompt_interest_capacity(self):
        for index in range(4):
            await self.bridge.track(Socket(), [f'{index}-{n}' for n in range(64 if index < 3 else 63)])
        task = asyncio.create_task(self.bridge.command(self.owner,
            self.command(type='queuePrompt', inputs={}), lambda _: None))
        await asyncio.sleep(0)
        with self.assertRaisesRegex(ValueError, 'Too many tracked'):
            await self.bridge.track(Socket(), ['extra'])
        with self.assertRaisesRegex(ValueError, 'Too many tracked'):
            await self.bridge.command(Socket(), self.command(commandId='next', type='queuePrompt', inputs={}), lambda _: None)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.bridge.track(Socket(), ['extra'])
        self.assertEqual(len(self.bridge.tracked_ids()), 256)


if __name__ == '__main__':
    unittest.main()
