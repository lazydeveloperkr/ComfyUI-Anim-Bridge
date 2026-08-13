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
3. In Builder mode, expose the positive prompt widget as a text User Input.
4. Expose each image, video, or audio loader that Anim may fill as a User
   Input.
5. Keep a save or output node that produces the file Anim should collect.
6. In Anim, refresh the workflow list and select the open workflow.

Model, resolution, aspect ratio, frame count, FPS, sampler, seed, and output
configuration stay in the ComfyUI workflow. Anim changes only the declared
User Inputs.

## Media input capacity

Each declared media input accepts one reference by default. A Builder input
that intentionally accepts a list may declare a larger `animCapacity` value in
its input metadata. Anim validates image, video, and audio counts before
submitting the workflow and does not silently omit extra references.

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
{"bridgeVersion": 1, "status": "ok"}
```

## Update

For a Git installation, run `git pull` inside the `comfyui_anim_bridge`
folder, then restart ComfyUI and reload its browser tabs.

## License

No open-source license has been granted yet. Copyright (c) LazyDeveloper.
