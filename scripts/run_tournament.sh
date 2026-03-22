#!/usr/bin/env bash
# Seeded single-elimination bracket tournament for budok-ai.
#
# Runs a best-of-3 bracket with per-game blind pick character selection.
# Each game is run via run_match.sh with a dynamically generated config.
#
# Usage:
#   scripts/run_tournament.sh [OPTIONS]
#
# Options:
#   --config PATH        Base tournament config JSON (default: daemon/config/tournament_bracket.json)
#   --best-of N          Games per series (default: 3)
#   --resume DIR         Resume from an existing tournament directory
#   --skip-mod-push      Skip mod packaging/push for all matches
#   --no-replay          Disable replay recording
#   --dry-run            Print bracket without running
#   -h, --help           Show this help

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ─── Defaults ────────────────────────────────────────────────────────────────

BASE_CONFIG="daemon/config/tournament_bracket.json"
BEST_OF=3
RESUME_DIR=""
SKIP_MOD_PUSH=false
NO_REPLAY=false
DRY_RUN=false

# Seeded bracket: #1 through #8 in seed order
SEEDS=(
    "google/gemini-3.1-pro-preview"
    "openai/gpt-5.4"
    "anthropic/claude-opus"
    "anthropic/claude-sonnet"
    "z-ai/glm-5"
    "minimax/minimax-m2.7"
    "xiaomi/mimo-v2-pro"
    "x-ai/grok-4.20-beta"
)

# ─── Parse arguments ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)          BASE_CONFIG="$2"; shift 2 ;;
        --config=*)        BASE_CONFIG="${1#*=}"; shift ;;
        --best-of)         BEST_OF="$2"; shift 2 ;;
        --best-of=*)       BEST_OF="${1#*=}"; shift ;;
        --resume)          RESUME_DIR="$2"; shift 2 ;;
        --resume=*)        RESUME_DIR="${1#*=}"; shift ;;
        --skip-mod-push)   SKIP_MOD_PUSH=true; shift ;;
        --no-replay)       NO_REPLAY=true; shift ;;
        --dry-run)         DRY_RUN=true; shift ;;
        -h|--help)         head -17 "$0" | tail -15; exit 0 ;;
        *)                 printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
    esac
done

# ─── Utility functions ───────────────────────────────────────────────────────

log() { printf '[tournament] %s\n' "$*" >&2; }
err() { printf '[tournament] ERROR: %s\n' "$*" >&2; }

short_name() {
    # Shorten policy ID for display: "google/gemini-3.1-pro-preview" -> "gemini-3.1-pro"
    echo "$1" | sed 's|.*/||' | sed 's/-preview$//' | sed 's/-beta$//'
}

# ─── Tournament directory ────────────────────────────────────────────────────

if [ -n "$RESUME_DIR" ]; then
    TOURNEY_DIR="$RESUME_DIR"
    if [ ! -f "$TOURNEY_DIR/bracket.json" ]; then
        err "No bracket.json found in $TOURNEY_DIR"
        exit 1
    fi
    log "Resuming tournament from $TOURNEY_DIR"
else
    TOURNEY_DIR="tournaments/$(date +%Y%m%dT%H%M%S)_bracket"
    mkdir -p "$TOURNEY_DIR"
    log "Tournament directory: $TOURNEY_DIR"
fi

# ─── Build bracket ───────────────────────────────────────────────────────────

