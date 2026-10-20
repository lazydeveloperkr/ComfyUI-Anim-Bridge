"""Opt-in end-to-end check against a running ComfyUI with Anim Bridge.

Not part of the unit suites. Run it after opening
test/fixtures/workflows/bridge_storyboard_cpu_smoke.json in a ComfyUI tab:

    python test/integration/desktop_smoke.py --url http://127.0.0.1:8188

It plays Anim's side of the protocol and checks, on the real browser tab:
1. starting from another active tab, inspect, apply inputs and run the
   selected workflow (not the previous tab's graph) and receive its prompt ID,
   completion event and one /history read containing the saved PNG;
2. a tab that cannot receive commands fails in about 5 s with a clear error
   and disappears from the workflow list (it used to wait 30 s silently);
3. a tab running an older browser script is refused immediately;
4. a real tab whose connection was dropped reconnects by itself.
It uses only aiohttp, which ships with ComfyUI.
"""
import argparse
import asyncio
import json
import time
import uuid

import aiohttp

WORKFLOW_TITLE = 'bridge_storyboard_cpu_smoke'


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


async def fake_browser(http, base, session, tab, client, workflow):
    socket = await http.ws_connect(f'{base}/anim_bridge/v1/events?role=browser'
                                   f'&session_id={session}&tab_id={tab}&client={client}')
    await socket.send_json({'event': 'metadata', 'workflows': [
        {'workflowId': workflow, 'title': workflow, 'isActive': True}]})
    await asyncio.sleep(.2)
    return socket


def check(condition, label):
    print(('PASS ' if condition else 'FAIL ') + label)
    if not condition:
        raise SystemExit(1)


async def main(base):
    async with aiohttp.ClientSession() as http:
        real = next((item for item in await listed(http, base) if item['title'] == WORKFLOW_TITLE), None)
        check(real is not None, f'{WORKFLOW_TITLE} is open in a ComfyUI tab')
        target = (real['sessionId'], real['tabId'], real['workflowId'])
        other = next((item for item in await listed(http, base) if item['tabId'] == real['tabId']
                      and item['workflowId'] != real['workflowId']), None)
        if other:
            # Put another tab in front so the run must switch tabs first.
            await command(http, base, 'refresh', (other['sessionId'], other['tabId'], other['workflowId']))
        inputs = {'2.prompt': 'smoke prompt', '3.duration': '5', '4.filename_prefix': 'S900_BridgeSmoke'}

        elapsed, result = await command(http, base, 'refresh', target)
        names = {(item['nodeId'], item['inputName']) for item in result['snapshot']['inputs']}
        check(elapsed < 2 and {('2', 'prompt'), ('3', 'duration'), ('4', 'filename_prefix')} <= names,
              f'inspection lists prompt, duration and filename inputs ({elapsed:.3f}s)')
        elapsed, result = await command(http, base, 'applyInputs', target, inputs=inputs)
        check(result.get('values', {}).get('4.filename_prefix') == 'S900_BridgeSmoke'
              and str(result['values'].get('3.duration')) in ('5', '5.0'),
              f'inputs applied and read back ({elapsed:.3f}s)')
        elapsed, done = await command(http, base, 'queuePrompt', target, wait_for_history=True, inputs=inputs)
        images = [image for output in done['history']['outputs'].values() for image in output.get('images', [])]
        ran = {node['class_type'] for node in done['history']['prompt'][2].values()}
        check(ran == {'LoadImage', 'AnimPromptInput', 'AnimDurationInput', 'AnimSequenceOutput',
                      'StringSubstring', 'StringConcatenate', 'SaveImage'},
              'the selected workflow ran, not the graph of the previously active tab')
        check(done['history']['status']['status_str'] == 'success'
              and any(image['filename'].startswith('S900_BridgeSmoke') for image in images),
              f'run completed with prompt ID {done["data"]["prompt_id"]} and a saved PNG ({elapsed:.3f}s)')

        dead = await fake_browser(http, base, 'smoke-dead', 'smoke-dead-tab', 13, 'workflows/dead.json')
        elapsed, result = await command(http, base, 'refresh', ('smoke-dead', 'smoke-dead-tab', 'workflows/dead.json'))
        check(elapsed < 8 and 'did not receive the command' in result.get('error', ''),
              f'unresponsive tab fails fast with a clear error ({elapsed:.1f}s)')
        check(all(item['tabId'] != 'smoke-dead-tab' for item in await listed(http, base)),
              'unresponsive tab is removed from the workflow list')
        await dead.close()

        stale = await fake_browser(http, base, 'smoke-old', 'smoke-old-tab', 0, 'workflows/old.json')
        elapsed, result = await command(http, base, 'refresh', ('smoke-old', 'smoke-old-tab', 'workflows/old.json'))
        check(elapsed < 1 and 'older Anim Bridge script' in result.get('error', ''),
              'tab with an older browser script is refused immediately')
        await stale.close()

        evict = await fake_browser(http, base, target[0], target[1], 13, 'workflows/evict.json')
        await evict.close()
        started = time.monotonic()
        while time.monotonic() - started < 10:
            await asyncio.sleep(.25)
            if any(item['tabId'] == target[1] and item['title'] == WORKFLOW_TITLE
                   for item in await listed(http, base)):
                break
        elapsed = time.monotonic() - started
        check(elapsed < 10, f'real tab reconnected by itself after its connection was dropped ({elapsed:.2f}s)')
        elapsed, result = await command(http, base, 'refresh', target)
        check('snapshot' in result, f'reconnected tab answers commands ({elapsed:.3f}s)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8188')
    asyncio.run(main(parser.parse_args().url.rstrip('/')))
