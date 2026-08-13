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
      inputs.push({
        nodeId,
        inputName: kind === 'image' ? 'image_paths' : 'references_json',
        kind,
        label: `${classType.replace(/([a-z])([A-Z])/g, '$1 $2')} · ${kind} files`,
        capacity: normalizedCapacity(node?.inputs?.max_references),
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
