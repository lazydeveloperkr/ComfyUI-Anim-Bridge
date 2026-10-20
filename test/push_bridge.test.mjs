import assert from 'node:assert/strict'
import { test } from 'node:test'
import { executeCommand, startPushBridge, workflowMetadata } from '../web/push_bridge.js'

function fixture() {
  const listeners = new Map()
  const workflow = { path: 'selected', filename: 'Selected' }
  Object.defineProperty(workflow, 'activeState', { get() { throw new Error('metadata inspected graph') } })
  const actions = []
  const store = { openWorkflows: [workflow], activeWorkflow: workflow,
    $onAction(callback) { actions.push(callback); return () => actions.splice(actions.indexOf(callback), 1) } }
  const app = { extensionManager: { workflow: store }, graphToPrompt() { throw new Error('idle graph conversion') } }
  const api = {
    apiURL: (path) => path,
    socket: new EventTarget(),
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: (name) => listeners.delete(name),
    fetchApi() { throw new Error('automatic HTTP') },
  }
  const sockets = []
  class Socket {
    constructor() { this.readyState = 0; this.messages = []; sockets.push(this) }
    open() { this.readyState = 1; this.onopen() }
    send(message) { this.messages.push(JSON.parse(message)) }
    close() { this.readyState = 3; this.onclose?.() }
  }
  const windowEvents = new Map()
  const timers = []
  const windowImpl = { location: { href: 'http://localhost:8188' },
    addEventListener: (type, callback) => windowEvents.set(type, callback),
    removeEventListener: (type) => windowEvents.delete(type) }
  const bridge = startPushBridge({ app, api, sessionId: 's', tabId: 't',
    WebSocketImpl: Socket, windowImpl,
    schedule: (callback, milliseconds) => { const timer = { callback, milliseconds }; timers.push(timer); return timer },
    unschedule: (timer) => { const index = timers.indexOf(timer); if (index >= 0) timers.splice(index, 1) } })
  return { bridge, app, api, sockets, timers, store, actions, listeners, windowEvents }
}

test('30 minutes virtual idle has zero scheduled work, HTTP, graph conversions or hashes', () => {
  const f = fixture()
  f.sockets[0].open()
  assert.equal(f.sockets[0].messages.length, 1)
  // No timer exists to advance, even at the 30-minute boundary.
  assert.equal(f.timers.length, 0)
  assert.equal(f.listeners.size, 3)
  assert.deepEqual(workflowMetadata(f.store), [{ workflowId: 'selected', title: 'Selected', isActive: true, isAvailable: true, isDirty: false }])
  f.bridge.stop()
  assert.equal(f.listeners.size, 0)
  assert.equal(f.actions.length, 0)
  assert.equal(f.windowEvents.size, 0)
})

test('workflow open/close/rename/switch publishes metadata after actual actions only', () => {
  const f = fixture(); f.sockets[0].open()
  const after = []
  f.actions[0]({ name: 'renameWorkflow', after: (callback) => after.push(callback) })
  f.store.openWorkflows[0].filename = 'Renamed'
  after[0]()
  assert.equal(f.sockets[0].messages.at(-1).workflows[0].title, 'Renamed')
  const count = f.sockets[0].messages.length
  f.bridge.publish()
  assert.equal(f.sockets[0].messages.length, count)
  f.store.openWorkflows = []
  f.bridge.publish()
  assert.deepEqual(f.sockets[0].messages.at(-1).workflows, [])
  f.bridge.stop()
})

test('disconnect alone schedules recovery; reconnect synchronizes once without command replay', () => {
  const f = fixture(); f.sockets[0].open(); f.sockets[0].close()
  assert.equal(f.timers.length, 1)
  f.timers.shift().callback(); f.sockets[1].open()
  assert.equal(f.sockets[1].messages.length, 1)
  assert.equal(f.listeners.size, 3)
  assert.equal(f.actions.length, 1)
  f.bridge.stop()
  assert.equal(f.timers.length, 0)
})

