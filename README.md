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

### Role image input contract

`Anim Image Input` receives exactly one Asset image for a fixed role. Its
`image_id` widget is a dropdown with three values only: `character`, `outfit`,
and `location`. Anim assigns one Asset to each role per Sequence and writes the
uploaded ComfyUI input file name into the node's `image` STRING widget. The
node shows a preview and an upload button, loads the file like `Load Image`,
and returns `image` (IMAGE) and `mask` (MASK).

- Use each role at most once per workflow. When two nodes share an `image_id`,
  the Bridge flags both with `duplicateSlotId: true` and Anim blocks
  generation instead of guessing which one to fill.
- An empty `image` or a file that is not in the ComfyUI input folder stops
  the run with a clear error. The Bridge never substitutes a placeholder.
- When the `image` output is wired to `TextEncodeQwenImage21`
  `images.image_N`, the Bridge publishes `promptToken: "<imageN>"`. Anim users
  write `{character}`, `{outfit}`, and `{location}` in the prompt, and Anim
  replaces each with the token of the matching node, so a prompt does not
  depend on which encoder slot a role is wired to. A role that does not reach
  the encoder has `promptToken: null`.

### Resolution input contract

`Anim Resolution Input` receives the output image size selected in Anim
through its `width` and `height` INT widgets and returns both as INT. Connect
them to the latent size of the workflow, such as `Empty Latent Image`. The
default is 2048×1152 (16:9, 2K). Each side must be between 256 and 4096 and a
multiple of 32, as Qwen-Image-2.1 requires; any other value stops the run
instead of being resized silently.

### Role text input contract

`Anim Text Input` receives one text for a fixed role. Its `text_id` widget is
a dropdown with two values only:

- `scene`: written per Sequence (action, pose, expression, camera, lighting)
- `character_appearance`: stored on the character Asset (face traits, hair,
  makeup) and filled by Anim in every Sequence that uses that character

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
The character from {character}: {character_appearance}. {scene}
```

- `{scene}` and `{character_appearance}` become the received texts.
- `{character}`, `{outfit}`, and `{location}` become the `<imageN>` token of
  the matching `Anim Image Input` on the Qwen encoder this node feeds, so the
  appearance sentence always points at the character image even when it is
  not wired to `images.image_1`. A role in the template that is not wired to
  that encoder stops the run.
- An empty `character_appearance` removes the whole sentence that contains it
  instead of leaving `: .` behind. An empty `scene` stops the run.

### Qwen-Image-2.1 first-frame workflow

`workflows/qwen21_firstframe_anim.json` is the Anim-driven version of
`workflows/qwen21_firstframe_3ref.json`:

| Anim node | Connected to |
| --- | --- |
| `Anim Image Input` `character` | `TextEncodeQwenImage21` `images.image_1` |
| `Anim Image Input` `outfit` | `TextEncodeQwenImage21` `images.image_2` |
| `Anim Image Input` `location` | `TextEncodeQwenImage21` `images.image_3` |
| `Anim Text Input` `scene`, `character_appearance` | `Anim Qwen Prompt Compose` |
| `Anim Qwen Prompt Compose` | `TextEncodeQwenImage21` `prompt` |
| `Anim Resolution Input` | `Empty Latent Image` `width`, `height` |
| `Anim Sequence Output` | `Save Image` `filename_prefix` |

It requires ComfyUI v0.37.0 or later and the `Comfy-Org/Qwen-Image-2.1`
models `qwen_image_2.1_int8_convrot`, `qwen3vl_8b_int8_convrot`, and
`qwen_image_2.1_vae_bf16`.

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
{"bridgeVersion": 5, "status": "ok"}
```

## Update

For a Git installation, run `git pull` inside the `comfyui_anim_bridge`
folder, then restart ComfyUI and reload its browser tabs.

## License

No open-source license has been granted yet. Copyright (c) LazyDeveloper.
