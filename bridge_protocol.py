"""Connection-owned metadata and bounded RPC. No graph/result cache or timers at idle."""
import asyncio
import uuid


class PushBridge:
    def __init__(self):
        self.browsers = {}
        self.clients = set()
        self.pending = {}
        self.subscriptions = {}
        self.queue_commands = set()

    def metadata(self):
        return [item for browser in self.browsers.values()
                for item in browser['workflows']]

    def tracked_ids(self):
        return set().union(*self.subscriptions.values()) if self.subscriptions else set()

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
             'isDirty': item.get('isDirty') is True,
             'isAvailable': True, 'protocolVersion': '2.0'}
            for item in workflows[:256] if isinstance(item, dict) and item.get('workflowId')
        ]

    async def attach(self, key, socket):
        previous = self.browsers.get(key)
        if previous:
            await previous['socket'].close()
            await self.detach(key, previous['socket'])
        self.browsers[key] = {'socket': socket, 'workflows': []}
        await self.send_tracking(socket)

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
            prompt_id = payload.get('promptId')
            if (pending[0] in self.clients and payload.get('commandId') in self.queue_commands
                    and isinstance(prompt_id, str) and 0 < len(prompt_id) <= 256):
                # Preserve status events between queue response and explicit watch.
                self.subscriptions.setdefault(pending[0], set()).add(prompt_id)
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
        if command_type == 'queuePrompt' and len(self.subscriptions.get(owner, set())) + sum(
                key in self.queue_commands and value[0] is owner for key, value in self.pending.items()) >= 64:
            raise ValueError('Too many tracked ComfyUI tasks')
        # Reserve interest before dispatch: concurrent queue results must not
        # bypass the global subscription bound before their explicit watch.
        if command_type == 'queuePrompt' and len(self.tracked_ids()) + len(self.queue_commands) >= 256:
            raise ValueError('Too many tracked ComfyUI prompts')
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
        if command_type == 'queuePrompt': self.queue_commands.add(command_id)
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
            self.queue_commands.discard(command_id)

    async def cancel(self, owner, command_id):
        pending = self.pending.get(command_id)
        if pending and pending[0] is owner and not pending[2].done():
            pending[2].cancel()

    async def send_tracking(self, browser):
        ids = sorted(self.tracked_ids())
        await browser.send_json({'event': 'tracking', 'promptIds': ids})

    async def track(self, owner, prompt_ids):
        if not isinstance(prompt_ids, list) or len(prompt_ids) > 64 or any(
                not isinstance(value, str) or not value or len(value) > 256 for value in prompt_ids):
            raise ValueError('Invalid prompt subscriptions')
        subscribed = set(prompt_ids)
        others = set().union(*(value for client, value in self.subscriptions.items()
                              if client is not owner)) if self.subscriptions else set()
        ids = others | subscribed
        if len(ids) > 256 or (len(ids) + len(self.queue_commands) > 256 and not ids <= self.tracked_ids()):
            raise ValueError('Too many tracked ComfyUI prompts')
        if subscribed:
            self.subscriptions[owner] = subscribed
        else:
            self.subscriptions.pop(owner, None)
        for browser in list(self.browsers.values()):
            try:
                await self.send_tracking(browser['socket'])
            except (ConnectionError, RuntimeError):
                pass

    async def track_one(self, owner, prompt_id, watching):
        if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 256:
            raise ValueError('Invalid prompt subscription')
        ids = set(self.subscriptions.get(owner, set()))
        if watching: ids.add(prompt_id)
        else: ids.discard(prompt_id)
        await self.track(owner, list(ids))

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
        prompt_id = data['prompt_id']
        # Queue results can race native execution. Forward to that queue's owner
        # until its response is delivered; all other clients opt in by prompt ID.
        queue_owners = {owner for command_id, (owner, target, future) in self.pending.items()
                        if command_id in self.queue_commands and target is socket and not future.done()}
        for client in list(self.clients):
            if prompt_id not in self.subscriptions.get(client, set()) and client not in queue_owners:
                continue
            try:
                await client.send_json(event)
            except (ConnectionError, RuntimeError):
                self.clients.discard(client)
