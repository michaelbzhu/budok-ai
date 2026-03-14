# Running Experiments

This guide covers how to run matches, tournaments, and analyze results. See [`setup.md`](setup.md) for prerequisites.

## Single match (daemon only)

Start the daemon and wait for a game client to connect:

```bash
scripts/run_local_match.sh --p1-policy baseline/random --p2-policy baseline/block_always
```

Or with a provider-backed policy:

```bash
ANTHROPIC_API_KEY=sk-ant-... scripts/run_local_match.sh \
  --p1-policy anthropic/claude --p2-policy baseline/greedy_damage \
  --trace-seed 42
```

The daemon listens on `ws://127.0.0.1:8765` and waits for the mod to connect. Launch YOMI Hustle with the mod enabled to start the match.

When the match ends, artifacts are written to `runs/<timestamp>_<match_id>/`.

## Live match (with mod install check)

For a more complete workflow that checks mod installation:

```bash
scripts/run_live_match.sh --game-dir /path/to/YomiHustle \
  --p1-policy anthropic/claude --p2-policy baseline/random
```

This script:
1. Checks that `uv` is installed
2. Verifies the mod zip exists (run `scripts/package_mod.sh` first)
3. Checks mod installation in the game directory
4. Warns about missing API keys for provider policies
5. Starts the daemon and waits for the game to connect
6. Prints a result summary when the match ends

## Tournament (round-robin)

Run a round-robin tournament across multiple policies:

```bash
scripts/run_round_robin.sh baseline/random baseline/block_always baseline/greedy_damage
```

With a custom config:

```bash
scripts/run_round_robin.sh --config daemon/config/tournament.json \
  baseline/random baseline/block_always baseline/greedy_damage baseline/scripted_safe
```

### Preview pairings without running

```bash
scripts/run_round_robin.sh schedule baseline/random baseline/block_always baseline/greedy_damage
```

This prints the match schedule with side-swap and mirror-match annotations.

### Tournament config options

Key `tournament` fields in the daemon config:

| Field | Default | Description |
|---|---|---|
| `format` | `round_robin` | `single`, `side_swapped_pair`, `round_robin`, `double_round_robin` |
| `games_per_pair` | `10` | Matches per ordered pair |
| `side_swap` | `true` | Alternate sides within each pair |
| `mirror_matches_first` | `true` | Schedule self-play before cross-policy matches |
| `fixed_stage` | `training_room` | Lock stage for all matches |

## Analyzing results

### Artifact directory

Each completed match produces:

```
runs/<timestamp>_<match_id>/
  manifest.json       Config, versions, seed
  events.jsonl        Lifecycle events (MatchStarted, TurnRequested, etc.)
  decisions.jsonl     Per-turn request/decision pairs
  prompts.jsonl       Rendered prompts (when logging.prompts enabled)
  metrics.json        Latency stats, fallback rate, token usage
  result.json         Winner, end reason, turn count, status
  replay_index.json   Per-turn pointers into decisions/prompts
  stderr.log          Error output
  replay.mp4          Replay video (when --record-replay enabled)
  match.replay        Game replay file (when --record-replay enabled)
```

### Quick result check

```bash
cat runs/*/result.json | python3 -c "
import json, sys
for line in sys.stdin:
    r = json.loads(line)
    print(f\"{r.get('match_id','?')[:8]}  winner={r.get('winner','?')}  reason={r.get('end_reason','?')}  turns={r.get('total_turns','?')}\")
"
```

### Tournament report

Generate a report from all completed runs:

```bash
scripts/run_round_robin.sh report --runs-dir runs/
```

Or filter to specific matches:

```bash
scripts/run_round_robin.sh report --match-ids match_abc match_def
```

The report includes:
- **Leaderboard** with Elo ratings, win/loss/draw counts, and win rates
- **Matchup table** showing head-to-head records
- **Latency summaries** per policy

Save to a file:

```bash
scripts/run_round_robin.sh report --output results/tournament_report.json
```

### Recomputing ratings

Ratings are derived from `result.json` artifacts and can be recomputed at any time with different parameters:

