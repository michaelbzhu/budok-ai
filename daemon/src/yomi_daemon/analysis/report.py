"""HTML report rendering for tournament summaries."""

from __future__ import annotations

from html import escape
from pathlib import Path

from yomi_daemon.analysis.summary import CharacterSummary, ModelTournamentSummary, TournamentSummary


def render_tournament_summary_html(summary: TournamentSummary) -> str:
    """Render a minimalist editorial HTML report for a tournament summary."""

    champion = summary.champion.policy_id if summary.champion is not None else "Unknown"
    tournament_name = Path(summary.tournament_dir).name
    overview = summary.overview

    cheapest = _best(summary.model_summaries, key=lambda item: item.average_cost_usd_per_turn)
    most_verbose = _best(
        summary.model_summaries,
        key=lambda item: item.average_output_tokens_per_turn,
        reverse=True,
    )
    best_win_rate = _best(
        summary.model_summaries, key=lambda item: item.game_win_rate, reverse=True
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(tournament_name)} Summary Report</title>
  <style>
    :root {{
      --paper: #f5f1e8;
      --paper-deep: #ece6d8;
      --ink: #111111;
      --muted: #5a5a52;
      --line: #1a1a1a;
      --line-soft: rgba(26, 26, 26, 0.24);
      --accent: #7c1108;
      --shadow: 0 18px 60px rgba(17, 17, 17, 0.08);
      --max-width: 1280px;
    }}

    * {{
      box-sizing: border-box;
    }}

    html {{
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.30), rgba(255, 255, 255, 0.00)),
        var(--paper);
      color: var(--ink);
      font-family: "Times New Roman", Times, serif;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(0, 0, 0, 0.04), transparent 30%),
        repeating-linear-gradient(
          180deg,
          transparent 0,
          transparent 31px,
          rgba(17, 17, 17, 0.022) 31px,
          rgba(17, 17, 17, 0.022) 32px
        );
    }}

    .page {{
      width: min(calc(100vw - 32px), var(--max-width));
      margin: 28px auto 48px;
      border: 1.5px solid var(--line);
      background: rgba(245, 241, 232, 0.92);
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
      animation: rise 520ms ease-out;
    }}

    .page::before {{
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(90deg, transparent 0, rgba(0, 0, 0, 0.035) 50%, transparent 100%);
      opacity: 0.7;
    }}

    .header {{
      display: grid;
      grid-template-columns: minmax(0, 1.7fr) minmax(300px, 0.9fr);
      gap: 24px;
      padding: 28px 28px 20px;
      border-bottom: 2px solid var(--line);
      position: relative;
    }}

    .eyebrow {{
      font-size: 0.82rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 16px;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(2.6rem, 5.2vw, 5.1rem);
      line-height: 0.95;
      font-weight: 600;
      letter-spacing: -0.03em;
      max-width: 12ch;
      text-wrap: balance;
    }}

    .dek {{
      margin: 18px 0 0;
      max-width: 58ch;
      color: var(--muted);
      font-size: 1.08rem;
      line-height: 1.55;
    }}

    .hero-meta {{
      border-left: 1.5px solid var(--line);
      padding-left: 20px;
      display: grid;
      align-content: start;
      gap: 18px;
    }}

    .meta-block {{
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line-soft);
    }}

    .meta-label {{
      font-size: 0.76rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }}

    .meta-value {{
      font-size: 1.35rem;
      line-height: 1.1;
    }}

    .content {{
      padding: 0 28px 28px;
    }}

    .section {{
      padding: 22px 0 0;
      border-top: 1.5px solid var(--line);
    }}

    .section:first-child {{
      border-top: none;
    }}

    .section-heading {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 16px;
    }}

    h2 {{
      margin: 0;
      font-size: 1.35rem;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }}

    .section-note {{
      color: var(--muted);
      font-size: 0.95rem;
      max-width: 52ch;
      text-align: right;
    }}

    .overview-grid,
    .leader-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 0;
      border-top: 1.5px solid var(--line);
      border-bottom: 1.5px solid var(--line);
    }}

    .metric-card,
    .leader-card {{
      padding: 16px 18px 18px;
      min-height: 136px;
      border-right: 1px solid var(--line);
      position: relative;
    }}

    .metric-card:last-child,
    .leader-card:last-child {{
      border-right: none;
    }}

    .metric-label,
    .leader-label {{
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.13em;
      font-size: 0.73rem;
    }}

    .metric-value,
    .leader-value {{
      margin-top: 18px;
      font-size: clamp(1.6rem, 2vw, 2.25rem);
      line-height: 1;
    }}

    .metric-detail,
    .leader-detail {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 0.96rem;
      line-height: 1.4;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.98rem;
    }}

    th,
    td {{
      padding: 11px 10px;
      vertical-align: top;
      border-bottom: 1px solid var(--line-soft);
    }}

    thead th {{
      text-align: left;
      font-size: 0.74rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      border-bottom: 1.5px solid var(--line);
    }}

    tbody tr:hover {{
      background: rgba(17, 17, 17, 0.035);
    }}

    .policy {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}

    .policy-main {{
      font-size: 1.03rem;
    }}

    .policy-sub {{
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.3;
    }}

    .champ-badge {{
      display: inline-block;
      margin-top: 6px;
      padding: 4px 8px;
      border: 1px solid var(--line);
      font-size: 0.73rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      background: rgba(17, 17, 17, 0.05);
    }}

    .character-blocks {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}

    .character-panel {{
      border-top: 1.5px solid var(--line);
      padding-top: 12px;
    }}

    .character-panel h3 {{
      margin: 0 0 10px;
      font-size: 1.18rem;
      font-weight: 600;
    }}

    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .chip {{
      padding: 8px 11px;
      border: 1px solid var(--line);
      font-size: 0.92rem;
      line-height: 1.2;
      background: rgba(255, 255, 255, 0.26);
    }}

    .chip strong {{
      display: block;
      font-size: 0.94rem;
      margin-bottom: 2px;
    }}

    .footer {{
      padding: 18px 28px 24px;
      border-top: 2px solid var(--line);
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.5;
    }}

    @keyframes rise {{
      from {{
        opacity: 0;
        transform: translateY(18px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}

    @media (max-width: 1040px) {{
      .header {{
        grid-template-columns: 1fr;
      }}

      .hero-meta {{
        border-left: none;
        border-top: 1.5px solid var(--line);
        padding-left: 0;
        padding-top: 18px;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}

      .overview-grid,
      .leader-grid,
      .character-blocks {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}

    @media (max-width: 720px) {{
      .page {{
        width: min(calc(100vw - 18px), var(--max-width));
        margin: 10px auto 20px;
      }}

      .header,
      .content,
      .footer {{
        padding-left: 16px;
        padding-right: 16px;
      }}

      .hero-meta,
      .overview-grid,
      .leader-grid,
      .character-blocks {{
        grid-template-columns: 1fr;
      }}

      .metric-card,
      .leader-card {{
        border-right: none;
        border-bottom: 1px solid var(--line);
      }}

      .metric-card:last-child,
      .leader-card:last-child {{
        border-bottom: none;
      }}

      .section-heading {{
        display: block;
      }}

      .section-note {{
        margin-top: 8px;
        text-align: left;
      }}

      table {{
        display: block;
        overflow-x: auto;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="header">
      <section>
        <div class="eyebrow">Tournament Summary Report</div>
        <h1>{escape(tournament_name)}</h1>
        <p class="dek">
          A normalized view of the bracket with per-model efficiency, output behavior, and character tendencies.
          Cost and token metrics are shown per model turn so entrants remain comparable despite unequal game counts and match lengths.
        </p>
      </section>
      <aside class="hero-meta">
        <div class="meta-block">
          <div class="meta-label">Champion</div>
          <div class="meta-value">{escape(champion)}</div>
        </div>
        <div class="meta-block">
          <div class="meta-label">Completed Series</div>
          <div class="meta-value">{overview.completed_series} / {overview.total_series}</div>
        </div>
        <div class="meta-block">
          <div class="meta-label">Games and Turns</div>
          <div class="meta-value">{overview.total_games} games, {overview.total_game_turns} game turns</div>
        </div>
      </aside>
    </header>

    <div class="content">
      <section class="section">
        <div class="section-heading">
          <h2>Overview</h2>
          <div class="section-note">Whole-bracket medians can hide matchup effects. The report emphasizes normalized rates and clear rankings instead.</div>
        </div>
        <div class="overview-grid">
          {_metric_card("Average Cost", _currency(overview.average_cost_usd_per_model_turn), "per model turn across all entrants")}
          {_metric_card("Average Output", _number(overview.average_output_tokens_per_model_turn), "output tokens per model turn")}
          {_metric_card("Average Input", _number(overview.average_input_tokens_per_model_turn), "input tokens per model turn")}
          {_metric_card("Match Length", _number(overview.average_game_turns_per_game), "average game turns per game")}
        </div>
      </section>

      <section class="section">
        <div class="section-heading">
          <h2>Leaderboards</h2>
          <div class="section-note">Fast comparisons for efficiency, verbosity, and outright performance.</div>
        </div>
        <div class="leader-grid">
          {_leader_card("Cheapest Per Turn", cheapest, cheapest.average_cost_usd_per_turn, "avg cost / turn", currency=True)}
          {_leader_card("Most Output Per Turn", most_verbose, most_verbose.average_output_tokens_per_turn, "avg output tokens / turn")}
          {_leader_card("Best Game Win Rate", best_win_rate, best_win_rate.game_win_rate, "game win rate", percent=True)}
          {_metric_card("Field Diversity", str(len(summary.character_summaries)), "distinct characters selected in tournament")}
        </div>
      </section>

      <section class="section">
        <div class="section-heading">
          <h2>Model Table</h2>
          <div class="section-note">Seeded entrants sorted by bracket seed, with normalized cost and output rates.</div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Record</th>
              <th>Turns</th>
              <th>Cost / Turn</th>
              <th>Output / Turn</th>
              <th>Input / Turn</th>
              <th>Remaining HP</th>
              <th>Characters</th>
            </tr>
          </thead>
          <tbody>
            {_render_model_rows(summary.model_summaries)}
          </tbody>
        </table>
      </section>

      <section class="section">
        <div class="section-heading">
          <h2>Character Patterns</h2>
          <div class="section-note">Overall pick rates and win rates, then the strongest identity picks by model.</div>
        </div>
        <div class="character-blocks">
          <div class="character-panel">
            <h3>Field-Wide Character Results</h3>
            <table>
              <thead>
                <tr>
                  <th>Character</th>
                  <th>Games</th>
                  <th>Pick Rate</th>
                  <th>Win Rate</th>
                </tr>
              </thead>
              <tbody>
                {_render_character_rows(summary.character_summaries)}
              </tbody>
            </table>
          </div>
          <div class="character-panel">
            <h3>Model Character Signatures</h3>
            <div class="chips">
              {_render_signature_chips(summary.model_summaries)}
            </div>
          </div>
        </div>
      </section>
    </div>

    <footer class="footer">
      Generated from persisted tournament artifacts at <code>{escape(summary.tournament_dir)}</code>.
      The companion JSON summary is intended for downstream tooling; this HTML view is optimized for quick human review.
    </footer>
  </main>
</body>
</html>
"""


def _metric_card(label: str, value: str, detail: str) -> str:
    return (
        '<article class="metric-card">'
        f'<div class="metric-label">{escape(label)}</div>'
        f'<div class="metric-value">{escape(value)}</div>'
        f'<div class="metric-detail">{escape(detail)}</div>'
        "</article>"
    )


def _leader_card(
    label: str,
    model: ModelTournamentSummary,
    value: float,
    metric_label: str,
    *,
    currency: bool = False,
    percent: bool = False,
) -> str:
    if currency:
        rendered_value = _currency(value)
    elif percent:
        rendered_value = _percent(value)
    else:
        rendered_value = _number(value)

    return (
        '<article class="leader-card">'
        f'<div class="leader-label">{escape(label)}</div>'
        f'<div class="leader-value">{escape(model.policy_id)}</div>'
        f'<div class="leader-detail">{escape(rendered_value)} {escape(metric_label)}</div>'
        "</article>"
    )


def _render_model_rows(models: list[ModelTournamentSummary]) -> str:
    rows: list[str] = []
    for model in models:
        characters = ", ".join(
            f"{item.character} ({item.games}, {_percent(item.win_rate)})"
            for item in model.characters
        )
        badge = '<span class="champ-badge">Champion</span>' if model.champion else ""
        remaining_hp = (
            f"W {_number(model.average_remaining_hp_on_wins)} / "
            f"L {_number(model.average_remaining_hp_on_losses)}"
        )
        rows.append(
            "<tr>"
            "<td>"
            '<div class="policy">'
            f'<div class="policy-main">#{model.seed} {escape(model.policy_id)}</div>'
            f'<div class="policy-sub">{escape(model.provider or "unknown")} / {escape(model.model or "unknown")}</div>'
            f"{badge}"
            "</div>"
            "</td>"
            f"<td>{model.game_wins}-{model.game_losses} games<br>{model.series_wins}-{model.series_losses} series</td>"
            f"<td>{model.total_turns} total<br>{_number(model.average_turns_per_game)} avg/game</td>"
            f"<td>{_currency(model.average_cost_usd_per_turn)}</td>"
            f"<td>{_number(model.average_output_tokens_per_turn)}</td>"
            f"<td>{_number(model.average_input_tokens_per_turn)}</td>"
            f"<td>{escape(remaining_hp)}</td>"
            f"<td>{escape(characters)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_character_rows(characters: list[CharacterSummary]) -> str:
    rows: list[str] = []
    for item in characters:
        rows.append(
            "<tr>"
            f"<td>{escape(item.character)}</td>"
            f"<td>{item.games}</td>"
            f"<td>{_percent(item.pick_rate)}</td>"
            f"<td>{_percent(item.win_rate)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_signature_chips(models: list[ModelTournamentSummary]) -> str:
    chips: list[str] = []
    for model in models:
        if not model.characters:
            continue
        top_pick = model.characters[0]
        chips.append(
            '<article class="chip">'
            f"<strong>{escape(model.policy_id)}</strong>"
            f"{escape(top_pick.character)} was the primary pick in {top_pick.games} game"
            f"{'s' if top_pick.games != 1 else ''}, winning {_percent(top_pick.win_rate)}."
            "</article>"
        )
    return "".join(chips)


def _best(
    items: list[ModelTournamentSummary],
    *,
    key,
    reverse: bool = False,
) -> ModelTournamentSummary:
    return sorted(
        items,
        key=lambda item: (
            key(item),
            item.game_win_rate,
            -item.average_cost_usd_per_turn,
        ),
        reverse=reverse,
    )[0]


def _currency(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.4f}"


def _number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"
