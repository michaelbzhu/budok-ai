# Running Budok-AI Arena on macOS

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
- [x] Step 11: First LLM vs LLM match (Claude Sonnet 4 vs Claude Sonnet 4, 54 turns, 0 fallbacks, 7.3s avg latency, concurrent processing)
- [x] Step 12: Replay recording pipeline overhaul (fixed timer scaling, replay loop, ffmpeg signal handling, recording timing)

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
5. `AutoMatchStarter` resolves characters from the config, constructs `match_data` (including `game_length` scaled from HP), and emits the `match_ready` signal on the game's `CharacterSelect` node
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

## Game internals reference (from decompiled source)

Key values from `game.gd` and `BaseChar.gd` that affect match configuration:

| Property | Default | Location | Notes |
|---|---|---|---|
| `MAX_HEALTH` | 1500 | `BaseChar.gd:20` | Per-fighter HP. The game's internal and display HP are the same scale. |
| `game.time` | 3000 | `game.gd:16` | Match timer in ticks. At 60 ticks/second = 50 seconds of game time. |
| `game_length` | (via `match_data`) | `game.gd:351` | Override for `game.time`, set in `match_data` dict passed to `setup_game()`. |
| `playback_speed_mod` | 1 | `Global.gd:20` | Replay speed: 1 = full speed (60 ticks/s), 2 = half speed (30 ticks/s), 4 = quarter speed. Higher = slower. |
| `game_end_tick + 120` | | `game.gd:1377` | Ticks after match end before auto-replay starts via `start_playback()`. |

Match timer scaling: the default ratio is 3000 ticks / 1500 HP = 2 ticks per HP. When `match_options.starting_hp` overrides the HP, `AutoMatchStarter` sets `match_data["game_length"]` to `starting_hp * 3` (50% margin) so the timer scales proportionally.

## ModLoader requirements

The game's built-in ModLoader (`res://modloader/ModLoader.gd`) has specific requirements that are not obvious from the mod API:

**Zip-only loading**: The loader calls `ProjectSettings.load_resource_pack()` on each `.zip` file in `<game_dir>/mods/`. Loose directories are ignored.

**`_metadata` must have all 10 fields**: `_verifyMetadata()` requires exactly these keys: `name`, `friendly_name`, `description`, `author`, `version`, `link`, `id`, `overwrites`, `requires`, `priority`. Missing any causes the mod to be silently skipped.

**Constructor receives ModLoader reference**: The loader calls `script.new(self)`, passing itself as a constructor argument. `ModMain.gd` must define `func _init(_mod_loader = null)` to accept this. Without it, `_create_instance` fails and the mod is silently skipped.

**`_editMetaData` writes fail silently**: The loader attempts to write back to `_metadata` inside the zip (to set `id = "12345"`). This fails because zip resource packs are read-only, producing `ERROR: File must be opened before use. at: store_string`. This is non-fatal.

**No mod output in stdout**: Godot `print()` calls from mods loaded via resource packs may not appear in stdout depending on the load order and buffering. Use daemon-side logs and screenshots for debugging instead.

## Match artifacts

Results land in `runs/<timestamp>_<match_id>/` on the Mac (where the daemon runs):

- `manifest.json` — match metadata
- `decisions.jsonl` — every decision
- `prompts.jsonl` — every prompt sent to LLMs
- `metrics.json` — latency, fallback rate, legality
- `result.json` — winner, final HP
- `replay.mp4` — replay video (when replay recording enabled, default on)
- `match.replay` — game replay file (when replay recording enabled, default on)

## Replay video recording

After a match ends, the game automatically replays it at full game speed (no decision-time pauses). The mod signals the daemon to record this replay via ffmpeg capturing the Xvfb display.

### How it works

1. Match ends → mod saves a `.replay` file via `ReplayManager.save_replay()`
2. Mod immediately sends `ReplayStarted` event → daemon starts `ffmpeg -f x11grab -t <duration>` on the Xvfb display (duration auto-scaled from HP)
3. The game runs 120 post-game ticks (~2 seconds of victory animation), then auto-starts replay playback
4. Replay plays at half speed (`playback_speed_mod = 2`, 30 ticks/second instead of 60)
5. When the replay game finishes, mod waits 5 seconds for post-KO animation, then sends `ReplayEnded`
6. Mod sets `ReplayManager.play_full = false` to prevent the game from starting an infinite replay loop
7. ffmpeg exits cleanly when its `-t` duration expires (no signal-based stopping needed)
8. Daemon pulls the video and replay file from the VM into `runs/<match>/`
9. Daemon shuts down after the first completed match

