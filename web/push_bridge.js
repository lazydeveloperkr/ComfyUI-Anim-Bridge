import { applyInputValues, declaredInputs, loadInstalledSample, queueAndCapturePromptId } from './input_contract.js'
import { CommandQueue, EditorOperations } from './command_queue.js'
import { workflowRevision } from './workflow_revision.js'

export function workflowMetadata(store) {
  return (store.openWorkflows || []).map((workflow) => ({
    workflowId: workflow.path,
    title: workflow.filename || workflow.key || workflow.path,
    isActive: workflow === store.activeWorkflow,
    isAvailable: true,
    isDirty: workflow.isModified === true,
  }))
}

export function readInputs(graph, assignments) {
  const values = {}
  for (const inputId of Object.keys(assignments || {})) {
    const separator = inputId.indexOf('.')
    const id = inputId.slice(0, separator)
    const name = inputId.slice(separator + 1)
    const node = graph?.getNodeById?.(id) || graph?.getNodeById?.(Number(id))
    const widget = node?.widgets?.find((item) => item.name === name)
    if (!widget) throw new Error(`ComfyUI widget ${inputId} is not available.`)
    values[inputId] = widget.value
  }
  return values
}

export async function executeCommand(app, api, command, checkCancelled = () => {}, editor = new EditorOperations(), tracking = {}) {
  checkCancelled()
  editor.checkIdle()
  const store = app.extensionManager.workflow
  let workflowId = command.workflowId
  if (command.type === 'loadSample') {
    workflowId = await editor.mutate(() => loadInstalledSample(app, store, command), checkCancelled)
  }
  const workflow = (store.openWorkflows || []).find((item) => item.path === workflowId)
  if (!workflow) throw new Error('The selected workflow tab is not available.')
  if (store.activeWorkflow !== workflow) {
    await editor.mutate(() => store.openWorkflow(workflow), checkCancelled)
  }
  checkCancelled()
  if (command.type === 'applyInputs') {
    applyInputValues((id) => app.graph?.getNodeById?.(id), command.inputs)
    app.graph?.change?.()
    app.canvas?.setDirty?.(true, true)
    return { values: readInputs(app.graph, command.inputs) }
  }
  if (command.type === 'queuePrompt') {
    const values = readInputs(app.graph, command.inputs)
    for (const [key, expected] of Object.entries(command.inputs || {})) {
      const actual = values[key]
      if (JSON.stringify(actual) !== JSON.stringify(expected) && !(
        typeof actual === 'number' && typeof expected === 'string' &&
        expected.trim() !== '' && actual === Number(expected)
      )) throw new Error(`ComfyUI input ${key} changed before execution.`)
    }
    tracking.begin?.()
    try {
      return { promptId: await editor.mutate(() => queueAndCapturePromptId(api,
        () => app.queuePrompt(0, 1), checkCancelled,
        (id) => { checkCancelled(); tracking.onPrompt?.(id) }), checkCancelled) }
    } finally { tracking.end?.() }
  }
  if (command.type !== 'refresh' && command.type !== 'loadSample') {
    throw new Error('Unsupported Anim Bridge command.')
  }
  const graph = app.graph
  const prompt = await editor.read(() => app.graphToPrompt(), checkCancelled)
  checkCancelled()
  if (app.graph !== graph || (store.activeWorkflow && store.activeWorkflow.path !== workflowId)) {
    throw new Error('The selected workflow changed during inspection. Inspect it again.')
  }
  const apiGraph = prompt.output || {}
  const inputs = declaredInputs(workflow, apiGraph)
  const outputNodeIds = (workflow.activeState?.extra?.linearData?.outputs || []).map(String)
  checkCancelled()
  return { snapshot: {
    sessionId: command.sessionId, tabId: command.tabId,
    workflowId, title: workflow.filename || workflow.path, protocolVersion: '2.0',
    revision: workflowRevision(apiGraph, inputs, outputNodeIds),
    isDirty: workflow.isModified === true, isAvailable: true, isActive: true,
    apiGraph, inputs, outputNodeIds,
  } }
}

