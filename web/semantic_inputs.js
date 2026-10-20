import { applyInputValues, declaredInputs } from './input_contract.js?v=13-diag2'

// Inspect only the selected live editor, only when a request arrives. This is
// a private input contract, never an executable prompt or a wire payload.
export function selectedInputs(app, workflow) {
  const nodes = new Map(), contract = {}
  function collect(graph, prefix = '') {
    for (const node of graph?._nodes || []) {
      const id = `${prefix}${node.id}`
      nodes.set(id, node)
      const inputs = {}
      for (const widget of node.widgets || []) {
        if (['image_id', 'text_id', 'max_references'].includes(widget.name)) inputs[widget.name] = widget.value
      }
      for (const input of node.inputs || []) {
        const link = graph.links?.[input.link]
        if (link) inputs[input.name] = [`${prefix}${link.origin_id}`, link.origin_slot]
      }
      contract[id] = { class_type: node.comfyClass || node.type, inputs }
      if (node.subgraph) collect(node.subgraph, `${id}:`)
    }
  }
  collect(app.rootGraph || app.graph)
  return { nodes, inputs: declaredInputs(workflow, contract) }
}

export function applySemanticInputs(app, workflow, request) {
  const { nodes, inputs } = selectedInputs(app, workflow)
  if (!inputs.length) throw new Error('Add an Anim Prompt Input or Anim Text Input to this workflow.')
  if (inputs.some(input => input.duplicateSlotId)) throw new Error('Duplicate Anim input roles; give each input a unique role.')
  const prompt = String(request.prompt || request.scene || '').trim()
  if (!prompt) throw new Error('A prompt is required.')
  const fileMap = value => {
    if (value == null) return {}
    if (typeof value !== 'object' || Array.isArray(value) || Object.values(value).some(file => typeof file !== 'string' || !file || /[\r\n]/.test(file))) throw new Error('Reference roles must contain uploaded file names.')
    return value
  }
  const references = fileMap(request.referenceFiles), roles = fileMap(request.imageFiles)
  const suppliedRoles = { ...roles, ...references }
  const media = { image: request.imageReferences || [], video: request.videoReferences || [], audio: request.audioReferences || [] }
  for (const files of Object.values(media)) {
    if (!Array.isArray(files) || files.some(file => typeof file !== 'string' || !file || /[\r\n]/.test(file))) throw new Error('Media references must be uploaded file names.')
  }
  const imageSlots = inputs.filter(input => input.kind === 'image' && input.slotId)
  for (const role of Object.keys(suppliedRoles)) {
    if (!imageSlots.some(input => input.slotId === role)) throw new Error(`Image role ${role} has no matching workflow input.`)
  }
  if (Object.keys(suppliedRoles).length && media.image.length) throw new Error('Choose role references or ordered image references, not both.')
  const cursors = { image: 0, video: 0, audio: 0 }, assignments = {}, tokens = {}
  const slotFiles = { ...suppliedRoles }
  // A generic video request can target ordered image slots too.
  for (const input of imageSlots) {
    if (media.image.length) slotFiles[input.slotId] = media.image[cursors.image++] || ''
  }
  const activeImages = imageSlots.filter(input => slotFiles[input.slotId] && input.promptToken)
  const socketNumber = input => Number(input.promptToken.match(/\d+/)?.[0] || 0)
  activeImages.sort((a, b) => socketNumber(a) - socketNumber(b))
  for (const [index, input] of activeImages.entries()) tokens[input.slotId] = input.promptToken.startsWith('<image') ? `<image${index + 1}>` : input.promptToken
  const optional = new Set(['character_2', 'character_3', 'outfit', 'keyframe_reference', 'reference_image'])
  const expand = raw => {
    let value = String(raw || '')
    for (const role of optional) {
      if (!slotFiles[role]) value = value.replace(new RegExp(`[^.]*\\{${role}\\}[^.]*\\.?`, 'g'), '')
    }
    return value.replace(/\{([a-z_][a-z_0-9]*)\}/g, (_, role) => {
      if (!tokens[role]) throw new Error(`Reference ${role} is not connected or supplied.`)
      return tokens[role]
    }).trim()
  }
  const appearanceInputs = new Set(inputs.filter(input => input.kind === 'text').map(input => input.slotId))
  const characterRoles = ['character', 'character_2', 'character_3']
  const appearances = [request.characterAppearance || '', request.extraAppearances?.character_2 || '', request.extraAppearances?.character_3 || '']
  let scene = expand(prompt)
  // Preserve appearance composition for workflows with only a scene input.
  if (inputs.some(input => input.kind === 'text' && (!input.slotId || input.slotId === 'scene'))) {
    const subjects = ['The character', 'The second character', 'The third character']
    const sentences = []
    for (const [index, role] of characterRoles.entries()) {
      const textRole = index ? `character_appearance_${index + 1}` : 'character_appearance'
      if ((index === 0 || slotFiles[role]) && !appearanceInputs.has(textRole) && String(appearances[index]).trim()) {
        sentences.push(`${subjects[index]}${tokens[role] ? ` from ${tokens[role]}` : ''}: ${expand(appearances[index])}.`)
      }
    }
    scene = [...sentences, scene].join(' ')
  }
  let promptInputs = 0
  for (const input of inputs) {
    let value
    if (input.kind === 'text') {
      if (input.slotId?.startsWith('character_appearance')) {
        const role = input.slotId === 'character_appearance' ? 'character' : `character_${input.slotId.slice(-1)}`
        value = expand(role === 'character' ? request.characterAppearance : request.extraAppearances?.[role])
      } else { value = input.slotId === 'prompt' ? expand(prompt) : scene; promptInputs++ }
    } else if (input.kind === 'number') {
      if (request.durationSeconds == null) continue
      value = Number(request.durationSeconds)
      if (!Number.isFinite(value) || value <= 0) throw new Error('Duration must be positive.')
    } else if (input.kind === 'width' || input.kind === 'height') {
      if (request[input.kind] == null) continue
      value = Number(request[input.kind])
      if (!Number.isInteger(value) || value < 256 || value > 4096 || value % 32) throw new Error('Image dimensions must be multiples of 32 between 256 and 4096.')
    } else if (input.kind === 'filenamePrefix') {
      value = String(request.filenamePrefix || 'ComfyUI').replace(/[\\/:*?"<>|%]/g, '_')
    } else if (input.slotId) {
      value = slotFiles[input.slotId] || ''
      if (input.required && !value) throw new Error(`Required image ${input.slotId} is missing.`)
    } else if (media[input.kind]) {
      const start = cursors[input.kind]
      const files = media[input.kind].slice(start, start + input.capacity)
      cursors[input.kind] += files.length
      if (input.encoding === 'jsonArray') value = JSON.stringify(files)
      else if (input.encoding === 'newlineSeparated') value = files.join('\n')
      else { if (files.length > 1) throw new Error('This input accepts one media file.'); value = files[0] || '' }
    }
    if (value !== undefined) assignments[`${input.nodeId}.${input.inputName}`] = value
  }
  if (!promptInputs) throw new Error('This workflow has no prompt input.')
  for (const kind of ['image', 'video', 'audio']) {
    if (cursors[kind] < media[kind].length) throw new Error(`Too many ${kind} references for this workflow.`)
  }
  // Validate every destination before changing any widget.
  for (const key of Object.keys(assignments)) {
    const at = key.indexOf('.')
    if (!nodes.get(key.slice(0, at))?.widgets?.some(widget => widget.name === key.slice(at + 1))) throw new Error(`Input ${key} is unavailable.`)
  }
  applyInputValues(id => nodes.get(String(id)), assignments)
  for (const [key, expected] of Object.entries(assignments)) {
    const at = key.indexOf('.')
    const value = nodes.get(key.slice(0, at)).widgets.find(widget => widget.name === key.slice(at + 1)).value
    if (String(value) !== String(expected)) throw new Error(`ComfyUI did not apply ${key}.`)
  }
  app.graph?.change?.()
  app.canvas?.setDirty?.(true, true)
  return { applied: true }
}
