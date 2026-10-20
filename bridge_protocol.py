"""Anim Bridge state kept on the ComfyUI server.

ComfyUI tabs publish their open workflows over HTTP when something changes:
metadata for every open tab plus the executable (API) graph of the active
one. Anim's commands are answered here from that state, and runs are queued
straight into ComfyUI. Nothing is sent to a browser tab and nothing polls.
"""
import copy
import logging
import time

log = logging.getLogger('anim_bridge')

MAX_TABS = 32
MAX_WORKFLOWS_PER_TAB = 64
MAX_TRACKED_PER_CLIENT = 64
MAX_TRACKED_TOTAL = 256
EXECUTION_EVENTS = ('execution_start', 'executing', 'execution_success',
                    'execution_error', 'execution_interrupted', 'progress')
FORWARDED_FIELDS = ('prompt_id', 'node', 'value', 'max', 'exception_message')

TAB_CLOSED = 'The selected ComfyUI tab is not open. Refresh the list in Anim and select the workflow again.'
NOT_READ_YET = ('ComfyUI has not shared this workflow yet. Click its tab in ComfyUI once, '
                'then check inputs again in Anim.')
SAMPLE_NOT_OPEN = ('Installed samples cannot be opened from Anim. Open the sample in ComfyUI, '
                   'then select it from the open workflows.')


class BridgeError(Exception):
    """A command failure whose message is shown to the Anim user."""


def _is_link(value):
    return (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
            and isinstance(value[1], int))


def _coerce(previous, value):
    """Matches ComfyUI's own widgets: numeric widgets store numbers, lists are wrapped."""
    if isinstance(value, list):
        return {'__value__': list(value)}
    if isinstance(previous, bool) or not isinstance(previous, (int, float)) or not isinstance(value, str):
        return value
    try:
        number = float(value)
    except ValueError:
        raise BridgeError(f'"{value}" is not a number.') from None
    return int(number) if isinstance(previous, int) and number.is_integer() else number


def apply_inputs(api_graph, inputs):
    """Returns a copy of the graph with Anim's values, and the values actually set."""
    graph = copy.deepcopy(api_graph)
    applied = {}
    for input_id, value in inputs.items():
        node_id, _, name = input_id.partition('.')
        node = graph.get(node_id)
        if not isinstance(node, dict) or name not in (node.get('inputs') or {}):
            raise BridgeError(f'ComfyUI input {input_id} is not in this workflow. Check inputs again in Anim.')
        previous = node['inputs'][name]
        if _is_link(previous):
            raise BridgeError(f'ComfyUI input {input_id} is connected to another node, so Anim cannot set it.')
        node['inputs'][name] = _coerce(previous, value)
        stored = node['inputs'][name]
        applied[input_id] = stored['__value__'] if isinstance(stored, dict) and '__value__' in stored else stored
    return graph, applied


