# few_shot_v1

You are selecting exactly one legal turn action for a YOMI Hustle agent.

Follow these examples:

Example 1:
- If `guard` and a risky `super_dash` are both legal while the state is neutral and information is limited, prefer `guard`.

Example 2:
- If a low-startup punish is legal after the opponent is unsafe, prefer the punish over a passive reset.

Apply the same pattern to the live turn:
- stay legal
- keep the output compact
- favor clear punish windows, otherwise take the safer option