// Each activation owns its hooks and each connection owns its command queue.
// Only persisted pageshow can resume a suspended page; explicit stop is final.
export function startPushBridge({ app, api, sessionId, tabId, WebSocketImpl = WebSocket,
  windowImpl = window, schedule = setTimeout, unschedule = clearTimeout }) {
  const store = app.extensionManager?.workflow
  if (!store) throw new Error('Update ComfyUI: the workflow store is unavailable.')
  const editor = new EditorOperations()
  let stopped = false, activation

  const activate = () => {
    if (stopped || activation) return
    const state = activation = { socket: null, queue: null, retry: null, previous: '', attempt: 0,
      removers: [], tracked: new Set(), local: new Set(), early: new Map(), pendingQueue: 0,
      native: null, listening: false }
    const active = () => activation === state && !stopped
    const send = (payload) => {
      if (active() && state.socket?.readyState === 1) state.socket.send(JSON.stringify(payload))
    }
    const publish = () => {
      if (!active() || state.socket?.readyState !== 1) return
      const workflows = workflowMetadata(store)
      const serialized = JSON.stringify(workflows)
      if (serialized === state.previous) return
      state.previous = serialized
      send({ event: 'metadata', workflows })
    }
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
        // Read the scalar dirty flag after ComfyUI's own change handlers.
        queueMicrotask(publish)
        return result
      }
      observedGraph.onAfterChange = wrappedChange
    }
    observeGraph()
    state.removers.push(() => {
      if (observedGraph && observedGraph.onAfterChange === wrappedChange) observedGraph.onAfterChange = previousChange
    })
    const changed = () => { if (active()) { observeGraph(); publish() } }
    const actions = new Set(['attachWorkflow', 'openWorkflow', 'openWorkflowsInBackground',
      'closeWorkflow', 'renameWorkflow', 'deleteWorkflow', 'saveAs', 'saveWorkflow',
      'createTemporary', 'createNewTemporary', 'syncWorkflows', 'reorderWorkflows'])
    if (store.$onAction) {
      state.removers.push(store.$onAction(({ name, after }) => {
        if (actions.has(name)) after(changed)
      }, true))
    } else {
      for (const name of actions) {
        const original = store[name]
        if (typeof original !== 'function') continue
        const wrapped = function (...args) {
          const result = original.apply(this, args)
          if (result?.then) return result.then((value) => { changed(); return value })
          changed(); return result
        }
        store[name] = wrapped
        state.removers.push(() => { if (store[name] === wrapped) store[name] = original })
      }
    }

    const relevant = (id) => state.tracked.has(id) || state.local.has(id)
    const forward = (type, source) => {
      if (!active() || !source?.prompt_id) return
      const data = {}
      for (const key of ['prompt_id', 'node', 'value', 'max', 'exception_message']) {
        if (Object.hasOwn(source, key)) data[key] = source[key]
      }
      if (relevant(data.prompt_id)) {
        send({ event: 'execution', type, data })
        if (type === 'execution_error' || type === 'execution_interrupted' ||
          (type === 'executing' && data.node === null)) {
          state.local.delete(data.prompt_id)
          updateNative()
        }
      } else if (state.pendingQueue && state.early.size < 64) {
        const old = state.early.get(data.prompt_id)
        const terminal = old && (['execution_error', 'execution_interrupted'].includes(old.type) ||
          (old.type === 'executing' && old.data.node === null))
        if (!terminal) state.early.set(data.prompt_id, { type, data })
      }
    }
    // All other statuses use the API's already-parsed events. Only executing
    // loses prompt_id there. ComfyUI server.send_json emits {type, data} in
    // this order: filter its tiny envelope header before allocating JSON data.
    const nativeMessage = (message) => {
      if (!active() || typeof message.data !== 'string' ||
          !/^\s*\{\s*"type"\s*:\s*"executing"\s*[,}]/.test(message.data)) return
      try {
        const event = JSON.parse(message.data)
        forward('executing', event.data)
      } catch { /* Ignore malformed/non-status frames; never decode previews. */ }
    }
    const statusTypes = ['execution_start', 'execution_success', 'execution_error', 'execution_interrupted', 'progress']
    const statusListeners = new Map(statusTypes.map(type => [type, event => forward(type, event.detail)]))
    function updateNative() {
      const wanted = active() && state.socket?.readyState === 1 &&
        (state.tracked.size || state.local.size || state.pendingQueue)
      const native = wanted ? api.socket : null
      if (native !== state.native) {
        state.native?.removeEventListener('message', nativeMessage)
        state.native = native
        native?.addEventListener('message', nativeMessage)
      }
      if (!!wanted !== state.listening) {
        state.listening = !!wanted
        for (const [type, listener] of statusListeners) {
          if (wanted) api.addEventListener(type, listener)
          else api.removeEventListener(type, listener)
        }
      }
    }
    const tracking = {
      begin() { if (active()) { state.pendingQueue++; updateNative() } },
      onPrompt(id) {
        if (!active()) return
        if (state.local.size >= 64) throw new Error('Too many tracked ComfyUI tasks')
        state.local.add(id)
        const early = state.early.get(id)
        if (early) { state.early.delete(id); forward(early.type, early.data) }
      },
      end() {
        if (!active()) return
        state.pendingQueue = Math.max(0, state.pendingQueue - 1)
        if (!state.pendingQueue) state.early.clear()
        updateNative()
      },
    }
    const reconnected = () => { if (active()) { updateNative(); state.previous = ''; publish() } }
    const reconnecting = () => {
      state.native?.removeEventListener('message', nativeMessage)
      state.native = null
      state.previous = ''
      send({ event: 'metadata', workflows: [] })
      send({ event: 'executionDisconnected' })
    }
    api.addEventListener('reconnected', reconnected)
    api.addEventListener('reconnecting', reconnecting)
    api.addEventListener('status', updateNative)
    state.removers.push(() => {
      state.native?.removeEventListener('message', nativeMessage)
      for (const [type, listener] of statusListeners) api.removeEventListener(type, listener)
      api.removeEventListener('reconnected', reconnected)
      api.removeEventListener('reconnecting', reconnecting)
      api.removeEventListener('status', updateNative)
    })
    const connect = () => {
      if (!active()) return
      const url = new URL(api.apiURL('/anim_bridge/v1/events'), windowImpl.location.href)
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
      url.search = new URLSearchParams({ role: 'browser', session_id: sessionId, tab_id: tabId })
      const connection = state.socket = new WebSocketImpl(url)
      const seen = new Set()
      const current = () => active() && state.socket === connection && connection.readyState === 1
      const queue = state.queue = new CommandQueue((command, signal) => {
        const check = () => {
          signal.throwIfAborted()
          if (!current()) throw new Error('Anim Bridge connection closed')
        }
        let began = false
        const end = () => {
          signal.removeEventListener('abort', end)
          if (began) { began = false; if (current()) tracking.end() }
        }
        const scopedTracking = {
          begin() { check(); began = true; tracking.begin(); signal.addEventListener('abort', end, { once: true }) },
          onPrompt(id) { check(); tracking.onPrompt(id) },
          end,
        }
        return executeCommand(app, api, command, check, editor, scopedTracking)
      }, (command, result, error) => {
        if (!current()) return
        send({ event: 'result', commandId: command.commandId,
          ...(error ? { error: String(error) } : result) })
        publish()
      }, { schedule, unschedule })
      connection.onopen = () => {
        if (!current()) return
        state.attempt = 0; state.previous = ''; publish(); updateNative()
      }
      connection.onmessage = (message) => {
        if (!current()) return
        let command
        try { command = JSON.parse(message.data) } catch { return }
        if (command.event === 'tracking') {
          state.tracked = new Set((command.promptIds || []).filter(id => typeof id === 'string').slice(0, 256))
          for (const id of state.tracked) state.local.delete(id)
          updateNative(); return
        }
        if (command.event === 'cancel') { queue.cancel(command.commandId); return }
        if (command.event !== 'command' || !command.commandId || seen.has(command.commandId)) return
        seen.add(command.commandId)
        if (seen.size > 128) seen.delete(seen.values().next().value)
        queue.enqueue(command)
      }
      connection.onerror = () => connection.close()
      connection.onclose = () => {
        queue.close()
        if (!active() || state.socket !== connection) return
        state.tracked.clear(); state.local.clear(); state.early.clear(); state.pendingQueue = 0
        updateNative()
        state.retry = schedule(connect, Math.min(30000, 500 * 2 ** Math.min(state.attempt++, 6)))
      }
    }
    state.publish = publish
    connect()
  }
  const suspend = () => {
    const state = activation
    if (!state) return
    activation = null
    unschedule(state.retry)
    state.queue?.close()
    for (const remove of state.removers) remove?.()
    state.tracked.clear(); state.local.clear(); state.early.clear()
    state.socket?.close()
  }
  const stop = () => {
    if (stopped) return
    stopped = true
    suspend()
    windowImpl.removeEventListener('pagehide', pagehide)
    windowImpl.removeEventListener('pageshow', pageshow)
  }
  const pagehide = (event) => { if (event.persisted) suspend(); else stop() }
  const pageshow = (event) => { if (event.persisted) activate() }
  windowImpl.addEventListener('pagehide', pagehide)
  windowImpl.addEventListener('pageshow', pageshow)
  activate()
  return { stop, publish: () => activation?.publish() }
}
