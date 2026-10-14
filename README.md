# ComfyUI Anim Bridge

ComfyUI Anim Bridge lets Anim discover the workflows that are currently open
in the ComfyUI frontend. It reports each open tab, workflow revision, Builder
User Inputs, and output nodes to the same ComfyUI server.

It does not install a video model or run generation by itself. Anim uses it
because the standard ComfyUI API does not expose the list of workflows open in
browser tabs. Immediately before generation, the Bridge writes Anim's prompt
and uploaded media file names into the mapped widgets in the selected open
workflow. This makes the exact inputs visible in ComfyUI before Anim submits
the same graph to `/prompt`.

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
4. If the workflow needs the Sequence's clip duration, add **Anim Duration
   Input** from `Anim / Inputs`. Connect its `duration` FLOAT output to the
   workflow component that consumes it, such as a length or frame-count
   calculation.
5. If the Storyboard may send image Assets, add **Anim Image References**.
   Set `max_references` to the maximum number accepted by this workflow. Its
   `images` output is a ComfyUI IMAGE list in the same order as the Asset
   references shown in Anim. Connect it to the workflow component that accepts
   the reference image list.
6. For video or audio Assets, add **Anim Video References** or **Anim Audio
   References**, set each `max_references`, and connect the ordered
   `file_names` output to the matching loader contract in your workflow.
7. Keep a save or output node that produces the file Anim should collect.
8. To name output files after the Storyboard Sequence, add **Anim Sequence
   Output** from `Anim / Inputs`. Convert the save node's `filename_prefix`
   widget to an input, such as on `Save Video` or `Video Combine`, and connect
   the Anim node's `filename_prefix` output to it.
9. Run the workflow once, keep its browser tab open, then refresh and select it
   in Anim.

Model, resolution, aspect ratio, frame count, FPS, sampler, seed, and output
configuration stay in the ComfyUI workflow. Anim changes only the explicit
Anim input node types above or legacy Builder User Inputs.

When generation starts, Anim first opens the selected ComfyUI workflow tab and
fills its mapped prompt, image, video, and audio widgets. `Anim Image
References` redraws its ordered image cards from the received paths. Anim then
reads the published workflow back and checks every mapped value. If any widget
is missing or does not show the exact value, generation stops without sending
the workflow to `/prompt`. Runtime prompt and reference values do not count as
a workflow structure revision, so one Sequence's visible inputs do not make
the next Sequence look stale.

### MiniMax H3 reference-to-video nodes

For a native MiniMax H3 reference-to-video workflow, keep using the shared
**Anim Image References**, **Anim Video References**, and **Anim Audio
References** input nodes. Add one **Anim MiniMax H3 Reference to Video** node
and connect each shared node's `references` output to the matching
`ref_images`, `ref_videos`, or `ref_audios` input.

Reconnect the same stock `CLIP`, video `VAE`, audio `VAE`, prompt, width,
height, and length inputs. Keep the H3 node's `positive` and `LATENT` outputs
connected to the existing guider and sampler. The H3 node loads video frames,
uses each video's embedded soundtrack when present, and loads standalone audio
references. Other loaders, samplers, decoders, and output nodes do not change.

The shared network Reference nodes accept and preserve up to 100 ordered file
paths. The MiniMax H3 adapter enforces the model limits of 9 images, 3 videos,
and 3 standalone audio references and never truncates extras. When connected,
the Bridge reports those effective capacities to Anim so oversized requests
are blocked before upload.

### MiniMax H3 Fused Mystic video workflows and dependencies

