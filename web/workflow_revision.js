// Called only for one explicit inspection. Exclude assigned runtime widget
// values without cloning the graph; no revision work occurs during listing.
export function workflowRevision(apiGraph, inputs, outputNodeIds) {
  const runtime = new Map()
  for (const input of inputs) {
    const id = String(input.nodeId)
    if (!runtime.has(id)) runtime.set(id, new Set())
    runtime.get(id).add(input.inputName)
  }
  let hash = 0xcbf29ce484222325n
  const feed = (text) => {
    for (let i = 0; i < text.length; i++) {
      hash = BigInt.asUintN(64, (hash ^ BigInt(text.charCodeAt(i))) * 0x100000001b3n)
    }
  }
  const visit = (value, path = []) => {
    if (path.length === 3 && path[1] === 'inputs' && runtime.get(path[0])?.has(path[2])) {
      feed('"__ANIM_RUNTIME_INPUT__"'); return
    }
    if (Array.isArray(value)) {
      feed('[')
      for (const item of value) { visit(item, [...path, '[]']); feed(',') }
      feed(']')
    } else if (value && typeof value === 'object') {
      feed('{')
      for (const key of Object.keys(value).sort()) {
        feed(JSON.stringify(key)); feed(':'); visit(value[key], [...path, key]); feed(',')
      }
      feed('}')
    } else { feed(JSON.stringify(value) ?? 'null') }
  }
  visit(apiGraph); feed('|'); visit(inputs); feed('|'); visit(outputNodeIds)
  return `fnv1a64-${hash.toString(16).padStart(16, '0')}`
}
