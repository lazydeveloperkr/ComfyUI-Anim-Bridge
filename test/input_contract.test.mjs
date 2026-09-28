import assert from 'node:assert/strict'

import {
  applyInputValues,
  declaredInputs,
  explicitAnimInputs,
  inferredKind,
  revisionPayload,
} from '../web/input_contract.js'

assert.equal(inferredKind('CLIPTextEncode', 'text'), 'text')
assert.equal(inferredKind('LoadVideo', 'video'), 'video')
assert.equal(inferredKind('LoadImage', 'image'), 'image')

const graph = {
  1: {
    class_type: 'AnimPromptInput',
    inputs: { prompt: '' },
  },
  5: {
    class_type: 'AnimDurationInput',
    inputs: { duration: 5.0 },
  },
  6: {
    class_type: 'AnimSequenceOutput',
    inputs: { filename_prefix: 'ComfyUI' },
  },
  2: {
    class_type: 'AnimImageReferences',
    inputs: { image_paths: 'one.png\ntwo.png', max_references: 100 },
  },
  3: {
    class_type: 'AnimVideoReferences',
    inputs: { references_json: '[]', max_references: 100 },
  },
  4: {
    class_type: 'AnimAudioReferences',
    inputs: { references_json: '[]', max_references: 100 },
  },
  143: {
    class_type: 'AnimMiniMaxH3ReferenceToVideo',
    inputs: {
      ref_images: ['2', 3],
      ref_videos: ['3', 1],
      ref_audios: ['4', 1],
    },
  },
}

assert.deepEqual(explicitAnimInputs(graph), [
  {
    nodeId: '1',
    inputName: 'prompt',
    kind: 'text',
    label: 'Anim Prompt Input · prompt',
    capacity: 1,
    encoding: 'scalar',
  },
  {
    nodeId: '2',
    inputName: 'image_paths',
    kind: 'image',
    label: 'Anim Image References · image files',
    capacity: 9,
    encoding: 'newlineSeparated',
  },
  {
    nodeId: '3',
    inputName: 'references_json',
    kind: 'video',
    label: 'Anim Video References · video files',
    capacity: 3,
    encoding: 'jsonArray',
  },
  {
    nodeId: '4',
    inputName: 'references_json',
    kind: 'audio',
    label: 'Anim Audio References · audio files',
    capacity: 3,
    encoding: 'jsonArray',
  },
  {
    nodeId: '5',
    inputName: 'duration',
    kind: 'number',
    label: 'Anim Duration Input · duration',
    capacity: 1,
    encoding: 'scalar',
  },
  {
    nodeId: '6',
    inputName: 'filename_prefix',
    kind: 'filenamePrefix',
    label: 'Anim Sequence Output · filename_prefix',
    capacity: 1,
    encoding: 'scalar',
  },
])

assert.equal(
  declaredInputs(
    {
      activeState: {
        extra: {
          linearData: {
            inputs: [['1', 'prompt', { description: 'Prompt' }]],
          },
        },
      },
    },
    graph,
  ).filter((input) => input.nodeId === '1').length,
  1,
)

const revisionInputs = explicitAnimInputs(graph)
const revisionA = revisionPayload(graph, revisionInputs, ['143'])
const graphWithRuntimeValues = structuredClone(graph)
graphWithRuntimeValues[1].inputs.prompt = 'A generated prompt'
graphWithRuntimeValues[2].inputs.image_paths = 'new-one.png\nnew-two.png'
graphWithRuntimeValues[3].inputs.references_json = '["clip.mp4"]'
graphWithRuntimeValues[6].inputs.filename_prefix = 'S003_거실_공놀이'
const revisionB = revisionPayload(
  graphWithRuntimeValues,
  explicitAnimInputs(graphWithRuntimeValues),
  ['143'],
)
assert.deepEqual(revisionB, revisionA)

const structurallyChangedGraph = structuredClone(graphWithRuntimeValues)
structurallyChangedGraph[143].inputs.length = 241
assert.notDeepEqual(
  revisionPayload(
    structurallyChangedGraph,
    explicitAnimInputs(structurallyChangedGraph),
    ['143'],
  ),
  revisionA,
)