Two reference-to-video templates adapt the [source article's attached
Ref2VA workflow](https://note.com/synth_brain/n/naecc37369fde) to Anim inputs:

| Workflow | Sampling | Default output |
| --- | --- | --- |
| [`minimax_h3_fused_mystic_fast4_anim.json`](workflows/minimax_h3_fused_mystic_fast4_anim.json) | 4 steps; upscale branch bypassed | 352×608, 24 FPS |
| [`minimax_h3_fused_mystic_hires4plus4_anim.json`](workflows/minimax_h3_fused_mystic_hires4plus4_anim.json) | 4 steps + learned 2× latent upscale + 4 steps at denoise 0.35 | 704×1216, 24 FPS |

Both use Fused Turbo INT8 with Mystic V2 0.7, 4B ClipProj, Spectrum,
`res_multistep` / `simple`, and sigma shift 12/3. Do not add another Turbo
LoRA: the diffusion model already includes Turbo. Duration defaults to 5
seconds; H3 frame alignment produces 124 frames at that setting. The seed is
fixed, and no RIFE frame interpolation is used.

#### Dependencies

Use ComfyUI v0.37.2 or later with native MiniMax H3 and INT8 ConvRot support,
and Anim Bridge 11. Install these custom nodes, then restart ComfyUI and
reload its frontend:

| Custom nodes | Used by |
| --- | --- |
| [ComfyUI-ClipProj](https://github.com/nicolab28/ComfyUI-ClipProj) | `ClipProjApply` |
| [ComfyUI-Spectrum-MiniMax-H3](https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3) | `SpectrumApplyMiniMaxH3` |
| [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) | resolution, math, FFN chunking and SageAttention patch |
| [ComfyUI-MiniMaxH3_LatentUpscaler](https://github.com/Tr1dae/ComfyUI-MiniMaxH3_LatentUpscaler) | H3 fast VAE decoder, latent upscale and sharpening |
| [ComfyUI-H3-Latent-Upscaler-Mamad8](https://github.com/mamad8c/ComfyUI-H3-Latent-Upscaler-Mamad8) | learned upscaler architecture dependency |

Both JSON files contain the upscale branch, so install its node packs even
when using Fast4. Install a [SageAttention](https://github.com/thu-ml/SageAttention)
build compatible with your PyTorch/CUDA environment for the included `auto`
attention patch; bypass that patch to use ComfyUI's configured attention
backend if SageAttention is unavailable. Compilation is disabled.

Download model weights into the following folders relative to `ComfyUI/`:

| Folder | File | Download source |
| --- | --- | --- |
| `models/diffusion_models/` | `minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors` | [MATLOWAI Fused Turbo INT8](https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot) |
| `models/text_encoders/` | `qwen3vl_4b_fp8_scaled.safetensors` | [Comfy-Org Krea-2](https://huggingface.co/Comfy-Org/Krea-2) |
| `models/clip_projections/` | `mmh3-4b-ClipProj-v3.1-mlp.safetensors` | [NicoLab28 ClipProj](https://huggingface.co/NicoLab28/ClipProj-MiniMax-H3) |
| `models/vae/` | `minimax_h3_video_vae_int8_convrot.safetensors` | [Kijai experimental H3](https://huggingface.co/Kijai/MiniMax-H3-experimental) |
| `models/vae/` | `minimax_h3_audio_vae_fp32.safetensors` | [Comfy-Org MiniMax H3](https://huggingface.co/Comfy-Org/MiniMax-H3) |
| `models/h3_latent_upscalers/` | `h3_clean_latent_upscaler_film_epoch200.safetensors` | [Tridae H3LatentUpscaler](https://huggingface.co/Tridae/H3LatentUpscaler) |

The learned upscaler weight is needed when enabling the HiRes branch. Audio
VAE remains required even without audio references because H3 generates
audio/video latents together.

The templates use FFN chunk 4 / threshold 4096, standard CLIP offloading,
and the article's stock H3 VAE decode for the primary output. The HiRes
decoder uses 256-pixel tiles with overlap 64 and CPU image output.
Ensure your ComfyUI core contains [the official H3 tile-composition fix
in PR #16436](https://github.com/Comfy-Org/ComfyUI/pull/16436): older raw-neighbour
blending can produce a grid at tile intersections. Check the implementation
or commit history rather than relying only on the version label.
Spectrum blend is 0.3 with additional offline
replay disabled. Spectrum uses approximate denoising; if motion artifacts
persist, compare the same seed with only Spectrum bypassed.

Open a template manually in ComfyUI and provide images through **Anim Image
References**, a prompt through **Anim Prompt Input**, and optional audio
through **Anim Audio References**. Saved prompt and image paths are blank
so the templates do not depend on another user's local input files. Select
the open video workflow in Anim; the installed image sample API intentionally
does not list video templates. **Anim Duration Input** controls clip length,
and **Anim Sequence Output** supplies the output prefix.

The graphs and dependency selections were checked against a local ComfyUI
v0.37.2 installation. Video generation was not run, so runtime, peak VRAM,
and motion quality are not benchmarked; neither template promises a
particular generation time.

### Prompt node contract

`Anim Prompt Input` is the recommended prompt contract. Anim recognizes it by
its fixed node type, so it does not guess from the workflow's node names. Its
STRING output can feed `CLIP Text Encode.text` or another prompt-processing
node. Do not connect it to the `clip` model socket on `CLIP Text Encode`.

For backward compatibility, a normal text widget exposed as a Builder User
Input is still supported. The exact widget must be the prompt string field,
such as `CLIPTextEncode.text`, not a model, conditioning, or CLIP socket.

### Duration node contract

`Anim Duration Input` receives the Sequence's clip duration, in seconds, as a
FLOAT widget. Anim recognizes it by its fixed node type. Connect its
`duration` output to whichever downstream node in the workflow turns duration
into a model parameter, such as a frame-count or length calculation. The node
does not enforce a duration range beyond the widget's own `min`/`max`; the
workflow decides what values are valid for the selected model.

### Sequence output name contract

`Anim Sequence Output` receives one output file name prefix through its
`filename_prefix` STRING widget and returns the same STRING. Anim builds the
prefix from the Sequence number and name for every run, for example
`S001_Opening` or `S012_거실_공놀이`, and removes characters that are unsafe in
file names or that ComfyUI treats specially, such as `/`, `\`, `:`, and `%`.
The save node then adds its usual counter, so repeated runs of the same
Sequence produce `S012_거실_공놀이_00001.mp4`, `S012_거실_공놀이_00002.mp4`, and
so on.

The node never saves a file itself; the workflow's existing save node still
decides the format and location. Keep the workflow a shared template: do not
type a Sequence name into the save node, because Anim replaces this widget at
execution time. When the node runs without a value from Anim, it returns
`ComfyUI`, the stock save-node default.

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

### Ordered reference image inputs

Use `Anim Image Input` with `image_id` set to `reference_1` through
`reference_8` for image generation. Anim fills them in the order chosen by the
user or agent, regardless of Asset category. Each node receives one uploaded
ComfyUI filename and outputs IMAGE and MASK. Empty inputs output None and are
cleared on every run. Missing files raise an error.

Each image ID must be unique across both required and optional image node
classes. Duplicate IDs block generation. The Bridge reports encoder socket
positions as `promptToken`; Anim numbers only the filled sockets when composing
the final prompt, matching Qwen's treatment of empty images.

Legacy semantic image IDs remain accepted for compatibility with edit inputs.
`image` is the required source of an image edit; `Anim Optional Image Input`
with `reference_image` remains the optional visual edit reference. These edit
exceptions do not define the reference list used for new image generation.

### Resolution input contract

`Anim Resolution Input` receives the output image size selected in Anim
through its `width` and `height` INT widgets and returns both as INT. Connect
them to the latent size of the workflow, such as `Empty Latent Image`. The
default is 2048×1152 (16:9, 2K). Each side must be between 256 and 4096 and a
multiple of 32, as Qwen-Image-2.1 requires; any other value stops the run
instead of being resized silently.

### Role text input contract

`Anim Text Input` receives one text for a fixed role. Its `text_id` widget is
a dropdown with five values only:

- `scene`: written per Sequence (action, pose, expression, camera, lighting)
- `character_appearance`: stored on the character Asset (face traits, hair,
  makeup) and filled by Anim in every Sequence that uses that character
- `character_appearance_2`, `character_appearance_3`: the same for the second
  and third character; an empty one drops its sentence from the template
- `prompt`: the required image-edit instruction, for example “Make the sky
  warmer but keep every person unchanged.”

Anim writes the text into the node's `text` STRING widget, and the node
returns it unchanged. The Bridge publishes `slotId` and `duplicateSlotId` like
the image input; two text nodes with the same role block generation. Keep
`Anim Prompt Input` for workflows that take a single prompt.

### Qwen prompt compose contract

`Anim Qwen Prompt Compose` combines the two texts into the one prompt that
`TextEncodeQwenImage21` accepts. Connect `scene` (required) and
`character_appearance` (optional) from the text inputs, and its `prompt`
output to the encoder's `prompt`. The `template` widget defaults to:

```text
{scene}
```

- `{scene}` and `{character_appearance}` become the received texts.
- `{character}`, `{outfit}`, and `{location}` become the `<imageN>` token of
  the matching `Anim Image Input` on the Qwen encoder this node feeds, so the
  appearance sentence always points at the character image even when it is
  not wired to `images.image_1`. A role in the template that is not wired to
  that encoder stops the run.
- An empty `character_appearance` removes the whole sentence that contains it
  instead of leaving `: .` behind. An empty `scene` stops the run.

### Qwen-Image-2.1 generation workflow

`workflows/qwen21_firstframe_anim.json` runs Qwen-Image-2.1 and saves its output
directly. It exposes eight generic optional references, a scene prompt, output
resolution and filename prefix. The compose template is `{scene}`. There is no
Krea2 or H3 face refinement stage.

It requires ComfyUI v0.37.0 or later and the `Comfy-Org/Qwen-Image-2.1`
models `qwen_image_2.1_int8_convrot`, `qwen3vl_8b_int8_convrot`, and
`qwen_image_2.1_vae_bf16`, plus Anim Bridge 10 for ordered reference IDs.

### Krea2 image-edit workflow

`workflows/krea2_image_edit_anim.json` adapts the upstream Krea2 Identity Edit
v1.2 workflow for Anim. It exposes the three content inputs needed by
keyframe editing:

| Anim node | Connected to |
| --- | --- |
| `Anim Image Input` `image` | Required Krea2 source VAE, source patch, and grounded encoder |
| `Anim Optional Image Input` `reference_image` | Optional second VAE, source patch, and grounded-encoder reference |
| `Anim Text Input` `prompt` | `Krea2EditGroundedEncode` `prompt` |
| `Anim Sequence Output` | `Save Image` `filename_prefix` |

The image being edited and the prompt are required. The additional reference
image is optional; its node and optional VAE encoder pass no second reference
into Krea2 when empty. Keyframe editing preselects the current keyframe as the
required source. The workflow
uses Krea2 Turbo plus the Identity Edit v1.2 LoRA and requires the upstream
`lbouaraba/comfyui-krea2edit` custom nodes and their documented model files.
Requires Bridge 9.

### Video and audio reference array contracts

`Anim Video References` and `Anim Audio References` use the same ordered JSON
array and capacity contract. They return a STRING list of ComfyUI input file
names instead of decoding the media, because video and audio node packs use
different runtime datatypes and loader APIs. Connect `file_names` to the loader
or adapter expected by the selected workflow. Normal ComfyUI list processing
handles one file at a time; a custom downstream node that needs the whole array
in one call must implement `INPUT_IS_LIST = True`.

`Anim Audio References` accepts an empty array because many Sequences have
no dialogue or voice timbre. It then returns an empty `file_names` list and an
empty `references` bundle, and **Anim MiniMax H3 Reference to Video** runs
without audio references. A loader connected to `file_names` receives no
files, so that branch must handle "no audio" itself.

`Anim Image References` and `Anim Video References` still reject an empty
array. If one of them is optional for some Sequences, route that branch
through the workflow's own switch/bypass logic, or keep a separate workflow
open and select it in Anim. The Bridge never invents a placeholder image,
video, or audio file because that would silently change the generated result.

## Running a workflow from its tab

Bridge 6 adds `POST /anim_bridge/v1/queue` with `sessionId`, `tabId`, and
`workflowId`. The open ComfyUI tab runs the workflow through its own Run
path (`app.queuePrompt`), exactly as if the Run button were pressed, and
reports the queued prompt id. Anim reads it from
`GET /anim_bridge/v1/command_result?command_id=...`, which answers
`{"status": "pending"}` until the tab reports
`{"status": "done", "promptId": "...", "error": ""}`. Anim uses this for image
workflows so the prompt is the one ComfyUI itself builds, then reads the
result from `/history`.

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
{"bridgeVersion": 11, "status": "ok"}
```

## Update

For a Git installation, run `git pull` inside the `comfyui_anim_bridge`
folder, then restart ComfyUI and reload its browser tabs.

## License

No open-source license has been granted yet. Copyright (c) LazyDeveloper.

## Ordered Qwen image references (Bridge 10)

`workflows/qwen21_firstframe_anim.json` now generates and saves directly with
Qwen-Image-2.1. It no longer uses Krea2, H3 face tracking, or face stitching.
Use `reference_1` through `reference_8` on Anim Image Input nodes, in the order
chosen in Anim. Empty inputs return no image and are cleared on every run.
The scene prompt may use `{reference_1}`, etc.; Anim resolves those to the
encoder image tokens. Categories such as character, wardrobe or background
are notes for the planner, not runtime slots. Legacy edit nodes remain supported.
Required models: `qwen_image_2.1_int8_convrot.safetensors`,
`qwen3vl_8b_int8_convrot.safetensors`, `qwen_image_2.1_vae_bf16.safetensors`.

### Anim generation modes

With Bridge 11, Anim reads image samples directly from this installed custom
node's `workflows/` folder. Select ComfyUI in Anim, reload the list, and choose an
installed sample. You do not need to open that sample beforehand. Keep one
ComfyUI browser with the Bridge enabled open: it imports the sample as a new
workflow and shares the resulting executable graph. Existing tabs and edits stay
intact. The picker also includes workflows already open in ComfyUI. Only installed
image samples with an Anim scene input are cataloged; raw and video examples are
not image-generation choices.

| Template | Inputs |
| --- | --- |
| `qwen21_text2image_anim.json` | scene text, size, output prefix; no images |
| `qwen21_image_text2image_anim.json` | scene text and up to eight ordered images |
| `krea2_text2image_anim.json` | scene text, size, output prefix; no images |
| `krea2_image_text2image_anim.json` | scene text, required reference_1 and optional reference_2 |

The Krea2 text workflow uses the base Turbo model with eight sampling steps,
without the identity-edit LoRA. The image-guided workflow uses the same
`comfyui-krea2edit` extension and identity-edit LoRA as the existing edit template.
All four expose Anim text, resolution and output inputs. Change the selected
workflow to change generation mode. Remove references explicitly when switching
to a text-only workflow; Anim never discards them automatically.


### Installed sample API (Bridge 11)

- `GET /anim_bridge/v1/samples`: image sample catalog from this extension's local
  workflows folder, available without browser sessions.
- `POST /anim_bridge/v1/load_sample` with an allowlisted `workflowId`: queues an
  import to the most recently active live Bridge browser. It returns command,
  session and tab IDs. A browser is required for import/execution, not listing.
- `command_result` returns the imported `workflowId` or a load error. The app
  waits for its published graph before allowing generation.

Imports use ComfyUI's normal new-workflow loader and fresh graph IDs; original
sample files and existing browser workflows are not overwritten.
