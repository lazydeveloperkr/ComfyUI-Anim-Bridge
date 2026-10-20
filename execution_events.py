"""Observe the existing ComfyUI completion event; never create another socket.

ComfyUI sends terminal executing(node=None) after committing prompt history.
The original dispatcher always receives the unchanged event, including previews.
Only a tiny terminal notification is scheduled onto the server's own event loop.
"""
from functools import wraps


def install_execution_observer(server, bridge):
    original = server.send_sync

    @wraps(original)
    def send_sync(event, data, sid=None):
        result = original(event, data, sid)
        if not bridge.pending and not bridge.jobs:
            return result
        if event not in ('executing', 'execution_error', 'execution_interrupted') or not isinstance(data, dict):
            return result
        if event == 'executing' and data.get('node') is not None:
            return result
        prompt_id = data.get('prompt_id')
        if isinstance(prompt_id, str) and prompt_id:
            error = '' if event == 'executing' else str(data.get('exception_message') or event)[:4096]
            try:
                server.loop.call_soon_threadsafe(bridge.execution, prompt_id, error)
            except RuntimeError:
                pass  # The ComfyUI event loop is shutting down.
        return result

    server.send_sync = send_sync
