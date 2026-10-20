import { applyInputValues, declaredInputs, loadInstalledSample, queueAndCapturePromptId } from './input_contract.js'

export function workflowMetadata(store) {
  return (store.openWorkflows || []).map((workflow) => ({
    workflowId: workflow.path,
    title: workflow.filename || workflow.key || workflow.path,
    isActive: workflow === store.activeWorkflow,
    isAvailable: true,
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

export async function executeCommand(app, api, command, checkCancelled = () => {}) {
  checkCancelled()
  const store = app.extensionManager.workflow
  let workflowId = command.workflowId
  if (command.type === 'loadSample') {
    workflowId = await loadInstalledSample(app, store, command)
  }
  const workflow = (store.openWorkflows || []).find((item) => item.path === workflowId)
  if (!workflow) throw new Error('The selected workflow tab is not available.')
  await store.openWorkflow(workflow)
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
    return { promptId: await queueAndCapturePromptId(api, () => { checkCancelled(); return app.queuePrompt(0, 1) }) }
  }
  if (command.type !== 'refresh' && command.type !== 'loadSample') {
    throw new Error('Unsupported Anim Bridge command.')
  }
  const prompt = await app.graphToPrompt()
  const apiGraph = prompt.output || {}
  return { snapshot: {
    sessionId: command.sessionId, tabId: command.tabId,
    workflowId, title: workflow.filename || workflow.path, protocolVersion: '2.0',
    revision: command.commandId, isAvailable: true, isActive: true,
    apiGraph, inputs: declaredInputs(workflow, apiGraph),
    outputNodeIds: (workflow.activeState?.extra?.linearData?.outputs || []).map(String),
  } }
}

// One persistent connection; reconnect timers exist only while disconnected.
// Pinia action completion observes metadata changes without deep state watchers.
export function startPushBridge({ app, api, sessionId, tabId, WebSocketImpl = WebSocket,
  windowImpl = window, schedule = setTimeout, unschedule = clearTimeout }) {
  const store = app.extensionManager?.workflow
  if (!store) throw new Error('Update ComfyUI: the workflow store is unavailable.')
  let socket, retry, stopped = false, attempt = 0, previous = ''
  let tail = Promise.resolve(), queued = 0
  const seen = new Set()
  const cancelled = new Set()
  const removers = []
  const send = (payload) => {
    if (socket?.readyState === 1) socket.send(JSON.stringify(payload))
  }
  const publish = () => {
    const workflows = workflowMetadata(store)
    const serialized = JSON.stringify(workflows)
    if (serialized === previous || socket?.readyState !== 1) return
    previous = serialized
    send({ event: 'metadata', workflows })
  }
  const actions = new Set(['attachWorkflow', 'openWorkflow', 'openWorkflowsInBackground',
    'closeWorkflow', 'renameWorkflow', 'deleteWorkflow', 'saveAs', 'saveWorkflow',
    'createTemporary', 'createNewTemporary', 'syncWorkflows', 'reorderWorkflows'])
  if (store.$onAction) {
    removers.push(store.$onAction(({ name, after }) => {
      if (actions.has(name)) after(publish)
    }, true))
  } else {
    // Older frontends expose the same official WorkflowStore methods without
    // Pinia hooks. Wrap only metadata operations, never graph lifecycle methods.
    for (const name of actions) {
      const original = store[name]
      if (typeof original !== 'function') continue
      const wrapped = function (...args) {
        const result = original.apply(this, args)
        if (result?.then) return result.then((value) => { publish(); return value })
        publish()
        return result
      }
      store[name] = wrapped
      removers.push(() => { if (store[name] === wrapped) store[name] = original })
    }
  }
  // ComfyUI's public `executing` CustomEvent drops prompt_id and exposes
  // only the node ID. Listen to its existing native socket's raw frames to
  // preserve identity (no additional native connection and no guessed IDs).
  let nativeSocket
  const executionTypes = new Set(['execution_start', 'executing', 'execution_success',
    'execution_error', 'execution_interrupted', 'progress'])
  const nativeMessage = (message) => {
    if (typeof message.data !== 'string') return
    try {
      const event = JSON.parse(message.data)
      if (executionTypes.has(event.type) && event.data?.prompt_id) {
        send({ event: 'execution', type: event.type, data: event.data })
      }
    } catch { /* Binary previews and unrelated non-JSON frames are not status. */ }
  }
  const attachNative = () => {
    if (nativeSocket === api.socket) return
    nativeSocket?.removeEventListener('message', nativeMessage)
    nativeSocket = api.socket
    nativeSocket?.addEventListener('message', nativeMessage)
  }
  attachNative()
  const reconnected = () => { attachNative(); previous = ''; publish() }
  const reconnecting = () => {
    nativeSocket?.removeEventListener('message', nativeMessage)
    nativeSocket = null
    previous = ''
    send({ event: 'metadata', workflows: [] })
    send({ event: 'executionDisconnected' })
  }
  api.addEventListener('reconnected', reconnected)
  api.addEventListener('reconnecting', reconnecting)
  api.addEventListener('status', attachNative)
  removers.push(() => {
    nativeSocket?.removeEventListener('message', nativeMessage)
    api.removeEventListener('reconnected', reconnected)
    api.removeEventListener('reconnecting', reconnecting)
    api.removeEventListener('status', attachNative)
  })
  const connect = () => {
    if (stopped) return
    const url = new URL(api.apiURL('/anim_bridge/v1/events'), windowImpl.location.href)
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    url.search = new URLSearchParams({ role: 'browser', session_id: sessionId, tab_id: tabId })
    const connection = socket = new WebSocketImpl(url)
    connection.onopen = () => { attempt = 0; previous = ''; publish() }
    connection.onmessage = (message) => {
      const command = JSON.parse(message.data)
      if (command.event === 'cancel') {
        cancelled.add(command.commandId)
        if (cancelled.size > 128) cancelled.delete(cancelled.values().next().value)
        return
      }
      if (command.event !== 'command' || !command.commandId || seen.has(command.commandId)) return
      seen.add(command.commandId)
      if (seen.size > 128) seen.delete(seen.values().next().value)
      if (queued >= 16) {
        send({ event: 'result', commandId: command.commandId, error: 'Too many pending commands' })
        return
      }
      queued++
      tail = tail.then(async () => {
        if (stopped || connection !== socket || connection.readyState !== 1) return
        try {
          const result = await executeCommand(app, api, command, () => {
            if (cancelled.has(command.commandId) || stopped || connection !== socket || connection.readyState !== 1) {
              throw new Error('Anim Bridge command was cancelled or disconnected.')
            }
          })
          // A reconnect never replays a command, especially a paid queue.
          if (connection === socket && connection.readyState === 1) {
            send({ event: 'result', commandId: command.commandId, ...result })
            publish()
          }
        } catch (error) {
          if (connection === socket) send({ event: 'result', commandId: command.commandId, error: String(error) })
        }
      }).finally(() => { queued--; cancelled.delete(command.commandId) })
    }
    connection.onerror = () => connection.close()
    connection.onclose = () => {
      if (!stopped && connection === socket) {
        retry = schedule(connect, Math.min(30000, 500 * 2 ** Math.min(attempt++, 6)))
      }
    }
  }
  const stop = () => {
    if (stopped) return
    stopped = true
    unschedule(retry)
    for (const remove of removers) remove?.()
    seen.clear()
    cancelled.clear()
    socket?.close()
  }
  windowImpl.addEventListener('pagehide', stop)
  removers.push(() => windowImpl.removeEventListener('pagehide', stop))
  connect()
  return { stop, publish }
}
