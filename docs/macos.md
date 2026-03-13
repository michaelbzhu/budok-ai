# Running YOMI Hustle LLM Arena on macOS

YOMI Hustle is only available for Windows and Linux on Steam. On macOS, we run the game headlessly inside an x86-64 Linux VM (via OrbStack) and connect it to the Python daemon running natively on the Mac.

## Progress

- [x] Step 1: Verify daemon works on Mac (408 tests passing)
- [x] Step 2: Create x86-64 Ubuntu VM in OrbStack (machine name: `ubuntu`, arch: amd64)
- [x] Step 3: Install VM dependencies (xvfb, mesa, x11 libs)
- [x] Step 4: Download game via DepotDownloader (SteamCMD crashes under emulation)
- [x] Step 4b: Copy game files to VM and verify bare game boots (confirmed working)
- [x] Step 5: Install the mod (packaged, pushed, extracted in VM)
- [x] Step 6: Configure networking (gateway IP: `192.168.139.1`, mod config updated)
- [ ] Step 7: Start the daemon
- [ ] Step 8: Launch the game headlessly

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
    xvfb libgl1 mesa-utils unzip scrot \
    libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 \
    libgles2-mesa libegl1-mesa libgl1-mesa-dri \
    libpulse0 libasound2"
```

The `libpulse0` and `libasound2` packages prevent audio library load errors (the game falls back to a dummy audio driver regardless, but having the libs avoids noisy warnings). `scrot` is used for taking debug screenshots of the virtual display.

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

Package the mod on your Mac and push it into the VM:

```bash
scripts/package_mod.sh
orb run -m ubuntu mkdir -p /home/$USER/games/yomi/mods
orb push -m ubuntu dist/YomiLLMBridge.zip /home/$USER/games/yomi/mods/YomiLLMBridge.zip
```

Extract the mod. The zip already contains a `YomiLLMBridge/` directory, so extract directly (do NOT use `-d`):

```bash
orb run -m ubuntu bash -c "cd /home/$USER/games/yomi/mods && rm -rf YomiLLMBridge && unzip -o YomiLLMBridge.zip"
```

Verify:

```bash
orb run -m ubuntu ls /home/$USER/games/yomi/mods/YomiLLMBridge/ModMain.gd
```

## Step 6: Configure networking

The mod needs to connect to the daemon on your Mac. The daemon needs to accept connections from the VM.

**Daemon side** — bind to `0.0.0.0` so the VM can reach it:

```bash
uv run --project daemon yomi-daemon --host 0.0.0.0 --port 8765
```

**Mod side** — update `transport.host` in the mod config to point at the Mac host. OrbStack VMs reach the host via the default gateway IP:

```bash
orb run -m ubuntu bash -c "ip route | grep default | awk '{print \$3}'"
# Expected output: 192.168.139.1
```

Update the mod config:

```bash
orb run -m ubuntu bash -c "sed -i 's/\"host\": \"127.0.0.1\"/\"host\": \"192.168.139.1\"/' /home/$USER/games/yomi/mods/YomiLLMBridge/config/default_config.json"
```

Verify:

```bash
orb run -m ubuntu head -5 /home/$USER/games/yomi/mods/YomiLLMBridge/config/default_config.json
# Should show "host": "192.168.139.1"
```

**Note**: The gateway IP (`192.168.139.1`) is stable across OrbStack VM restarts but may differ on your machine. Always check with `ip route` if connectivity fails.

## Step 7: Start the daemon on Mac

```bash
uv run --project daemon yomi-daemon --host 0.0.0.0 --port 8765
```

## Step 8: Launch the game headlessly in the VM

```bash
orb shell -m ubuntu

export LIBGL_ALWAYS_SOFTWARE=1
export LD_LIBRARY_PATH=$HOME/games/yomi:$LD_LIBRARY_PATH
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

To take a debug screenshot at any point, find the display number and use `scrot`:

```bash
# From another terminal
orb run -m ubuntu bash -c "DISPLAY=:99 scrot /tmp/yomi_debug.png"
orb pull -m ubuntu /tmp/yomi_debug.png /tmp/yomi_debug.png
open /tmp/yomi_debug.png
```

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