```bash
scripts/run_round_robin.sh report --k-factor 16
```

Mirror matches (same policy on both sides) are excluded from Elo calculations.

## Reproducibility

### Baseline runs

Baseline-only runs are fully deterministic. To reproduce a run exactly, reuse:

- `trace_seed` from the original `manifest.json`
- `match_id`
- Full config (policy_mapping, fallback_mode, timeout_profile)

```bash
scripts/run_local_match.sh \
  --p1-policy baseline/random --p2-policy baseline/block_always \
  --trace-seed 42
```

The seed combines with `match_id`, `player_id`, `turn_id`, `state_hash`, and `legal_actions_hash` to derive per-turn RNG state.

### Provider-backed runs

Provider decisions are inherently non-deterministic due to model sampling. The `trace_seed` still controls fallback selection and tie-breaking, but primary decisions may vary across runs.

## Benchmarking

### Latency benchmarks

Test conformance against timeout profile budgets:

```bash
uv run --project daemon python scripts/benchmark_latency.py --profile strict_local --turns 20
uv run --project daemon python scripts/benchmark_latency.py --profile llm_tournament --turns 30
```

The benchmark prints p50/p95/p99 latency and fallback rate against spec budgets:

| Profile | Default timeout | p95 budget | Fallback rate budget |
|---|---|---|---|
| `strict_local` | 2500 ms | 1200 ms | 0% |
| `llm_tournament` | 10000 ms | 15000 ms | < 5% |

### Per-match metrics

Each run's `metrics.json` contains latency statistics and fallback counts. Compare across runs to detect regressions.

## Prompt variants

Four prompt templates are available for provider-backed policies:

| Template | Use case |
|---|---|
| `minimal_v1` | Compact, fast, low token usage (default) |
| `strategic_v1` | Expanded strategic reasoning guidance |
| `few_shot_v1` | Includes worked examples of legal decisions |
| `reasoning_v1` | Encourages chain-of-thought reasoning |

Set per-policy in the config:

```json
{
  "anthropic/claude-strategic": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "prompt_version": "strategic_v1",
    "credential_env_var": "ANTHROPIC_API_KEY"
  }
}
```

## Correction retry

Provider policies can optionally retry once when the model returns invalid output:

```json
{
  "anthropic/claude-with-retry": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "prompt_version": "minimal_v1",
    "credential_env_var": "ANTHROPIC_API_KEY",
    "options": {
      "response_parser": {
        "enable_correction_retry": true,
        "max_correction_retries": 1
      }
    }
  }
}
```

When enabled, a bounded correction prompt is sent if the first response fails parsing with `malformed_output` or `illegal_output`. The second attempt is parsed normally. If it also fails, the turn falls back.

## Example experiment: comparing prompt strategies

1. Create a config with multiple policies using different prompts:

```json
{
  "timeout_profile": "llm_tournament",
  "policies": {
    "anthropic/minimal": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-6",
      "prompt_version": "minimal_v1",
      "credential_env_var": "ANTHROPIC_API_KEY"
    },
    "anthropic/strategic": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-6",
      "prompt_version": "strategic_v1",
      "credential_env_var": "ANTHROPIC_API_KEY"
    },
    "anthropic/reasoning": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-6",
      "prompt_version": "reasoning_v1",
      "credential_env_var": "ANTHROPIC_API_KEY"
    },
    "baseline/greedy_damage": {
      "provider": "baseline",
      "prompt_version": "none"
    }
  },
  "tournament": {
    "format": "round_robin",
    "games_per_pair": 5,
    "side_swap": true
  }
}
```

2. Run the tournament:

```bash
ANTHROPIC_API_KEY=sk-ant-... scripts/run_round_robin.sh \
  --config daemon/config/prompt_experiment.json \
  anthropic/minimal anthropic/strategic anthropic/reasoning baseline/greedy_damage
```

3. Generate a report:

```bash
scripts/run_round_robin.sh report --output results/prompt_comparison.json
```

4. Compare Elo ratings, win rates, fallback rates, and latency across prompt strategies.
