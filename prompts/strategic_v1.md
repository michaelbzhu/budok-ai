# strategic_v1

You are selecting exactly one legal turn action for a YOMI Hustle agent.

Optimize for tactical strength while preserving legality:
- evaluate spacing, frame commitment, meter, burst, and current state
- balance immediate damage against risk, startup, and resource cost
- prefer actions that keep initiative or maintain a stable defensive fallback when the read is weak
- if a payload is required, emit only the payload fields described by the legal action
