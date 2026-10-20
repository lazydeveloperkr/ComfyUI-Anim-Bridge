// The queue waits for cancellation, not for a foreign Promise to settle. A
// cancelled operation cannot release or complete a newer operation's slot.
export class CommandQueue {
  constructor(execute, complete, { schedule = setTimeout, unschedule = clearTimeout, timeout = 30000 } = {}) {
    Object.assign(this, { execute, complete, timeout })
    // Browser timer functions reject a CommandQueue receiver (Illegal invocation).
    // Invoke the captured functions without turning them into queue methods.
    this.schedule = (callback, delay) => schedule(callback, delay)
    this.unschedule = (timer) => unschedule(timer)
    this.jobs = new Map()
    this.pending = []
    this.running = null
    this.closed = false
  }

  enqueue(command) {
    if (this.closed) return
    if (this.jobs.size >= 32) {
      this.complete(command, null, new Error('Too many pending commands'))
      return
    }
    const job = { command, abort: new AbortController() }
    this.jobs.set(command.commandId, job)
    this.pending.push(job)
    job.timer = this.schedule(() => this.cancel(command.commandId, 'Command timed out; retry or reload the ComfyUI browser.'), this.timeout)
    this.next()
  }

  cancel(id, message = 'Command cancelled') {
    const job = this.jobs.get(id)
    if (!job) return
    job.abort.abort(new Error(message))
    this.finish(job, null, job.abort.signal.reason)
  }

  finish(job, value, error) {
    if (this.jobs.get(job.command.commandId) !== job) return
    this.jobs.delete(job.command.commandId)
    this.unschedule(job.timer)
    this.pending = this.pending.filter(item => item !== job)
    if (this.running === job) this.running = null
    if (!this.closed) this.complete(job.command, value, error)
    this.next()
  }

  next() {
    if (this.closed || this.running || !this.pending.length) return
    const job = this.running = this.pending.shift()
    Promise.resolve().then(() => {
      job.abort.signal.throwIfAborted()
      return this.execute(job.command, job.abort.signal)
    }).then(value => this.finish(job, value), error => this.finish(job, null, error))
  }

  close() {
    this.closed = true
    for (const job of this.jobs.values()) {
      this.unschedule(job.timer)
      job.abort.abort(new Error('Anim Bridge connection closed'))
    }
    this.jobs.clear()
    this.pending = []
    this.running = null
  }
}

// ComfyUI's native open/load/queue APIs do not accept AbortSignal. Never
// overlap a newer editor mutation with an abandoned one: fail promptly until
// it settles (or the user reloads). Unresolved graph reads are bounded too.
export class EditorOperations {
  constructor() { this.mutating = false; this.reads = 0 }
  checkIdle() {
    if (this.mutating) throw new Error('ComfyUI editor is still finishing an earlier command. Reload its browser tab if it does not finish.')
  }
  async mutate(run, check) {
    check(); this.checkIdle(); this.mutating = true
    try { const value = await run(); check(); return value }
    finally { this.mutating = false }
  }
  async read(run, check) {
    check(); this.checkIdle()
    if (this.reads >= 2) throw new Error('Earlier ComfyUI graph inspections are still running. Reload its browser tab.')
    this.reads++
    try { const value = await run(); check(); return value }
    finally { this.reads-- }
  }
}
