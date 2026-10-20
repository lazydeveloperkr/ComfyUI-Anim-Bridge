import { CommandQueue, EditorOperations } from './command_queue.js?v=13-diag2'
import { loadInstalledSample, queueAndCapturePromptId } from './input_contract.js?v=13-diag2'
import { applySemanticInputs } from './semantic_inputs.js?v=13-diag2'
import { trace as consoleTrace, FRONTEND_BUILD } from './bridge_logging.js?v=13-diag2'

export function workflowMetadata(store) {
  return (store.openWorkflows || []).map(workflow => ({
    workflowId: workflow.path, name: workflow.filename || workflow.key || workflow.path,
  }))
}

export async function executeCommand(app, api, command, check, editor, trace = consoleTrace) {
  const log = (stage, fields = {}) => trace(stage, { requestId: command.requestId, ...fields })
  log('command.started')
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
    log('tab.loading')
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
  log('tab.ready')
  check()
  log('inputs.applying')
  applySemanticInputs(app, workflow, command.parameters)
  log('inputs.applied')
  check()
  if (command.type === 'apply') return { applied: true }
  log('run.started')
  const promptId = await editor.mutate(() => queueAndCapturePromptId(api,
      () => app.queuePrompt(0, 1), check), check)
  log('run.received', { promptId })
  return { applied: true, promptId }
}

export function startPushBridge({ app, api, sessionId, tabId, WebSocketImpl = WebSocket,
  windowImpl = window, schedule = setTimeout, unschedule = clearTimeout }) {
  const store = app.extensionManager?.workflow
  consoleTrace('frontend.starting')
  if (!store) throw new Error('ComfyUI workflow store is unavailable')
  const editor = new EditorOperations()
  let stopped = false, state
  function activate() {
    if (stopped || state) return
    const scope = state = { socket: null, queue: null, retry: null, removers: [], previous: '', attempt: 0,
    }
    const active = () => state === scope && !stopped
    const send = payload => { if (active() && scope.socket?.readyState === 1) scope.socket.send(JSON.stringify(payload)) }
    const trace = (stage, fields) => {
      const entry = consoleTrace(stage, fields)
      try { send({ event: 'diagnostic', ...entry }) } catch { /* Preserve command error handling if diagnostics cannot send. */ }
    }
    const reportError = error => trace('frontend.error', {
      requestId: scope.queue?.running?.command?.requestId,
      error: String(error?.message || error),
    })
    const onError = event => {
      if (/anim_bridge|push_bridge|command_queue|semantic_inputs|input_contract|bridge_logging/.test(event.filename || '')) reportError(event.error || event.message)
    }
    const onRejection = event => {
      if (/anim_bridge|push_bridge|command_queue|semantic_inputs|input_contract|bridge_logging/.test(String(event.reason?.stack || ''))) reportError(event.reason)
    }
    windowImpl.addEventListener('error', onError)
    windowImpl.addEventListener('unhandledrejection', onRejection)
    scope.removers.push(() => windowImpl.removeEventListener('error', onError),
      () => windowImpl.removeEventListener('unhandledrejection', onRejection))
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
      url.search = new URLSearchParams({ role: 'browser', session_id: sessionId, tab_id: tabId, frontend_build: FRONTEND_BUILD })
      const socket = scope.socket = new WebSocketImpl(url)
      const current = () => active() && scope.socket === socket && socket.readyState === 1
      const seen = new Set()
      const queue = scope.queue = new CommandQueue((command, signal) => {
        const check = () => { signal.throwIfAborted(); if (!current()) throw new Error('Bridge disconnected') }
        return executeCommand(app, api, command, check, editor, trace)
      }, (command, result, error) => {
        if (current()) {
          trace(error ? 'command.failed' : 'command.finished', { requestId: command.requestId, promptId: result?.promptId, error: error ? String(error) : '' })
          send({ event: 'result', requestId: command.requestId, ...(error ? { error: String(error) } : result) }); publish()
        }
      }, { schedule, unschedule })
      socket.onopen = () => { if (current()) { consoleTrace('socket.connected'); scope.attempt = 0; scope.previous = ''; publish() } }
      socket.onmessage = async message => {
        if (!current()) return
        let command
        try {
          const data = typeof message.data === 'string' ? message.data
            : typeof message.data?.text === 'function' ? await message.data.text()
            : new TextDecoder().decode(message.data)
          if (!current()) return
          command = JSON.parse(data)
          if (!command || typeof command !== 'object') throw new Error('Invalid Bridge message object')
          if (command.event === 'ready') { trace('socket.ready'); return }
          if (command.event === 'cancel') { trace('command.cancelled', { requestId: command.requestId }); queue.cancel(command.requestId); return }
          if (command.event !== 'command' || !command.requestId || seen.has(command.requestId)) return
          trace('command.received', { requestId: command.requestId })
          seen.add(command.requestId); if (seen.size > 128) seen.delete(seen.values().next().value)
          queue.enqueue(command)
        } catch (error) {
          trace('message.failed', { requestId: command?.requestId, error: String(error) })
          if (current() && command?.event === 'command' && command.requestId) {
            send({ event: 'result', requestId: command.requestId, error: String(error) })
          }
        }
      }
      socket.onerror = () => { consoleTrace('socket.error'); socket.close() }
      socket.onclose = () => {
        consoleTrace('socket.disconnected')
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
