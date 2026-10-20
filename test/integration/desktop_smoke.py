"""Opt-in end-to-end check against a running ComfyUI with Anim Bridge.

Not part of the unit suites. Open
test/fixtures/workflows/bridge_storyboard_cpu_smoke.json in a ComfyUI tab
(click the tab once so it is the active one), then run:

    python test/integration/desktop_smoke.py --url http://127.0.0.1:8188

It plays Anim's side of the protocol and checks that the open workflow is
listed, inspected, given Anim's inputs and run by the ComfyUI server itself;
that the original prompt ID, the completion event and one /history read
with the saved PNG arrive; and that problems answer at once with a message
instead of a 30 second wait. It uses only aiohttp, which ships with ComfyUI.
"""
import argparse
import asyncio
import json
import time
import uuid

import aiohttp

WORKFLOW_TITLE = 'bridge_storyboard_cpu_smoke'
NODES = {'LoadImage', 'AnimPromptInput', 'AnimDurationInput', 'AnimSequenceOutput',
         'StringSubstring', 'StringConcatenate', 'SaveImage'}


async def listed(http, base):
    response = await http.get(base + '/anim_bridge/v1/workflows')
    return (await response.json())['workflows']


async def command(http, base, kind, target, wait_for_history=False, **extra):
    async with http.ws_connect(base + '/anim_bridge/v1/events?role=anim') as socket:
        command_id = str(uuid.uuid4())
        started = time.monotonic()
        await socket.send_json({'event': 'command', 'type': kind, 'commandId': command_id,
                                'sessionId': target[0], 'tabId': target[1],
                                'workflowId': target[2], **extra})
        prompt_id = None
        while True:
            message = json.loads((await socket.receive(timeout=40)).data)
            if message.get('event') == 'result' and message.get('commandId') == command_id:
                elapsed = time.monotonic() - started
                prompt_id = message.get('promptId')
                if not (wait_for_history and prompt_id):
                    return elapsed, message
                await socket.send_json({'event': 'watchPrompt', 'promptId': prompt_id})
            elif (message.get('event') == 'execution' and message['type'] == 'executing'
                  and message['data'].get('prompt_id') == prompt_id
                  and message['data'].get('node') is None):
                history = await (await http.get(f'{base}/history/{prompt_id}')).json()
                return time.monotonic() - started, {**message, 'history': history[prompt_id]}


def check(condition, label):
    print(('PASS ' if condition else 'FAIL ') + label)
    if not condition:
        raise SystemExit(1)


async def main(base):
    async with aiohttp.ClientSession() as http:
        rows = await listed(http, base)
        real = next((item for item in rows if item['title'] == WORKFLOW_TITLE), None)
        check(real is not None, f'{WORKFLOW_TITLE} is listed as an open ComfyUI workflow')
        target = (real['sessionId'], real['tabId'], real['workflowId'])
        inputs = {'2.prompt': 'smoke prompt', '3.duration': '5', '4.filename_prefix': 'S900_BridgeSmoke'}

        elapsed, result = await command(http, base, 'refresh', target)
        snapshot = result.get('snapshot') or {}
        names = {(item['nodeId'], item['inputName']) for item in snapshot.get('inputs', [])}
        check({('2', 'prompt'), ('3', 'duration'), ('4', 'filename_prefix')} <= names,
              f'inspection lists prompt, duration and filename inputs ({elapsed:.3f}s) {result.get("error", "")}')
        elapsed, result = await command(http, base, 'applyInputs', target, inputs=inputs)
        values = result.get('values', {})
        check(values.get('4.filename_prefix') == 'S900_BridgeSmoke' and values.get('3.duration') == 5,
              f'inputs are checked and read back ({elapsed:.3f}s)')
        elapsed, done = await command(http, base, 'queuePrompt', target, wait_for_history=True, inputs=inputs)
        history = done['history']
        images = [image for output in history['outputs'].values() for image in output.get('images', [])]
        ran = {node['class_type'] for node in history['prompt'][2].values()}
        check(ran == NODES, 'the selected workflow ran')
        check(history['status']['status_str'] == 'success'
              and any(image['filename'].startswith('S900_BridgeSmoke') for image in images),
              f'run finished with prompt ID {done["data"]["prompt_id"]}, completion event and saved PNG ({elapsed:.3f}s)')

        missing = (target[0], target[1], 'workflows/not-open.json')
        elapsed, result = await command(http, base, 'refresh', missing)
        check(elapsed < 1 and 'not open' in result.get('error', ''),
              f'a workflow that is not open fails at once with a message ({elapsed:.3f}s)')

        response = await http.get(base + '/anim_bridge/v1/events?role=browser&session_id=s&tab_id=t')
        check(response.status == 410, 'a page still running an older Bridge script is told to reload')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8188')
    asyncio.run(main(parser.parse_args().url.rstrip('/')))
