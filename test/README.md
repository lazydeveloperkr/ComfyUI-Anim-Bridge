# Tests

## Unit suites

```sh
npm test
python -m unittest discover -s test -p "test_*.py"
```

Run Python with the interpreter of a ComfyUI install (it provides `aiohttp`).

## CPU smoke workflows (real ComfyUI, no GPU)

These files check the whole Anim ↔ Bridge ↔ ComfyUI path with stock nodes only:
no checkpoint, CLIP, VAE, KSampler, model download or GPU inference. They are
test assets and are not listed as Anim samples.

| File | What it checks |
|---|---|
| `fixtures/workflows/bridge_cpu_smoke.json` | Anim image generation: `Anim Text Input` (prompt) and `Anim Sequence Output` are joined into the `Save Image` file name. |
| `fixtures/workflows/bridge_storyboard_cpu_smoke.json` | Storyboard Sequence: `Anim Prompt Input`, `Anim Duration Input` and `Anim Sequence Output` (file name `S001_…`). |
| `fixtures/workflows/bridge_storyboard_delay_smoke.json` | The storyboard workflow with a 60 second wait, for long runs. Needs the test-only delay node below. |
| `fixtures/assets/bridge_smoke.png` | The 64×64 input image every workflow loads. |
| `fixtures/custom_nodes/anim_bridge_test_delay/` | Test-only node that returns its image after N seconds. It is not part of the Bridge package. |

All workflows were saved from the ComfyUI editor, so they open as normal tabs.

### Set up

1. Install this Bridge in `ComfyUI/custom_nodes` (see the main README).
2. Copy `fixtures/assets/bridge_smoke.png` into the ComfyUI **input** folder
   (ComfyUI Desktop: the input folder chosen in its settings).
3. Copy the workflow JSON files into `ComfyUI/user/default/workflows/`.
4. Only for the 60 second workflow: copy
   `fixtures/custom_nodes/anim_bridge_test_delay` into `ComfyUI/custom_nodes`.
   Remove it again after testing.
5. Restart ComfyUI, then reload every ComfyUI browser tab.
6. In the ComfyUI **Workflows** sidebar, open the workflows you want to test
   and click each tab once so ComfyUI shares its graph with the Bridge.

### Check with Anim

Point Anim's ComfyUI setting at this ComfyUI (Localhost or the server
address), then:

- **Image:** Image generation → ComfyUI → refresh the list → choose
  `bridge_cpu_smoke` → *Check inputs* → write a short prompt → *Generate image*.
  The result is `Anim_Image_<prompt>_00001_.png` and appears in Anim.
- **Storyboard:** create a storyboard with one Sequence, choose
  `bridge_storyboard_cpu_smoke` (or the delay workflow) as the open ComfyUI
  workflow → *Check inputs* → approve → video generation → *Send and run*.
  The result is `S001_<name>_0000N_.png` and the Sequence is marked done.

Each run finishes in well under a second (60 s for the delay workflow) and
must never show `TimeoutException … Future not completed`.

### Automated check

With `bridge_storyboard_cpu_smoke` open and clicked once (so ComfyUI has
shared its graph):

```sh
python test/integration/desktop_smoke.py --url http://127.0.0.1:8188
```

It plays Anim's side of the protocol and checks inspection, input readback, a
run of exactly the selected workflow with its original prompt ID, the
completion event and one `/history` read with the saved PNG, an immediate
message for a workflow that is not open, and that a page still running an
older Bridge script is told to reload.

## Reading failures

The ComfyUI server log shows each tab and run:

```text
[Anim Bridge] ComfyUI tab opened session=… tab=…
[Anim Bridge] queued prompt <prompt id> for workflows/<name>.json in 0.005s
[Anim Bridge] ComfyUI tab closed session=… tab=…
```

The ComfyUI tab's console shows `[Anim Bridge] Publishing workflows failed`
when it could not reach the server.
