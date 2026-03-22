"""Live match viewer TUI for Budok-AI Arena."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, RichLog, Static


RUNS_DIR = Path(__file__).resolve().parents[3] / "runs"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FighterState:
    player_id: str = ""
    character: str = "?"
    policy_id: str = "?"
    hp: int = 0
    max_hp: int = 0
    meter: int = 0
    burst: int = 0
    position_x: float = 0.0
    position_y: float = 0.0
    current_state: str = "?"
    facing: str = "?"
    combo_count: int = 0


@dataclass
class MatchState:
    match_id: str = ""
    tick: int = 0
    frame: int = 0
    turn_id: int = 0
    p1: FighterState = field(default_factory=FighterState)
    p2: FighterState = field(default_factory=FighterState)
    total_decisions: int = 0
    total_fallbacks: int = 0
    status: str = "waiting"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_latest_run() -> Path | None:
    """Return the most recently modified run directory."""
    if not RUNS_DIR.is_dir():
        return None
    candidates = sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.is_dir() and (path / "decisions.jsonl").exists():
            return path
    return None


def _fighter_from_obs(obs: dict[str, Any], player_id: str) -> FighterState | None:
    """Extract fighter state from an observation dict."""
    fighters = obs.get("fighters", [])
    for f in fighters:
        if f.get("id") == player_id:
            pos = f.get("position", {})
            return FighterState(
                player_id=player_id,
                character=f.get("character", "?"),
                hp=f.get("hp", 0),
                max_hp=f.get("max_hp", 0),
                meter=f.get("meter", 0),
                burst=f.get("burst", 0),
                position_x=pos.get("x", 0.0),
                position_y=pos.get("y", 0.0),
                current_state=f.get("current_state", "?"),
                facing=f.get("facing", "?"),
                combo_count=f.get("combo_count", 0),
            )
    return None


def _hp_bar(hp: int, max_hp: int, width: int = 20) -> Text:
    """Render a colored HP bar."""
    if max_hp <= 0:
        return Text("---")
    ratio = max(0.0, min(1.0, hp / max_hp))
    filled = int(ratio * width)
    empty = width - filled
    if ratio > 0.5:
        color = "green"
    elif ratio > 0.25:
        color = "yellow"
    else:
        color = "red"
    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * empty, style="dim")
    bar.append(f" {hp}/{max_hp}", style="bold")
    return bar


def _outcome_label(outcome: str, hp_delta: int) -> Text:
    """Render an outcome tag like 'HIT -57' or 'blocked' or 'neutral'."""
    text = Text()
    if outcome == "hit" and hp_delta < 0:
        text.append(f"HIT {hp_delta}", style="bold red")
    elif outcome == "hit" and hp_delta == 0:
        text.append("HIT", style="bold yellow")
    elif outcome == "blocked":
        text.append("BLOCKED", style="bold cyan")
    elif outcome == "neutral":
        text.append("whiff", style="dim")
    else:
        text.append(f"{outcome}", style="dim")
    return text


def _render_fighter(slot: str, f: FighterState, policy: str) -> Text:
    """Build the Rich Text for a fighter panel."""
    text = Text()
    text.append(f"  {slot} ", style="bold reverse")
    text.append(f"  {f.character}", style="bold cyan")
    text.append(f"  [{policy}]", style="dim italic")
    text.append("\n")

    text.append("  HP  ", style="bold")
    text.append_text(_hp_bar(f.hp, f.max_hp))
    text.append("\n")

    text.append("  Meter ", style="bold")
    text.append(f"{f.meter}", style="magenta")
    text.append("  Burst ", style="bold")
    text.append(f"{f.burst}", style="yellow")
    text.append("  Combo ", style="bold")
    text.append(f"{f.combo_count}", style="red" if f.combo_count > 0 else "dim")
    text.append("\n")

    text.append("  Pos ", style="bold")
    text.append(f"({f.position_x:.0f}, {f.position_y:.0f})", style="")
    text.append(f"  {f.facing}", style="dim")
    text.append("  State ", style="bold")
    state_style = "green" if f.current_state in ("neutral", "Start", "Idle") else "yellow"
    text.append(f"{f.current_state}", style=state_style)
    return text


def _render_match_header(m: MatchState) -> Text:
    """Build the Rich Text for the match header bar."""
    text = Text()
    text.append("  Match ", style="bold")
    text.append(f"{m.match_id[:24]}", style="cyan")
    text.append("  Tick ", style="bold")
    text.append(f"{m.tick}", style="green")
    text.append("  Decisions ", style="bold")
    text.append(f"{m.total_decisions}", style="")
    text.append("  Fallbacks ", style="bold")
    fb_style = "red bold" if m.total_fallbacks > 0 else "dim"
    text.append(f"{m.total_fallbacks}", style=fb_style)
    text.append("  Status ", style="bold")
    status_style = (
        "green bold"
        if m.status == "completed"
        else "yellow bold"
        if m.status == "in_progress"
        else "dim"
    )
    text.append(f"{m.status}", style=status_style)
    return text


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

APP_CSS = """
Screen {
    background: $surface;
}

