import { CommandQueue, EditorOperations } from './command_queue.js'
import { loadInstalledSample, queueAndCapturePromptId } from './input_contract.js'
import { applySemanticInputs } from './semantic_inputs.js'

export function workflowMetadata(store) {
  return (store.openWorkflows || []).map(workflow => ({
    workflowId: workflow.path, name: workflow.filename || workflow.key || workflow.path,
  }))
}

export async function executeCommand(app, api, command, check, editor) {
  check(); editor.checkIdle()
  const store = app.extensionManager.workflow
  let path = command.workflowId
  if (command.sample) {
    path = await editor.mutate(() => loadInstalledSample(app, store,
      { ...command, sampleName: command.name }), check)
  }
  const workflow = store.openWorkflows.find(workflow => workflow.path === path)
  if (!workflow || (!command.sample && (workflow.filename || workflow.key || workflow.path) !== command.name)) throw new Error('The workflow name or ID changed. Select it again.')
  if (store.activeWorkflow?.path !== workflow.path) {
    await editor.mutate(async () => {
      if (!workflow.isLoaded && workflow.load) await workflow.load()
      check()
      // Store.openWorkflow only changes tab bookkeeping in current ComfyUI.
      // The native graph loader saves the previous tab and restores this tab.
      const state = workflow.activeState
      if (!state) throw new Error('The selected workflow could not be loaded.')
      const loaded = await app.loadGraphData(structuredClone(state), true, true, workflow)
      check()
      if (loaded === false || store.activeWorkflow?.path !== workflow.path) throw new Error('ComfyUI did not activate the selected workflow.')
    }, check)
  }
  check()
  applySemanticInputs(app, workflow, command.parameters)
  check()
  if (command.type === 'apply') return { applied: true }
  const promptId = await editor.mutate(() => queueAndCapturePromptId(api,
      () => app.queuePrompt(0, 1), check), check)
  return { applied: true, promptId }
}

export function startPushBridge({ app, api, sessionId, tabId, WebSocketImpl = WebSocket,
  windowImpl = window, schedule = setTimeout, unschedule = clearTimeout }) {
  const store = app.extensionManager?.workflow
  if (!store) throw new Error('ComfyUI workflow store is unavailable')
  const editor = new EditorOperations()
  let stopped = false, state
  function activate() {
    if (stopped || state) return
    const scope = state = { socket: null, queue: null, retry: null, removers: [], previous: '', attempt: 0,
    }
    const active = () => state === scope && !stopped
    const send = payload => { if (active() && scope.socket?.readyState === 1) scope.socket.send(JSON.stringify(payload)) }
    const publish = () => {
      if (!active() || scope.socket?.readyState !== 1) return
      const workflows = workflowMetadata(store), serialized = JSON.stringify(workflows)
      if (scope.previous === serialized) return
      scope.previous = serialized
      send({ event: 'metadata', workflows })
    }
    const actions = new Set(['attachWorkflow', 'openWorkflow', 'openWorkflowsInBackground', 'closeWorkflow',
      'renameWorkflow', 'deleteWorkflow', 'saveAs', 'saveWorkflow', 'createTemporary', 'createNewTemporary', 'syncWorkflows'])
    if (store.$onAction) scope.removers.push(store.$onAction(({ name, after }) => { if (actions.has(name)) after(publish) }, true))
    function connect() {
      if (!active()) return
      const url = new URL(api.apiURL('/anim_bridge/v1/events'), windowImpl.location.href)
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
      url.search = new URLSearchParams({ role: 'browser', session_id: sessionId, tab_id: tabId })
      const socket = scope.socket = new WebSocketImpl(url)
      const current = () => active() && scope.socket === socket && socket.readyState === 1
      const seen = new Set()
      const queue = scope.queue = new CommandQueue((command, signal) => {
        const check = () => { signal.throwIfAborted(); if (!current()) throw new Error('Bridge disconnected') }
        return executeCommand(app, api, command, check, editor)
      }, (command, result, error) => { if (current()) { send({ event: 'result', requestId: command.requestId, ...(error ? { error: String(error) } : result) }); publish() } }, { schedule, unschedule })
      socket.onopen = () => { if (current()) { scope.attempt = 0; scope.previous = ''; publish() } }
      socket.onmessage = message => {
        if (!current()) return
        let command
        try { command = JSON.parse(message.data) } catch { return }
        if (command.event === 'cancel') { queue.cancel(command.requestId); return }
        if (command.event !== 'command' || !command.requestId || seen.has(command.requestId)) return
        seen.add(command.requestId); if (seen.size > 128) seen.delete(seen.values().next().value)
        queue.enqueue(command)
      }
      socket.onerror = () => socket.close()
      socket.onclose = () => {
        queue.close()
        if (!active() || scope.socket !== socket) return
        scope.retry = schedule(connect, Math.min(30000, 500 * 2 ** Math.min(scope.attempt++, 6)))
      }
    }
    connect()
    scope.publish = publish
  }
  function suspend() {
    const old = state
    if (!old) return
    state = null; unschedule(old.retry); old.queue?.close()
    for (const remove of old.removers) remove?.()
    old.socket?.close()
  }
  function stop() { stopped = true; suspend(); windowImpl.removeEventListener('pagehide', hide); windowImpl.removeEventListener('pageshow', show) }
  const hide = event => event.persisted ? suspend() : stop()
  const show = event => { if (event.persisted) activate() }
  windowImpl.addEventListener('pagehide', hide); windowImpl.addEventListener('pageshow', show)
  activate()
  return { stop, publish: () => state?.publish() }
}
