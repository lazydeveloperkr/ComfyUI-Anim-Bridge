import test from 'node:test'
import assert from 'node:assert/strict'
import { applySemanticInputs } from '../web/semantic_inputs.js'
const node = (id, type, widgets = {}, inputs = []) => ({ id, type, widgets: Object.entries(widgets).map(([name, value]) => ({ name, value })), inputs })
const value = (n, name) => n.widgets.find(w => w.name === name).value
function app(nodes, links = {}) { return { graph: { _nodes: nodes, links }, canvas: {}, graphToPrompt() { throw Error('Must never convert outside native Run') } } }

test('semantic prompt, duration, dimensions and prefix are applied without a graph conversion', () => {
  const nodes = [node(1,'AnimPromptInput',{prompt:'old'}),node(2,'AnimDurationInput',{duration:1}),node(3,'AnimResolutionInput',{width:512,height:512}),node(4,'AnimSequenceOutput',{filename_prefix:'old'})]
  assert.deepEqual(applySemanticInputs(app(nodes),{}, {prompt:'Scene',durationSeconds:6,width:1024,height:768,filenamePrefix:'S01/name'}),{applied:true})
  assert.equal(value(nodes[0],'prompt'),'Scene'); assert.equal(value(nodes[1],'duration'),6)
  assert.equal(value(nodes[2],'width'),1024); assert.equal(value(nodes[3],'filename_prefix'),'S01_name')
})

test('ordered references compact encoder tokens and clear missing references', () => {
  const nodes = [node(1,'AnimTextInput',{text_id:'scene',text:'old'}),node(2,'AnimImageInput',{image_id:'reference_1',image:'old.png'}),node(3,'AnimImageInput',{image_id:'reference_2',image:'old.png'}),node(4,'TextEncodeQwenImage21',{},[{name:'images.image_4',link:1},{name:'images.image_2',link:2}])]
  const a=app(nodes,{1:{origin_id:2,origin_slot:0},2:{origin_id:3,origin_slot:0}})
  applySemanticInputs(a,{}, {scene:'{reference_1} and {reference_2}',referenceFiles:{reference_1:'one.png',reference_2:'two.png'}})
  assert.equal(value(nodes[0],'text'),'<image2> and <image1>')
  applySemanticInputs(a,{}, {scene:'{reference_2}',referenceFiles:{reference_2:'two.png'}})
  assert.equal(value(nodes[0],'text'),'<image1>'); assert.equal(value(nodes[1],'image'),'')
})

test('unmatched roles, missing required image and invalid sizes fail before any widget changes', () => {
  const nodes=[node(1,'AnimPromptInput',{prompt:'old'}),node(2,'AnimImageInput',{image_id:'image',image:'old.png'}),node(3,'VAEEncode',{},[{name:'pixels',link:1}]),node(4,'AnimResolutionInput',{width:512,height:512})]
  const a=app(nodes,{1:{origin_id:2,origin_slot:0}})
  for (const input of [{prompt:'x',imageFiles:{other:'a.png'}},{prompt:'x'},{prompt:'x',imageFiles:{image:'a.png'},width:33}]) assert.throws(()=>applySemanticInputs(a,{},input))
  assert.equal(value(nodes[0],'prompt'),'old'); assert.equal(value(nodes[1],'image'),'old.png')
})

test('multiple scalar media inputs consume distinct files and capacity limits are enforced', () => {
  const nodes=[node(1,'AnimPromptInput',{prompt:''}),node(2,'LoadImage',{image:'old'}),node(3,'LoadImage',{image:'old'})]
  const workflow={activeState:{extra:{linearData:{inputs:[[2,'image',{}],[3,'image',{}]]}}}}
  applySemanticInputs(app(nodes),workflow,{prompt:'x',imageReferences:['a.png','b.png']})
  assert.equal(value(nodes[1],'image'),'a.png'); assert.equal(value(nodes[2],'image'),'b.png')
  assert.throws(()=>applySemanticInputs(app(nodes),workflow,{prompt:'x',imageReferences:['a','b','c']}),/Too many image/)
})

test('H3 limits are derived in Bridge only and never silently drop references', () => {
  const nodes=[node(1,'AnimPromptInput',{prompt:''}),node(2,'AnimImageReferences',{image_paths:'',max_references:100}),node(3,'AnimMiniMaxH3ReferenceToVideo',{},[{name:'ref_images',link:1}])]
  assert.throws(()=>applySemanticInputs(app(nodes,{1:{origin_id:2,origin_slot:0}}),{},{prompt:'x',imageReferences:Array(10).fill('a.png')}),/Too many image/)
  assert.equal(value(nodes[0],'prompt'),'')
})

test('appearance text and optional image sentences retain their existing behavior', () => {
  const nodes=[node(1,'AnimTextInput',{text_id:'scene',text:''})]
  applySemanticInputs(app(nodes),{}, {scene:'A smile. {outfit} is blue. Sunset.',characterAppearance:'red hair'})
  assert.equal(value(nodes[0],'text'),'The character: red hair. A smile. Sunset.')
})

test('readback failure rejects a run and duplicate roles cannot choose an arbitrary node', () => {
  const n=node(1,'AnimPromptInput',{prompt:''});n.widgets[0].callback=()=>{n.widgets[0].value='changed by widget'}
  assert.throws(()=>applySemanticInputs(app([n]),{}, {prompt:'expected'}),/did not apply/)
  assert.throws(()=>applySemanticInputs(app([node(1,'AnimTextInput',{text_id:'scene',text:''}),node(2,'AnimTextInput',{text_id:'scene',text:''})]),{}, {scene:'x'}),/Duplicate/)
})