class BridgeHub:
    def __init__(self, is_connected=lambda client_id: True, clock=time.monotonic):
        self.tabs = {}
        self.clients = set()
        self.subscriptions = {}
        self.queueing = {}
        self.is_connected = is_connected
        self.clock = clock

    # ---- ComfyUI tabs ----

    def publish(self, payload):
        key = (str(payload.get('sessionId') or ''), str(payload.get('tabId') or ''))
        if not all(key):
            raise BridgeError('A ComfyUI tab must identify itself.')
        if key not in self.tabs and len(self.tabs) >= MAX_TABS:
            self.prune()
            if len(self.tabs) >= MAX_TABS:
                raise BridgeError('Too many ComfyUI tabs are open.')
        rows = []
        for item in (payload.get('workflows') or [])[:MAX_WORKFLOWS_PER_TAB]:
            if isinstance(item, dict) and item.get('workflowId'):
                rows.append({'workflowId': str(item['workflowId']), 'title': str(item.get('title', '')),
                             'isActive': item.get('isActive') is True, 'isDirty': item.get('isDirty') is True})
        open_ids = {row['workflowId'] for row in rows}
        previous = self.tabs.get(key, {})
        graphs = {workflow_id: graph for workflow_id, graph in previous.get('graphs', {}).items()
                  if workflow_id in open_ids}
        snapshot = payload.get('snapshot')
        if isinstance(snapshot, dict) and snapshot.get('workflowId') in open_ids \
                and isinstance(snapshot.get('apiGraph'), dict):
            graphs[snapshot['workflowId']] = {
                'apiGraph': snapshot['apiGraph'],
                'inputs': [item for item in snapshot.get('inputs') or [] if isinstance(item, dict)],
                'outputNodeIds': [str(item) for item in snapshot.get('outputNodeIds') or []],
                'revision': str(snapshot.get('revision', '')),
            }
        if key not in self.tabs:
            log.info('[Anim Bridge] ComfyUI tab opened session=%s tab=%s', key[0], key[1])
        self.tabs[key] = {'clientId': str(payload.get('clientId') or ''), 'workflows': rows,
                          'graphs': graphs}

    def unpublish(self, payload):
        key = (str(payload.get('sessionId') or ''), str(payload.get('tabId') or ''))
        if self.tabs.pop(key, None) is not None:
            log.info('[Anim Bridge] ComfyUI tab closed session=%s tab=%s', key[0], key[1])

    def prune(self):
        """Forget tabs whose ComfyUI page is no longer connected to this server."""
        for key, tab in list(self.tabs.items()):
            if tab['clientId'] and not self.is_connected(tab['clientId']):
                del self.tabs[key]
                log.info('[Anim Bridge] ComfyUI tab gone session=%s tab=%s', key[0], key[1])

    def metadata(self):
        self.prune()
        return [{'sessionId': key[0], 'tabId': key[1], 'workflowId': row['workflowId'],
                 'title': row['title'], 'isActive': row['isActive'], 'isDirty': row['isDirty'],
                 'isAvailable': True, 'protocolVersion': '2.0'}
                for key, tab in self.tabs.items() for row in tab['workflows']]

    def _target(self, payload):
        self.prune()
        key = (str(payload.get('sessionId', '')), str(payload.get('tabId', '')))
        tab = self.tabs.get(key)
        workflow_id = payload.get('workflowId')
        row = next((item for item in (tab or {}).get('workflows', []) if item['workflowId'] == workflow_id), None)
        if row is None:
            raise BridgeError(TAB_CLOSED)
        graph = tab['graphs'].get(workflow_id)
        if graph is None:
            raise BridgeError(NOT_READ_YET)
        return key, tab, row, graph

    # ---- Anim commands ----

    async def command(self, owner, payload, submit):
        command_type = payload.get('type')
        if command_type not in ('refresh', 'loadSample', 'applyInputs', 'queuePrompt'):
            raise BridgeError('Unsupported command')
        if command_type == 'loadSample':
            raise BridgeError(SAMPLE_NOT_OPEN)
        inputs = payload.get('inputs')
        if command_type in ('applyInputs', 'queuePrompt'):
            if not isinstance(inputs, dict) or (command_type == 'applyInputs' and not inputs):
                raise BridgeError('inputs must be a non-empty object')
            for name, value in inputs.items():
                if not isinstance(name, str) or '.' not in name:
                    raise BridgeError('Invalid input mapping')
                if not isinstance(value, (str, list)) or (isinstance(value, list) and
                        not all(isinstance(item, str) for item in value)):
                    raise BridgeError('Unsupported value for input mapping')
        if command_type == 'queuePrompt' and len(self.subscriptions.get(owner, set())) >= MAX_TRACKED_PER_CLIENT:
            raise BridgeError('Too many tracked ComfyUI tasks')
        key, tab, row, graph = self._target(payload)
        if command_type == 'refresh':
            return {'snapshot': {
                'sessionId': key[0], 'tabId': key[1], 'workflowId': row['workflowId'],
                'title': row['title'], 'protocolVersion': '2.0', 'revision': graph['revision'],
                'isDirty': row['isDirty'], 'isAvailable': True, 'isActive': row['isActive'],
                'apiGraph': copy.deepcopy(graph['apiGraph']), 'inputs': copy.deepcopy(graph['inputs']),
                'outputNodeIds': list(graph['outputNodeIds']),
            }}
        prompt, applied = apply_inputs(graph['apiGraph'], inputs or {})
        if command_type == 'applyInputs':
            return {'values': applied}
        started = self.clock()
        # Execution can start before ComfyUI's queue response arrives. Until
        # the prompt ID is known, this owner receives every execution status.
        self.queueing[owner] = self.queueing.get(owner, 0) + 1
        try:
            prompt_id = await submit(prompt, tab['clientId'])
            self.subscriptions.setdefault(owner, set()).add(prompt_id)
        finally:
            self.queueing[owner] -= 1
            if not self.queueing[owner]:
                del self.queueing[owner]
        log.info('[Anim Bridge] queued prompt %s for %s in %.3fs', prompt_id, row['workflowId'],
                 self.clock() - started)
        return {'promptId': prompt_id}

    # ---- Execution status ----

    async def track(self, owner, prompt_ids):
        if not isinstance(prompt_ids, list) or len(prompt_ids) > MAX_TRACKED_PER_CLIENT or any(
                not isinstance(value, str) or not value or len(value) > 256 for value in prompt_ids):
            raise BridgeError('Invalid prompt subscriptions')
        others = set().union(*(value for client, value in self.subscriptions.items()
                              if client is not owner)) if self.subscriptions else set()
        if len(others | set(prompt_ids)) > MAX_TRACKED_TOTAL:
            raise BridgeError('Too many tracked ComfyUI prompts')
        if prompt_ids:
            self.subscriptions[owner] = set(prompt_ids)
        else:
            self.subscriptions.pop(owner, None)

    async def track_one(self, owner, prompt_id, watching):
        if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 256:
            raise BridgeError('Invalid prompt subscription')
        ids = set(self.subscriptions.get(owner, set()))
        if watching:
            ids.add(prompt_id)
        else:
            ids.discard(prompt_id)
        await self.track(owner, list(ids))

    async def execution(self, event, data):
        """Forwards one ComfyUI execution status to the Anim clients that asked for it."""
        if event not in EXECUTION_EVENTS or not isinstance(data, dict) or not data.get('prompt_id'):
            return
        prompt_id = data['prompt_id']
        message = {'event': 'execution', 'type': event,
                   'data': {name: data[name] for name in FORWARDED_FIELDS if name in data}}
        for client in list(self.clients):
            if prompt_id not in self.subscriptions.get(client, set()) and client not in self.queueing:
                continue
            try:
                await client.send_json(message)
            except (ConnectionError, RuntimeError):
                self.forget(client)

    def forget(self, owner):
        self.clients.discard(owner)
        self.subscriptions.pop(owner, None)
        self.queueing.pop(owner, None)