N=${#SEEDS[@]}
if [ "$N" -ne 8 ]; then
    err "Bracket requires exactly 8 seeds, got $N"
    exit 1
fi

# Build bracket state using Python
python3 -c "
import json, sys

seeds = json.loads(sys.argv[1])
best_of = int(sys.argv[2])
tourney_dir = sys.argv[3]
resume = sys.argv[4] == 'true'

if resume:
    with open(f'{tourney_dir}/bracket.json') as f:
        bracket = json.load(f)
    print(json.dumps(bracket))
    sys.exit(0)

# Build seeded bracket
n = len(seeds)
half = n // 2
round_names = ['Quarterfinals', 'Semifinals', 'Final']

rounds = []
# QF: 1v8, 2v7, 3v6, 4v5
qf = []
for i in range(half):
    high = {'seed': i + 1, 'policy_id': seeds[i]}
    low = {'seed': n - i, 'policy_id': seeds[n - 1 - i]}
    qf.append({
        'series_id': f'QF-{i+1}',
        'round_name': round_names[0],
        'high_seed': high,
        'low_seed': low,
        'wins': {'high': 0, 'low': 0},
        'games': [],
        'status': 'pending'
    })
rounds.append(qf)

# SF placeholders
sf = []
for i in range(2):
    sf.append({
        'series_id': f'SF-{i+1}',
        'round_name': round_names[1],
        'high_seed': None,
        'low_seed': None,
        'wins': {'high': 0, 'low': 0},
        'games': [],
        'status': 'pending'
    })
rounds.append(sf)

# Final placeholder
final = [{
    'series_id': 'F-1',
    'round_name': round_names[2],
    'high_seed': None,
    'low_seed': None,
    'wins': {'high': 0, 'low': 0},
    'games': [],
    'status': 'pending'
}]
rounds.append(final)

bracket = {
    'seeds': [{'seed': i+1, 'policy_id': s} for i, s in enumerate(seeds)],
    'rounds': rounds,
    'best_of': best_of,
    'champion': None
}

with open(f'{tourney_dir}/bracket.json', 'w') as f:
    json.dump(bracket, f, indent=2)
    f.write('\n')

print(json.dumps(bracket))
" "$(printf '%s\n' "${SEEDS[@]}" | python3 -c "import json,sys; print(json.dumps([l.strip() for l in sys.stdin]))")" \
  "$BEST_OF" "$TOURNEY_DIR" "$([ -n "$RESUME_DIR" ] && echo true || echo false)" > /tmp/bracket_state.json

# ─── Print bracket ───────────────────────────────────────────────────────────

log ""
log "╔══════════════════════════════════════════════════════════╗"
log "║              SEEDED BRACKET TOURNAMENT                  ║"
log "╠══════════════════════════════════════════════════════════╣"

python3 -c "
import json, sys

bracket = json.load(open('/tmp/bracket_state.json'))

def short(pid):
    return pid.split('/')[-1].replace('-preview','').replace('-beta','')

for ri, rnd in enumerate(bracket['rounds']):
    name = rnd[0]['round_name'] if rnd else f'Round {ri}'
    print(f'║  {name}:')
    for s in rnd:
        if s['high_seed'] and s['low_seed']:
            h = f\"#{s['high_seed']['seed']} {short(s['high_seed']['policy_id'])}\"
            l = f\"#{s['low_seed']['seed']} {short(s['low_seed']['policy_id'])}\"
            status = s.get('status', 'pending')
            if status == 'completed':
                winner = '✓'
            else:
                winner = ''
            print(f'║    {s[\"series_id\"]}: {h} vs {l}  {winner}')
        else:
            print(f'║    {s[\"series_id\"]}: TBD vs TBD')
    print('║')
" >&2
log "║  Best of: $BEST_OF | Character selection: blind pick    ║"
log "╚══════════════════════════════════════════════════════════╝"
log ""

if [ "$DRY_RUN" = true ]; then
    log "DRY RUN — bracket printed, no matches executed."
    exit 0
fi

# ─── Run bracket ─────────────────────────────────────────────────────────────

# Generate a per-match config by patching policy_mapping in the base config
generate_match_config() {
    local p1_policy="$1"
    local p2_policy="$2"
    local match_config="$3"

    python3 -c "
import json, sys
p1, p2, base_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(base_path) as f:
    config = json.load(f)
config['policy_mapping'] = {'p1': p1, 'p2': p2}
with open(out_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" "$p1_policy" "$p2_policy" "$BASE_CONFIG" "$match_config"
}

# Poll and print HP status once per minute from the latest run directory
hp_monitor() {
    local monitor_pid_file="$1"
    echo $$ > "$monitor_pid_file"
    while true; do
        sleep 60
        # Find the latest active run directory
        local latest_run
        latest_run=$(ls -td runs/*/ 2>/dev/null | head -1)
        if [ -z "$latest_run" ] || [ ! -f "${latest_run}decisions.jsonl" ]; then
            continue
        fi
        # Skip if match is already done
        if [ -f "${latest_run}result.json" ] && \
           python3 -c "import json; exit(0 if json.load(open('${latest_run}result.json')).get('status')=='completed' else 1)" 2>/dev/null; then
            continue
        fi
        # Read last decision to get current HP
        python3 -c "
import json, sys
run_dir = sys.argv[1]
try:
    lines = open(run_dir + 'decisions.jsonl').readlines()
    if not lines:
        sys.exit(0)
    last = json.loads(lines[-1])
    req = last.get('request_payload', {})
    obs = req.get('observation', {})
    fighters = obs.get('fighters', [])
    if len(fighters) < 2:
        sys.exit(0)
    p1 = fighters[0]
    p2 = fighters[1]
    p1_hp = p1.get('hp', '?')
    p2_hp = p2.get('hp', '?')
    p1_char = p1.get('character', '?')
    p2_char = p2.get('character', '?')
    turn = req.get('turn_id', '?')
    from datetime import datetime
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[tournament] [{ts}] Turn {turn}: {p1_char} {p1_hp} HP vs {p2_char} {p2_hp} HP', file=sys.stderr)
except Exception:
    pass
" "$latest_run" 2>/dev/null || true
    done
}

stop_hp_monitor() {
    if [ -n "${HP_MONITOR_PID_FILE:-}" ] && [ -f "$HP_MONITOR_PID_FILE" ]; then
        local pid
        pid=$(cat "$HP_MONITOR_PID_FILE" 2>/dev/null)
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
        fi
        rm -f "$HP_MONITOR_PID_FILE"
    fi
}

# Print a clean game summary from result.json and character_selection.json
print_game_summary() {
    local result_file="$1"
    local game_num="$2"

    python3 -c "
import json, sys

result_file = sys.argv[1]
game_num = sys.argv[2]

result = json.load(open(result_file))
run_dir = result_file.rsplit('/', 1)[0] + '/'

# Get character picks
p1_char = '?'
p2_char = '?'
try:
    cs = json.load(open(run_dir + 'character_selection.json'))
    for trace in cs:
        if trace['player_slot'] == 'p1':
            p1_char = trace['character']
        elif trace['player_slot'] == 'p2':
            p2_char = trace['character']
except (FileNotFoundError, json.JSONDecodeError):
    pass

winner = result.get('winner', '?')
end_reason = result.get('end_reason', '?')
turns = result.get('total_turns', '?')

metrics = result.get('metrics', {})
fallbacks = metrics.get('fallback_count', 0)
errors = metrics.get('error_count', 0)
error_list = result.get('errors', [])

winner_char = p1_char if winner == 'p1' else p2_char if winner == 'p2' else '?'

# Compute per-policy cost and token stats from prompts.jsonl
p1_cost = 0.0
p2_cost = 0.0
p1_tokens_out = 0
p2_tokens_out = 0
p1_reasoning = 0
p2_reasoning = 0
p1_policy = '?'
p2_policy = '?'
try:
    with open(run_dir + 'prompts.jsonl') as pf:
        for pline in pf:
            p = json.loads(pline)
            pid = p.get('player_id', '')
            policy = p.get('policy_id', '')
            resp = p.get('provider_response', {})
            for att in resp.get('attempts', []):
                usage = att.get('usage', {})
                cost = usage.get('cost', 0) or 0
                comp = usage.get('completion_tokens', 0) or 0
                details = usage.get('completion_tokens_details', {}) or {}
                reasoning = details.get('reasoning_tokens', 0) or 0
                if pid == 'p1':
                    p1_cost += cost
                    p1_tokens_out += comp
                    p1_reasoning += reasoning
                    p1_policy = policy
                elif pid == 'p2':
                    p2_cost += cost
                    p2_tokens_out += comp
                    p2_reasoning += reasoning
                    p2_policy = policy
except (FileNotFoundError, json.JSONDecodeError):
    pass

summary = f'[tournament]   Game {game_num}: {p1_char} vs {p2_char} | Winner: {winner_char} ({end_reason}) | {turns} turns'
if fallbacks:
    summary += f', {fallbacks} fallbacks'
if errors:
    summary += f', {errors} errors'
print(summary)

# Cost breakdown per player
def fmt_policy(pid):
    return pid.split('/')[-1].replace('-preview','').replace('-beta','')

if p1_cost > 0 or p2_cost > 0:
    p1_reasoning_pct = f' ({p1_reasoning} reasoning)' if p1_reasoning else ''
    p2_reasoning_pct = f' ({p2_reasoning} reasoning)' if p2_reasoning else ''
    print(f'[tournament]     {fmt_policy(p1_policy)}: \${p1_cost:.4f} | {p1_tokens_out} tokens out{p1_reasoning_pct}')
    print(f'[tournament]     {fmt_policy(p2_policy)}: \${p2_cost:.4f} | {p2_tokens_out} tokens out{p2_reasoning_pct}')
    print(f'[tournament]     Total: \${p1_cost + p2_cost:.4f}')

for e in error_list:
    print(f'[tournament]     ERROR: {e}')
" "$result_file" "$game_num" 2>/dev/null >&2 || log "  Game ${game_num}: completed (details unavailable)"
}

# Run a single game and return the result
run_game() {
    local p1_policy="$1"
    local p2_policy="$2"
    local series_id="$3"
    local game_num="$4"
    local history_file="${5:-}"

    local match_config="$TOURNEY_DIR/${series_id}_game${game_num}_config.json"
    generate_match_config "$p1_policy" "$p2_policy" "$match_config"

    local match_args=("--daemon-config" "$match_config")
    if [ "$SKIP_MOD_PUSH" = true ]; then
        # Only push mod on very first game
        if [ "$game_num" -gt 1 ] || [ -f "$TOURNEY_DIR/.mod_pushed" ]; then
            match_args+=("--skip-mod-push")
        fi
    fi
    if [ "$NO_REPLAY" = true ]; then
        match_args+=("--no-replay")
    fi
    # Pass match history for character selection context
    if [ -n "$history_file" ] && [ -f "$history_file" ]; then
        match_args+=("--match-history" "$history_file")
    fi

    log "  Game ${game_num} starting..."

    local game_log="$TOURNEY_DIR/${series_id}_game${game_num}.log"

    # Start HP + error monitor (reads daemon output for errors/fallbacks, polls HP once/min)
    HP_MONITOR_PID_FILE=$(mktemp)
    hp_monitor "$HP_MONITOR_PID_FILE" &

    # Run match: tee full output to log, filter important lines to stderr for live display
    # Temporarily disable errexit/pipefail for the pipeline — grep exits 1 when the
    # pipe closes with no pending match, and tee/sed may also exit non-zero.
    set +eo pipefail
    scripts/run_match.sh "${match_args[@]}" 2>&1 \
        | tee "$game_log" \
        | grep --line-buffered -iE "character selection resolved|HP_STATUS|FALLBACK|ERROR|malformed|illegal|timeout|refused|failed|MATCH RESULT|Status:|Winner:|Reason:|Turns:" \
        | sed 's/^/[tournament]   /' >&2
    local match_exit=${PIPESTATUS[0]}
    set -eo pipefail
    stop_hp_monitor

    # Find the latest result
    local latest_run
    latest_run=$(ls -td runs/*/ 2>/dev/null | head -1)

    if [ -n "$latest_run" ] && [ -f "${latest_run}result.json" ]; then
        local match_status
        match_status=$(python3 -c "import json; print(json.load(open('${latest_run}result.json')).get('status','?'))" 2>/dev/null || echo "?")
        if [ "$match_status" = "completed" ]; then
            print_game_summary "${latest_run}result.json" "$game_num"
            echo "${latest_run}result.json"
            return 0
        elif [ "$match_status" = "failed" ]; then
            log "  Game ${game_num}: FAILED (game crashed/disconnected)"
            echo "${latest_run}result.json"
            return 1
        fi
    fi

    err "Game failed: ${series_id} Game ${game_num} (exit=$match_exit, see $game_log)"
    return 1
}

# Build match history JSON for character selection from previous game results in the series
build_series_history() {
    local p1_policy="$1"
    local p2_policy="$2"
    local history_file="$3"
    shift 3
    local result_files=("$@")

    python3 -c "
import json, sys

p1_policy = sys.argv[1]
p2_policy = sys.argv[2]
out_path = sys.argv[3]
result_files = sys.argv[4:]

p1_history = []
p2_history = []

for rf in result_files:
    try:
        result = json.load(open(rf))
        # Find character selection traces
        run_dir = rf.rsplit('/', 1)[0] + '/'
        chars = {'p1': 'Unknown', 'p2': 'Unknown'}
        try:
            cs = json.load(open(run_dir + 'character_selection.json'))
            for trace in cs:
                if trace['player_slot'] == 'p1':
                    chars['p1'] = trace['character']
                elif trace['player_slot'] == 'p2':
                    chars['p2'] = trace['character']
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        winner = result.get('winner', '')
        # Read final HP from the last decision or metrics
        p1_hp = 0
        p2_hp = 0
        try:
            metrics = json.load(open(run_dir + 'metrics.json'))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # P1's perspective
        p1_history.append({
            'your_character': chars['p1'],
            'opponent_character': chars['p2'],
            'result': 'win' if winner == 'p1' else 'loss' if winner == 'p2' else 'draw',
            'your_final_hp': p1_hp,
            'opponent_final_hp': p2_hp,
        })
        # P2's perspective
        p2_history.append({
            'your_character': chars['p2'],
            'opponent_character': chars['p1'],
            'result': 'win' if winner == 'p2' else 'loss' if winner == 'p1' else 'draw',
            'your_final_hp': p2_hp,
            'opponent_final_hp': p1_hp,
        })
    except (FileNotFoundError, json.JSONDecodeError):
        pass

history = {'p1': p1_history, 'p2': p2_history}
with open(out_path, 'w') as f:
    json.dump(history, f, indent=2)
    f.write('\n')
" "$p1_policy" "$p2_policy" "$history_file" "${result_files[@]}"
}

# Run a best-of-N series
run_series() {
    local series_id="$1"
    local p1_policy="$2"
    local p2_policy="$3"
    local p1_seed="$4"
    local p2_seed="$5"

    local p1_wins=0
    local p2_wins=0
    local wins_needed=$(( (BEST_OF + 1) / 2 ))
    local game_results=()

    log ""
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log "  ${series_id}: #${p1_seed} $(short_name "$p1_policy") vs #${p2_seed} $(short_name "$p2_policy")"
    log "  Best of ${BEST_OF}"
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    for game_num in $(seq 1 "$BEST_OF"); do
        if [ "$p1_wins" -ge "$wins_needed" ] || [ "$p2_wins" -ge "$wins_needed" ]; then
            break
        fi

        # Build match history from previous games in this series
        local history_file=""
        if [ "${#game_results[@]}" -gt 0 ]; then
            history_file="$TOURNEY_DIR/${series_id}_history.json"
            build_series_history "$p1_policy" "$p2_policy" "$history_file" "${game_results[@]}"
        fi

        local result_file
        if ! result_file=$(run_game "$p1_policy" "$p2_policy" "$series_id" "$game_num" "$history_file"); then
            err "  Game ${game_num} failed, counting as loss for higher seed"
            p2_wins=$((p2_wins + 1))
            continue
        fi

        # Extract winner from result file
        if [ -n "$result_file" ] && [ -f "$result_file" ]; then
            local winner
            winner=$(python3 -c "import json; print(json.load(open('$result_file')).get('winner','?'))")

            if [ "$winner" = "p1" ]; then
                p1_wins=$((p1_wins + 1))
            elif [ "$winner" = "p2" ]; then
                p2_wins=$((p2_wins + 1))
            fi
            log "  Series: $(short_name "$p1_policy") ${p1_wins} - ${p2_wins} $(short_name "$p2_policy")"
            game_results+=("$result_file")
        fi
    done

    # Determine series winner
    local series_winner_policy series_winner_seed
    if [ "$p1_wins" -ge "$wins_needed" ]; then
        series_winner_policy="$p1_policy"
        series_winner_seed="$p1_seed"
    else
        series_winner_policy="$p2_policy"
        series_winner_seed="$p2_seed"
    fi

    log ""
    log "  ✓ ${series_id} WINNER: #${series_winner_seed} $(short_name "$series_winner_policy") (${p1_wins}-${p2_wins})"
    log ""

    # Update bracket state
    python3 -c "
import json, sys

series_id = sys.argv[1]
winner_policy = sys.argv[2]
winner_seed = int(sys.argv[3])
p1_wins = int(sys.argv[4])
p2_wins = int(sys.argv[5])
tourney_dir = sys.argv[6]

with open(f'{tourney_dir}/bracket.json') as f:
    bracket = json.load(f)

# Mark series as completed
for ri, rnd in enumerate(bracket['rounds']):
    for si, series in enumerate(rnd):
        if series['series_id'] == series_id:
            series['status'] = 'completed'
            series['wins'] = {'high': p1_wins, 'low': p2_wins}
            winner_entry = {'seed': winner_seed, 'policy_id': winner_policy}

            # Advance winner to next round
            if ri + 1 < len(bracket['rounds']):
                next_round = bracket['rounds'][ri + 1]
                next_slot = si // 2
                if next_slot < len(next_round):
                    if si % 2 == 0:
                        next_round[next_slot]['high_seed'] = winner_entry
                    else:
                        next_round[next_slot]['low_seed'] = winner_entry
            else:
                # This was the final
                bracket['champion'] = winner_entry

with open(f'{tourney_dir}/bracket.json', 'w') as f:
    json.dump(bracket, f, indent=2)
    f.write('\n')
" "$series_id" "$series_winner_policy" "$series_winner_seed" "$p1_wins" "$p2_wins" "$TOURNEY_DIR"

    echo "$series_winner_policy:$series_winner_seed"
}

# ─── Execute bracket rounds ──────────────────────────────────────────────────

# Push mod once at the start
FIRST_MATCH_ARGS=()
if [ "$SKIP_MOD_PUSH" = false ]; then
    FIRST_MATCH_ARGS=()  # run_match.sh will handle it
fi

# Read bracket and iterate through rounds
for round_idx in 0 1 2; do
    round_series=$(python3 -c "
import json
bracket = json.load(open('$TOURNEY_DIR/bracket.json'))
if $round_idx < len(bracket['rounds']):
    rnd = bracket['rounds'][$round_idx]
    for s in rnd:
        if s['status'] != 'completed' and s.get('high_seed') and s.get('low_seed'):
            print(f\"{s['series_id']}|{s['high_seed']['policy_id']}|{s['low_seed']['policy_id']}|{s['high_seed']['seed']}|{s['low_seed']['seed']}\")
")

    if [ -z "$round_series" ]; then
        continue
    fi

    while IFS='|' read -r series_id p1_policy p2_policy p1_seed p2_seed; do
        run_series "$series_id" "$p1_policy" "$p2_policy" "$p1_seed" "$p2_seed"
        # After first match, skip mod push for remaining
        SKIP_MOD_PUSH=true
    done <<< "$round_series"
done

# ─── Print final results ─────────────────────────────────────────────────────

log ""
log "╔══════════════════════════════════════════════════════════╗"
log "║              TOURNAMENT COMPLETE                        ║"
log "╠══════════════════════════════════════════════════════════╣"

python3 -c "
import json

bracket = json.load(open('$TOURNEY_DIR/bracket.json'))

def short(pid):
    return pid.split('/')[-1].replace('-preview','').replace('-beta','')

for rnd in bracket['rounds']:
    name = rnd[0]['round_name'] if rnd else '?'
    print(f'║  {name}:')
    for s in rnd:
        if s.get('high_seed') and s.get('low_seed'):
            h = short(s['high_seed']['policy_id'])
            l = short(s['low_seed']['policy_id'])
            hw = s['wins']['high']
            lw = s['wins']['low']
            if s['status'] == 'completed':
                print(f'║    {h} {hw} - {lw} {l}')
            else:
                print(f'║    {h} vs {l}  (not played)')
        else:
            print(f'║    TBD vs TBD')
    print('║')

champ = bracket.get('champion')
if champ:
    print(f'║  🏆 CHAMPION: #{champ[\"seed\"]} {short(champ[\"policy_id\"])}')
else:
    print('║  No champion yet')
" >&2

log "╚══════════════════════════════════════════════════════════╝"
log ""
log "Full bracket state: $TOURNEY_DIR/bracket.json"