### Key design decisions and why

**Fixed-duration recording (`-t <duration>`)**: ffmpeg is given a calculated max duration and exits on its own, writing a proper MP4 trailer. The duration is auto-scaled from HP: `(starting_hp * 3 / 30) + 15` seconds. For 1500 HP: `(4500/30)+15 = 165s`. For 500 HP: `(1500/30)+15 = 65s`. Signal-based stopping (SIGINT) is unreliable through the OrbStack process boundary — the `orb run` wrapper doesn't forward signals to ffmpeg inside the VM, producing truncated 48-byte files.

**Early ReplayStarted emission**: The mod sends `ReplayStarted` immediately when the match ends (in `_begin_replay_monitoring`), BEFORE the replay game is created. This gives ffmpeg ~5 seconds to start before the replay begins playing. Without this, short matches finish their replay before ffmpeg even starts capturing.

**Half-speed replay (`playback_speed_mod = 2`)**: The game's `_physics_process` checks `real_tick % playback_speed_mod == 0` to gate tick processing. With mod=2, the replay runs at 30 ticks/second instead of 60, doubling the playback time. Without this, a 500 HP match's ~1300 ticks of combat play in ~22 seconds, making the action hard to follow.

**Replay loop prevention**: After the game finishes a replay, `game.gd:_physics_process` waits 120 ticks then calls `start_playback()`, which creates a new replay game via `main.gd._on_playback_requested()`. This repeats infinitely (normal singleplayer behavior for rewatching). The mod sets `ReplayManager.play_full = false` after the first replay to break this loop.

**Daemon shutdown after match**: The daemon calls `stop()` after the first match completes. Without this, the mod's BridgeClient reconnects after the WebSocket closes, potentially triggering a new match.

### Recommended HP for video recording

The replay plays at half speed. HP directly controls how many game ticks of combat occur and thus how long the video's action portion is:

| Starting HP | Action ticks | Action duration (half speed) | Notes |
|---|---|---|---|
| 100 | ~120 | ~4 seconds | Too short to watch — combat is a blur |
| 500 | ~1300 | ~43 seconds | Good for quick reviews |
| 1000 | ~2500 | ~83 seconds | Full match, may exceed 60s recording window |
| 1500 (default) | ~2900 | ~97 seconds | Game default, exceeds 60s — increase `-t` or accept truncation |

For reviewable videos, **500 HP is the sweet spot** — enough combat to see the full fight within the 60-second recording window.

### Config file settings

```json
{
  "replay_capture": {
    "enabled": true,
    "vm_machine": "ubuntu",
    "display": ":99",
    "resolution": "1280x720",
    "framerate": 30,
    "video_codec": "libx264",
    "preset": "fast"
  }
}
```

All fields are optional and have sensible defaults.

### Prerequisites

- `ffmpeg` installed in the VM (included in Step 3)
- Game launched on a fixed Xvfb display (`:99`, as in Step 8)
- Replay recording is enabled by default; disable with `--no-record-replay` or `replay_capture.enabled: false` in config

### Running with replay recording

Replay recording is on by default. To explicitly control it:

**Daemon side** (on your Mac):

```bash
# Default: replay recording enabled
uv run --project daemon yomi-daemon --host 0.0.0.0 --port 8765

# Explicitly disable:
uv run --project daemon yomi-daemon --host 0.0.0.0 --port 8765 --no-record-replay
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

### Debugging replay videos

**Extract frames from an MP4 for visual inspection** — this is the most effective debugging technique for replay recording issues. It closes the loop so you can see exactly what each second of the video shows without playing it:

```bash
# Extract one frame per second
MDIR="runs/<timestamp>_<match_id>"
mkdir -p /tmp/replay_frames
ffmpeg -i $MDIR/replay.mp4 -vf "fps=1" -q:v 2 /tmp/replay_frames/frame_%03d.jpg

