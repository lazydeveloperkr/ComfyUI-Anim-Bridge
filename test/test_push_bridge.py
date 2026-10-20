import asyncio
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('bridge_protocol', pathlib.Path(__file__).parents[1] / 'bridge_protocol.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Socket:
    def __init__(self): self.messages = []; self.closed = False
    async def send_json(self, message): self.messages.append(message)
    async def close(self): self.closed = True

class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.history_reads = []
        self.history_data = {}
        def history(prompt):
            self.history_reads.append(prompt)
            return self.history_data.get(prompt, {})
        self.bridge = module.PushBridge(history)
        self.browser, self.owner = Socket(), Socket()
        self.key = ('session', 'tab')
        await self.bridge.attach(self.key, self.browser)
        await self.bridge.publish(self.key, self.browser, [{'workflowId':'w','name':'W','apiGraph':{'huge':'graph'}}])
        self.selected = self.bridge.metadata()[0]
        self.bridge.clients.add(self.owner)

    async def request(self, request_id='r', **extra):
        payload = {'requestId':request_id,'type':'run',**self.selected,'parameters':{'prompt':'Scene'},**extra}
        task = asyncio.create_task(self.bridge.command(self.owner, payload, lambda _: None))
        await asyncio.sleep(0)
        return task

    async def test_metadata_is_only_name_id_and_checks_both(self):
        self.assertEqual(set(self.selected), {'id','name'})
        with self.assertRaisesRegex(ValueError,'does not match'):
            await self.bridge.command(self.owner, {'requestId':'r','type':'run',**self.selected,'name':'wrong','parameters':{}},lambda _:None)
        self.assertEqual(self.browser.messages, [])
        self.assertEqual(self.history_reads, [])

    async def test_completion_before_receipt_reads_history_once_and_never_relays_graph(self):
        task = await self.request()
        self.bridge.execution('p')
        self.history_data['p'] = {'p': {'status':{'completed':True},'outputs':{'3':{'images':[{'filename':'x.png','type':'output','apiGraph':'must not relay'}]}}}}
        self.bridge.result(self.browser,{'requestId':'r','applied':True,'promptId':'p','snapshot':{'huge':'graph'}})
        result = await task
        self.assertEqual(result,{'requestId':'r','applied':True,'promptId':'p'})
        await asyncio.gather(*self.bridge.finalizers)
        terminal = self.owner.messages[-1]
        self.assertEqual(terminal['files'],[{'filename':'x.png','type':'output'}])
        self.assertEqual(self.history_reads,['p'])
        self.bridge.execution('p')
        self.assertEqual(self.history_reads,['p'])
        await self.bridge.watch(self.owner,'p')
        self.assertEqual(self.owner.messages[-1],terminal)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            await self.bridge.command(self.owner,{'requestId':'r','type':'run',**self.selected,'parameters':{}},lambda _:None)

    async def test_error_before_receipt_survives_later_terminal_frame(self):
        task = await self.request()
        self.bridge.execution('p', 'Intentional Anim smoke failure')
        self.bridge.execution('p')
        self.bridge.result(self.browser, {'requestId': 'r', 'applied': True, 'promptId': 'p'})
        await task
        await asyncio.gather(*self.bridge.finalizers)
        self.assertEqual(self.owner.messages[-1]['error'], 'Intentional Anim smoke failure')
        self.assertEqual(self.history_reads, [])

    async def test_30_commands_correlate_out_of_order(self):
        tasks = [await self.request(str(i)) for i in range(30)]
        for i in reversed(range(30)):
            self.bridge.result(self.browser,{'requestId':str(i),'applied':True,'promptId':f'p{i}'})
        self.assertEqual([r['promptId'] for r in await asyncio.gather(*tasks)],[f'p{i}' for i in range(30)])
        self.assertEqual(self.history_reads,[])
        self.assertEqual(len(self.bridge.jobs),30)

    async def test_timeout_cancels_without_replay_and_late_receipt_is_ignored(self):
        payload={'requestId':'r','type':'run',**self.selected,'parameters':{}}
        with self.assertRaises(asyncio.TimeoutError):
            await self.bridge.command(self.owner,payload,lambda _:None,timeout=0.001)
        self.assertEqual(self.browser.messages[-1],{'event':'cancel','requestId':'r'})
        self.bridge.result(self.browser,{'requestId':'r','applied':True,'promptId':'late'})
        self.assertFalse(self.bridge.jobs)
        self.assertFalse(self.bridge.pending)

    async def test_disconnect_cancels_pending_and_new_connection_accepts_new_request(self):
        task=await self.request()
        await self.bridge.detach(self.key,self.browser)
        with self.assertRaises(ConnectionError): await task
        other=Socket();await self.bridge.attach(self.key,other)
        await self.bridge.publish(self.key,other,[{'workflowId':'w','name':'W'}])
        task=await self.request('new')
        self.bridge.result(other,{'requestId':'new','applied':True,'promptId':'p'})
        self.assertEqual((await task)['promptId'],'p')

    async def test_browser_disconnect_does_not_interrupt_native_completion(self):
        task=await self.request()
        self.bridge.result(self.browser,{'requestId':'r','applied':True,'promptId':'p'})
        await task
        await self.bridge.detach(self.key,self.browser)
        self.history_data['p']={'p':{'status':{'completed':True},'outputs':{'3':{'images':[{'filename':'x.png'}]}}}}
        self.bridge.execution('p')
        await asyncio.gather(*self.bridge.finalizers)
        self.assertEqual(self.owner.messages[-1]['event'],'completed')
        self.assertFalse(self.bridge.jobs)
        other=Socket();await self.bridge.attach(self.key,other)
        self.assertFalse(any(e['event']=='command' for e in other.messages))

    async def test_failure_and_unknown_watch_are_explicit_and_bounded(self):
        task=await self.request()
        self.bridge.result(self.browser,{'requestId':'r','applied':True,'promptId':'p'})
        await task;self.bridge.execution('p','Intentional failure')
        await asyncio.gather(*self.bridge.finalizers)
        self.assertEqual(self.owner.messages[-1]['error'],'Intentional failure')
        self.assertEqual(self.history_reads,[])
        await self.bridge.watch(self.owner,'unknown')
        self.assertIn('no longer tracks',self.owner.messages[-1]['error'])

    async def test_sample_identity_is_checked_and_graph_goes_only_to_browser(self):
        sample={'id':'anim-sample/qwen21_firstframe_anim.json','name':'qwen21_firstframe_anim.json'}
        task=asyncio.create_task(self.bridge.command(self.owner,{'requestId':'sample','type':'apply',**sample,'parameters':{'scene':'x'}},lambda _:{'nodes':[1]}))
        await asyncio.sleep(0)
        self.assertEqual(self.browser.messages[-1]['sample'],{'nodes':[1]})
        self.bridge.result(self.browser,{'requestId':'sample','applied':True})
        self.assertNotIn('sample',await task)
        with self.assertRaises(ValueError):
            await self.bridge.command(self.owner,{'requestId':'bad','type':'run',**sample,'name':'other','parameters':{}},lambda _:{'nodes':[1]})
