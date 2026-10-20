import assert from 'node:assert/strict'
import { test } from 'node:test'
import { CommandQueue } from '../web/command_queue.js'

function browserTimers() {
  const timers = new Set()
  return {
    timers,
    schedule(callback) {
      assert.equal(this, undefined, 'native browser timers must not receive the queue as this')
      const timer = { callback }
      timers.add(timer)
      return timer
    },
    unschedule(timer) {
      assert.equal(this, undefined, 'native browser timer cleanup must not receive the queue as this')
      timers.delete(timer)
    },
  }
}

test('browser timer receiver is preserved when a command completes', async () => {
  const clock = browserTimers()
  const result = new Promise((resolve) => {
    const queue = new CommandQueue(async () => 'done', (command, value, error) => {
      resolve({ command, value, error })
    }, clock)
    queue.enqueue({ commandId: 'complete' })
  })
  assert.deepEqual(await result, { command: { commandId: 'complete' }, value: 'done', error: undefined })
  assert.equal(clock.timers.size, 0)
})

test('browser timer receiver is preserved during cancellation, timeout and disposal', async () => {
  const clock = browserTimers()
  const completed = []
  const queue = new CommandQueue(() => new Promise(() => {}),
    (command, value, error) => completed.push({ id: command.commandId, error }), clock)
  queue.enqueue({ commandId: 'cancel' })
  await Promise.resolve()
  queue.cancel('cancel')
  assert.match(String(completed[0].error), /cancelled/)
  assert.equal(clock.timers.size, 0)
  queue.enqueue({ commandId: 'timeout' })
  await Promise.resolve()
  clock.timers.values().next().value.callback()
  assert.match(String(completed[1].error), /timed out/)
  assert.equal(clock.timers.size, 0)
  queue.enqueue({ commandId: 'close' })
  queue.close()
  assert.equal(clock.timers.size, 0)
  assert.equal(completed.length, 2)
})