# View specific frames (Claude Code can view images directly with the Read tool)
# Look for: timer values, health bar states, "P1 WIN"/"P2 WIN" text,
# "E: Edit replay" banner (indicates replay mode), fighter positions
```

When debugging, check each frame for:
- **"E: Edit replay" banner at top** → this is the replay, not the live match
- **Timer value** → tells you where in the match timeline this frame is
- **Health bar levels** → confirms combat is happening (bars should drain over time)
- **"P1 WIN" / "P2 WIN" text** → match-end screen or replay-end screen
- **Fighter positions** → are they fighting or standing idle?

Common video problems and their causes:

| Symptom | Cause | Fix |
|---|---|---|
| Video is 48 bytes / corrupt | ffmpeg killed before writing MP4 trailer | Use `-t` duration flag instead of signal-based stopping |
| Video starts with "P1 WIN" screen | Recording started at match end, before replay began | Emit `ReplayStarted` earlier so ffmpeg starts during post-game animation |
| Combat happens in <1 second | Replay plays at full speed (60 ticks/s) | Set `Global.playback_speed_mod = 2` for half speed |
| Fighters stand idle for most of video | Match timer much longer than actual combat | Scale timer properly: `starting_hp * 3` ticks |
| Second match starts in video | Game's infinite replay loop creates new game instance | Set `ReplayManager.play_full = false` after first replay |
| Frames skip / timer jumps | Game runs ticks faster than ffmpeg captures | Use `playback_speed_mod = 2` or higher; increase ffmpeg framerate |

### Troubleshooting

**ffmpeg can't connect to display**: Make sure the game is launched on `:99` (not via `xvfb-run --auto-servernum`). When using `xvfb-run`, the display number is dynamic and ffmpeg can't find it.

**Video is black**: The game must be rendering to the virtual display. Verify with `DISPLAY=:99 scrot /tmp/test.png` from the VM.

**replay_path is null in result.json**: The `ReplaySaved` event arrives after `match_ended` finalization, so `replay_path` in `result.json` is currently `null`. The replay video is still saved correctly to the run directory. This is a cosmetic gap.

## Running an LLM match

Use `scripts/run_match.sh` to automate the entire workflow:

```bash
scripts/run_match.sh
```

This single command handles bridge IP detection, mod packaging/push, Xvfb, process cleanup, daemon startup, game launch, match polling, replay capture, and result reporting. See `docs/operations.md` for full usage and options.

For configuration, copy `match.conf.example` to `match.conf`:

```bash
cp match.conf.example match.conf
# Edit VM_NAME, VM_GAME_DIR, DAEMON_CONFIG, etc.
scripts/run_match.sh
```

### Manual steps (for reference)

The ad-hoc manual steps are preserved below for debugging. Normal usage should prefer `scripts/run_match.sh`.

<details>
<summary>Manual match workflow</summary>

```bash
# 1. Package and push mod with VM host IP baked in
cd /path/to/budok-ai
sed -i '' 's/"host": "127.0.0.1"/"host": "192.168.139.3"/' mod/YomiLLMBridge/config/default_config.json
scripts/package_mod.sh
orb push -m ubuntu dist/YomiLLMBridge.zip /home/$USER/games/yomi/mods/YomiLLMBridge.zip
sed -i '' 's/"host": "192.168.139.3"/"host": "127.0.0.1"/' mod/YomiLLMBridge/config/default_config.json

# 2. Clean up any stale processes
orb run -m ubuntu bash -c "ps aux | grep YourOnly | grep -v grep | awk '{print \$2}' | xargs -r kill -9"

# 3. Start Xvfb if not already running
orb run -m ubuntu bash -c "Xvfb :99 -screen 0 1280x720x24 -nocursor &>/dev/null &"

# 4. Start daemon with LLM config
source .env && export ANTHROPIC_API_KEY
cd daemon && uv run python -m yomi_daemon.cli --config config/llm_v_llm.json --log-level INFO

