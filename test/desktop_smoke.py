"""Opt-in real Desktop protocol probe (aiohttp and Pillow from ComfyUI venv).
Open bridge_reference_smoke.json in Desktop, save as bridge_native_reference_smoke.
Run: python test/desktop_smoke.py --count 30 --idle-seconds 1800
No model, cloud credentials or GPU inference is used by this fixture.
"""
import argparse
import asyncio
import hashlib
import io
import json
import time
import uuid
from pathlib import Path

import aiohttp
from PIL import Image

async def run(args):
    started = time.monotonic()
    report = {'workflow': args.workflow, 'count': args.count, 'receipts': [], 'completed': [], 'idleEvents': []}
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect(args.origin + '/anim_bridge/v1/events?role=anim') as ws:
            assert (await ws.receive_json())['bridgeVersion'] == 13
            listed = await ws.receive_json()
            assert all(set(w) == {'id', 'name'} for w in listed['workflows'])
            selected = next(w for w in listed['workflows'] if w['name'] == args.workflow)
            requests = {str(uuid.uuid4()): {'prompt': f'Bridge semantic request {i}', 'filenamePrefix': f'anim185-{i}-{uuid.uuid4().hex[:6]}'} for i in range(args.count)}
            for request_id, parameters in requests.items():
                await ws.send_json({'event': 'command', 'type': 'run', 'requestId': request_id, **selected, 'parameters': parameters})
            receipts, completed = {}, {}
            while len(receipts) < args.count or len(completed) < args.count:
                event = await ws.receive_json(timeout=40)
                assert not event.get('error'), event
                assert 'apiGraph' not in json.dumps(event) and 'nodeId' not in json.dumps(event)
                if event['event'] == 'result':
                    assert event['requestId'] not in receipts
                    receipts[event['requestId']] = event['promptId']
                if event['event'] == 'completed':
                    assert event['requestId'] not in completed
                    completed[event['requestId']] = event
            assert len(set(receipts.values())) == args.count
            for request_id, event in completed.items():
                assert receipts[request_id] == event['promptId']
                file = event['files'][0]
                assert file['filename'].startswith(requests[request_id]['filenamePrefix'])
                async with client.get(args.origin + '/view', params=file) as response:
                    assert response.status == 200
                    image = Image.open(io.BytesIO(await response.read())).convert('RGB')
                    expected = tuple(hashlib.sha256(requests[request_id]['prompt'].encode()).digest()[:3])
                    assert image.getpixel((0, 0)) == expected, (request_id, expected, image.getpixel((0, 0)))
            report['receipts'] = receipts
            report['completed'] = list(completed.values())
            report['runSeconds'] = round(time.monotonic() - started, 3)
            # Same ID must never execute twice, including after completion.
            duplicate = next(iter(requests))
            await ws.send_json({'event': 'command', 'type': 'run', 'requestId': duplicate, **selected, 'parameters': requests[duplicate]})
            while True:
                event = await ws.receive_json(timeout=5)
                if event['event'] == 'result':
                    assert 'Duplicate request ID' in event['error']; break
            await ws.send_json({'event': 'command', 'type': 'run', 'requestId': str(uuid.uuid4()), **selected, 'name': 'wrong identity', 'parameters': {'prompt': 'must not run'}})
            while True:
                event = await ws.receive_json(timeout=5)
                if event['event'] == 'result':
                    assert 'does not match' in event['error']; break
            print(json.dumps({'runs': args.count, 'verifiedDownloads': len(completed), 'seconds': report['runSeconds'], 'duplicateRejected': True, 'identityMismatchRejected': True}), flush=True)
            Path(args.report).write_text(json.dumps(report, indent=2))
            deadline = time.monotonic() + args.idle_seconds
            while time.monotonic() < deadline:
                try:
                    event = await ws.receive_json(timeout=min(60, deadline - time.monotonic()))
                    report['idleEvents'].append(event)
                except asyncio.TimeoutError:
                    pass
            report['idleSeconds'] = args.idle_seconds
            Path(args.report).write_text(json.dumps(report, indent=2))
            print(json.dumps({'idleSeconds': args.idle_seconds, 'events': len(report['idleEvents'])}), flush=True)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--origin', default='http://127.0.0.1:8188')
    p.add_argument('--workflow', default='bridge_native_reference_smoke')
    p.add_argument('--count', type=int, default=30)
    p.add_argument('--idle-seconds', type=int, default=0)
    p.add_argument('--report', default='/tmp/anim185-desktop-smoke.json')
    asyncio.run(run(p.parse_args()))
