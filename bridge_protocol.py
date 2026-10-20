"""Reference-only Anim RPC; installed samples travel privately to the browser."""
import asyncio
import base64
import json
import logging
import re


def trace(event, **fields):
    # Never pass workflow graphs, prompts, reference paths or credentials here.
    def safe(value):
        if not isinstance(value, str):
            return value
        value = re.sub(r'(?i)(bearer\s+)\S+', r'\1<redacted>', value)
        value = re.sub(r'(?i)((?:api[_-]?key|token|authorization)\s*[=:]\s*)[^\s,;]+', r'\1<redacted>', value)
        return value[:500]
    logging.getLogger('anim_bridge').info('[Anim Bridge] %s', json.dumps(
        {'stage': event, **{key: safe(value) for key, value in fields.items()}}, ensure_ascii=False))


class PushBridge:
    def __init__(self, history):
        self.history = history
        self.browsers = {}
        self.clients = set()
        self.pending = {}
        self.jobs = {}
        self.early = {}
        self.finished = {}
        self.finalizers = set()
        self.seen = {}

    @staticmethod
    def identity(key, path):
        return base64.urlsafe_b64encode(json.dumps([*key, path], separators=(',', ':')).encode()).decode().rstrip('=')

    def metadata(self):
        return [{'id': self.identity(key, path), 'name': name}
                for key, browser in self.browsers.items() for path, name in browser['workflows'].items()]

    async def lists(self):
        for client in list(self.clients):
            await self.send(client, {'event': 'workflows', 'workflows': self.metadata()})

    async def send(self, socket, payload):
        try:
            await socket.send_json(payload)
        except (ConnectionError, RuntimeError):
            self.clients.discard(socket)

    async def attach(self, key, socket):
        trace('browser.connected', sessionId=key[0], tabId=key[1])
        old = self.browsers.get(key)
        if old:
            await old['socket'].close()
            await self.detach(key, old['socket'])
        self.browsers[key] = {'socket': socket, 'workflows': {}}

    async def publish(self, key, socket, workflows):
        browser = self.browsers.get(key)
        if not browser or browser['socket'] is not socket:
            return
        browser['workflows'] = {str(w['workflowId']): str(w['name']) for w in workflows[:256]
                                if isinstance(w, dict) and w.get('workflowId') and w.get('name')}
        trace('workflows.received', tabId=key[1], count=len(browser['workflows']))
        await self.lists()

    async def detach(self, key, socket):
        if self.browsers.get(key, {}).get('socket') is not socket:
            return
        del self.browsers[key]
        trace('browser.disconnected', tabId=key[1])
        for owner, target, future in list(self.pending.values()):
            if target is socket and not future.done():
                future.set_exception(ConnectionError('The selected ComfyUI tab disconnected. Select its new identity after reconnecting.'))
        await self.lists()

    async def disconnect(self, owner):
        self.clients.discard(owner)
        for request_id, (client, target, future) in list(self.pending.items()):
            if client is owner and not future.done():
                future.cancel()
        for job in self.jobs.values():
            job['owners'].discard(owner)

    async def command(self, owner, payload, samples, timeout=30):
        request_id = payload.get('requestId')
        trace('command.received', requestId=str(request_id), command=str(payload.get('type')),
              workflowId=str(payload.get('id')), workflowName=str(payload.get('name')),
              connectedBrowsers=len(self.browsers), openWorkflows=len(self.metadata()))
        if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
            raise ValueError('A request ID is required')
        if request_id in self.seen:
            raise ValueError('Duplicate request ID; execution was not repeated')
        if len(self.pending) >= 64 or len(self.jobs) >= 256:
            raise ValueError('Too many active requests')
        kind = payload.get('type')
        if kind not in ('run', 'apply'):
            raise ValueError('Unsupported command')
        if not isinstance(payload.get('parameters'), dict):
            raise ValueError('Semantic input parameters are required')
        target_id, name = payload.get('id'), payload.get('name')
        selected = next(((key, browser, path) for key, browser in self.browsers.items()
                         for path, title in browser['workflows'].items()
                         if self.identity(key, path) == target_id and title == name), None)
        sample = None
        if isinstance(target_id, str) and target_id.startswith('anim-sample/'):
            sample = samples(target_id)
            if sample is not None and name == target_id.removeprefix('anim-sample/') and self.browsers:
                key, browser = next(reversed(self.browsers.items()))
                selected = (key, browser, target_id)
        if selected is None:
            raise ValueError('Workflow name or ID does not match an open ComfyUI tab. Refresh the list and select it again.')
        key, browser, path = selected
        target = browser['socket']
        future = asyncio.get_running_loop().create_future()
        self.seen[request_id] = True
        if len(self.seen) > 1024:
            self.seen.pop(next(iter(self.seen)))
        self.pending[request_id] = (owner, target, future)
        try:
            trace('command.forward', requestId=request_id, tabId=key[1], command=kind)
            await target.send_json({'event': 'command', 'requestId': request_id, 'commandId': request_id,
                                    'type': kind, 'workflowId': path, 'name': name,
                                    'parameters': payload['parameters'], **({'sample': sample} if sample else {})})
            return await asyncio.wait_for(future, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            trace('command.cancelled_or_timed_out', requestId=request_id)
            await self.send(target, {'event': 'cancel', 'requestId': request_id})
            raise
        finally:
            self.pending.pop(request_id, None)

    def result(self, socket, payload):
        request_id = payload.get('requestId')
        item = self.pending.get(request_id)
        if not item or item[1] is not socket or item[2].done():
            return
        owner, _, future = item
        trace('command.receipt', requestId=request_id, applied=payload.get('applied') is True,
              promptId=str(payload.get('promptId') or ''), error=str(payload.get('error') or ''))
        # Only tiny receipts cross the boundary. Never relay a supplied snapshot.
        result = {'requestId': request_id, 'applied': payload.get('applied') is True}
        if payload.get('error'):
            result['error'] = str(payload['error'])[:4096]
        prompt_id = payload.get('promptId')
        if isinstance(prompt_id, str) and prompt_id and not result.get('error'):
            self.jobs[prompt_id] = {'requestId': request_id, 'owners': {owner}}
            result['promptId'] = prompt_id
            early = self.early.pop(prompt_id, None)
            if early:
                self.finish(prompt_id, early)
        future.set_result(result)

    async def watch(self, owner, prompt_id):
        if prompt_id in self.finished:
            await self.send(owner, self.finished[prompt_id])
        elif prompt_id in self.jobs:
            self.jobs[prompt_id]['owners'].add(owner)
        else:
            await self.send(owner, {'event': 'completed', 'promptId': prompt_id, 'files': [],
                'error': 'Bridge no longer tracks this task. Inspect it in ComfyUI before submitting a new request.'})

    def execution(self, prompt_id, error=''):
        # Called only by the native server observer on its own event loop.
        if prompt_id in self.jobs or self.pending:
            trace('execution.terminal', promptId=prompt_id, error=error, waitingForReceipt=prompt_id not in self.jobs)
        event = {'error': error}
        if prompt_id in self.jobs:
            self.finish(prompt_id, event)
        elif self.pending:
            previous = self.early.get(prompt_id)
            # A terminal executing frame follows execution_error. Preserve the
            # actual error when both arrive before the browser's queue receipt.
            if previous is None or error or not previous['error']:
                self.early[prompt_id] = event
                if len(self.early) > 64:
                    self.early.pop(next(iter(self.early)))

    def finish(self, prompt_id, event):
        job = self.jobs.get(prompt_id)
        if not job or job.get('finishing'):
            return
        job['finishing'] = True
        task = asyncio.create_task(self._finish(prompt_id, job, event))
        self.finalizers.add(task)
        task.add_done_callback(self.finalizers.discard)

    async def _finish(self, prompt_id, job, event):
        result = {'event': 'completed', 'requestId': job['requestId'], 'promptId': prompt_id,
                  'error': event['error'], 'files': []}
        try:
            if not result['error']:
                # ComfyUI commits history before its terminal executing frame.
                history = self.history(prompt_id)
                entry = history.get(prompt_id, {})
                for output in entry.get('outputs', {}).values():
                    for kind in ('images', 'videos', 'gifs', 'audio'):
                        for file in output.get(kind, []):
                            if isinstance(file, dict) and file.get('filename'):
                                if len(result['files']) >= 256:
                                    raise ValueError('Too many output files; inspect this task in ComfyUI')
                                result['files'].append({key: file[key][:4096] for key in ('filename', 'subfolder', 'type', 'format') if isinstance(file.get(key), str)})
                if entry.get('status', {}).get('status_str') == 'error':
                    result['error'] = 'ComfyUI execution failed'
                elif not result['files']:
                    result['error'] = 'ComfyUI finished without a saved output file'
        except Exception as error:
            result['error'] = str(error)
        self.finished[prompt_id] = result
        if len(self.finished) > 64:
            self.finished.pop(next(iter(self.finished)))
        self.jobs.pop(prompt_id, None)
        trace('completion.send', requestId=job['requestId'], promptId=prompt_id,
              fileCount=len(result['files']), error=result['error'], listeners=len(job['owners']))
        for owner in list(job['owners']):
            await self.send(owner, result)