# 5. In another terminal, launch the game
orb run -m ubuntu bash -c '
export LIBGL_ALWAYS_SOFTWARE=1
export LD_LIBRARY_PATH=$HOME/games/yomi:$LD_LIBRARY_PATH
DISPLAY=:99 $HOME/games/yomi/YourOnlyMoveIsHUSTLE.x86_64
'
```

**Important**: Kill ALL old game processes before starting a new match. Each `orb run` that launches the game creates a new process. Accumulated game processes consume ~800MB each and will exhaust VM memory.

</details>

## Known issues (resolved)

**Match crash from FixedMath panics (FIXED)**: The first test (50 decisions) ended with a segfault from `tbfg.so`. Root cause was three interacting bugs:

1. **Missing payload_spec for game UI widgets**: `LegalActionBuilder._classify_data_child()` failed to recognize `XYPlot` and `CountOption` nodes because they extend generic containers and their node names don't match existing patterns. The baseline produced empty `data` for moves like Geyser, causing null access → FixedMath panic.

2. **DI key case mismatch**: Daemon sends lowercase `"di"`, game's `process_extra()` checks uppercase `"DI"`. The `ActionApplier` was passing through lowercase.

3. **Prediction format mismatch**: Daemon sends `prediction: null`, game expects integer `-1`.

Fixed by adding XYPlot/CountOption detection in LegalActionBuilder, uppercase DI normalization in ActionApplier, and integer prediction normalization.

**Fighter name returns "P1"/"P2" (FIXED)**: `game.gd` renames fighter nodes to `"P1"`/`"P2"` after instantiation, so `fighter.name` never returns the character name. This broke the move catalog lookup and `_build_character_data` dispatch. Fixed by resolving character name from `fighter.filename` (the `.tscn` scene path) via `SCENE_PATH_TO_CHARACTER` reverse-lookup.

**LLM prediction validation failures (FIXED)**: LLMs consistently included `extra.prediction` for actions with `supports.prediction=false`, causing `illegal_output` validation failures and high fallback rates (~70%). Fixed by silently stripping unsupported prediction during response normalization in `response_parser.py`.

**DashForward spam (FIXED)**: Without character-specific move descriptions, LLMs defaulted to DashForward ~50% of turns. Fixed by the character name resolution (enables move catalog), a pre-computed situation summary with distance/range labels, concrete spacing thresholds in the strategic prompt, and explicit anti-repetition rules.

**Payload validation failures / high fallback rate (FIXED)**: Many actions (Grab, Roll, etc.) have required payload fields (Direction, Dash, Jump) that are trivial (boolean checkboxes defaulting to false, zero-range XY plots). The LLM omits these, causing `illegal_output`. Fixed by auto-filling payload defaults from `payload_spec` in the response parser.

**Match timer too long / fighters idle in replay (FIXED)**: The match timer was not properly scaled for custom HP values. With 1000 HP, the old code set `game.time = 54000` (54 ticks/HP) instead of the correct ~2-3 ticks/HP. The replay showed combat for ~25 seconds then idle standing for minutes. Fixed by using `starting_hp * 3` for the timer (3 ticks/HP, 50% margin over the game's default 2:1 ratio).

**Replay video truncated to 48 bytes (FIXED)**: Sending SIGINT to ffmpeg via the `orb run` wrapper process doesn't reliably forward the signal to ffmpeg inside the VM. ffmpeg dies without writing an MP4 trailer. Fixed by using ffmpeg's `-t` flag for fixed-duration recording (auto-scaled from HP) — ffmpeg exits cleanly on its own.

**Replay starts a second match (FIXED)**: After a replay finishes, `game.gd:_physics_process` auto-calls `start_playback()` 120 ticks later, creating an infinite replay loop. The second replay was visible in the recorded video. Fixed by setting `ReplayManager.play_full = false` after the first replay, and shutting down the daemon server after the match.

## Known issues (open)

**Observation float types**: Values from `get_pos()` and `get_vel()` may serialize as strings or floats depending on the game's FixedMath types. The `ObservationBuilder` must cast with `int()` to satisfy the daemon's JSON schema validation (which expects `"type": "number"`).

**GDScript 3.x compatibility**: Several GDScript patterns that work in 3.6+ fail in 3.5.1:
- `max()`/`min()` return `float`, not `int` — wrap in `int()` when the function declares `-> int`
- `seed` is a built-in function name and cannot be used as a parameter name
- `Array.slice()` does not exist — use manual iteration
- `JSON.parse()` returns ALL numbers as `TYPE_REAL` (float), never `TYPE_INT` — integer type checks in `DecisionValidator.gd` must accept `TYPE_REAL` values that are whole numbers (`value == floor(value)`)

**Video captures first ~4 seconds of match-end screen**: ffmpeg starts recording at match end (before the replay game is created). The first few seconds of video show the victory screen with "P1 WIN" / "P2 WIN" before the replay begins. This is cosmetic — the replay itself plays fully within the recording window.

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

**VM memory exhaustion**: Each game process uses ~800MB. If you run multiple matches without killing old game processes, the VM will run out of memory. Always kill all game processes before starting a new match (see "Running an LLM match" above).

**Singleplayer replay saving**: The game only autosaves replays for multiplayer matches. The mod now explicitly calls `ReplayManager.save_replay()` after every singleplayer match. This was needed because our matches use singleplayer mode.

**Default policy**: By default both players use `baseline/random`. To use an LLM, edit the `policy_mapping` in `daemon/config/llm_v_llm.json`.

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
- **Signals do not pass through `orb run`**. SIGINT sent to an `orb run` process kills the wrapper but does NOT forward to the child process inside the VM. Use `orb run -m ubuntu kill -INT <PID>` as a separate command to signal processes inside the VM.
