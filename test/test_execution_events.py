import importlib.util
import pathlib
import types
import unittest
from unittest.mock import Mock
spec=importlib.util.spec_from_file_location('execution_events',pathlib.Path(__file__).parents[1]/'execution_events.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class ObserverTest(unittest.TestCase):
    def setUp(self):
        self.original=Mock(return_value='unchanged')
        self.loop=types.SimpleNamespace(call_soon_threadsafe=Mock())
        self.server=types.SimpleNamespace(send_sync=self.original,loop=self.loop)
        self.bridge=types.SimpleNamespace(pending={},jobs={},execution=Mock())
        module.install_execution_observer(self.server,self.bridge)

    def test_idle_preview_and_progress_never_schedule_or_copy_payloads(self):
        data={'large':'original payload'}
        for event in ('executing','progress','executed',1):
            self.assertEqual(self.server.send_sync(event,data,'sid'),'unchanged')
            self.assertIs(self.original.call_args.args[1],data)
        self.loop.call_soon_threadsafe.assert_not_called()

    def test_only_terminal_events_schedule_small_threadsafe_notifications(self):
        self.bridge.jobs['p']={}
        self.server.send_sync('executing',{'node':'1','prompt_id':'p'},'sid')
        self.server.send_sync('executed',{'prompt_id':'p','output':{'large':'image'}},'sid')
        self.loop.call_soon_threadsafe.assert_not_called()
        self.server.send_sync('executing',{'node':None,'prompt_id':'p','large':'ignored'},'sid')
        self.loop.call_soon_threadsafe.assert_called_once_with(self.bridge.execution,'p','')
        self.server.send_sync('execution_error',{'prompt_id':'p','exception_message':'failed','traceback':['large']},'sid')
        self.loop.call_soon_threadsafe.assert_called_with(self.bridge.execution,'p','failed')

    def test_pending_run_captures_fast_completion_and_shutdown_preserves_original_dispatch(self):
        self.bridge.pending['r']={}
        self.loop.call_soon_threadsafe.side_effect=RuntimeError('closed')
        self.assertEqual(self.server.send_sync('executing',{'node':None,'prompt_id':'p'}),'unchanged')
        self.original.assert_called_once()
