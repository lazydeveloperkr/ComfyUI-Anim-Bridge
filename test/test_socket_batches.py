"""Exercise the socket handler, including EOF before queue receipts arrive."""
import asyncio
import json
import types
import unittest
from unittest.mock import patch

from test_nodes import bridge


class Socket:
    def __init__(self, messages=()):
        self.incoming = iter(messages)
        self.sent = []

    async def prepare(self, request):
        pass

    async def send_json(self, payload):
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            payload = next(self.incoming)
        except StopIteration:
            raise StopAsyncIteration
        return types.SimpleNamespace(type=bridge.WSMsgType.TEXT, data=json.dumps(payload))


class SocketBatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_socket_accepts_1000_commands_and_eof_does_not_cancel_them(self):
        push = bridge.PushBridge(lambda _: {})
        browser = Socket()
        key = ('session', 'tab')
        await push.attach(key, browser)
        await push.publish(key, browser, [{'workflowId': 'workflow', 'name': 'Workflow'}])
        selected = push.metadata()[0]
        sender = Socket({'event': 'command', 'requestId': str(i), 'type': 'run',
                         **selected, 'parameters': {'prompt': f'Scene {i}'}} for i in range(1000))
        with patch.object(bridge, '_push', push), patch.object(
            bridge.web, 'WebSocketResponse', return_value=sender, create=True
        ):
            result = await bridge.anim_bridge_events(types.SimpleNamespace(query={'role': 'anim'}))
        self.assertIs(result, sender)
        self.assertNotIn(sender, push.clients)
        await asyncio.sleep(0)
        self.assertEqual(len(browser.sent), 1000)
        self.assertEqual([payload['requestId'] for payload in browser.sent], [str(i) for i in range(1000)])
        self.assertEqual(len(push.pending), 1000)
        for i in range(1000):
            push.result(browser, {'requestId': str(i), 'applied': True, 'promptId': f'p{i}'})
        await asyncio.gather(*push.commands)
        self.assertFalse(push.commands)
        self.assertFalse(push.pending)
        self.assertEqual(len(push.jobs), 1000)
        self.assertFalse(any(payload['event'] == 'cancel' for payload in browser.sent))
