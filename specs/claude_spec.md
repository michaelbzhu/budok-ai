# Yomi Hustle LLM Arena — Full Specification

## 1. Project Overview

### What This Is
A system that enables LLMs to play **Your Only Move Is HUSTLE** ("Yomi Hustle") against each other autonomously, with no human intervention during matches. The system consists of a Godot game mod that extracts game state and injects moves, a Python bridge/daemon that routes decisions to LLM APIs, and a tournament runner that orchestrates matches and tracks results.

### Why Yomi Hustle Is Uniquely Suited for LLMs
Unlike real-time fighting games (where LLM Colosseum found that latency dominates and the fastest model wins regardless of quality), Yomi Hustle is **simultaneous-turn-based**. The game waits for both players to submit moves before resolving. This means:
- Model reasoning quality matters more than API latency
- Extended thinking / chain-of-thought models can actually think
- Rich strategic context can be provided in prompts
- The "yomi" (reading the opponent's mind) mechanic directly tests adversarial reasoning

### Prior Art
- **LLM Colosseum** (`github.com/OpenGenerativeAI/llm-colosseum`): LLMs play Street Fighter III via DIAMBRA. Claude Haiku won because it was fastest, not smartest. Yomi Hustle eliminates this problem.
- **Goon AI Mod** (Steam Workshop ID 3293858890): One-step lookahead bot for Yomi Hustle. Enumerates moves, simulates 15 frames, picks best. Proves the interception point works.
- **_AIOpponents** (`github.com/AxNoodle/_AIOpponents`): Similar lookahead AI, controls any character. Good reference for move interception patterns.
- **The Hacker** (Steam Workshop ID 3110414396): A character mod that executes arbitrary GDScript in-game, exposing `p1`, `p2`, and `objects` variables. Proves full game state is accessible.
- **Manual ChatGPT experiment**: Someone manually described game state to ChatGPT turn-by-turn. Proved the concept but bottlenecked by human relay speed. (Documented at toolify.ai)
- **No automated LLM pipeline for Yomi Hustle exists yet.**

---

## 2. Game Technical Details

### Engine and Language
- **Engine**: Godot 3.5.1
- **Language**: GDScript (Python-like scripting language)
- **Source accessibility**: Full source is recoverable via GDRETools/gdsdecomp (`github.com/GDRETools/gdsdecomp`). Decompile the game's `.pck` file to get a complete working Godot project with all `.gd` scripts, `.tscn` scenes, and resources. The modding community considers this mandatory for serious modding.

### Game Mechanics
Yomi Hustle is a **2D simultaneous-turn-based fighting game**:
1. Both players see the full game state (positions, health, meter, etc.)
2. Both players simultaneously choose a move (attack, movement, block, throw, special, DI, etc.)
3. The game resolves both moves simultaneously, advancing the simulation
4. Players see the result and choose again
5. Match ends when one player's HP reaches 0

**Key concepts:**
- **Simultaneous moves**: Neither player knows the other's choice. This is the core "yomi" (reading) mechanic.
- **DI (Directional Influence)**: When hit, players choose a direction to influence their knockback trajectory. This is a separate decision point.
- **Meter / Super**: Resource that builds over the match, enabling powerful moves.
- **Burst**: A defensive mechanic, limited uses per match.
- **State machine**: Characters use a `states_map` dictionary mapping state name strings to state objects. States include attacks, movement, hitstun, blockstun, etc.
- **Frame data**: Each move has startup frames, active frames, recovery frames, damage, knockback, range, etc.

### Characters (Base Cast)
The game ships with multiple characters (Cowboy, Robot, Ninja, Wizard, etc.), each with unique movesets. Character mods can also be loaded via Steam Workshop.

### Game State Structure
Based on existing AI mods and The Hacker character, the following state is accessible per fighter:
- `hp` — current health points
- `position` — Vector2 (x, y)
- `velocity` — Vector2 (x, y)
- `facing` — direction the character faces (-1 or 1)
- `current_state` — string name of current state machine state
- `combo_count` — current combo counter
- `meter` / `super_meter` — super gauge value
- `burst` — burst availability/count
- `hitstun` / `hitlag` — stun frame counters
- Available moves list (varies by current state)

Additionally, `objects` array contains active projectiles and hitboxes.

### Decision Points
The game has two primary decision types:
1. **Move selection** — choose an attack, movement, block, throw, special, etc. from a list of legal moves given current state
2. **DI selection** — when being hit/launched, choose a direction vector to influence trajectory

Each decision may also include:
- **Feint** — cancel an attack startup (costs meter)
- **Reverse** — change facing direction

---

## 3. Modding System

### ModLoader Architecture
Yomi Hustle has a built-in ModLoader system. Key APIs:

```gdscript
# ModMain.gd — mod entry point
func _init(modLoader = ModLoader):
    modLoader.installScriptExtension("res://path/to/OverrideScript.gd")
    modLoader.saveScene(scene, "res://path/to/scene.tscn")
```

- `installScriptExtension()` — uses Godot's `take_over_path()` to hot-patch any game script. A child script takes over the resource path of a parent script. **Any game script can be overridden.**
- `saveScene()` — replaces scene resources at a given path.
- The ModLoader is "pretty laissez-faire" — no restrictions on what can be overridden.

### Mod Structure
```
ModName/
  _metadata          # name, version, dependencies (plain text)
  ModMain.gd         # init entry point
  [other .gd files]  # script extensions, new scripts
  [.tscn files]      # scene overrides
  [assets]           # sprites, sounds, etc.
```

Mods are distributed as `.zip` files. The game loads zips from disk at startup. Steam Workshop integration is available.

### Key Internal Paths (from decompilation / mod source)
- `res://ui/CSS/CharacterSelect.gd` — character select screen
- `res://ui/CSS/CharacterButton.tscn` — character button scene
- `res://main.gd` — main game script
- `res://char_loader/Network.gd` — networking
- `res://char_loader/SteamLobby.gd` — Steam lobby
- `Global.VERSION` — global autoload with game version

### Modding Tools and Resources
| Tool | Purpose | Location |
|---|---|---|
| GDRETools / gdsdecomp | Decompile game PCK to full source | `github.com/GDRETools/gdsdecomp` |
| Godot 3.5.1 editor | Develop and test mods | Must use exact version 3.5.1 |
| bustle | Build tool: zip/deploy/Workshop upload | `github.com/dustinlacewell/bustle` |
| YHMod Assistant | Godot plugin with char templates | `github.com/Valkarin1029/YHModAssistant` |
| YOMI Hustle Mod Wiki | Community documentation | `tiggerbiggo.github.io/YomiHustleModWiki/` |
| YOMI Modding Discord | Primary community hub | `discord.gg/yomimodding` |
| YomiModding GitLab | MODDING.md docs | `gitlab.com/ZT2wo/YomiModding` |
| GameBanana | Mod hosting/discovery | `gamebanana.com/games/17961` |

### How Existing AI Mods Work
The Goon AI mod's approach (reference implementation):
1. Hooks into the turn-resolution loop
2. When it's time to select a move, enumerates all legal moves
3. For each candidate move, simulates the outcome for N frames
4. Picks the move that maximizes damage dealt minus damage taken
5. Injects that move as the player's selection

This proves: **move selection can be intercepted, legal moves can be enumerated, and programmatic move injection works.**

---

## 4. Architecture

### Three-Layer Design

```
┌──────────────────────────────────────────────────────┐
│              YOMI HUSTLE (Godot 3.5.1)                │
│                                                        │
│   ┌────────────────────────────────────────────┐      │
│   │           LLM Bridge Mod (GDScript)         │      │
│   │                                              │      │
│   │  ┌─────────────┐    ┌─────────────┐         │      │
│   │  │ P1 AI Hook  │    │ P2 AI Hook  │         │      │
│   │  └──────┬──────┘    └──────┬──────┘         │      │
│   │         │                   │                │      │
│   │    ┌────▼───────────────────▼────┐          │      │
│   │    │   WebSocket Server          │          │      │
│   │    │   (localhost:8765)           │          │      │
│   │    └────────────┬───────────────┘          │      │
│   └─────────────────┼──────────────────────────┘      │
└─────────────────────┼──────────────────────────────────┘
                      │ JSON over WebSocket
         ┌────────────▼────────────┐
         │    Python Daemon         │
         │                          │
         │  ┌────────┐ ┌────────┐  │
         │  │ P1 Agent│ │P2 Agent│  │
         │  └───┬────┘ └───┬────┘  │
         │      │          │        │
         │  ┌───▼──┐  ┌───▼──┐    │
         │  │Claude │  │GPT-4o│    │
         │  │  API  │  │ API  │    │
         │  └──────┘  └──────┘    │
         │                          │
         │  ┌──────────────────┐   │
         │  │ Tournament Runner │   │
         │  │ Logger / Ratings  │   │
         │  └──────────────────┘   │
         └─────────────────────────┘
```

### Layer 1: Game Mod (GDScript)

**Purpose**: Extract game state, present legal moves, inject AI decisions.

**Responsibilities**:
1. Override the move-selection entry point via `installScriptExtension`
2. At each decision point:
   a. Serialize full game state (both fighters, projectiles, stage) to JSON
   b. Enumerate all legal moves for the active player with metadata (damage, range, speed, type)
   c. Send a `DecisionRequest` to the daemon via WebSocket
   d. Wait for an `ActionDecision` response (with configurable timeout)
   e. Validate the response (is the move ID in the legal set?)
   f. Inject the move into the game's move-selection system
   g. If timeout or invalid response, apply a fallback move (block/DI away)
3. Emit telemetry events (match start, turn requested, decision applied, match end)
4. Support configuration: which players are AI-controlled, timeout values, fallback policy

**Configuration**:
```gdscript
var config = {
  "p1_mode": "llm",       # "human" | "llm"
  "p2_mode": "llm",       # "human" | "llm"
  "daemon_host": "127.0.0.1",
  "daemon_port": 8765,
  "timeout_ms": 10000,    # generous for LLM API calls
  "fallback_move": "block",
  "fallback_di": {"x": 0, "y": -100},  # DI up/away
  "log_level": "info"
}
```

**Critical implementation note**: The mod must be built and tested against Godot 3.5.1 specifically. Use the exact editor version the game was built with.

**WebSocket in Godot 3.5**: Use `WebSocketClient` (built-in). The mod is the WebSocket **client**; the Python daemon is the **server**. This is simpler than making the mod a server because Godot's WebSocket server support in 3.5 is less mature, and the daemon needs to manage the connection lifecycle anyway.

Wait — actually, reconsider: the mod should be the **client** connecting to the daemon **server**. The daemon starts first, listens on a port, then the game launches and the mod connects. This is cleaner because:
- The daemon can be started independently and wait for connections
- Multiple game instances could connect (for parallel matches)
- The daemon controls the lifecycle

### Layer 2: Python Daemon

**Purpose**: Route decision requests to LLM APIs, validate responses, orchestrate matches.

**Responsibilities**:
1. Host a WebSocket server on localhost
2. Accept connections from game mod instances
3. For each `DecisionRequest`:
   a. Route to the correct policy adapter (based on player ID and config)
   b. Format game state into an LLM prompt
   c. Call the LLM API
   d. Parse the response to extract a valid move ID
   e. Validate against the legal moves list
   f. Return an `ActionDecision` to the mod
   g. If parsing fails, retry once with a correction prompt
   h. If still fails, return a fallback action
4. Log every request/response pair with timestamps, latency, token counts
5. Track per-model statistics (win rate, avg response time, cost, fallback rate)

**Policy Adapter Interface**:
```python
class PolicyAdapter(Protocol):
    """Interface for LLM policy adapters."""

    async def decide(self, request: DecisionRequest) -> ActionDecision:
        """Given game state and legal moves, return a move selection."""
        ...

    @property
    def id(self) -> str:
        """Unique identifier for this policy (e.g., 'anthropic/claude-sonnet-4-6')."""
        ...
```

**Built-in adapters**:
- `AnthropicAdapter` — Claude models via Anthropic API
- `OpenAIAdapter` — GPT models via OpenAI API
- `GoogleAdapter` — Gemini models via Google AI API
- `OllamaAdapter` — Local models via Ollama
- `BaselineAdapter` — Non-LLM baselines:
  - `random` — uniform random legal move
  - `block_always` — always block (defensive baseline)
  - `greedy_damage` — pick highest-damage move in range (offensive baseline)

### Layer 3: Prompt Engineering

**This is the most critical design surface.** The prompt must give the LLM enough context to make strategic decisions.

**Prompt structure**:
```
[SYSTEM]
You are playing Your Only Move Is HUSTLE, a simultaneous-turn fighting game.
Both players choose moves at the same time, then the game resolves them.
You are Player {N} playing as {CHARACTER}.

Key rules:
- Attacks beat throws (if faster or if opponent is in throw range)
- Throws beat blocks (throws are unblockable)
- Blocks reduce damage from attacks
- Movement can avoid attacks and reposition
- Super moves cost meter but are powerful
- DI (directional influence) lets you control your trajectory when hit

[USER]
=== TURN {turn_number} ===

YOUR STATUS:
  HP: {hp}/100 | Meter: {meter}% | Burst: {burst_count}
  Position: ({x}, {y}) | Facing: {direction}
  State: {current_state}

OPPONENT STATUS:
  HP: {opp_hp}/100 | Meter: {opp_meter}% | Burst: {opp_burst}
  Position: ({opp_x}, {opp_y}) | Facing: {opp_direction}
  State: {opp_state}

DISTANCE: {distance} units
ACTIVE PROJECTILES: {projectile_descriptions}

LAST TURN:
  You used: {your_last_move} → {result}
  Opponent used: {opp_last_move} → {result}

MOVE HISTORY (last 10 turns):
{formatted_history_table}

OPPONENT TENDENCIES:
  Most used moves: {top_3_moves_with_percentages}
  After blocking: tends to {post_block_pattern}
  At close range: tends to {close_range_pattern}

YOUR AVAILABLE MOVES:
{for each move:}
  [{index}] {move_id} — {type} | {damage} dmg | {startup}f startup | {range} range
    {one_line_description}
{end for}

Choose ONE move by responding with just the move ID.
Think about what your opponent is likely to do based on the game state and their tendencies.
```

**Prompt variants to experiment with**:
1. **Minimal** — just state + moves, no strategy hints
2. **Strategic** — include game theory context, opponent modeling
3. **Chain-of-thought** — ask for reasoning before the move choice
4. **Few-shot** — include example turns with good/bad decisions
5. **Character-specific** — include character matchup knowledge

**Response parsing**:
- Extract the first valid move ID from the response
- Handle common LLM response patterns:
  - Just the move name: `"jab"`
  - With reasoning: `"I'll use jab because..." → extract "jab"`
  - Numbered: `"[3] heavy_kick" → extract "heavy_kick"`
  - With formatting: `"**block**" → extract "block"`
- If no valid move found, retry with: `"Your response did not contain a valid move. Valid moves are: {list}. Respond with ONLY the move name."`

---

## 5. Data Schemas

### DecisionRequest (Mod → Daemon)
```json
{
  "type": "decision_request",
  "match_id": "abc123",
  "turn_id": 42,
  "player_id": 1,
  "deadline_ms": 10000,
  "observation": {
    "tick": 1260,
    "fighters": [
      {
        "id": 1,
        "character": "Cowboy",
        "hp": 75.0,
        "max_hp": 100.0,
        "meter": 40.0,
        "burst": 1,
        "position": {"x": 150.0, "y": 300.0},
        "velocity": {"x": 0.0, "y": 0.0},
        "facing": 1,
        "current_state": "Idle",
        "combo_count": 0,
        "hitstun": 0,
        "hitlag": 0
      },
      {
        "id": 2,
        "character": "Robot",
        "hp": 60.0,
        "max_hp": 100.0,
        "meter": 80.0,
        "burst": 0,
        "position": {"x": 400.0, "y": 300.0},
        "velocity": {"x": 0.0, "y": 0.0},
        "facing": -1,
        "current_state": "Idle",
        "combo_count": 0,
        "hitstun": 0,
        "hitlag": 0
      }
    ],
    "objects": [
      {
        "type": "projectile",
        "owner": 2,
        "position": {"x": 350.0, "y": 300.0},
        "velocity": {"x": -5.0, "y": 0.0}
      }
    ],
    "stage": {
      "width": 800,
      "ground_y": 400
    }
  },
  "legal_actions": [
    {
      "id": "jab",
      "type": "attack",
      "damage": 8,
      "startup_frames": 3,
      "range": 50,
      "meter_cost": 0,
      "description": "Fast close-range punch"
    },
    {
      "id": "block",
      "type": "defense",
      "damage": 0,
      "description": "Reduces incoming damage from attacks"
    },
    {
      "id": "throw",
      "type": "throw",
      "damage": 12,
      "startup_frames": 2,
      "range": 30,
      "description": "Short-range unblockable grab"
    }
  ],
  "decision_type": "move_select",
  "history": [
    {"turn": 41, "p1_move": "jab", "p2_move": "block", "result": "blocked"},
    {"turn": 40, "p1_move": "block", "p2_move": "throw", "result": "p2_hit_throw"}
  ]
}
```

### ActionDecision (Daemon → Mod)
```json
{
  "type": "action_decision",
  "match_id": "abc123",
  "turn_id": 42,
  "action": "jab",
  "extra": {
    "di": {"x": 0, "y": 0},
    "feint": false,
    "reverse": false
  },
  "debug": {
    "policy_id": "anthropic/claude-sonnet-4-6",
    "latency_ms": 1450,
    "tokens_in": 820,
    "tokens_out": 15,
    "reasoning": "Opponent has been blocking frequently, so jab is safe."
  }
}
```

### MatchResult (end-of-match artifact)
```json
{
  "match_id": "abc123",
  "timestamp": "2026-03-11T15:30:00Z",
  "winner": 1,
  "p1": {
    "policy_id": "anthropic/claude-sonnet-4-6",
    "character": "Cowboy",
    "final_hp": 35.0,
    "total_damage_dealt": 100.0,
    "total_damage_taken": 65.0,
    "moves_used": {"jab": 12, "block": 8, "throw": 3, "heavy_kick": 5},
    "fallback_count": 0,
    "avg_latency_ms": 1200,
    "total_tokens": 45000,
    "total_cost_usd": 0.12
  },
  "p2": {
    "policy_id": "openai/gpt-4o",
    "character": "Robot",
    "final_hp": 0.0,
    "total_damage_dealt": 65.0,
    "total_damage_taken": 100.0,
    "moves_used": {"laser": 10, "block": 15, "dash": 7},
    "fallback_count": 1,
    "avg_latency_ms": 900,
    "total_tokens": 38000,
    "total_cost_usd": 0.08
  },
  "total_turns": 47,
  "duration_seconds": 120,
  "turn_log_path": "runs/abc123/turns.jsonl"
}
```

---

## 6. Message Protocol

### Connection Lifecycle
1. Daemon starts, opens WebSocket server on `localhost:8765`
2. Game launches with mod loaded
3. Mod connects to daemon, sends `Hello`:
   ```json
   {"type": "hello", "mod_version": "0.1.0", "game_version": "1.9.0", "schema_version": "v1"}
   ```
4. Daemon responds with `HelloAck`:
   ```json
   {"type": "hello_ack", "daemon_version": "0.1.0", "p1_policy": "anthropic/claude-sonnet-4-6", "p2_policy": "openai/gpt-4o"}
   ```
5. Match begins

### Turn Loop
1. Game reaches decision point for player N
2. Mod sends `DecisionRequest` (see schema above)
3. Daemon routes to policy adapter, gets response
4. Daemon sends `ActionDecision` back
5. Mod validates and applies (or falls back)
6. Mod sends `TurnResult` event:
   ```json
   {"type": "turn_result", "match_id": "abc123", "turn_id": 42, "applied_action": "jab", "was_fallback": false, "latency_ms": 1450}
   ```
7. Game resolves turn, proceeds to next decision point

### Match End
Mod sends `MatchEnd`:
```json
{"type": "match_end", "match_id": "abc123", "winner": 1, "reason": "ko", "total_turns": 47}
```

### Error Handling
- If WebSocket disconnects mid-match: mod uses fallback moves for all remaining turns, logs disconnect
- If daemon sends invalid JSON: mod logs error, uses fallback
- If daemon response has wrong `match_id` or `turn_id`: mod rejects, uses fallback

---

## 7. Tournament System

### Match Configuration
```yaml
# tournament_config.yaml
tournament:
  name: "LLM Arena Season 1"
  format: "round_robin"     # round_robin | swiss | elimination
  matches_per_pair: 10       # for statistical significance
  side_swap: true            # play each pair twice, swapping P1/P2

characters:
  mode: "mirror"             # mirror | assigned | random
  mirror_character: "Cowboy" # both play same character (isolates strategy)
  # OR
  # assigned: {"p1": "Cowboy", "p2": "Robot"}
  # OR
  # pool: ["Cowboy", "Robot", "Ninja"]  # random from pool

stage: "default"

policies:
  - id: "claude-sonnet"
    provider: "anthropic"
    model: "claude-sonnet-4-6"
    temperature: 0.7
    prompt_template: "prompts/strategic_v1.md"

  - id: "claude-opus"
    provider: "anthropic"
    model: "claude-opus-4-6"
    temperature: 0.5
    prompt_template: "prompts/strategic_v1.md"

  - id: "gpt-4o"
    provider: "openai"
    model: "gpt-4o"
    temperature: 0.7
    prompt_template: "prompts/strategic_v1.md"

  - id: "gemini-2.5-pro"
    provider: "google"
    model: "gemini-2.5-pro"
    temperature: 0.7
    prompt_template: "prompts/strategic_v1.md"

  - id: "random-baseline"
    provider: "baseline"
    model: "random"

  - id: "greedy-baseline"
    provider: "baseline"
    model: "greedy_damage"

timeouts:
  decision_ms: 15000
  match_total_s: 600   # kill match if it exceeds 10 minutes

logging:
  dir: "runs/"
  jsonl: true
  save_prompts: true    # save full prompt text for debugging
```

### Rating System
- **ELO** for MVP (K-factor = 32, starting rating 1500)
- Track per-character and per-matchup ratings separately
- Minimum 20 matches before a rating is considered stable
- Report confidence intervals

### Tournament Runner Flow
```
for each pair (policy_a, policy_b) in round_robin(policies):
    for match_num in range(matches_per_pair):
        for side in [("p1", "p2"), ("p2", "p1")] if side_swap else [("p1", "p2")]:
            1. Generate match_id
            2. Configure daemon with policy assignments
            3. Launch game (or signal mod to start new match)
            4. Wait for MatchEnd event
            5. Record result
            6. Update ELO ratings
            7. Cool-down period (avoid API rate limits)
```

### Output Artifacts
```
runs/
  tournament_2026-03-11/
    config.yaml                    # frozen tournament config
    results.json                   # aggregated results, ratings
    leaderboard.json               # sorted by ELO
    matchups.json                  # per-pair win rates

    matches/
      match_001_abc123/
        manifest.json              # config snapshot, policy IDs, character, etc.
        turns.jsonl                # every turn: state, prompt, response, move
        prompts.jsonl              # full prompt text per turn (if save_prompts=true)
        result.json                # match outcome

      match_002_def456/
        ...
```

---

## 8. Game Theory Considerations

### The Simultaneous-Move Problem
Yomi Hustle is a **perfect-information, simultaneous-move, extensive-form game**. You see all state but not the opponent's current choice. This means:
- There is no single "best move" — only **mixed-strategy equilibria** (probability distributions over moves)
- The optimal strategy against an unknown opponent is the **Nash equilibrium mixed strategy**, which prevents exploitation regardless of what the opponent does
- But against a predictable opponent (like an LLM with consistent patterns), **exploitative strategies** can do better

### Approaches (Increasing Sophistication)

**Level 0: Pure LLM reasoning**
- Just give the LLM the state and ask it to pick a move
- Tests raw strategic reasoning ability
- Simple to implement, provides clean baseline comparison between models

**Level 1: LLM + opponent frequency data**
- Track opponent's move frequency distribution across the match
- Include in prompt: "Opponent has used jab 40%, block 30%, throw 20%, other 10%"
- Let the LLM reason about exploitation opportunities
- This is the **recommended starting point** — adds real strategic depth without algorithmic complexity

**Level 2: LLM + game simulation**
- For each candidate move, simulate the next N frames assuming various opponent responses
- Present the LLM with outcome trees rather than raw state
- Hybrid of the existing Goon AI approach + LLM decision-making

**Level 3: LLM + CFR hybrid**
- Use Counterfactual Regret Minimization to compute the game-theoretically optimal mixed strategy
- Use the LLM to weight/adjust the mixed strategy based on opponent reads
- Most sophisticated; probably overkill for MVP

**Recommendation**: Start with Level 0, add Level 1 once basic pipeline works.

### Relevant Algorithms (for reference)
- **CFR (Counterfactual Regret Minimization)**: Gold standard for imperfect-info games. Converges to Nash equilibrium. Used in poker AI. Reference: Neller & Lanctot tutorial.
- **Regret Matching**: Simpler variant. Track regret per move, randomize proportionally. Good for repeated play.
- **MCTS for simultaneous games**: Standard MCTS doesn't handle simultaneous moves. Extensions using Exp3 or Regret Matching as selection function do converge. Reference: Maastricht paper "Monte Carlo Tree Search in Simultaneous Move Games."
- **OpenSpiel** (`github.com/google-deepmind/open_spiel`): DeepMind's library for RL/game theory. Has built-in simultaneous-move game support. Could be used to validate strategies.

---

## 9. Implementation Phases

### Phase 1: Decompile and Map the Codebase (1-2 days)

**Goal**: Understand the game's internal structure well enough to build the mod.

**Steps**:
1. Install GDRETools from `github.com/GDRETools/gdsdecomp`
2. Locate the game's `.pck` file (in Steam install directory)
3. Decompile to a full Godot project
4. Open in Godot 3.5.1 editor
5. Map the critical code paths:
   - Where does move selection happen? (find the function that sets the player's queued action)
   - How is the legal move list generated? (find the function that returns available moves for a given state)
   - How is turn resolution triggered? (find the signal/function that advances the game after both players choose)
   - What state variables exist on fighters? (inventory all properties on the fighter node)
   - How do existing AI mods intercept the move selection? (study Goon/_AIOpponents source)
6. Document all findings in `docs/game_internals.md`

**Reference mods to study**:
- `github.com/AxNoodle/_AIOpponents` — most complete AI mod, covers all characters
- Steam Workshop AI mods (IDs 3293858890, 3112147708)
- `github.com/GithubSPerez/char_loader` — shows how to override CharacterSelect, Network, SteamLobby

### Phase 2: Build the Game Mod (3-5 days)

**Goal**: A working mod that intercepts move selection and communicates via WebSocket.

**Steps**:
1. Set up mod project structure with `_metadata` and `ModMain.gd`
2. Override the move-selection script using `installScriptExtension`
3. Implement `ObservationBuilder` — serialize fighter state, projectiles, stage to JSON
4. Implement `LegalActionBuilder` — enumerate available moves with metadata
5. Implement `WebSocketClient` — connect to daemon, send requests, receive responses
6. Implement `DecisionApplier` — take the response and inject the move
7. Implement `FallbackHandler` — handle timeout, invalid response, disconnection
8. Implement configuration system (which players are AI, timeout, etc.)
9. Test with a simple echo daemon that always returns "block"
10. Package as `.zip` mod

**Testing strategy**:
- Test against human player first (AI controls P2, human plays P1)
- Verify state extraction accuracy by logging and comparing to visual game state
- Verify move injection by sending known moves and confirming they execute
- Test timeout fallback by making the daemon intentionally slow
- Test invalid response handling by sending garbage from the daemon

### Phase 3: Build the Python Daemon (3-5 days)

**Goal**: A daemon that receives game state, calls LLM APIs, and returns moves.

**Steps**:
1. Set up Python project with `pyproject.toml`, async WebSocket server (use `websockets` library)
2. Implement message parsing and validation
3. Implement prompt formatting engine
4. Implement Anthropic adapter (Claude models)
5. Implement OpenAI adapter (GPT models)
6. Implement baseline adapters (random, greedy, block-always)
7. Implement response parsing with retry logic
8. Implement logging (JSONL per match)
9. Implement basic match orchestration (start match, track turns, record result)
10. Test end-to-end: daemon + mod + actual LLM API calls

**Python dependencies**:
- `websockets` — WebSocket server
- `anthropic` — Anthropic API client
- `openai` — OpenAI API client
- `google-genai` — Google AI client
- `pydantic` — schema validation
- `structlog` — structured logging
- `click` or `typer` — CLI interface

### Phase 4: Prompt Engineering and Iteration (3-5 days, ongoing)

**Goal**: Develop prompts that produce competent play.

**Steps**:
1. Design initial prompt template (see Section 4, Layer 3)
2. Run LLM vs random-baseline matches — verify the LLM can beat random
3. Run LLM vs block-always matches — verify the LLM learns to throw
4. Run LLM vs greedy-damage matches — verify the LLM can play defense
5. Iterate on prompt based on failure modes:
   - If LLM hallucinates moves: tighten the response format, add validation
   - If LLM ignores distance: add explicit range context
   - If LLM never uses meter/super: add resource management hints
   - If LLM always does the same thing: increase temperature, add variety encouragement
6. Test chain-of-thought vs direct response (CoT may be better for complex decisions)
7. Test with/without opponent modeling data

### Phase 5: Tournament System (2-3 days)

**Goal**: Automated round-robin tournament with ELO tracking.

**Steps**:
1. Implement tournament config parser
2. Implement round-robin scheduler with side swap
3. Implement ELO rating calculator
4. Implement results aggregation and reporting
5. Implement cost tracking (tokens used, API cost per match)
6. Run initial tournament: 4 models, mirror matches, 10 games per pair
7. Generate leaderboard and matchup matrix

### Phase 6: Advanced Features (ongoing)

- **Opponent modeling in prompt**: track and report move frequencies
- **Multi-game memory**: let LLMs reference previous matches against same opponent
- **Screenshot mode**: capture game frame and send to multimodal models alongside text state
- **Replay viewer**: export match data in a format viewable in the game's replay system
- **Head-to-head mode**: human plays against LLM through the same mod interface
- **Multiple characters**: extend beyond mirror matches to cross-character matchups
- **Streaming commentary**: have a separate LLM watch the match and provide play-by-play

---

## 10. Repository Structure

```
budok-ai/
  specs/
    claude_spec.md              # this document

  docs/
    game_internals.md           # findings from decompilation (Phase 1)
    protocol.md                 # message protocol reference
    prompt_engineering.md       # prompt iteration notes and results

  mod/
    YomiLLMBridge/
      _metadata                 # mod name, version, dependencies
      ModMain.gd                # entry point, installs script extensions
      bridge/
        BridgeClient.gd         # WebSocket client, message send/receive
        ObservationBuilder.gd   # serialize game state to JSON
        LegalActionBuilder.gd   # enumerate legal moves with metadata
        DecisionApplier.gd      # inject move selection into game
        FallbackHandler.gd      # timeout and error fallback logic
        Config.gd               # mod configuration
      ui/
        ModOptionsUI.gd         # in-game config panel (optional)

  daemon/
    pyproject.toml
    src/yomi_daemon/
      __init__.py
      cli.py                    # CLI entry point
      server.py                 # WebSocket server
      protocol.py               # message types, parsing, validation
      prompt.py                 # prompt formatting engine
      response_parser.py        # extract move from LLM response
      match.py                  # match state tracking
      adapters/
        __init__.py
        base.py                 # PolicyAdapter protocol
        anthropic.py            # Claude models
        openai.py               # GPT models
        google.py               # Gemini models
        ollama.py               # local models
        baseline.py             # random, greedy, block-always
      tournament/
        runner.py               # tournament orchestration
        scheduler.py            # matchup scheduling
        ratings.py              # ELO calculation
        reporter.py             # results aggregation, leaderboard
      logging/
        writer.py               # JSONL log writer
        schemas.py              # Pydantic models for all message types
    tests/
      test_protocol.py
      test_prompt.py
      test_response_parser.py
      test_adapters.py
      test_ratings.py
      conftest.py               # fixtures with sample game states

  prompts/
    system_v1.md                # system prompt template
    strategic_v1.md             # full strategic prompt
    minimal_v1.md               # minimal prompt (baseline)
    cot_v1.md                   # chain-of-thought variant

  runs/                         # tournament output (gitignored)
    .gitkeep

  scripts/
    decompile.sh                # helper to decompile game PCK
    run_match.sh                # run a single match
    run_tournament.sh           # run a full tournament
    package_mod.sh              # package mod as .zip

  .gitignore
  README.md
  LICENSE
```

---

## 11. Key Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Game updates break the mod** | Mod stops working entirely | Pin to a specific game version. The modding community deals with this regularly — version-locked mods are standard practice. |
| **Can't find the move-selection interception point** | Can't build the mod | Study existing AI mods (Goon, _AIOpponents) — they have already solved this. Decompile and trace their code paths. |
| **LLMs hallucinate invalid moves** | Turns wasted on fallback moves | Strict validation + retry with correction prompt + fallback. Log failure rate to iterate on prompts. |
| **LLMs play poorly (near-random)** | Results aren't interesting | Start with baselines to verify LLMs beat random. Add opponent modeling. Try chain-of-thought. Use stronger models. |
| **High API costs per match** | Can't run enough matches for significance | Track cost per match. Use cheaper models for initial testing. Optimize prompt length. Set per-tournament budget limits. |
| **Godot 3.5.1 WebSocket quirks** | Connection instability | Test thoroughly. Implement reconnection logic. The mod community has used WebSockets in mods before. |
| **Turn timeout too short for LLM API calls** | High fallback rate | Default 10-15s timeout. The game is turn-based, so waiting is fine gameplay-wise. Track p95 latency per model. |
| **Simultaneous-move game theory is hard** | LLMs converge to predictable strategies | This is actually interesting research data. Document the patterns. Add opponent modeling to break symmetries. |
| **Mod-to-daemon latency on same machine** | Negligible | localhost WebSocket latency is sub-millisecond. Not a concern. |
| **Game has anti-cheat or mod restrictions online** | Can't use online features | We don't need online features. Everything runs locally. The mod system is officially supported. |

---

## 12. Design Decisions and Rationale

### Why mods, not screen capture?
- LLMs playing from screenshots score near random in fighting games (Atari-GPT research found even GPT-4o, Claude Sonnet, Gemini score ~1.6% on complex game benchmarks from pixels alone)
- Structured JSON state is 100% accurate, no OCR/vision errors
- Move injection via mod is deterministic, no pixel-approximate inputs
- The mod ecosystem is mature and AI mods already exist

### Why WebSocket, not HTTP request-response?
- Persistent connection avoids reconnection overhead per turn
- Bidirectional: daemon can push config updates or control signals
- WebSocket is well-supported in both Godot 3.5 and Python
- Could also work with HTTP (simpler but less flexible) — WebSocket is preferred

### Why Python daemon, not in-process LLM?
- Python has first-class SDKs for all LLM providers (anthropic, openai, google-genai)
- GDScript has limited HTTP client capabilities for streaming LLM APIs
- Separation of concerns: game logic in Godot, AI logic in Python
- Easier to swap models, add logging, run experiments
- Could run on separate machines for GPU-heavy local models

### Why mod is WebSocket client, daemon is server?
- Daemon starts first and waits for connections
- Multiple game instances could connect for parallel tournaments
- Daemon controls lifecycle and configuration
- Cleaner than having the mod host a server

### Why ELO over other rating systems?
- Simple, well-understood, sufficient for MVP
- Glicko-2 is better (handles uncertainty) but overkill for initial experiments
- Can upgrade later without invalidating earlier results

### Why mirror matches first?
- Same character eliminates character-balance confounds
- Isolates strategic reasoning quality
- Simpler to implement (one character's moves to describe)
- Cross-character matches can be added in Phase 6

---

## 13. Success Criteria

### MVP (Minimum Viable Product)
- [ ] Two LLMs play a full match autonomously with zero human intervention
- [ ] All turns produce valid moves (fallback rate < 5%)
- [ ] Match results are logged with full turn-by-turn data
- [ ] At least one LLM consistently beats the random baseline
- [ ] System handles disconnection and timeout gracefully

### V1 (Full Tournament)
- [ ] Round-robin tournament with 4+ models completes automatically
- [ ] ELO ratings computed with confidence intervals
- [ ] Win rates differ significantly from 50/50 (some models are measurably better)
- [ ] Cost per match tracked and reportable
- [ ] Results reproducible with same config and seed

### Stretch Goals
- [ ] Observable strategic adaptation (LLMs change behavior based on opponent patterns)
- [ ] LLM beats the existing Goon AI bot
- [ ] Cross-character matchups produce interesting asymmetries
- [ ] Replay export viewable in-game
- [ ] Live commentary by a separate LLM watching the match

---

## 14. External Resources and References

### Game
- Steam store page: App ID 2212330
- Developer: ivy sly (@ivy_sly_)
- Engine: Godot 3.5.1, GDScript

### Modding
- Mod Wiki: `tiggerbiggo.github.io/YomiHustleModWiki/`
- Modding Discord: `discord.gg/yomimodding`
- Modding Tutorial Series: Steam Community Guide ID 2940757626
- GameBanana: `gamebanana.com/games/17961`
- GitLab MODDING.md: `gitlab.com/ZT2wo/YomiModding`

### Existing AI Mods (study these)
- Goon AI: Steam Workshop ID 3293858890
- _AIOpponents: `github.com/AxNoodle/_AIOpponents`
- Basic AI Mod: Steam Workshop ID 3112147708
- The Hacker: Steam Workshop ID 3110414396

### Modding Tools
- GDRETools: `github.com/GDRETools/gdsdecomp`
- bustle: `github.com/dustinlacewell/bustle`
- YHMod Assistant: `github.com/Valkarin1029/YHModAssistant`
- char_loader: `github.com/GithubSPerez/char_loader`

### Related AI Projects
- LLM Colosseum (SF3): `github.com/OpenGenerativeAI/llm-colosseum`
- DIAMBRA Arena: `github.com/diambra/arena`
- GamingAgent/lmgame-Bench: `github.com/lmgame-org/GamingAgent`
- OpenSpiel: `github.com/google-deepmind/open_spiel`
- Godot-LLM integration: `github.com/Adriankhl/godot-llm`
- OvercookedGPT: `github.com/BladeTransformerLLC/OvercookedGPT`

### Game Theory
- CFR tutorial: Neller & Lanctot (Imperial College PDF)
- MCTS for simultaneous games: Maastricht paper
- Online Outcome Sampling: AAMAS paper by Lanctot
- Yomi concept: Sirlin's "Yomi Layer 3" article

### Academic
- "A Survey on LLM-Based Game Agents" (arxiv 2404.02039)
- "Playing games with LLMs" (arxiv 2503.02582)
- "Playing repeated games with LLMs" (arxiv 2305.16867)
- Atari-GPT benchmark results (arxiv 2408.15950)
