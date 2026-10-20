import assert from 'node:assert/strict'
import { test } from 'node:test'
import { activeSnapshot, startBridge, workflowMetadata } from '../web/publish_bridge.js'

const tick = () => new Promise(resolve => setImmediate(resolve))

function fixture({ fetchOk = true } = {}) {
  const selected = { path: 'workflows/a.json', filename: 'A', activeState: { extra: { linearData: { outputs: [9] } } } }
  const other = { path: 'workflows/b.json', filename: 'B', get activeState() { throw new Error('inactive graph read') } }
  const actions = []
  const store = { openWorkflows: [selected, other], activeWorkflow: selected,
    $onAction(callback) { actions.push(callback); return () => actions.splice(actions.indexOf(callback), 1) } }
  let conversions = 0
  const app = { extensionManager: { workflow: store }, graph: {},
    async graphToPrompt() {
      conversions++
      return { output: { 1: { class_type: 'AnimPromptInput', inputs: { prompt: 'p' } } } }
    } }
  const posts = []
  const listeners = new Map()
  const api = { clientId: 'client-1', apiURL: path => `/api${path}`,
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: name => listeners.delete(name),
    async fetchApi(path, options) { posts.push({ path, body: JSON.parse(options.body) }); return { ok: fetchOk, status: fetchOk ? 200 : 500 } } }
  const timers = []
  const windowEvents = new Map()
  const beacons = []
  const bridge = startBridge({ app, api, sessionId: 's', tabId: 't',
    windowImpl: { addEventListener: (type, callback) => windowEvents.set(type, callback),
      removeEventListener: type => windowEvents.delete(type) },
    schedule: (callback, delay) => { const timer = { callback, delay }; timers.push(timer); return timer },
    unschedule: timer => { const index = timers.indexOf(timer); if (index >= 0) timers.splice(index, 1) },
    beacon: (url, body) => beacons.push({ url, body: JSON.parse(body) }) })
  const flush = async () => { while (timers.length) { timers.shift().callback(); await tick() } }
  return { bridge, app, api, store, posts, listeners, timers, windowEvents, beacons, actions, flush,
    conversions: () => conversions }
}

test('publishes metadata and the active workflow graph once at start', async () => {
  const f = fixture()
  await f.flush()
  assert.equal(f.posts.length, 1)
  const { path, body } = f.posts[0]
  assert.equal(path, '/anim_bridge/v1/publish')
  assert.deepEqual(body.workflows.map(row => [row.workflowId, row.isActive]), [['workflows/a.json', true], ['workflows/b.json', false]])
  assert.equal(body.clientId, 'client-1')
  assert.equal(body.snapshot.workflowId, 'workflows/a.json')
  assert.deepEqual(body.snapshot.outputNodeIds, ['9'])
  assert.equal(body.snapshot.inputs[0].inputName, 'prompt')
  assert.match(body.snapshot.revision, /^fnv1a64-/)
  f.bridge.stop()
})

test('idle tab sends nothing and schedules nothing', async () => {
  const f = fixture()
  await f.flush()
  assert.equal(f.timers.length, 0)
  assert.equal(f.posts.length, 1)
  f.bridge.stop()
  assert.equal(f.listeners.size, 0)
  assert.equal(f.windowEvents.size, 0)
  assert.equal(f.actions.length, 0)
})

test('workflow actions publish after they finish; unchanged state is not resent', async () => {
  const f = fixture()
  await f.flush()
  const after = []
  f.actions[0]({ name: 'renameWorkflow', after: callback => after.push(callback) })
  f.store.openWorkflows[0].filename = 'Renamed'
  after[0](); await f.flush()
  assert.equal(f.posts.length, 2)
  assert.equal(f.posts[1].body.workflows[0].title, 'Renamed')
  f.actions[0]({ name: 'saveWorkflow', after: callback => after.push(callback) })
  after[1](); await f.flush()
  assert.equal(f.posts.length, 2, 'identical state is not sent twice')
  f.actions[0]({ name: 'unrelatedAction', after: () => assert.fail('not observed') })
  f.bridge.stop()
})

test('editing the graph publishes once after edits settle', async () => {
  const f = fixture()
  await f.flush()
  let graphPrompt = 'p'
  f.app.graphToPrompt = async () => ({ output: { 1: { class_type: 'KSampler', inputs: { seed: graphPrompt } } } })
  for (let i = 0; i < 5; i++) { graphPrompt = `p${i}`; f.app.graph.onAfterChange() }
  assert.equal(f.timers.length, 1)
  assert.equal(f.timers[0].delay, 1000)
  await f.flush()
  assert.equal(f.posts.length, 2)
  assert.equal(f.posts[1].body.snapshot.apiGraph[1].inputs.seed, 'p4')
  f.bridge.stop()
  assert.equal(f.app.graph.onAfterChange, undefined)
})

test('only the active workflow is converted; inactive tabs are never read', async () => {
  const f = fixture()
  await f.flush()
  assert.equal(f.conversions(), 1)
  f.bridge.stop()
})

test('a snapshot taken while the tab switches is discarded', async () => {
  const f = fixture()
  f.app.graphToPrompt = async () => { f.store.activeWorkflow = f.store.openWorkflows[1]; return { output: {} } }
  assert.equal(await activeSnapshot(f.app, f.store), null)
  f.bridge.stop()
})

test('leaving the page tells the server; returning and server restart publish again', async () => {
  const f = fixture()
  await f.flush()
  f.windowEvents.get('pagehide')({ persisted: true })
  assert.deepEqual(f.beacons, [{ url: '/api/anim_bridge/v1/unpublish', body: { sessionId: 's', tabId: 't' } }])
  f.windowEvents.get('pageshow')({ persisted: true }); await f.flush()
  assert.equal(f.posts.length, 2)
  f.listeners.get('reconnected')(); await f.flush()
  assert.equal(f.posts.length, 3, 'a restarted server receives the state again')
  f.bridge.stop()
})

test('the ComfyUI client ID arriving late is published once', async () => {
  const f = fixture()
  f.api.clientId = ''
  await f.flush()
  f.api.clientId = 'late'
  f.listeners.get('status')(); await f.flush()
  f.listeners.get('status')(); await f.flush()
  assert.equal(f.posts.length, 2)
  assert.equal(f.posts[1].body.clientId, 'late')
  f.bridge.stop()
})

test('a failed publish is retried by the next change', async () => {
  const f = fixture({ fetchOk: false })
  await f.flush()
  f.api.fetchApi = async (path, options) => { f.posts.push({ path, body: JSON.parse(options.body) }); return { ok: true } }
  f.app.graph.onAfterChange(); await f.flush()
  assert.equal(f.posts.length, 2)
  f.bridge.stop()
})

test('metadata never reads a workflow graph', () => {
  const store = { openWorkflows: [{ path: 'x', get activeState() { throw new Error('graph read') } }], activeWorkflow: null }
  assert.deepEqual(workflowMetadata(store), [{ workflowId: 'x', title: 'x', isActive: false, isDirty: false }])
})
