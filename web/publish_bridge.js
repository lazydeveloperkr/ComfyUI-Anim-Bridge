import { declaredInputs } from './input_contract.js'
import { workflowRevision } from './workflow_revision.js'

// Store actions that change which workflows are open, active or saved.
const WORKFLOW_ACTIONS = new Set(['attachWorkflow', 'openWorkflow', 'openWorkflowsInBackground',
  'closeWorkflow', 'renameWorkflow', 'deleteWorkflow', 'saveAs', 'saveWorkflow',
  'createTemporary', 'createNewTemporary', 'syncWorkflows', 'reorderWorkflows'])
// Edits are published once they settle, not on every keystroke or drag.
const EDIT_SETTLE_MS = 1000
const ACTION_SETTLE_MS = 100

export function workflowMetadata(store) {
  return (store.openWorkflows || []).map((workflow) => ({
    workflowId: workflow.path,
    title: workflow.filename || workflow.key || workflow.path,
    isActive: workflow === store.activeWorkflow,
    isDirty: workflow.isModified === true,
  }))
}

// The executable graph of the active workflow, with the inputs Anim can set.
// Only the active tab's graph is loaded in the editor, so only it is read.
export async function activeSnapshot(app, store) {
  const workflow = store.activeWorkflow
  const graph = app.graph
  if (!workflow || !graph) return null
  const prompt = await app.graphToPrompt()
  if (store.activeWorkflow !== workflow || app.graph !== graph) return null
  const apiGraph = prompt.output || {}
  const inputs = declaredInputs(workflow, apiGraph)
  const outputNodeIds = (workflow.activeState?.extra?.linearData?.outputs || []).map(String)
  return { workflowId: workflow.path, apiGraph, inputs, outputNodeIds,
    revision: workflowRevision(apiGraph, inputs, outputNodeIds) }
}

// Sends this tab's open workflows to the Bridge server when they change.
// Nothing is received from the server and nothing runs while idle.
export function startBridge({ app, api, sessionId, tabId, windowImpl = window,
  schedule = setTimeout, unschedule = clearTimeout, beacon = (url, body) => navigator.sendBeacon(url, body) }) {
  const store = app.extensionManager?.workflow
  if (!store) throw new Error('Update ComfyUI: the workflow store is unavailable.')
  let stopped = false, timer = null, sending = false, again = false, last = ''
  const removers = []

  const send = async () => {
    if (stopped) return
    if (sending) { again = true; return }
    sending = true
    try {
      let snapshot = null
      try { snapshot = await activeSnapshot(app, store) } catch (error) {
        console.warn('[Anim Bridge] Could not read the active workflow:', error)
      }
      const body = JSON.stringify({ sessionId, tabId, clientId: api.clientId || '',
        workflows: workflowMetadata(store), snapshot })
      if (body === last || stopped) return
      const response = await api.fetchApi('/anim_bridge/v1/publish', { method: 'POST', body,
        headers: { 'Content-Type': 'application/json' } })
      if (response.ok) last = body
      else console.warn(`[Anim Bridge] Publishing workflows failed (${response.status}).`)
    } catch (error) {
      console.warn('[Anim Bridge] Publishing workflows failed:', error)
    } finally {
      sending = false
      if (again) { again = false; publishSoon(ACTION_SETTLE_MS) }
    }
  }
  const publishSoon = (delay) => {
    if (stopped) return
    if (timer) unschedule(timer)
    timer = schedule(() => { timer = null; void send() }, delay)
  }
  const republish = () => { last = ''; publishSoon(ACTION_SETTLE_MS) }

  let observedGraph, previousChange, wrappedChange
  const observeGraph = () => {
    if (observedGraph === app.graph) return
    if (observedGraph && observedGraph.onAfterChange === wrappedChange) observedGraph.onAfterChange = previousChange
    observedGraph = app.graph
    if (!observedGraph) return
    previousChange = observedGraph.onAfterChange
    const original = previousChange
    wrappedChange = function (...args) {
      const result = original?.apply(this, args)
      publishSoon(EDIT_SETTLE_MS)
      return result
    }
    observedGraph.onAfterChange = wrappedChange
  }
  removers.push(() => {
    if (observedGraph && observedGraph.onAfterChange === wrappedChange) observedGraph.onAfterChange = previousChange
  })
  const changed = () => { observeGraph(); publishSoon(ACTION_SETTLE_MS) }
  if (store.$onAction) {
    removers.push(store.$onAction(({ name, after }) => {
      if (WORKFLOW_ACTIONS.has(name)) after(changed)
    }, true))
  } else {
    for (const name of WORKFLOW_ACTIONS) {
      const original = store[name]
      if (typeof original !== 'function') continue
      const wrapped = function (...args) {
        const result = original.apply(this, args)
        if (result?.then) return result.then((value) => { changed(); return value })
        changed(); return result
      }
      store[name] = wrapped
      removers.push(() => { if (store[name] === wrapped) store[name] = original })
    }
  }
  // A restarted ComfyUI server has forgotten every tab.
  api.addEventListener('reconnected', republish)
  // The server recognises a live tab by ComfyUI's client ID, which arrives
  // with ComfyUI's first status message.
  const identified = () => { if (api.clientId && !last.includes(`"clientId":"${api.clientId}"`)) republish() }
  api.addEventListener('status', identified)
  removers.push(() => {
    api.removeEventListener('reconnected', republish)
    api.removeEventListener('status', identified)
  })

  const leave = () => {
    beacon(api.apiURL('/anim_bridge/v1/unpublish'), JSON.stringify({ sessionId, tabId }))
    last = ''
  }
  const pageEvents = [['pagehide', leave], ['pageshow', republish]]
  for (const [type, listener] of pageEvents) windowImpl.addEventListener(type, listener)
  removers.push(() => { for (const [type, listener] of pageEvents) windowImpl.removeEventListener(type, listener) })

  observeGraph()
  publishSoon(0)
  return {
    publish: () => send(),
    stop() {
      if (stopped) return
      stopped = true
      if (timer) unschedule(timer)
      for (const remove of removers) remove()
    },
  }
}
