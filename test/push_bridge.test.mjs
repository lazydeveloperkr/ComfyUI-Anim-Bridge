import test from 'node:test'
import assert from 'node:assert/strict'
import { startPushBridge, executeCommand } from '../web/push_bridge.js'
import { CommandQueue, EditorOperations } from '../web/command_queue.js'
const tick = () => new Promise(resolve => setImmediate(resolve))
function fixture() {
  const browser = new EventTarget(); browser.location={href:'http://localhost:8188/'}
  const first={path:'one',filename:'One',isLoaded:true,activeState:{nodes:[]}}
  const second={path:'two',filename:'Two',isLoaded:true,activeState:{nodes:[]}}
  let action, conversions=0, loads=0
  const store={openWorkflows:[first,second],activeWorkflow:first,$onAction(fn){action=fn;return()=>{action=null}}}
  const prompt={id:1,type:'AnimPromptInput',widgets:[{name:'prompt',value:'old'}]}
  const api=new EventTarget();api.socket=new EventTarget();api.apiURL=p=>p
  let runs=0
  api.queuePrompt=async()=>{const id=`p${++runs}`;api.socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'executing',data:{prompt_id:id,node:null}})}));return {prompt_id:id}}
  const app={extensionManager:{workflow:store},graph:{_nodes:[prompt]},canvas:{},graphToPrompt(){conversions++;throw Error('No inspection allowed')},async loadGraphData(graph,a,b,workflow){loads++;store.activeWorkflow=workflow;app.graph={_nodes:[prompt]}},async queuePrompt(){await api.queuePrompt(0,{output:{}})}}
  class Socket { static all=[]; constructor(){this.readyState=0;this.sent=[];Socket.all.push(this)} open(){this.readyState=1;this.onopen?.()} send(value){this.sent.push(JSON.parse(value))} receive(command){this.onmessage?.({data:JSON.stringify(command)})} close(){this.readyState=3;this.onclose?.()} }
  return {app,api,store,browser,Socket,prompt,second,get conversions(){return conversions},get loads(){return loads},get runs(){return runs},change(){let publish;action?.({name:'renameWorkflow',after(fn){publish=fn}});publish?.()}}
}

test('idle lists names/paths only without inspecting graphs, timers or native message parsing', () => {
  const f=fixture();let timers=0
  Object.defineProperty(f.store.openWorkflows[0],'activeState',{get(){throw Error('idle graph read')}})
  const bridge=startPushBridge({...f,sessionId:'s',tabId:'t',WebSocketImpl:f.Socket,windowImpl:f.browser,schedule(){timers++;return 1},unschedule(){}})
  const socket=f.Socket.all[0];socket.open()
  assert.equal(socket.sent.length,1)
  assert.deepEqual(socket.sent[0].workflows,[{workflowId:'one',name:'One'},{workflowId:'two',name:'Two'}])
  assert.equal(socket.sent[0].comfyClientId,'')
  assert.ok(socket.sent[0].bridgeConnectionId)
  assert.equal(timers,0);assert.equal(f.conversions,0)
  f.store.openWorkflows[1].filename='Renamed';f.change()
  assert.equal(socket.sent.length,2);assert.equal(socket.sent[1].workflows[1].name,'Renamed')
  bridge.stop()
})

test('selected inactive tab is loaded through the native graph loader and queued once', async () => {
  const f=fixture();const bridge=startPushBridge({...f,sessionId:'s',tabId:'t',WebSocketImpl:f.Socket,windowImpl:f.browser})
  const socket=f.Socket.all[0];socket.open()
  const command={event:'command',commandId:'r',requestId:'r',type:'run',workflowId:'two',name:'Two',parameters:{prompt:'Applied prompt'}}
  socket.receive(command);await tick();await tick()
  assert.equal(f.loads,1);assert.equal(f.runs,1);assert.equal(f.prompt.widgets[0].value,'Applied prompt');assert.equal(f.conversions,0)
  assert.equal(socket.sent.filter(v=>v.event==='completed').length,0)
  assert.deepEqual(socket.sent.find(v=>v.event==='result'),{event:'result',requestId:'r',applied:true,promptId:'p1'})
  socket.receive(command);await tick();assert.equal(f.runs,1)
  bridge.stop()
})

test('BFCache page restore starts a fresh socket and removes old listeners/queues', () => {
  const f=fixture();const bridge=startPushBridge({...f,sessionId:'s',tabId:'t',WebSocketImpl:f.Socket,windowImpl:f.browser})
  const socket=f.Socket.all[0];socket.open()
  const hidden=new Event('pagehide');hidden.persisted=true;f.browser.dispatchEvent(hidden)
  assert.equal(socket.readyState,3)
  const shown=new Event('pageshow');shown.persisted=true;f.browser.dispatchEvent(shown)
  assert.equal(f.Socket.all.length,2)
  f.Socket.all[1].open();bridge.stop()
})

test('native ComfyUI events target the exact Bridge connection and execute once', async () => {
  const f=fixture();f.api.clientId='native-client'
  const bridge=startPushBridge({...f,sessionId:'s',tabId:'t',WebSocketImpl:f.Socket,windowImpl:f.browser})
  const socket=f.Socket.all[0];socket.open()
  assert.equal(socket.sent[0].comfyClientId,'native-client')
  const command={event:'command',requestId:'native-r',commandId:'native-r',type:'run',workflowId:'two',name:'Two',parameters:{prompt:'Native video input'},bridgeSessionId:'s',bridgeTabId:'t',bridgeConnectionId:socket.sent[0].bridgeConnectionId}
  const emit=value=>{const event=new Event('anim_bridge_command');event.detail=value;f.api.dispatchEvent(event)}
  emit({...command,bridgeConnectionId:'expired'});await tick();assert.equal(f.runs,0)
  emit(command);emit(command);await tick();await tick()
  assert.equal(f.runs,1)
  assert.equal(socket.sent.find(v=>v.event==='result').promptId,'p1')
  bridge.stop();emit({...command,requestId:'after-close'});await tick();assert.equal(f.runs,1)
})

test('cancelled unresolved work frees the command queue and late completion is ignored', async () => {
  let resolveOld;const values=[]
  const queue=new CommandQueue(command=>command.commandId==='old'?new Promise(resolve=>{resolveOld=resolve}):'new result',(command,value,error)=>values.push({id:command.commandId,value,error}))
  queue.enqueue({commandId:'old'});await tick();queue.enqueue({commandId:'new'});queue.cancel('old');await tick()
  assert.equal(values[1].value,'new result')
  resolveOld('stale');await tick();assert.equal(values.length,2);queue.close()
})

test('abandoned native editor mutations reject newer work promptly until the mutation settles', async () => {
  const editor=new EditorOperations();let release
  const old=editor.mutate(()=>new Promise(resolve=>{release=resolve}),()=>{})
  assert.throws(()=>editor.checkIdle(),/earlier command/)
  release();await old;assert.doesNotThrow(()=>editor.checkIdle())
})