const callbackValues = []
const dirtyNodes = []
const nodes = {
  1: {
    widgets: [{ name: 'prompt', value: '', callback: (value) => callbackValues.push(value) }],
    setDirtyCanvas: (...args) => dirtyNodes.push(args),
  },
  2: {
    widgets: [{ name: 'image_paths', value: '' }],
    setDirtyCanvas: (...args) => dirtyNodes.push(args),
  },
}
assert.deepEqual(
  applyInputValues(
    (nodeId) => nodes[nodeId],
    { '1.prompt': 'Visible prompt', '2.image_paths': 'first.png\nlast.png' },
  ),
  ['1.prompt', '2.image_paths'],
)
assert.equal(nodes[1].widgets[0].value, 'Visible prompt')
assert.equal(nodes[2].widgets[0].value, 'first.png\nlast.png')
assert.deepEqual(callbackValues, ['Visible prompt'])
assert.equal(dirtyNodes.length, 2)
assert.throws(
  () => applyInputValues((nodeId) => nodes[nodeId], { '3.prompt': 'missing' }),
  /node 3 is not open/,
)

const durationCallbackValues = []
const durationNodes = {
  5: {
    widgets: [{
      name: 'duration',
      type: 'number',
      value: 0,
      callback: (value) => durationCallbackValues.push(value),
    }],
    setDirtyCanvas: () => {},
  },
}
applyInputValues((nodeId) => durationNodes[nodeId], { '5.duration': '5.5' })
assert.equal(durationNodes[5].widgets[0].value, 5.5)
assert.deepEqual(durationCallbackValues, [5.5])

assert.equal(
  explicitAnimInputs({
    2: {
      class_type: 'AnimImageReferences',
      inputs: { image_paths: '', max_references: 100 },
    },
  })[0].capacity,
  100,
)

assert.equal(
  explicitAnimInputs({
    9: {
      class_type: 'AnimVideoReferences',
      inputs: { references_json: '[]', max_references: ['8', 0] },
    },
  })[0].capacity,
  1,
)

const qwenGraph = {
  12: { class_type: 'AnimImageInput', inputs: { image_id: 'character', image: 'char.webp' } },
  13: { class_type: 'AnimImageInput', inputs: { image_id: 'outfit', image: 'outfit.webp' } },
  14: { class_type: 'AnimImageInput', inputs: { image_id: 'location', image: 'place.webp' } },
  21: { class_type: 'AnimResolutionInput', inputs: { width: 2048, height: 1152 } },
  30: {
    class_type: 'TextEncodeQwenImage21',
    inputs: {
      prompt: ['20', 0],
      'images.image_1': ['12', 0],
      'images.image_2': ['13', 0],
      'images.image_3': ['14', 0],
    },
  },
}
assert.deepEqual(explicitAnimInputs(qwenGraph), [
  {
    nodeId: '12',
    inputName: 'image',
    kind: 'image',
    label: 'Anim Image Input · character',
    capacity: 1,
    encoding: 'scalar',
    slotId: 'character',
    promptToken: '<image1>',
    duplicateSlotId: false,
  },
  {
    nodeId: '13',
    inputName: 'image',
    kind: 'image',
    label: 'Anim Image Input · outfit',
    capacity: 1,
    encoding: 'scalar',
    slotId: 'outfit',
    promptToken: '<image2>',
    duplicateSlotId: false,
  },
  {
    nodeId: '14',
    inputName: 'image',
    kind: 'image',
    label: 'Anim Image Input · location',
    capacity: 1,
    encoding: 'scalar',
    slotId: 'location',
    promptToken: '<image3>',
    duplicateSlotId: false,
  },
  {
    nodeId: '21',
    inputName: 'width',
    kind: 'width',
    label: 'Anim Resolution Input · width',
    capacity: 1,
    encoding: 'scalar',
  },
  {
    nodeId: '21',
    inputName: 'height',
    kind: 'height',
    label: 'Anim Resolution Input · height',
    capacity: 1,
    encoding: 'scalar',
  },
])

// An image that does not reach the Qwen encoder has no prompt token.
const unwiredGraph = structuredClone(qwenGraph)
delete unwiredGraph[30].inputs['images.image_3']
assert.equal(explicitAnimInputs(unwiredGraph)[2].promptToken, null)

const duplicateGraph = structuredClone(qwenGraph)
duplicateGraph[13].inputs.image_id = 'character'
assert.deepEqual(
  explicitAnimInputs(duplicateGraph)
    .filter((input) => input.kind === 'image')
    .map((input) => [input.slotId, input.duplicateSlotId]),
  [['character', true], ['character', true], ['location', false]],
)

