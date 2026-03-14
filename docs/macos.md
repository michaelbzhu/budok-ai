# Running YOMI Hustle LLM Arena on macOS

YOMI Hustle is only available for Windows and Linux on Steam. On macOS, we run the game headlessly inside an x86-64 Linux VM (via OrbStack) and connect it to the Python daemon running natively on the Mac.

## Progress

- [x] Step 1: Verify daemon works on Mac (408 tests passing)
- [x] Step 2: Create x86-64 Ubuntu VM in OrbStack (machine name: `ubuntu`, arch: amd64)
- [x] Step 3: Install VM dependencies (xvfb, mesa, x11 libs)
- [x] Step 4: Download game via DepotDownloader (SteamCMD crashes under emulation)
- [x] Step 4b: Copy game files to VM and verify bare game boots (confirmed working)
- [x] Step 5: Install the mod (packaged, pushed, extracted in VM)
- [x] Step 6: Configure networking (bridge IP: `192.168.139.3`, mod config updated)
- [x] Step 7: Start the daemon
- [x] Step 8: Launch the game headlessly (596-decision match completed to KO, baseline/random)
- [x] Step 9: Test replay video recording (713-turn match, 68s replay video, 3.2 MB H.264 1280x720@30fps)
- [x] Step 10: First LLM match (Claude Sonnet 4 vs greedy_damage, 204 decisions, 0 fallbacks, 5.6s avg latency)

## Architecture

```
┌─────────── macOS (your Mac) ───────────┐     ┌──── OrbStack amd64 VM ────┐
│                                         │     │                           │
│  Python daemon (yomi-daemon)            │◄────┤  YOMI Hustle + mod        │
│  listens on 0.0.0.0:8765               │ ws  │  (headless via Xvfb)      │
│                                         │     │                           │
│  LLM API calls (Anthropic/OpenAI)       │     │  tbfg.so ✓ (x86 Linux)   │
└─────────────────────────────────────────┘     └───────────────────────────┘
```

The daemon runs natively on Mac. The game runs headlessly in a Linux x86-64 VM. They connect via WebSocket.

### Why not run the decompiled game in Godot on Mac?

The game's simulation core is a compiled C++ library (`tbfg`) loaded via GDNative. It ships only as `tbfg.dll` (Windows) and `tbfg.so` (Linux x86-64). There is no `tbfg.dylib` for macOS. Without this library the game crashes immediately — it provides `GameSimulation`, `FixedMath`, `FGCharacter`, and `FGObject`, which are required for all gameplay.

### Why not the Godot headless/server build?

The Godot 3.x server build treats "server" as a distinct platform. GDNative libraries compiled for `X11.64` won't load on it. We need the standard x11 Godot binary (the game's own `Yomi_Hustle.x86_64`) with Xvfb providing a virtual display.

## Prerequisites

