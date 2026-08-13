# ComfyUI Anim Bridge

ComfyUI Anim Bridge lets Anim discover the workflows that are currently open
in the ComfyUI frontend. It reports each open tab, workflow revision, Builder
User Inputs, and output nodes to the same ComfyUI server.

It does not install a video model, change a workflow, or run generation by
itself. Anim uses it because the standard ComfyUI API does not expose the list
of workflows open in browser tabs.

## Install in ComfyUI-Easy-Install on Windows

1. Close ComfyUI and EZi Desktop.
2. Open the `ComfyUI-Easy-Install\ComfyUI\custom_nodes` folder.
3. Download this repository as a ZIP and extract it there.
4. Rename the extracted folder to `comfyui_anim_bridge`.
5. Confirm the final layout matches the example below.
6. Start `ComfyUI-EZi.bat`, then reload every browser tab where ComfyUI is
   open.

```text
ComfyUI-Easy-Install\ComfyUI\custom_nodes\comfyui_anim_bridge\
  __init__.py
  web\anim_bridge.js
  web\input_contract.js
```

If Git is available, you can install it from a Windows terminal instead:

```bat
cd /d "YOUR_COMFYUI_EASY_INSTALL_FOLDER\ComfyUI\custom_nodes"
git clone https://github.com/lazydeveloperkr/ComfyUI-Anim-Bridge.git comfyui_anim_bridge
```

## Install in another ComfyUI distribution

Place this repository at `ComfyUI/custom_nodes/comfyui_anim_bridge`, restart
ComfyUI, and reload every open ComfyUI browser tab.

## Prepare a workflow for Anim

1. Run the workflow successfully in ComfyUI once.
2. Keep the workflow open in a ComfyUI browser tab.
3. Add **Anim Prompt Input** from `Anim / Inputs`. Connect its `prompt`
   output to the positive prompt input used by the workflow. For the built-in
   `CLIP Text Encode` node, convert its `text` widget to an input and connect
   `Anim Prompt Input` there.
4. If the Storyboard may send image Assets, add **Anim Image References**.
   Set `max_references` to the maximum number accepted by this workflow. Its
   `images` output is a ComfyUI IMAGE list in the same order as the Asset
   references shown in Anim. Connect it to the workflow component that accepts
   the reference image list.
5. For video or audio Assets, add **Anim Video References** or **Anim Audio
   References**, set each `max_references`, and connect the ordered
   `file_names` output to the matching loader contract in your workflow.
6. Keep a save or output node that produces the file Anim should collect.
7. Run the workflow once, keep its browser tab open, then refresh and select it
   in Anim.

Model, resolution, aspect ratio, frame count, FPS, sampler, seed, and output
configuration stay in the ComfyUI workflow. Anim changes only the explicit
Anim input node types above or legacy Builder User Inputs.

### Prompt node contract

`Anim Prompt Input` is the recommended prompt contract. Anim recognizes it by
its fixed node type, so it does not guess from the workflow's node names. Its
STRING output can feed `CLIP Text Encode.text` or another prompt-processing
node. Do not connect it to the `clip` model socket on `CLIP Text Encode`.

For backward compatibility, a normal text widget exposed as a Builder User
Input is still supported. The exact widget must be the prompt string field,
such as `CLIPTextEncode.text`, not a model, conditioning, or CLIP socket.

### Image reference array contract

`Anim Image References` receives uploaded ComfyUI input file names through its
hidden `image_paths` field, one file per line. Its card UI supports upload,
remove, and drag reorder while preserving the saved order. Anim replaces the
same field at execution time in Asset order. The node validates the paths
against `max_references`, loads every file,
and returns:

- `images`: an IMAGE list preserving Asset reference order
- `masks`: the matching MASK list
- `file_names`: the same STRING list of uploaded file names

The downstream workflow decides how that list is consumed. A downstream node
that uses ComfyUI list processing receives the references in order; a node that
needs the whole list in a single call must implement `INPUT_IS_LIST = True`.
This is different from an IMAGE batch: references can have different sizes and
remain separate list entries.

### Video and audio reference array contracts

`Anim Video References` and `Anim Audio References` use the same ordered JSON
array and capacity contract. They return a STRING list of ComfyUI input file
names instead of decoding the media, because video and audio node packs use
different runtime datatypes and loader APIs. Connect `file_names` to the loader
or adapter expected by the selected workflow. Normal ComfyUI list processing
handles one file at a time; a custom downstream node that needs the whole array
in one call must implement `INPUT_IS_LIST = True`.

Reference nodes intentionally reject an empty array. If a media reference is
optional for some Sequences, route that reference branch through the
workflow's own switch/bypass logic, or keep a separate text-only workflow open
and select it in Anim. The Bridge never invents a placeholder image, video, or
audio file because that would silently change the generated result.

## Media input capacity

The `max_references` field on each Anim Image, Video, or Audio References node
is the recommended capacity declaration. Anim reads each capacity separately
and blocks generation when a Sequence contains too many references of that
media type.

Legacy Builder media inputs accept one reference by default. Only a custom
input that truly parses a JSON array should declare a larger `animCapacity`.
Increasing capacity on the built-in `Load Image.image` widget does not make it
an array input. Anim validates image, video, and audio counts before submitting
the workflow and does not silently omit extra references.

## Network and security

The Bridge uses routes on the same ComfyUI server and follows that server's
network and origin policy. It does not add authentication and does not make a
private ComfyUI server safe to expose to the public internet.

Use a trusted LAN or VPN and allow the ComfyUI port only on the intended
private network. Do not forward an unauthenticated ComfyUI port directly to the
public internet.

## Health check

After installation, this ComfyUI route returns the Bridge status:

```text
/anim_bridge/v1/health
```

Expected response:

```json
{"bridgeVersion": 2, "status": "ok"}
```

## Update

For a Git installation, run `git pull` inside the `comfyui_anim_bridge`
folder, then restart ComfyUI and reload its browser tabs.

## License

No open-source license has been granted yet. Copyright (c) LazyDeveloper.