// Anim sees the role and token in the revision, but not runtime file names
// or sizes, so one Sequence's values do not look like a structural change.
const qwenRevision = revisionPayload(qwenGraph, explicitAnimInputs(qwenGraph), ['40'])
assert.deepEqual(qwenRevision.inputs[0], {
  nodeId: '12',
  inputName: 'image',
  kind: 'image',
  capacity: 1,
  encoding: 'scalar',
  slotId: 'character',
  promptToken: '<image1>',
  duplicateSlotId: false,
})
assert.deepEqual(qwenRevision.inputs[3], {
  nodeId: '21',
  inputName: 'width',
  kind: 'width',
  capacity: 1,
  encoding: 'scalar',
})
assert.equal(qwenRevision.apiGraph[12].inputs.image, '__ANIM_RUNTIME_INPUT__')
assert.equal(qwenRevision.apiGraph[12].inputs.image_id, 'character')
assert.equal(qwenRevision.apiGraph[21].inputs.width, '__ANIM_RUNTIME_INPUT__')
const resizedGraph = structuredClone(qwenGraph)
resizedGraph[12].inputs.image = 'other.webp'
resizedGraph[21].inputs.width = 1152
resizedGraph[21].inputs.height = 2048
assert.deepEqual(
  revisionPayload(resizedGraph, explicitAnimInputs(resizedGraph), ['40']),
  qwenRevision,
)

// An earlier keyframe of the Sequence is a fourth fixed role.
const keyframeGraph = structuredClone(qwenGraph)
keyframeGraph[15] = {
  class_type: 'AnimImageInput',
  inputs: { image_id: 'keyframe_reference', image: 'kf.webp' },
}
keyframeGraph[30].inputs['images.image_4'] = ['15', 0]
assert.deepEqual(
  explicitAnimInputs(keyframeGraph)
    .filter((input) => input.kind === 'image')
    .map((input) => [input.slotId, input.promptToken]),
  [
    ['character', '<image1>'],
    ['outfit', '<image2>'],
    ['location', '<image3>'],
    ['keyframe_reference', '<image4>'],
  ],
)

const textGraph = {
  23: { class_type: 'AnimTextInput', inputs: { text_id: 'scene', text: 'She sits.' } },
  24: { class_type: 'AnimTextInput', inputs: { text_id: 'character_appearance', text: 'freckles' } },
  30: { class_type: 'AnimImageInput', inputs: { image_id: 'character', image: 'c.webp' } },
}
assert.deepEqual(explicitAnimInputs(textGraph).slice(0, 2), [
  {
    nodeId: '23',
    inputName: 'text',
    kind: 'text',
    label: 'Anim Text Input · scene',
    capacity: 1,
    encoding: 'scalar',
    slotId: 'scene',
    duplicateSlotId: false,
  },
  {
    nodeId: '24',
    inputName: 'text',
    kind: 'text',
    label: 'Anim Text Input · character_appearance',
    capacity: 1,
    encoding: 'scalar',
    slotId: 'character_appearance',
    duplicateSlotId: false,
  },
])

// Text and image roles are checked separately, and the revision carries the
// text role without a prompt token or the runtime text.
const textRevision = revisionPayload(textGraph, explicitAnimInputs(textGraph), [])
assert.deepEqual(textRevision.inputs[0], {
  nodeId: '23',
  inputName: 'text',
  kind: 'text',
  capacity: 1,
  encoding: 'scalar',
  slotId: 'scene',
  duplicateSlotId: false,
})
assert.equal(textRevision.apiGraph[23].inputs.text, '__ANIM_RUNTIME_INPUT__')

const duplicateTextGraph = structuredClone(textGraph)
duplicateTextGraph[24].inputs.text_id = 'scene'
assert.deepEqual(
  explicitAnimInputs(duplicateTextGraph).map((input) => input.duplicateSlotId),
  [true, true, false],
)

// Queue in the tab: the prompt id comes from the tab's own api.queuePrompt.
{
  const { queueAndCapturePromptId } = await import('../web/input_contract.js')
  const calls = []
  const fakeApi = {
    async queuePrompt(number, prompt) {
      calls.push([number, prompt])
      return { prompt_id: 'prompt-7', number: 1 }
    },
  }
  const original = fakeApi.queuePrompt
  const promptId = await queueAndCapturePromptId(fakeApi, () =>
    fakeApi.queuePrompt(0, { output: {} }),
  )
  assert.equal(promptId, 'prompt-7')
  assert.equal(calls.length, 1)
  assert.equal(fakeApi.queuePrompt, original)

  await assert.rejects(
    queueAndCapturePromptId(fakeApi, async () => {}),
    /did not queue/,
  )
  assert.equal(fakeApi.queuePrompt, original)
}
