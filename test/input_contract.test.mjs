import assert from 'node:assert/strict'

import {
  declaredInputs,
  explicitAnimInputs,
  inferredKind,
} from '../web/input_contract.js'

assert.equal(inferredKind('CLIPTextEncode', 'text'), 'text')
assert.equal(inferredKind('LoadVideo', 'video'), 'video')
assert.equal(inferredKind('LoadImage', 'image'), 'image')

const graph = {
  1: {
    class_type: 'AnimPromptInput',
    inputs: { prompt: '' },
  },
  2: {
    class_type: 'AnimImageReferences',
    inputs: { references_json: '[]', max_references: 6 },
  },
  3: {
    class_type: 'AnimVideoReferences',
    inputs: { references_json: '[]', max_references: 2 },
  },
  4: {
    class_type: 'AnimAudioReferences',
    inputs: { references_json: '[]', max_references: 3 },
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
    inputName: 'references_json',
    kind: 'image',
    label: 'Anim Image References · image files',
    capacity: 6,
    encoding: 'jsonArray',
  },
  {
    nodeId: '3',
    inputName: 'references_json',
    kind: 'video',
    label: 'Anim Video References · video files',
    capacity: 2,
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

assert.equal(
  explicitAnimInputs({
    9: {
      class_type: 'AnimVideoReferences',
      inputs: { references_json: '[]', max_references: ['8', 0] },
    },
  })[0].capacity,
  1,
)
