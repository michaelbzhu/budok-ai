# budok-ai

LLMs can fight!

budok-ai allows LLMs to play a 2d fighting game against each other by modding the brilliant [YOMI Hustle](https://store.steampowered.com/app/2212330/Your_Only_Move_Is_HUSTLE/). YOMI Hustle is a turn-based, frame-by-frame, simulataneous resolution fighting game. It's like the chess of rock-paper-scissors. The magic trick is, we can replay completed matches to simulate LLMs competing in a fast-twitch game.

budok-ai consists of a YOMI Hustle mod in gdscript and a python daemon for coordinating LLM responses. We run the modded game in Steam within a linux vm. You must own the game on Steam for this to work (and everyone should buy the game even if not playing with LLMs - watch this YouTube video for an intro: [The Greatest Fighting Game You've Never Heard Of
](https://www.youtube.com/watch?v=tVWtyXQhdHQ)).

## Setup

Currently macOS-only (Apple Silicon). The game runs headlessly in a Linux VM via OrbStack; the daemon runs natively on your Mac.

### Prerequisites

- macOS with Apple Silicon
- [YOMI Hustle](https://store.steampowered.com/app/2212330/Your_Only_Move_Is_HUSTLE/) on Steam (you must own the game)
- A Steam account with Steam Guard enabled (needed for downloading game files)
- An API key from [Anthropic](https://console.anthropic.com/), [OpenAI](https://platform.openai.com/), or [OpenRouter](https://openrouter.ai/) (for LLM-backed policies)

### Step-by-step

1. **Install system dependencies:**
   ```bash
   brew install uv
   brew install --cask orbstack
   brew tap steamre/tools
   brew install depotdownloader
   ```
   Open OrbStack once after install to finish its setup.

2. **Clone the repo and install Python dependencies:**
   ```bash
   git clone https://github.com/nickslevine/budok-ai.git
   cd budok-ai
   uv sync --project daemon
   ```

3. **Set up API keys:**
   ```bash
   cp .env.example .env
   # Edit .env and uncomment/fill your API key(s)
   ```

4. **Create an x86-64 Ubuntu VM in OrbStack:**
   In OrbStack, create a new Linux machine: Ubuntu Jammy (22.04), architecture **amd64**. Default name `ubuntu` is fine.
   ```bash
   orb run -m ubuntu uname -m   # should print "x86_64"
   ```

5. **Install VM dependencies:**
   ```bash
   orb run -m ubuntu bash -c "\
     sudo apt-get update -qq && \
     sudo apt-get install -y \
       xvfb libgl1 mesa-utils unzip scrot ffmpeg \
       libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 \
       libgles2-mesa libegl1-mesa libgl1-mesa-dri \
       libpulse0 libasound2"
   ```

6. **Download the game via DepotDownloader:**
   ```bash
   mkdir -p ~/yomi-steam
   depotdownloader -app 2212330 -depot 2232859 -username YOUR_STEAM_USERNAME -dir ~/yomi-steam
   ```
   This prompts for your password and Steam Guard code. If macOS blocks it, run:
   ```bash
   xattr -d com.apple.quarantine /opt/homebrew/bin/depotdownloader
   ```

7. **Copy game files into the VM:**
   ```bash
   orb run -m ubuntu mkdir -p /home/$USER/games/yomi
   for f in ~/yomi-steam/*; do [ -f "$f" ] && orb push -m ubuntu "$f" /home/$USER/games/yomi/; done
   orb run -m ubuntu chmod +x /home/$USER/games/yomi/YourOnlyMoveIsHUSTLE.x86_64
   ```

8. **Create your match config:**
   ```bash
   cp match.conf.example match.conf
   ```
   The defaults work out of the box. Edit `match.conf` if your VM name or game path differs.

9. **Verify everything works:**
   ```bash
   uv run --project daemon pytest   # all tests should pass
   ```

See [docs/macos.md](docs/macos.md) for detailed troubleshooting and advanced configuration.

## Running a Match

1. **Run a match:**
   ```bash
   scripts/run_match.sh
   ```
   This single command handles everything: mod packaging, VM setup, daemon startup, game launch, match execution, and replay recording.

2. **Wait for the match to complete.** The script polls automatically and prints results when done. A typical LLM v LLM match (750 HP) takes 10-15 minutes.

3. **View the results.** When the match finishes you'll see a summary like:
   ```
   ╔══════════════════════════════════╗
   ║         MATCH RESULT             ║
   ╠══════════════════════════════════╣
   ║  Status:  completed             ║
   ║  Winner:  p2                    ║
   ║  Reason:  ko                    ║
   ║  Turns:   150                   ║
   ╚══════════════════════════════════╝
   ```
   Artifacts (decisions, prompts, metrics, replay video) are saved to `runs/<timestamp>_<match_id>/`.

4. **Watch the replay:** Open `runs/<match>/replay.mp4` to see the fight.

### Common options

```bash
# Use a specific daemon config
scripts/run_match.sh --daemon-config daemon/config/llm_v_llm.json

# Skip mod packaging (faster iteration, reuses mod already in VM)
scripts/run_match.sh --skip-mod-push

# Disable replay recording
scripts/run_match.sh --no-replay

# Preview what would happen without executing
scripts/run_match.sh --dry-run
```

### Configuring policies

Edit `daemon/config/llm_v_llm.json` to change which models fight. The `policy_mapping` assigns policies to P1 and P2:

```json
{
  "policy_mapping": {
    "p1": "anthropic/claude-sonnet",
    "p2": "anthropic/claude-opus"
  }
}
```

Available providers: `anthropic`, `openai`, `openrouter`. Baselines (`baseline/random`, `baseline/block_always`, `baseline/greedy_damage`, `baseline/scripted_safe`) need no API key.

Characters: set `character_selection.mode` to `llm_choice` (LLMs pick), `mirror` (random, same for both), `assigned` (you pick), or `random_from_pool`. Available characters: Ninja, Cowboy, Wizard, Robot, Mutant.

## Repository layout

- `daemon/`: Python package, local tooling, and daemon-side implementation.
- `docs/`: architecture, data flow, operations, protocol, game-internals, and prompt-engineering notes.
- `mod/`: Godot mod sources under `mod/YomiLLMBridge/`.
- `prompts/`: versioned prompt templates used by daemon adapters.
- `schemas/`: versioned JSON schemas for the IPC protocol.
- `scripts/`: local helper scripts for schema checks, decompilation, and orchestration.
- `runs/`: per-match artifacts
- `tests/`: reusable fixtures plus repository-level tests.

## Next Steps
- Model vs. model tournaments and benchmarking
- RL experiments
- Centaur battles where humans compete to write the best prompts.
