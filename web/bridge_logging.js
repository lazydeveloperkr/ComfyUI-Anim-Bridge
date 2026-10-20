// Small diagnostic events only. Never serialize commands, graphs or prompts.
export function trace(stage, fields = {}) {
  const safe = value => String(value || '')
    .replace(/(bearer\s+)\S+/ig, '$1<redacted>')
    .replace(/((?:api[_-]?key|token|authorization)\s*[=:]\s*)[^\s,;]+/ig, '$1<redacted>')
    .slice(0, 500)
  const entry = { stage, requestId: safe(fields.requestId), promptId: safe(fields.promptId), error: safe(fields.error) }
  console.info('[Anim Bridge]', JSON.stringify(entry))
  return entry
}