#match-header {
    dock: top;
    height: 1;
    background: $primary-background;
    color: $text;
    padding: 0;
}

#fighter-bar {
    dock: top;
    height: auto;
    min-height: 7;
    max-height: 9;
    background: $surface;
}

#event-log {
    border-top: solid $primary;
    height: 1fr;
    scrollbar-size: 1 1;
}

.fighter-panel {
    width: 1fr;
    height: auto;
    min-height: 5;
    padding: 0 1;
    border-right: solid $primary-lighten-2;
}

.fighter-panel:last-child {
    border-right: none;
}
"""


class YomiTUI(App):
    """Live match viewer for Budok-AI Arena."""

    CSS = APP_CSS
    TITLE = "YOMI Hustle - Match Viewer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "toggle_follow", "Follow"),
    ]

    def __init__(self, run_dir: Path) -> None:
        super().__init__()
        self.run_dir = run_dir
        self.decisions_path = run_dir / "decisions.jsonl"
        self.events_path = run_dir / "events.jsonl"
        self.result_path = run_dir / "result.json"
        self.manifest_path = run_dir / "manifest.json"
        self._decisions_offset = 0
        self._events_offset = 0
        self._match = MatchState()
        self._policy_map: dict[str, str] = {}
        self._last_tick: int | None = None
        self._seen_outcomes: set[int] = set()  # game_ticks whose outcomes we've rendered
        self._seen_decisions: set[tuple[str, int]] = set()  # (player_id, tick) dedup
        self._follow = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="match-header")
        with Horizontal(id="fighter-bar"):
            yield Static(id="p1-panel", classes="fighter-panel")
            yield Static(id="p2-panel", classes="fighter-panel")
        yield RichLog(id="event-log", highlight=True, markup=True, wrap=True, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        self._load_manifest()

        # Render initial state
        self._refresh_panels()
        self._refresh_header()

        log = self.query_one("#event-log", RichLog)
        log.write(
            Text.from_markup(
                f"[bold cyan]Watching[/] [dim]{self.run_dir}[/]\n"
                f"[dim]Press [bold]q[/bold] to quit, [bold]f[/bold] to toggle auto-follow[/]"
            )
        )

        self.set_interval(0.5, self._poll_updates)

    def _load_manifest(self) -> None:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text())
                pm = data.get("policy_mapping", {})
                self._policy_map = {"p1": pm.get("p1", "?"), "p2": pm.get("p2", "?")}
                self._match.match_id = data.get("match_id", "")
            except (json.JSONDecodeError, OSError):
                pass

    def _refresh_panels(self) -> None:
        p1 = self.query_one("#p1-panel", Static)
        p2 = self.query_one("#p2-panel", Static)
        p1.update(_render_fighter("P1", self._match.p1, self._policy_map.get("p1", "?")))
        p2.update(_render_fighter("P2", self._match.p2, self._policy_map.get("p2", "?")))

    def _refresh_header(self) -> None:
        header = self.query_one("#match-header", Static)
        header.update(_render_match_header(self._match))

    def _poll_updates(self) -> None:
        self._poll_decisions()
        self._poll_events()
        self._poll_result()

    def _poll_decisions(self) -> None:
        if not self.decisions_path.exists():
            return

        try:
            with self.decisions_path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return

        new_lines = lines[self._decisions_offset :]
        if not new_lines:
            return
        self._decisions_offset = len(lines)

        log = self.query_one("#event-log", RichLog)
        panels_dirty = False

        for raw_line in new_lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            request = record.get("request_payload", {})
            decision = record.get("decision_payload", {})
            player_id = record.get("player_id", "?")

            # Update match state from observation
            obs = request.get("observation", {})
            tick = obs.get("tick", self._match.tick) if obs else self._match.tick

            # Deduplicate: the game sometimes fires player_actionable twice
            # for the same player at the same tick, causing duplicate API calls.
            # Only show the first decision per (player, tick).
            dedup_key = (player_id, tick)
            if dedup_key in self._seen_decisions:
                continue
            self._seen_decisions.add(dedup_key)

            self._match.total_decisions += 1

            if obs:
                self._match.tick = tick
                self._match.frame = obs.get("frame", self._match.frame)

                p1_f = _fighter_from_obs(obs, "p1")
                p2_f = _fighter_from_obs(obs, "p2")
                if p1_f is not None:
                    self._match.p1 = p1_f
                    panels_dirty = True
                if p2_f is not None:
                    self._match.p2 = p2_f
                    panels_dirty = True

            # Render outcome lines from history (resolution of previous ticks)
            if obs:
                for h in obs.get("history", []):
                    gt = h.get("game_tick")
                    if gt is None or gt in self._seen_outcomes:
                        continue
                    # Only render entries that have outcome data
                    p1_out = h.get("p1_outcome")
                    p2_out = h.get("p2_outcome")
                    if p1_out is None and p2_out is None:
                        continue
                    self._seen_outcomes.add(gt)
                    self._render_outcome(log, h)

            # Track fallbacks
            fallback = decision.get("fallback_reason") if decision else None
            if fallback:
                self._match.total_fallbacks += 1

            # Tick separator — show when game tick changes
            if self._last_tick is None or tick != self._last_tick:
                self._last_tick = tick
                sep = Text()
                sep.append(f"── tick {tick} ", style="dim bold")
                sep.append("─" * 40, style="dim")
                log.write(sep)

            action = decision.get("action", "?") if decision else "?"
            latency = decision.get("latency_ms") if decision else None

            p_color = "blue" if player_id == "p1" else "magenta"

            # Action line
            action_text = Text()
            action_text.append(f"{player_id.upper()} ", style=f"bold {p_color}")
            action_text.append(f"{action}", style="bold white")
            if latency is not None:
                action_text.append(f"  {latency}ms", style="dim")
            if fallback:
                action_text.append(f"  FALLBACK:{fallback}", style="bold red")

            tokens_in = decision.get("tokens_in") if decision else None
            tokens_out = decision.get("tokens_out") if decision else None
            if tokens_in or tokens_out:
                action_text.append(
                    f"  tokens:{tokens_in or '?'}/{tokens_out or '?'}",
                    style="dim",
                )

            log.write(action_text)

            # Reasoning
            reasoning = decision.get("reasoning") if decision else None
            if reasoning:
                reason_text = Text()
                reason_text.append("     ", style="")
                reason_text.append(escape(reasoning), style="italic dim")
                log.write(reason_text)

            # Notes (if different from reasoning)
            notes = decision.get("notes") if decision else None
            if notes and notes != reasoning:
                notes_text = Text()
                notes_text.append("     ", style="")
                notes_text.append(escape(notes), style="dim green")
                log.write(notes_text)

        if panels_dirty:
            self._refresh_panels()
        self._refresh_header()

    def _poll_events(self) -> None:
        if not self.events_path.exists():
            return

        try:
            with self.events_path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return

        new_lines = lines[self._events_offset :]
        if not new_lines:
            return
        self._events_offset = len(lines)

        log = self.query_one("#event-log", RichLog)

        for raw_line in new_lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            payload = record.get("payload", record)
            event_name = payload.get("event", "")
            details = payload.get("details", {})

            # Skip noisy events already covered by decisions
            if event_name in ("TurnRequested", "DecisionReceived"):
                continue

            if event_name == "MatchStarted":
                p1_pol = details.get("p1_policy", "?")
                p2_pol = details.get("p2_policy", "?")
                text = Text()
                text.append(">>> MATCH STARTED ", style="bold green")
                text.append(f"P1={p1_pol} ", style="blue")
                text.append(f"vs P2={p2_pol}", style="magenta")
                log.write(text)

            elif event_name == "MatchEnded":
                text = Text()
                text.append(">>> MATCH ENDED ", style="bold red")
                log.write(text)

            elif event_name == "DecisionFallback":
                player = payload.get("player_id", "?")
                reason = payload.get("fallback_reason", "?")
                lat = payload.get("latency_ms")
                text = Text()
                text.append(f"{player.upper()} ", style="bold red")
                text.append("FALLBACK ", style="bold red")
                text.append(f"{reason}", style="red")
                if lat:
                    text.append(f"  {lat}ms", style="dim")
                log.write(text)

            elif event_name == "DecisionApplied":
                player = payload.get("player_id", "?")
                action = details.get("action", "?")
                p_color = "blue" if player == "p1" else "magenta"
                text = Text()
                text.append(f"{player.upper()} ", style=p_color)
                text.append("applied ", style="dim green")
                text.append(f"{action}", style="green")
                log.write(text)

            elif event_name in ("ReplayStarted", "ReplayEnded", "ReplaySaved"):
                text = Text()
                text.append(f"  {event_name}", style="dim cyan")
                log.write(text)

            elif event_name == "Error":
                text = Text()
                text.append("  ERROR: ", style="bold red")
                text.append(str(details), style="red")
                log.write(text)

    def _poll_result(self) -> None:
        if not self.result_path.exists():
            return
        try:
            data = json.loads(self.result_path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        status = data.get("status", "")
        if status == "completed" and self._match.status != "completed":
            self._match.status = "completed"
            winner = data.get("winner", "?")
            end_reason = data.get("end_reason", "?")
            total_turns = data.get("total_turns", "?")

            self._refresh_header()

            log = self.query_one("#event-log", RichLog)
            text = Text()
            text.append("\n")
            text.append("=" * 60, style="bold")
            text.append("\n")
            text.append("  MATCH COMPLETE  ", style="bold reverse green")
            text.append("\n")
            text.append("  Winner: ", style="bold")
            w_color = "blue" if winner == "p1" else "magenta" if winner == "p2" else "yellow"
            text.append(f"{winner}", style=f"bold {w_color}")
            text.append("  Reason: ", style="bold")
            text.append(f"{end_reason}", style="")
            text.append("  Turns: ", style="bold")
            text.append(f"{total_turns}", style="")
            text.append("\n")
            text.append("=" * 60, style="bold")
            log.write(text)

    def _render_outcome(self, log: RichLog, h: dict[str, Any]) -> None:
        """Render a tick resolution line from a history entry."""
        gt = h.get("game_tick", "?")
        p1_action = h.get("p1_action", "")
        p2_action = h.get("p2_action", "")
        p1_out = h.get("p1_outcome", "neutral")
        p2_out = h.get("p2_outcome", "neutral")
        p1_delta = h.get("p1_hp_delta") or 0
        p2_delta = h.get("p2_hp_delta") or 0
        p1_hp = h.get("p1_hp")
        p2_hp = h.get("p2_hp")

        text = Text()
        text.append(f"  ⚔ tick {gt}  ", style="bold")

        # P1 side
        if p1_action:
            text.append("P1 ", style="bold blue")
            text.append(f"{p1_action}", style="blue")
            text.append(" → ", style="dim")
            text.append_text(_outcome_label(p1_out, p1_delta))

        if p1_action and p2_action:
            text.append("  vs  ", style="dim")

        # P2 side
        if p2_action:
            text.append("P2 ", style="bold magenta")
            text.append(f"{p2_action}", style="magenta")
            text.append(" → ", style="dim")
            text.append_text(_outcome_label(p2_out, p2_delta))

        # HP summary when damage was dealt
        if (p1_delta != 0 or p2_delta != 0) and p1_hp is not None and p2_hp is not None:
            text.append("  │  ", style="dim")
            text.append("HP ", style="bold")
            p1_hp_style = "red bold" if p1_delta < 0 else "blue"
            p2_hp_style = "red bold" if p2_delta < 0 else "magenta"
            text.append(f"{p1_hp}", style=p1_hp_style)
            text.append(" vs ", style="dim")
            text.append(f"{p2_hp}", style=p2_hp_style)

        log.write(text)

    def action_toggle_follow(self) -> None:
        log = self.query_one("#event-log", RichLog)
        self._follow = not self._follow
        log.auto_scroll = self._follow
        self.notify(f"Auto-follow: {'ON' if self._follow else 'OFF'}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="YOMI Hustle live match viewer TUI")
    parser.add_argument(
        "run_dir",
        nargs="?",
        default=None,
        help="Path to a specific run directory. If omitted, uses the latest run.",
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        help="Root directory containing run directories (default: runs/)",
    )
    args = parser.parse_args()

    global RUNS_DIR
    if args.runs_dir:
        RUNS_DIR = Path(args.runs_dir)

    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        run_dir = _find_latest_run()

    if run_dir is None or not run_dir.is_dir():
        print("Error: No run directory found. Provide a path or ensure runs/ has match data.")
        sys.exit(1)

    app = YomiTUI(run_dir)
    app.run()


if __name__ == "__main__":
    main()
