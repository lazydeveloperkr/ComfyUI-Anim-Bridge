# Issue 185 validation

Implementation contract: https://github.com/lazydeveloperkr/anim/issues/185#issuecomment-6284019461

## Acceptance and approach

- Expose only workflow names and opaque IDs to Anim.
- Map semantic arguments privately in the selected native ComfyUI tab and run
  that tab. Return small receipts and terminal output descriptors over sockets.
- Remove periodic graph inspection, command retrieval and execution-status
  queries. Preserve input-only requests and legacy Qwen sample discovery.
- Bound queues, deadlines and caches; cancel disconnected work without replay;
  resume sockets after BFCache restoration.

## Completed locally on macOS

The following completed checks apply to the earlier Bridge-socket command
transport. They do not validate the subsequent native-event delivery change.

- `npm test`: 13 tests pass, including inactive-tab native loading, zero idle
  graph reads/timers, cancellation of unresolved work, and BFCache restore.
- ComfyUI venv Python `-m unittest discover -s test -p 'test_*.py'`: 68 tests
  pass. Covers 30 correlated requests, early completion, error precedence,
  disconnect, duplicate rejection and sample privacy.
- Installed ComfyUI Desktop 1.1.7 / ComfyUI 0.42.1 / frontend 1.55.18:
  `test/desktop_smoke.py --count 30` verified native queue receipts, correlated
  terminal events, downloaded PNG pixels and per-request filename prefixes;
  duplicate request and wrong workflow identity were rejected.
- Anim's Dart client/provider/manager probe completed 30 requests and downloads
  with zero automatic task-status queries. The production image-generation
  screen also completed a request and saved its returned image.

The real Desktop fixture is deliberately model-free and produces images. It
does **not** verify the requested video-generation flow, H3 model inference,
Windows memory behavior, or video download/playback. Those remain acceptance
work. The PR must stay draft until video integration is verified together with
the matching Anim changes. Final early-error precedence is covered by Python
tests; the running Desktop has not been restarted to load that last adjustment.

## Subsequent Windows diagnosis and delivery change

The user's Windows ComfyUI EZ Desktop reported frontend build `13-diag2` and
`command.sent`, followed by a timeout without a frontend acknowledgment. The
same records were then read directly from that server's existing internal log
endpoint; no generated request was sent for this diagnosis.

Frontend commands now use ComfyUI's native `anim_bridge_command` socket event
with an exact client ID, Bridge tab/session and per-connection identity. Bridge
socket uplink still carries metadata, diagnostics and receipts. Missing native
connection fails immediately, stale connection events are ignored, and duplicate
request IDs cannot execute twice. Python/JS coverage was updated for those
conditions, but **these new tests and real generation have not been run**, at the
user's explicit request to perform validation themselves. Frontend build is
`13-native1`; Windows video execution remains unconfirmed.

## Reproduce the image transport probe

The fixture is opt-in; it is never installed by normal Bridge startup.

1. Link `test/fixtures/custom_nodes/anim_bridge_smoke` into the test ComfyUI's
   custom_nodes directory, restart ComfyUI, and reload its frontend.
2. Open `test/fixtures/bridge_reference_smoke.json` and save the tab as
   `bridge_native_reference_smoke`.
3. Run `test/desktop_smoke.py --count 30 --report /tmp/bridge-185-report.json`
   with the ComfyUI Python environment (aiohttp and Pillow required).
4. Add `--idle-seconds 1800` for a separately recorded idle observation. This
   does not measure JavaScript heap or GPU-model memory peaks.
