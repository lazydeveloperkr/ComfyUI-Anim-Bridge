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
  assert.deepEqual(workflowMetadata(f.store), [{ workflowId: 'selected', title: 'Selected', isActive: true, isAvailable: true }])
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
    openWorkflow: async () => {},
  } }, graph: { getNodeById: () => node },
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
  f.api.socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ type: 'executing', data: { prompt_id: 'p', node: null } }) }))
  assert.equal(f.sockets[0].messages.at(-1).data.prompt_id, 'p')
  f.bridge.stop()
  assert.equal(f.listeners.size, 0)
})
