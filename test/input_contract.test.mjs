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
    inputs: { image_paths: 'one.png\ntwo.png', max_references: 9 },
  },
  3: {
    class_type: 'AnimVideoReferences',
    inputs: { references_json: '[]', max_references: 2 },
  },
  4: {
    class_type: 'AnimAudioReferences',
    inputs: { references_json: '[]', max_references: 3 },
  },
  142: {
    class_type: 'AnimMiniMaxH3ReferenceImageLoader',
    inputs: { image_paths: 'one.png\ntwo.png' },
  },
  143: {
    class_type: 'AnimMiniMaxH3ReferenceToVideo',
    inputs: { ref_images: ['142', 0] },
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
  {
    nodeId: '142',
    inputName: 'image_paths',
    kind: 'image',
    label: 'Anim MiniMax H3 Reference Image Loader · image files',
    capacity: 9,
    encoding: 'newlineSeparated',
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
    142: {
      class_type: 'AnimMiniMaxH3ReferenceImageLoader',
      inputs: { image_paths: '' },
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