test('only explicit inspection converts the selected workflow; apply returns minimal readback', async () => {
  let conversions = 0
  const node = { widgets: [{ name: 'prompt', value: 'before' }] }
  const workflow = { path: 'w', filename: 'W', activeState: {} }
  const app = { extensionManager: { workflow: {
    openWorkflows: [workflow, { path: 'other', get activeState() { throw new Error('unrelated graph') } }],
  } }, graph: { getNodeById: () => node },
    async loadGraphData(state, clean, restore, target) { this.extensionManager.workflow.activeWorkflow = target },
    graphToPrompt: async () => { conversions++; return { output: { 1: { class_type: 'AnimPromptInput', inputs: { prompt: node.widgets[0].value } } } } } }
  const result = await executeCommand(app, {}, { type: 'applyInputs', workflowId: 'w', inputs: { '1.prompt': 'after' } })
  assert.deepEqual(result, { values: { '1.prompt': 'after' } })
  assert.equal(conversions, 0)
  const snapshot = await executeCommand(app, {}, { type: 'refresh', workflowId: 'w', commandId: 'id' })
  assert.equal(conversions, 1)
  assert.equal(snapshot.snapshot.apiGraph[1].inputs.prompt, 'after')
})

test('duplicate command executes once; missing workflow returns an error event', async () => {
  const f = fixture(); f.sockets[0].open()
  const command = { event: 'command', type: 'refresh', commandId: 'c', workflowId: 'missing' }
  f.sockets[0].onmessage({ data: JSON.stringify(command) })
  f.sockets[0].onmessage({ data: JSON.stringify(command) })
  await new Promise((resolve) => setImmediate(resolve))
  const results = f.sockets[0].messages.filter((value) => value.event === 'result')
  assert.equal(results.length, 1)
  assert.match(results[0].error, /not available/)
  f.bridge.stop()
})

test('execution forwards the original prompt identity and listeners are released', () => {
  const f = fixture(); f.sockets[0].open()
  f.sockets[0].onmessage({ data: JSON.stringify({ event: 'tracking', promptIds: ['p'] }) })
  f.api.socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'executing', data: { prompt_id: 'p', node: null } }) }))
  assert.equal(f.sockets[0].messages.at(-1).data.prompt_id, 'p')
  f.bridge.stop()
  assert.equal(f.listeners.size, 0)
})

const tick = () => new Promise(resolve => setImmediate(resolve))
const deliver = (socket, command) => socket.onmessage({ data: JSON.stringify(command) })
const refresh = (id, workflowId = 'selected') => ({ event: 'command', type: 'refresh', commandId: id, workflowId })
function inspectionFixture() {
  const f = fixture()
  const workflow = { path: 'selected', filename: 'Selected', activeState: {} }
  f.store.openWorkflows = [workflow]; f.store.activeWorkflow = workflow
  f.app.graph = {}
  f.app.loadGraphData = async (state, clean, restore, workflow) => { f.store.activeWorkflow = workflow }
  f.sockets[0].open()
  return f
}

test('stalled graph read does not block commands after reconnect and late completion is discarded', async () => {
  const f = inspectionFixture()
  let release, reads = 0
  const blocked = new Promise(resolve => { release = resolve })
  f.app.graphToPrompt = () => ++reads === 1 ? blocked : Promise.resolve({ output: { 1: { class_type: 'AnimPromptInput', inputs: { prompt: 'ok' } } } })
  deliver(f.sockets[0], refresh('A')); await tick()
  f.sockets[0].close()
  assert.equal(f.timers.length, 1)
  f.timers.shift().callback(); f.sockets[1].open()
  deliver(f.sockets[1], refresh('B')); await tick()
  assert.equal(reads, 2)
  const results = f.sockets[1].messages.filter(value => value.event === 'result')
  assert.equal(results.length, 1); assert.equal(results[0].commandId, 'B')
  assert.ok(results[0].snapshot)
  release({ output: {} }); await tick()
  assert.equal(f.sockets[1].messages.filter(value => value.event === 'result').length, 1)
  f.bridge.stop(); assert.equal(f.timers.length, 0)
})

