export function inferredKind(classType, inputName) {
  const className = String(classType || '').toLowerCase()
  const name = String(inputName || '').toLowerCase()
  if (className.includes('cliptextencode') || /(text|prompt|string)/.test(name)) return 'text'
  if (/(audio|sound|voice)/.test(`${className} ${name}`)) return 'audio'
  if (/(video|movie)/.test(`${className} ${name}`) || /(^|_)clip($|_)/.test(name)) return 'video'
  if (/(image|frame|photo|picture)/.test(`${className} ${name}`)) return 'image'
  return 'unknown'
}

function normalizedCapacity(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 1 ? Math.floor(parsed) : 1
}

function miniMaxH3Capacity(apiGraph, inputNodeId, kind, declaredCapacity) {
  const inputName = kind === 'image'
    ? 'ref_images'
    : kind === 'video'
      ? 'ref_videos'
      : 'ref_audios'
  const modelCapacity = kind === 'image' ? 9 : 3
  for (const node of Object.values(apiGraph || {})) {
    if (String(node?.class_type || '') !== 'AnimMiniMaxH3ReferenceToVideo') continue
    const connection = node?.inputs?.[inputName]
    if (Array.isArray(connection) && String(connection[0]) === String(inputNodeId)) {
      return Math.min(declaredCapacity, modelCapacity)
    }
  }
  return declaredCapacity
}

export function explicitAnimInputs(apiGraph) {
  const inputs = []
  for (const [nodeId, node] of Object.entries(apiGraph || {})) {
    const classType = String(node?.class_type || '')
    if (classType === 'AnimPromptInput') {
      inputs.push({
        nodeId,
        inputName: 'prompt',
        kind: 'text',
        label: 'Anim Prompt Input · prompt',
        capacity: 1,
        encoding: 'scalar',
      })
    } else if (
      classType === 'AnimImageReferences' ||
      classType === 'AnimVideoReferences' ||
      classType === 'AnimAudioReferences'
    ) {
      const kind = classType === 'AnimImageReferences'
        ? 'image'
        : classType === 'AnimVideoReferences'
          ? 'video'
          : 'audio'
      const declaredCapacity = normalizedCapacity(node?.inputs?.max_references)
      inputs.push({
        nodeId,
        inputName: kind === 'image' ? 'image_paths' : 'references_json',
        kind,
        label: `${classType.replace(/([a-z])([A-Z])/g, '$1 $2')} · ${kind} files`,
        capacity: miniMaxH3Capacity(apiGraph, nodeId, kind, declaredCapacity),
        encoding: kind === 'image' ? 'newlineSeparated' : 'jsonArray',
      })
    }
  }
  return inputs
}

export function declaredInputs(workflow, apiGraph) {
  const linearInputs = workflow.activeState?.extra?.linearData?.inputs || []
  const preferredInputs = explicitAnimInputs(apiGraph)
  const preferredByKey = new Map(
    preferredInputs.map((input) => [
      `${input.nodeId}\0${input.inputName}`,
      input,
    ]),
  )
  const inputs = linearInputs.map((entry) => {
    const nodeId = String(entry[0])
    const inputName = String(entry[1] || '')
    const preferred = preferredByKey.get(`${nodeId}\0${inputName}`)
    if (preferred) return preferred
    const node = apiGraph?.[nodeId] || {}
    const classType = String(node.class_type || '')
    const config = entry[2] || {}
    return {
      nodeId,
      inputName,
      kind: inferredKind(classType, inputName),
      label: config.description || `${classType} · ${inputName}`,
      capacity: normalizedCapacity(config.animCapacity || config.capacity),
      encoding: 'scalar',
    }
  })
  const keys = new Set(inputs.map((input) => `${input.nodeId}\0${input.inputName}`))
  for (const input of preferredInputs) {
    const key = `${input.nodeId}\0${input.inputName}`
    if (!keys.has(key)) inputs.push(input)
  }
  return inputs
}

export function revisionPayload(apiGraph, inputs, outputNodeIds = []) {
  const graph = JSON.parse(JSON.stringify(apiGraph || {}))
  for (const input of inputs || []) {
    const node = graph[String(input.nodeId)]
    if (node?.inputs && Object.hasOwn(node.inputs, input.inputName)) {
      node.inputs[input.inputName] = '__ANIM_RUNTIME_INPUT__'
    }
  }
  return {
    apiGraph: graph,
    inputs: (inputs || []).map((input) => ({
      nodeId: String(input.nodeId),
      inputName: String(input.inputName),
      kind: String(input.kind),
      capacity: normalizedCapacity(input.capacity),
      encoding: String(input.encoding || 'scalar'),
    })),
    outputNodeIds: (outputNodeIds || []).map(String),
  }
}

export function applyInputValues(getNodeById, inputValues) {
  if (!inputValues || typeof inputValues !== 'object' || Array.isArray(inputValues)) {
    throw new Error('Anim input assignments must be an object.')
  }
  const applied = []
  for (const [inputId, value] of Object.entries(inputValues)) {
    const separator = inputId.indexOf('.')
    if (separator <= 0 || separator === inputId.length - 1) {
      throw new Error(`Invalid Anim input mapping: ${inputId}`)
    }
    const nodeId = inputId.slice(0, separator)
    const inputName = inputId.slice(separator + 1)
    const numericNodeId = Number(nodeId)
    const node = getNodeById(nodeId) || (
      Number.isFinite(numericNodeId) ? getNodeById(numericNodeId) : null
    )
    if (!node) throw new Error(`ComfyUI node ${nodeId} is not open.`)
    const widget = node.widgets?.find((item) => item.name === inputName)
    if (!widget) throw new Error(`ComfyUI widget ${inputId} is not available.`)
    widget.value = value
    widget.callback?.(value)
    node.setDirtyCanvas?.(true, true)
    applied.push(inputId)
  }
  return applied
}