- macOS with Apple Silicon (for Rosetta 2 x86-64 translation)
- [OrbStack](https://orbstack.dev/) — install with `brew install --cask orbstack`, then open the app once to finish setup
- A Steam account that owns YOMI Hustle (App ID `2212330`)
- Python 3.12+ and `uv` (for the daemon)

## Step 1: Verify the daemon works on Mac

```bash
uv sync --project daemon
uv run --project daemon pytest
```

All 408 tests should pass. This confirms the daemon is healthy before setting up the VM.

## Step 2: Create an x86-64 Ubuntu VM

In OrbStack, create a new Linux machine: Ubuntu, version Jammy (22.04 LTS), architecture amd64 (x86 emulated). The default machine name will be `ubuntu`.

Verify it's running:

```bash
orb list                        # should show "ubuntu  running  ... amd64"
orb run -m ubuntu uname -m      # should print "x86_64"
```

This uses Rosetta 2 for x86-64 translation (~70-85% native speed — plenty for a turn-based game).

## Step 3: Install dependencies inside the VM

```bash
orb run -m ubuntu bash -c "\
  sudo apt-get update -qq && \
  sudo apt-get install -y \
    xvfb libgl1 mesa-utils unzip scrot ffmpeg \
    libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 \
    libgles2-mesa libegl1-mesa libgl1-mesa-dri \
    libpulse0 libasound2"
```

The `libpulse0` and `libasound2` packages prevent audio library load errors (the game falls back to a dummy audio driver regardless, but having the libs avoids noisy warnings). `scrot` is used for taking debug screenshots of the virtual display. `ffmpeg` is required for replay video recording.

## Step 4: Download the game files

**SteamCMD does not work** under x86-64 emulation on Apple Silicon — it crashes with `futex robust_list not initialized by pthreads`. Use [DepotDownloader](https://github.com/SteamRE/DepotDownloader) instead, which runs natively on macOS.

Install DepotDownloader:

```bash
brew tap steamre/tools
brew install depotdownloader
```

If macOS blocks it with a Gatekeeper warning, clear the quarantine flag:

```bash
xattr -d com.apple.quarantine /opt/homebrew/bin/depotdownloader
```

Download the Linux depot (depot `2232859`) to a local directory:

```bash
mkdir -p ~/yomi-steam
depotdownloader -app 2212330 -depot 2232859 -username YOUR_STEAM_USERNAME -dir ~/yomi-steam
```

This will prompt for your password and Steam Guard code. The download puts the Linux build files directly on your Mac.

Verify the key files exist:

```bash
ls ~/yomi-steam/YourOnlyMoveIsHUSTLE.x86_64 ~/yomi-steam/tbfg.so ~/yomi-steam/YourOnlyMoveIsHUSTLE.pck
```

## Step 4b: Copy game files to the VM and verify

Copy the downloaded game files into the VM. Note: `orb push` requires absolute paths for the destination (not `~`), and you must push files individually, not directories:

```bash
orb run -m ubuntu mkdir -p /home/$USER/games/yomi
for f in ~/yomi-steam/*; do [ -f "$f" ] && orb push -m ubuntu "$f" /home/$USER/games/yomi/; done
orb run -m ubuntu chmod +x /home/$USER/games/yomi/YourOnlyMoveIsHUSTLE.x86_64
```

Verify the key files are present:

```bash
orb run -m ubuntu ls /home/$USER/games/yomi/YourOnlyMoveIsHUSTLE.x86_64
```

Test that the bare game boots before adding the mod. Launch it into Xvfb (a virtual framebuffer — no visible window), wait 15 seconds, then take a screenshot:

```bash
orb run -m ubuntu bash -c '
export LIBGL_ALWAYS_SOFTWARE=1
export LD_LIBRARY_PATH=/home/'"$USER"'/games/yomi:$LD_LIBRARY_PATH

Xvfb :50 -screen 0 1280x720x24 &
sleep 2
DISPLAY=:50 /home/'"$USER"'/games/yomi/YourOnlyMoveIsHUSTLE.x86_64 &
sleep 15
DISPLAY=:50 scrot /tmp/yomi_screenshot.png
kill %2 2>/dev/null; kill %1 2>/dev/null
echo "Screenshot saved"
'
orb pull -m ubuntu /tmp/yomi_screenshot.png /tmp/yomi_screenshot.png
open /tmp/yomi_screenshot.png
```

You should see the YOMI Hustle main menu (version `1.9.20-steam`, "Mod List" in the bottom left). Expected non-fatal warnings in stderr:
- ALSA audio errors → game falls back to dummy audio driver
- `SteamAPI_Init` failures → game continues without Steam (`SteamHustle.STARTED` stays `false`)

**Important**: Do NOT use Godot's `--no-window` flag if you want to see the rendered output — it suppresses window creation and results in a black screenshot. Only use `--no-window` for truly headless operation where you don't need visual output.

**Important**: `LD_LIBRARY_PATH` must include the game directory so the linker can find `libsteam_api.so` and `tbfg.so`.

## Step 5: Install the mod

Package the mod on your Mac and push the **zip** into the VM's `mods/` directory:

```bash
scripts/package_mod.sh
orb run -m ubuntu mkdir -p /home/$USER/games/yomi/mods
orb push -m ubuntu dist/YomiLLMBridge.zip /home/$USER/games/yomi/mods/YomiLLMBridge.zip
```

**Important**: The ModLoader loads mods from `.zip` files via `ProjectSettings.load_resource_pack()`. It does NOT load loose directories. Only the zip file in `mods/` matters. The extracted directory is irrelevant to the loader.

Verify:

```bash
orb run -m ubuntu ls /home/$USER/games/yomi/mods/YomiLLMBridge.zip
```

## Step 6: Configure networking

The mod needs to connect to the daemon on your Mac. The daemon needs to accept connections from the VM.

**Daemon side** — bind to `0.0.0.0` so the VM can reach it:

```bash
uv run --project daemon yomi-daemon --host 0.0.0.0 --port 8765
```

**Mod side** — update `transport.host` in the mod config to point at the Mac's bridge interface IP. Find it with:

```bash
ifconfig bridge100 | grep "inet " | awk '{print $2}'
# Expected output: 192.168.139.3
```

**Note**: The default gateway IP from `ip route` inside the VM (`192.168.139.1`) may not work — use the Mac's `bridge100` interface IP instead.

Update the host in the mod source, repackage, and push the zip:

```bash
# On your Mac — temporarily set VM host, package, push, then restore default
sed -i '' 's/"host": "127.0.0.1"/"host": "192.168.139.3"/' mod/YomiLLMBridge/config/default_config.json
scripts/package_mod.sh
orb push -m ubuntu dist/YomiLLMBridge.zip /home/$USER/games/yomi/mods/YomiLLMBridge.zip
sed -i '' 's/"host": "192.168.139.3"/"host": "127.0.0.1"/' mod/YomiLLMBridge/config/default_config.json
```

The config must be baked into the zip because the ModLoader reads it via `res://` paths from the resource pack.

**Note**: The bridge IP (`192.168.139.3`) is stable across OrbStack VM restarts but may differ on your machine. Always check with `ifconfig bridge100` if connectivity fails.

## Step 7: Start the daemon on Mac

```bash
uv run --project daemon yomi-daemon --host 0.0.0.0 --port 8765
```

## Step 8: Launch the game headlessly in the VM

Use a fixed display (`:99`) so that replay video recording can find the display reliably:

```bash
orb shell -m ubuntu

export LIBGL_ALWAYS_SOFTWARE=1
export LD_LIBRARY_PATH=$HOME/games/yomi:$LD_LIBRARY_PATH

# Start Xvfb on a fixed display
Xvfb :99 -screen 0 1280x720x24 -nocursor &
sleep 1

# Launch the game
DISPLAY=:99 ~/games/yomi/YourOnlyMoveIsHUSTLE.x86_64
```

Alternatively, `xvfb-run` still works but makes replay recording harder (dynamic display, Xauthority):

```bash
xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" \
  ~/games/yomi/YourOnlyMoveIsHUSTLE.x86_64
```

The full automated flow is:

1. Game boots to main menu
2. ModLoader loads `YomiLLMBridge`
3. `ModMain` connects to the daemon via WebSocket
4. Daemon sends `hello_ack` with config (including `character_selection` and `policy_mapping`)
5. `AutoMatchStarter` resolves characters from the config, constructs `match_data`, and emits the `match_ready` signal on the game's `CharacterSelect` node
6. The game starts a singleplayer match via its normal `setup_game()` path
7. `TurnHook` detects `Global.current_game` and begins intercepting turns
8. Each turn, the daemon routes decisions to the configured policy (LLM or baseline)

No manual menu interaction is needed.

To take a debug screenshot when using a fixed display (`:99`):

```bash
orb run -m ubuntu bash -c 'DISPLAY=:99 scrot /tmp/yomi_debug.png'
orb pull -m ubuntu /tmp/yomi_debug.png /tmp/yomi_debug.png
open /tmp/yomi_debug.png
```

If you used `xvfb-run` instead, you need the Xauthority file it created:

```bash
orb run -m ubuntu bash -c '
DISP=:$(ls /tmp/.X11-unix/ | tail -1 | tr -d "X")
for xauth in /tmp/xvfb-run.*/Xauthority; do
  DISPLAY=$DISP XAUTHORITY=$xauth scrot /tmp/yomi_debug.png 2>/dev/null && break
done
'
orb pull -m ubuntu /tmp/yomi_debug.png /tmp/yomi_debug.png
open /tmp/yomi_debug.png
```

`xvfb-run` sets up its own Xauthority, so plain `DISPLAY=:99 scrot` will fail with "Authorization required". You must pass the matching Xauthority file.

## ModLoader requirements

The game's built-in ModLoader (`res://modloader/ModLoader.gd`) has specific requirements that are not obvious from the mod API:

**Zip-only loading**: The loader calls `ProjectSettings.load_resource_pack()` on each `.zip` file in `<game_dir>/mods/`. Loose directories are ignored.

**`_metadata` must have all 10 fields**: `_verifyMetadata()` requires exactly these keys: `name`, `friendly_name`, `description`, `author`, `version`, `link`, `id`, `overwrites`, `requires`, `priority`. Missing any causes the mod to be silently skipped.

**Constructor receives ModLoader reference**: The loader calls `script.new(self)`, passing itself as a constructor argument. `ModMain.gd` must define `func _init(_mod_loader = null)` to accept this. Without it, `_create_instance` fails and the mod is silently skipped.

**`_editMetaData` writes fail silently**: The loader attempts to write back to `_metadata` inside the zip (to set `id = "12345"`). This fails because zip resource packs are read-only, producing `ERROR: File must be opened before use. at: store_string`. This is non-fatal.

**No mod output in stdout**: Godot `print()` calls from mods loaded via resource packs may not appear in stdout depending on the load order and buffering. Use daemon-side logs and screenshots for debugging instead.

## First complete match

The first full match ran to natural completion: **596 decisions over 2887 ticks, P2 won by KO** (P1 HP dropped to 31). The daemon received a proper `match_ended` envelope with `reason=ko` and wrote results to `runs/`. Both players used `baseline/random` policy with random character selection (Wizard mirror).

This validates the full pipeline: game boots → mod connects → daemon orchestrates → turns resolve → match ends cleanly → results persist.

### Non-fatal warnings during match

- ~1207 `FGObject` errors in the game log — these are non-fatal and the game continues through them. Likely related to headless rendering or zero-direction moves.
- Some XYPlot nodes (Roll/Direction, Summon/Direction, DiveKick2/Direction, Grab/Direction) produce zero-bound payload_specs, meaning direction-based moves default to `{"x": 0, "y": 0}`. The `panel_radius` property detection works for Geyser but may not be readable on all widget instances. This doesn't crash the game but limits move variety.

## Known issues (resolved)

**Match crash from FixedMath panics (FIXED)**: The first test (50 decisions) ended with a segfault from `tbfg.so`. Root cause was three interacting bugs:

1. **Missing payload_spec for game UI widgets**: `LegalActionBuilder._classify_data_child()` failed to recognize `XYPlot` and `CountOption` nodes because they extend generic containers and their node names don't match existing patterns. The baseline produced empty `data` for moves like Geyser, causing null access → FixedMath panic.

2. **DI key case mismatch**: Daemon sends lowercase `"di"`, game's `process_extra()` checks uppercase `"DI"`. The `ActionApplier` was passing through lowercase.

3. **Prediction format mismatch**: Daemon sends `prediction: null`, game expects integer `-1`.

Fixed by adding XYPlot/CountOption detection in LegalActionBuilder, uppercase DI normalization in ActionApplier, and integer prediction normalization.

## Known issues (open)

**Observation float types**: Values from `get_pos()` and `get_vel()` may serialize as strings or floats depending on the game's FixedMath types. The `ObservationBuilder` must cast with `int()` to satisfy the daemon's JSON schema validation (which expects `"type": "number"`).

**GDScript 3.x compatibility**: Several GDScript patterns that work in 3.6+ fail in 3.5.1:
- `max()`/`min()` return `float`, not `int` — wrap in `int()` when the function declares `-> int`
- `seed` is a built-in function name and cannot be used as a parameter name
- `Array.slice()` does not exist — use manual iteration

## Caveats

**Steam init on startup**: Confirmed non-fatal. The game logs `SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running instance of Steam` and `Sys_LoadModule failed to load: .../steamclient.so`, but `SteamHustle.STARTED` stays `false` and the game boots to the main menu without issues.

**SteamCMD does not work on Apple Silicon**: Both Rosetta-based emulation (OrbStack amd64 VM) and Box64/Box86 (`steamcmd-arm64` Docker image) crash with `futex robust_list not initialized by pthreads`. Use DepotDownloader instead.

**Character selection**: The daemon config controls which characters play. Default mode is `mirror` (random character, same for both sides). Use `assigned` mode for specific matchups:

```json
"character_selection": {
  "mode": "assigned",
  "assignments": {"p1": "Ninja", "p2": "Robot"}
}
```

Available built-in characters: `Ninja`, `Cowboy`, `Wizard`, `Robot`, `Mutant`.

**Xvfb cleanup**: If Xvfb crashes or is killed uncleanly, stale lock files may prevent it from restarting. Clean up with:

```bash
orb run -m ubuntu bash -c "killall Xvfb 2>/dev/null; rm -f /tmp/.X*-lock /tmp/.X11-unix/X*"
```

**Singleplayer replay saving**: The game only autosaves replays for multiplayer matches. The mod now explicitly calls `ReplayManager.save_replay()` after every singleplayer match. This was needed because our matches use singleplayer mode.

**Default policy**: By default both players use `baseline/random`. To use an LLM, edit the `policy_mapping` in `daemon/config/default_config.json`:

```json
"policy_mapping": {
  "p1": "anthropic/claude-sonnet-4-20250514",
  "p2": "baseline/random"
}
```

## Match artifacts

Results land in `runs/<timestamp>_<match_id>/` on the Mac (where the daemon runs):

- `manifest.json` — match metadata
- `decisions.jsonl` — every decision
- `prompts.jsonl` — every prompt sent to LLMs
- `metrics.json` — latency, fallback rate, legality
- `result.json` — winner, final HP
- `replay.mp4` — replay video (when `--record-replay` enabled)
- `match.replay` — game replay file (when `--record-replay` enabled)

## Replay video recording

After a match ends, the game automatically replays it without decision-time pauses — it looks like a real-time fighting game. The mod detects this replay playback and signals the daemon to record it via ffmpeg.

### How it works

1. Match ends → mod saves a `.replay` file via `ReplayManager.save_replay()`
2. Mod sends a `ReplaySaved` event to the daemon with the file path
3. ~120 ticks later, the game auto-starts replay playback
4. Mod detects `ReplayManager.playback == true` on the new game instance
5. Mod sends `ReplayStarted` event → daemon starts `ffmpeg -f x11grab` on the Xvfb display
6. Replay finishes → mod sends `ReplayEnded` → daemon stops ffmpeg
7. Daemon pulls the video and replay file from the VM into `runs/<match>/`

### Prerequisites

- `ffmpeg` installed in the VM (included in Step 3)
- Game launched on a fixed Xvfb display (`:99`, as in Step 8)
- Daemon started with `--record-replay`

### Running with replay recording

**Daemon side** (on your Mac):

```bash
uv run --project daemon yomi-daemon --host 0.0.0.0 --port 8765 --record-replay
```

**Game side** (in the VM):

```bash
orb shell -m ubuntu

export LIBGL_ALWAYS_SOFTWARE=1
export LD_LIBRARY_PATH=$HOME/games/yomi:$LD_LIBRARY_PATH

Xvfb :99 -screen 0 1280x720x24 -nocursor &
sleep 1

DISPLAY=:99 ~/games/yomi/YourOnlyMoveIsHUSTLE.x86_64
```

After the match completes and the replay plays through, the daemon will save:
- `runs/<match>/replay.mp4` — the replay video
- `runs/<match>/match.replay` — the game's replay file (can be loaded in the game)

### Optional: custom display or VM name

```bash
uv run --project daemon yomi-daemon --host 0.0.0.0 --record-replay \
  --replay-display :42 --replay-vm my-ubuntu-vm
```

### Replay file saving

Even without `--record-replay`, the mod now saves replay files after every match. The game normally only autosaves replays for multiplayer matches, but our mod calls `ReplayManager.save_replay()` explicitly for singleplayer matches. The replay path is reported in the `match_ended` envelope and recorded in `result.json`.

Replay files are saved to `user://replay/autosave/` inside the VM, which resolves to `~/.local/share/godot/app_userdata/Your Only Move Is HUSTLE/replay/autosave/`.

### Troubleshooting

**ffmpeg can't connect to display**: Make sure the game is launched on `:99` (not via `xvfb-run --auto-servernum`). When using `xvfb-run`, the display number is dynamic and ffmpeg can't find it.

**Video is black**: The game must be rendering to the virtual display. Verify with `DISPLAY=:99 scrot /tmp/test.png` from the VM.

**Recording never starts**: The replay only begins ~120 ticks after the match ends. The mod waits up to 60 seconds for replay detection. Check daemon logs for `ReplayStarted` events.

**ffmpeg doesn't exit cleanly on SIGINT**: When ffmpeg runs inside the VM via `orb run`, SIGINT goes to the `orb` wrapper process, not directly to ffmpeg. The daemon handles this with a 15-second timeout after SIGINT, then kills the process. A fallback `pgrep`/`kill` inside the VM catches any orphaned ffmpeg processes. The video file is still valid because ffmpeg flushes data continuously.

**replay_path is null in result.json**: The `ReplaySaved` event arrives after `match_ended` finalization, so `replay_path` in `result.json` is currently `null`. The replay video is still saved correctly to the run directory. This is a cosmetic gap.

## First replay video recording

The first replay video was captured successfully: **713 turns, P1 won by KO, 68-second replay video** (3.2 MB, H.264, 1280x720 @ 30fps). The full pipeline validated:

1. Match completed normally (baseline/random mirror, Ninja)
2. Mod saved replay file via `ReplayManager.save_replay()`
3. Game auto-started replay playback ~3 seconds after match end
4. Mod detected `ReplayManager.playback == true` on new game instance
5. Mod sent `ReplayStarted` with display `:99` → daemon started ffmpeg
6. Replay played for ~53 seconds
7. Mod sent `ReplayEnded` → daemon stopped ffmpeg and pulled video
8. `replay.mp4` written to run directory (valid, playable)

## First LLM match

The first LLM-backed match ran successfully: **Claude Sonnet 4 (P1) vs baseline/greedy_damage (P2)**, 300 HP starting health, strategic_v1 prompt.

**Results:**

| Metric | Value |
|---|---|
| Total decisions | 204 (99 LLM, 105 baseline) |
| Fallbacks | 0 |
| Avg LLM latency | 5,565ms |
| Tokens consumed | 603K in / 19K out |
| Starting HP | 300 each |
| Final HP | P1: 30, P2: 300 |
| Match ended | Manually killed (defensive stalemate) |

**Config used:** `daemon/config/llm_first_test.json`

**Key observations:**
- Zero fallbacks — response parsing worked perfectly on every turn
- Claude produced coherent strategic reasoning (references HP, positioning, opponent state)
- Claude played defensively at low HP (rolls, dodges, parries), surviving 150+ turns at 30 HP
- The greedy_damage baseline's grab spam couldn't finish Claude off — defensive stalemate
- `match_options.starting_hp = 300` shortened the match from 1500 HP default

**Critical bug fixed:** `config.py` defaulted `resolved_env` to `{}` instead of `os.environ`, so API keys from environment variables were never resolved via the CLI path. Fixed in commit `83432e0`.

**Running an LLM match:**

```bash
# Start daemon with LLM config (source API key first)
cd daemon
source ../.env && export ANTHROPIC_API_KEY
uv run python -m yomi_daemon.cli --config config/llm_first_test.json

# In another terminal, package and push mod with VM host IP baked in
cd mod && zip -r /tmp/YomiLLMBridge.zip YomiLLMBridge/
# Update host to 192.168.139.3 in the zip (see Step 6)
orb push -m ubuntu /tmp/YomiLLMBridge.zip /home/$USER/games/yomi/mods/YomiLLMBridge.zip

# Launch game in VM
orb run -m ubuntu bash -c '
export LIBGL_ALWAYS_SOFTWARE=1
export LD_LIBRARY_PATH=$HOME/games/yomi:$LD_LIBRARY_PATH
DISPLAY=:99 $HOME/games/yomi/YourOnlyMoveIsHUSTLE.x86_64
'
```

## OrbStack CLI reference

```bash
orb list                                    # list machines and their status
orb shell -m ubuntu                         # interactive shell into the VM
orb run -m ubuntu <command>                 # run a single command in the VM
orb push -m ubuntu <local-file> <abs-dest>  # copy a file from Mac into the VM
orb pull -m ubuntu <abs-src> <local-file>   # copy a file from the VM to Mac
```

Notes:
- `orb run` does not use `--` before the command — just `orb run -m ubuntu uname -m`.
- `orb push`/`orb pull` require `-m <machine>` and absolute paths for the remote side (not `~`). Push files individually, not directories.
- The default VM name when created via the OrbStack GUI is `ubuntu` (not a custom name).
