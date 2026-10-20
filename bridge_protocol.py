"""Connection-owned metadata and bounded RPC. No graph/result cache or timers at idle."""
import asyncio
import uuid


class PushBridge:
    def __init__(self):
        self.browsers = {}
        self.clients = set()
        self.pending = {}

    def metadata(self):
        return [item for browser in self.browsers.values()
                for item in browser['workflows']]

    async def publish(self, key, socket, workflows):
        current = self.browsers.get(key)
        if current is None or current['socket'] is not socket:
            return
        # Whitelist scalar metadata. Never retain a browser-supplied graph.
        current['workflows'] = [
            {'sessionId': key[0], 'tabId': key[1],
             'workflowId': str(item.get('workflowId', '')),
             'title': str(item.get('title', '')),
             'isActive': item.get('isActive') is True,
             'isAvailable': True, 'protocolVersion': '2.0'}
            for item in workflows[:256] if isinstance(item, dict) and item.get('workflowId')
        ]

    async def attach(self, key, socket):
        previous = self.browsers.get(key)
        if previous:
            await previous['socket'].close()
            await self.detach(key, previous['socket'])
        self.browsers[key] = {'socket': socket, 'workflows': []}

    async def detach(self, key, socket):
        if self.browsers.get(key, {}).get('socket') is not socket:
            return
        del self.browsers[key]
        for _, (owner, target, future) in list(self.pending.items()):
            if target is socket and not future.done():
                future.set_exception(ConnectionError('ComfyUI browser disconnected'))

    def result(self, socket, payload):
        pending = self.pending.get(payload.get('commandId'))
        if pending and pending[1] is socket and not pending[2].done():
            pending[2].set_result(payload)

    async def command(self, owner, payload, samples, timeout=30):
        command_id = str(payload.get('commandId') or '')
        if not command_id or command_id in self.pending:
            raise ValueError('Missing or duplicate command ID')
        if len(self.pending) >= 64:
            raise ValueError('Too many pending commands')
        command_type = payload.get('type')
        if command_type not in ('refresh', 'loadSample', 'applyInputs', 'queuePrompt'):
            raise ValueError('Unsupported command')
        if command_type in ('applyInputs', 'queuePrompt'):
            inputs = payload.get('inputs')
            if not isinstance(inputs, dict) or (command_type == 'applyInputs' and not inputs):
                raise ValueError('inputs must be a non-empty object')
            for name, value in inputs.items():
                if not isinstance(name, str) or '.' not in name:
                    raise ValueError('Invalid input mapping')
                if not isinstance(value, (str, list)) or (isinstance(value, list) and
                        not all(isinstance(item, str) for item in value)):
                    raise ValueError('Unsupported value for input mapping')
        key = (str(payload.get('sessionId', '')), str(payload.get('tabId', '')))
        sample = None
        if command_type == 'loadSample':
            sample = samples(payload.get('workflowId'))
            if sample is None:
                raise ValueError('Installed image sample was not found')
            if not self.browsers:
                raise ConnectionError('Open a ComfyUI browser with Anim Bridge 12')
            key = next(reversed(self.browsers))
        browser = self.browsers.get(key)
        if browser is None or (sample is None and not any(
            item['workflowId'] == payload.get('workflowId') for item in browser['workflows']
        )):
            raise ConnectionError('The selected workflow tab is not available')
        command = {name: payload[name] for name in
                   ('commandId', 'type', 'workflowId', 'inputs') if name in payload}
        command.update(sessionId=key[0], tabId=key[1])
        if sample is not None:
            command.update(sample={**sample, 'id': str(uuid.uuid4())},
                           sampleName=payload['workflowId'].split('/', 1)[1])
        future = asyncio.get_running_loop().create_future()
        target = browser['socket']
        self.pending[command_id] = (owner, target, future)
        try:
            await target.send_json({'event': 'command', **command})
            return await asyncio.wait_for(future, timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            try:
                await target.send_json({'event': 'cancel', 'commandId': command_id})
            except (ConnectionError, RuntimeError):
                pass
            raise
        finally:
            self.pending.pop(command_id, None)

    async def cancel(self, owner, command_id):
        pending = self.pending.get(command_id)
        if pending and pending[0] is owner and not pending[2].done():
            pending[2].cancel()

    async def execution(self, socket, payload):
        if not any(browser['socket'] is socket for browser in self.browsers.values()):
            return
        if payload.get('type') not in ('execution_start', 'executing', 'execution_success',
                                        'execution_error', 'execution_interrupted', 'progress'):
            return
        data = payload.get('data') or {}
        if not isinstance(data, dict) or not data.get('prompt_id'):
            return
        # Forward only identity/status, never executed image tensors or graph data.
        event = {'event': 'execution', 'type': payload['type'], 'data': {
            name: data[name] for name in ('prompt_id', 'node', 'value', 'max', 'exception_message')
            if name in data
        }}
        for client in list(self.clients):
            try:
                await client.send_json(event)
            except (ConnectionError, RuntimeError):
                self.clients.discard(client)