test('cancel and timeout release the same connection queue while a read remains unresolved', async () => {
  const f = inspectionFixture()
  let reads = 0
  f.app.graphToPrompt = () => ++reads === 1 ? new Promise(() => {}) : Promise.resolve({ output: {} })
  deliver(f.sockets[0], refresh('A')); await tick()
  deliver(f.sockets[0], { event: 'cancel', commandId: 'A' })
  deliver(f.sockets[0], refresh('B')); await tick()
  assert.ok(f.sockets[0].messages.some(value => value.commandId === 'B' && value.snapshot))
  f.app.graphToPrompt = () => new Promise(() => {})
  deliver(f.sockets[0], refresh('C')); await tick()
  f.timers.find(value => value.milliseconds === 30000).callback()
  deliver(f.sockets[0], refresh('D')); await tick()
  // Two unresolved native reads are the cap; failure is immediate, not a hang.
  assert.match(f.sockets[0].messages.find(value => value.event === 'result' && value.commandId === 'D').error, /Reload/)
  f.bridge.stop()
})

test('non-cancellable editor mutation fails new commands promptly until actual completion', async () => {
  const f = inspectionFixture()
  let finish, opens = 0, reads = 0
  f.store.activeWorkflow = null
  f.app.loadGraphData = (state, clean, restore, workflow) => { opens++; return new Promise(resolve => {
    finish = () => { f.store.activeWorkflow = workflow; resolve() }
  }) }
  f.app.graphToPrompt = async () => { reads++; return { output: {} } }
  deliver(f.sockets[0], refresh('A')); await tick()
  deliver(f.sockets[0], { event: 'cancel', commandId: 'A' })
  deliver(f.sockets[0], refresh('B')); await tick()
  assert.equal(opens, 1); assert.equal(reads, 0)
  assert.match(f.sockets[0].messages.find(value => value.event === 'result' && value.commandId === 'B').error, /Reload/)
  finish(); await tick()
  assert.equal(reads, 0) // The cancelled A cannot continue into inspection.
  deliver(f.sockets[0], refresh('C')); await tick()
  assert.equal(reads, 1)
  assert.ok(f.sockets[0].messages.some(value => value.commandId === 'C' && value.snapshot))
  f.bridge.stop()
})

test('BFCache resume installs one connection and hook set; repeated pageshow and final stop are safe', () => {
  const f = fixture(); f.sockets[0].open()
  const show = f.windowEvents.get('pageshow')
  for (let i = 0; i < 3; i++) {
    f.windowEvents.get('pagehide')({ persisted: true })
    assert.equal(f.listeners.size, 0); assert.equal(f.actions.length, 0)
    show({ persisted: true }); f.sockets.at(-1).open()
    const count = f.sockets.length
    show({ persisted: true })
    assert.equal(f.sockets.length, count)
    assert.equal(f.listeners.size, 3); assert.equal(f.actions.length, 1)
    assert.equal(f.sockets.at(-1).messages.length, 1)
    assert.equal(f.timers.length, 0)
  }
  f.bridge.stop(); show({ persisted: true })
  assert.equal(f.windowEvents.size, 0); assert.equal(f.sockets.length, 4)
})

