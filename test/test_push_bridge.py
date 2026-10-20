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
        self.assertEqual(self.browser.messages, [])

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
        self.assertEqual(len(second.messages), 1)
        self.assertEqual(read, ['anim-sample/one.json'])
        self.bridge.result(second, {'commandId': 'c', 'snapshot': {}})
        await task

    async def test_execution_has_prompt_identity_and_no_output_payload(self):
        self.bridge.clients.add(self.owner)
        await self.bridge.execution(self.browser, {'type': 'execution_success', 'data': {'prompt_id': 'p', 'outputs': 'huge'}})
        self.assertEqual(self.owner.messages, [{'event': 'execution', 'type': 'execution_success', 'data': {'prompt_id': 'p'}}])
        await self.bridge.execution(self.browser, {'type': 'progress', 'data': {}})
        self.assertEqual(len(self.owner.messages), 1)


if __name__ == '__main__':
    unittest.main()
