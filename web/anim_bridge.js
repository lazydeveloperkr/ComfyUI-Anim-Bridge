import { app } from '../../scripts/app.js'
import { api } from '../../scripts/api.js'
import { declaredInputs } from './input_contract.js'

function makeId() {
  return globalThis.crypto?.randomUUID?.() || `anim-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

const sessionId = sessionStorage.getItem('anim-bridge-session') || makeId()
const tabId = sessionStorage.getItem('anim-bridge-tab') || makeId()
sessionStorage.setItem('anim-bridge-session', sessionId)
sessionStorage.setItem('anim-bridge-tab', tabId)

let completedCommandId = ''
let publishing = false

function stableStringify(value) {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

async function digest(value) {
  const serialized = stableStringify(value)
  if (globalThis.crypto?.subtle) {
    const bytes = new TextEncoder().encode(serialized)
    const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes)
    return Array.from(new Uint8Array(hash)).map((item) => item.toString(16).padStart(2, '0')).join('')
  }
  let hash = 2166136261
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return `fnv1a-${(hash >>> 0).toString(16).padStart(8, '0')}`
}

async function workflowSummary(workflow, activeWorkflow) {
  const state = workflow.activeState || {}
  const active = workflow === activeWorkflow
  let apiGraph = {}
  let inputs = []
  let outputNodeIds = []
  if (active) {
    const prompt = await app.graphToPrompt()
    apiGraph = prompt.output || {}
    inputs = declaredInputs(workflow, apiGraph)
    outputNodeIds = (state.extra?.linearData?.outputs || []).map(String)
  }
  return {
    workflowId: workflow.path,
    title: workflow.filename || workflow.key || workflow.path,
    revision: await digest(state),
    isActive: active,
    isDirty: workflow.isModified === true,
    apiGraph,
    inputs,
    outputNodeIds,
  }
}

async function publish() {
  if (publishing || !app.extensionManager?.workflow) return
  publishing = true
  try {
    const store = app.extensionManager.workflow
    const workflows = []
    for (const workflow of store.openWorkflows || []) {
      workflows.push(await workflowSummary(workflow, store.activeWorkflow))
    }
    await api.fetchApi('/anim_bridge/v1/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId, tabId, workflows, completedCommandId }),
    })
    completedCommandId = ''
  } finally {
    publishing = false
  }
}

async function runCommands() {
  const response = await api.fetchApi(`/anim_bridge/v1/commands?session_id=${encodeURIComponent(sessionId)}&tab_id=${encodeURIComponent(tabId)}`)
  if (!response.ok) return
  const payload = await response.json()
  for (const command of payload.commands || []) {
    const store = app.extensionManager.workflow
    const workflow = (store.openWorkflows || []).find((item) => item.path === command.workflowId)
    if (!workflow) continue
    await store.openWorkflow(workflow)
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    completedCommandId = command.commandId
    await publish()
  }
}

app.registerExtension({
  name: 'Anim.WorkflowBridge',
  async setup() {
    await publish()
    setInterval(() => void publish(), 3000)
    setInterval(() => void runCommands(), 1000)
  },
})