test('untracked and irrelevant messages are not parsed; forwarded status contains only minimal fields', () => {
  const f = fixture(); f.sockets[0].open()
  const raw = JSON.stringify({ type: 'executing', data: { prompt_id: 'p', node: null } })
  const irrelevant = JSON.stringify({ type: 'executed', data: { output: 'x'.repeat(1000000) } })
  const parse = JSON.parse
  let parses = 0
  JSON.parse = value => { if (value === raw || value === irrelevant) parses++; return parse(value) }
  try {
    f.api.socket.dispatchEvent(new MessageEvent('message', { data: raw }))
    assert.equal(parses, 0)
    deliver(f.sockets[0], { event: 'tracking', promptIds: ['p'] })
    f.api.socket.dispatchEvent(new MessageEvent('message', { data: irrelevant }))
    assert.equal(parses, 0)
    f.listeners.get('execution_error')({ detail: { prompt_id: 'p', exception_message: 'failed', current_inputs: 'x'.repeat(1000000) } })
    assert.deepEqual(f.sockets[0].messages.at(-1).data, { prompt_id: 'p', exception_message: 'failed' })
    deliver(f.sockets[0], { event: 'tracking', promptIds: [] })
    f.api.socket.dispatchEvent(new MessageEvent('message', { data: raw }))
    assert.equal(parses, 0)
    assert.equal(f.listeners.size, 3)
  } finally { JSON.parse = parse; f.bridge.stop() }
})

test('inspection revision ignores runtime prompt changes but detects graph changes and dirty state', async () => {
  const f = inspectionFixture()
  let prompt = 'first', sampler = 'one'
  f.app.graphToPrompt = async () => ({ output: {
    1: { class_type: 'AnimPromptInput', inputs: { prompt } },
    2: { class_type: 'KSampler', inputs: { sampler_name: sampler } },
  } })
  const first = (await executeCommand(f.app, f.api, refresh('a'))).snapshot
  prompt = 'second'
  const second = (await executeCommand(f.app, f.api, refresh('b'))).snapshot
  assert.equal(first.revision, second.revision)
  sampler = 'two'; f.store.activeWorkflow.isModified = true
  const third = (await executeCommand(f.app, f.api, refresh('c'))).snapshot
  assert.notEqual(second.revision, third.revision)
  assert.equal(third.isDirty, true)
  f.bridge.stop()
})

test('native dirty changes publish metadata without converting a graph', async () => {
  const f = fixture()
  f.app.graph = {}
  // Re-activate to observe the newly installed graph.
  f.windowEvents.get('pagehide')({ persisted: true })
  f.windowEvents.get('pageshow')({ persisted: true }); f.sockets.at(-1).open()
  f.store.activeWorkflow.isModified = true
  f.app.graph.onAfterChange()
  await tick()
  assert.equal(f.sockets.at(-1).messages.at(-1).workflows[0].isDirty, true)
  f.bridge.stop(); assert.equal(f.app.graph.onAfterChange, undefined)
})

test('every new command is acknowledged on receipt before it runs; duplicates are not', async () => {
  const f = inspectionFixture()
  f.app.graphToPrompt = () => new Promise(() => {})
  deliver(f.sockets[0], refresh('A'))
  // The receipt is sent synchronously, before the (stalled) inspection starts.
  assert.deepEqual(f.sockets[0].messages.at(-1), { event: 'ack', commandId: 'A' })
  deliver(f.sockets[0], refresh('A'))
  assert.equal(f.sockets[0].messages.filter(value => value.event === 'ack').length, 1)
  f.bridge.stop()
})

test('the browser connects with its script version so the server can refuse stale tabs', () => {
  const urls = []
  const f = fixture()
  class Probe { constructor(url) { urls.push(String(url)) } close() {} }
  const bridge = startPushBridge({ app: f.app, api: f.api, sessionId: 's', tabId: 't', WebSocketImpl: Probe,
    windowImpl: { location: { href: 'http://localhost:8188' }, addEventListener() {}, removeEventListener() {} },
    schedule: () => ({}), unschedule: () => {} })
  assert.match(urls[0], /client=13/)
  bridge.stop(); f.bridge.stop()
})

