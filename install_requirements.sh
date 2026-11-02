#!/usr/bin/env bash
# Install the MiniMax H3 latent-upscaler custom node and its model files.
# Usage: install_requirements.sh COMFYUI_ROOT [--with-mystic07]
set -euo pipefail

usage() {
  echo "Usage: install_requirements.sh COMFYUI_ROOT [--with-mystic07]" >&2
  echo "  COMFYUI_ROOT    ComfyUI folder that contains custom_nodes/ and models/" >&2
  echo "  --with-mystic07 also download the optional ~20 GB derivative diffusion model" >&2
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 1
fi

ROOT=$1
WITH_MYSTIC=0
if [[ $# -eq 2 ]]; then
  if [[ $2 == "--with-mystic07" ]]; then
    WITH_MYSTIC=1
  else
    echo "ERROR: unknown option: $2" >&2
    usage
    exit 1
  fi
fi

if [[ ! -d "$ROOT" ]]; then
  echo "ERROR: ComfyUI root does not exist: $ROOT" >&2
  exit 1
fi
if [[ ! -d "$ROOT/custom_nodes" ]]; then
  echo "ERROR: missing custom_nodes directory under: $ROOT" >&2
  echo "Pass the ComfyUI folder that contains custom_nodes and models." >&2
  exit 1
fi
if [[ ! -d "$ROOT/models" ]]; then
  echo "ERROR: missing models directory under: $ROOT" >&2
  echo "Pass the ComfyUI folder that contains custom_nodes and models." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is not installed or not on PATH." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is not installed or not on PATH." >&2
  exit 1
fi
if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
  echo "ERROR: sha256sum or shasum is required to verify downloads." >&2
  exit 1
fi

file_size() {
  wc -c < "$1" | tr -d '[:space:]'
}

file_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "ERROR: sha256sum or shasum is required to verify downloads." >&2
    return 1
  fi
}

# Download to dest.partial, verify, then rename. An existing nonempty file is left as-is.
download() {
  local url=$1 dest=$2 expect_size=$3 expect_sha=$4
  local dest_dir tmp size sha
  dest_dir=$(dirname "$dest")
  mkdir -p "$dest_dir"
  if [[ -f "$dest" ]]; then
    size=$(file_size "$dest")
    if [[ $size != "0" ]]; then
      echo "skip existing $(basename "$dest")"
      return 0
    fi
    echo "replacing empty file $(basename "$dest")"
  fi
  tmp="${dest}.partial"
  rm -f "$tmp"
  echo "downloading $(basename "$dest")"
  if ! curl -fL --retry 3 --retry-delay 2 -o "$tmp" "$url"; then
    rm -f "$tmp"
    echo "ERROR: download failed: $url" >&2
    echo "Nothing was written to $dest. Re-run this script to retry." >&2
    return 1
  fi
  size=$(file_size "$tmp")
  if [[ $size == "0" || $size != "$expect_size" ]]; then
    rm -f "$tmp"
    echo "ERROR: size mismatch for $(basename "$dest"): expected ${expect_size} bytes, got ${size}." >&2
    echo "The file was not installed. Re-run this script to retry." >&2
    return 1
  fi
  sha=$(file_sha256 "$tmp")
  if [[ $sha != "$expect_sha" ]]; then
    rm -f "$tmp"
    echo "ERROR: checksum mismatch for $(basename "$dest")." >&2
    echo "  expected $expect_sha" >&2
    echo "  got      $sha" >&2
    echo "The file was not installed. Re-run this script to retry." >&2
    return 1
  fi
  mv -f "$tmp" "$dest"
  echo "installed $(basename "$dest")"
}

NODE_URL="https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git"
NODE="$ROOT/custom_nodes/Comfyui_Minimax_h3_latent_Upscaler"
if [[ -d "$NODE/.git" ]]; then
  echo "skip existing custom node Comfyui_Minimax_h3_latent_Upscaler"
elif [[ -e "$NODE" ]]; then
  echo "ERROR: $NODE already exists and is not a git clone." >&2
  echo "This script will not overwrite it. Move it aside and re-run." >&2
  exit 1
else
  echo "cloning Comfyui_Minimax_h3_latent_Upscaler"
  if ! git clone --depth 1 "$NODE_URL" "$NODE"; then
    rm -rf "$NODE"
    echo "ERROR: git clone failed. Check network access and that git can write to:" >&2
    echo "  $NODE" >&2
    exit 1
  fi
fi

# Sizes and SHA256 are the Hugging Face x-linked-size / x-linked-etag values.
# The upscaler file was renamed upstream; these bytes are the workflow's
# minimax_h3_latent_upscaler_3d_fp16.safetensors checkpoint.
download \
  "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" \
  "$ROOT/models/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" \
  1956193000 \
  "5b9ab5ade15d0775676d01a907268a69a1468dc6033b3b0d3ded5502f3ebb84c"

download \
  "https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler/resolve/main/minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors" \
  "$ROOT/models/latent_upscale_models/minimax_h3_latent_upscaler_3d_fp16.safetensors" \
  690592672 \
  "043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2"

download \
  "https://huggingface.co/Comfy-Org/Frame-Interpolation/resolve/main/frame_interpolation/rife_v4.26.safetensors" \
  "$ROOT/models/frame_interpolation/rife_v4.26.safetensors" \
  22674688 \
  "151874592c877740e5db11522f4514df569eeafb0a0fcb2696f16e9e8d317c94"

if [[ $WITH_MYSTIC -eq 1 ]]; then
  download \
    "https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot/resolve/main/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors" \
    "$ROOT/models/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors" \
    20980178976 \
    "4262e4e9963c553fa00016bbe83961407a4fc0a888be95fd836c8d4f2304e48b"
fi

echo "Done. Restart ComfyUI so the new custom node is loaded."