test('a command on a connection the page no longer uses closes it instead of being dropped silently', () => {
  const f = fixture(); f.sockets[0].open()
  f.windowEvents.get('pagehide')({ persisted: false })
  // The page is suspended; the server must stop routing to this connection.
  const old = f.sockets[0]
  old.readyState = 1
  old.onmessage({ data: JSON.stringify(refresh('X')) })
  assert.equal(old.readyState, 3)
  assert.equal(old.messages.filter(value => value.event === 'ack').length, 0)
  f.bridge.stop()
})

test('a page that saw pagehide without unloading reconnects on focus; stop stays final', () => {
  const f = fixture(); f.sockets[0].open()
  f.windowEvents.get('pagehide')({ persisted: false })
  assert.equal(f.sockets.length, 1)
  f.windowEvents.get('focus')()
  assert.equal(f.sockets.length, 2)
  f.sockets[1].open()
  assert.equal(f.sockets[1].messages[0].event, 'metadata')
  f.windowEvents.get('focus')()
  assert.equal(f.sockets.length, 2, 'an open connection is never duplicated')
  const focus = f.windowEvents.get('focus')
  f.bridge.stop(); focus()
  assert.equal(f.sockets.length, 2)
})

test('a failing cleanup during close still schedules the reconnect', () => {
  const f = fixture(); f.sockets[0].open()
  f.api.removeEventListener = () => { throw new Error('cleanup failed') }
  f.sockets[0].onmessage({ data: JSON.stringify({ event: 'tracking', promptIds: ['p'] }) })
  assert.throws(() => f.sockets[0].close(), /cleanup failed/)
  assert.equal(f.timers.length, 1)
  f.timers.shift().callback()
  assert.equal(f.sockets.length, 2)
})

test('ComfyUI status events never open a duplicate connection while one is open or pending', () => {
  const f = fixture(); f.sockets[0].open()
  f.listeners.get('status')()
  assert.equal(f.sockets.length, 1)
  f.sockets[0].close()
  f.listeners.get('status')()
  assert.equal(f.sockets.length, 1, 'the scheduled retry owns the reconnect')
  f.timers.shift().callback()
  assert.equal(f.sockets.length, 2)
  f.bridge.stop()
})

test('switching to the selected tab loads its graph before inspection, never the previous graph', async () => {
  const previous = { path: 'previous', activeState: { nodes: ['previous'] } }
  const selected = { path: 'selected', activeState: { nodes: ['selected'] }, isLoaded: false,
    async load() { this.isLoaded = true } }
  const store = { openWorkflows: [previous, selected], activeWorkflow: previous,
    // The store alone marks a tab active but keeps the old canvas graph.
    async openWorkflow(workflow) { store.activeWorkflow = workflow } }
  const loads = []
  const app = { extensionManager: { workflow: store }, graph: { name: 'previous' },
    async loadGraphData(state, clean, restore, workflow) {
      loads.push(state); app.graph = { name: workflow.path }; store.activeWorkflow = workflow
    },
    async graphToPrompt() { return { output: { graph: { class_type: app.graph.name, inputs: {} } } } } }
  const result = await executeCommand(app, {}, { type: 'refresh', workflowId: 'selected', commandId: 'c' })
  assert.equal(selected.isLoaded, true)
  assert.deepEqual(loads, [{ nodes: ['selected'] }])
  assert.equal(result.snapshot.apiGraph.graph.class_type, 'selected')
})

test('a tab switch that ComfyUI refuses fails the command instead of using another graph', async () => {
  const previous = { path: 'previous', activeState: {} }
  const selected = { path: 'selected', activeState: {} }
  const store = { openWorkflows: [previous, selected], activeWorkflow: previous }
  let reads = 0
  const app = { extensionManager: { workflow: store }, graph: {},
    async loadGraphData() { return false }, async graphToPrompt() { reads++; return { output: {} } } }
  await assert.rejects(executeCommand(app, {}, { type: 'refresh', workflowId: 'selected', commandId: 'c' }),
    /could not switch/)
  assert.equal(reads, 0)
})
