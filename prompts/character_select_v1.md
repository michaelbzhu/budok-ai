# Character Selection

You are about to play a match in YOMI Hustle, a turn-based fighting game built on reads, predictions, and the classic fighting game triangle: **attacks beat grabs, grabs beat blocks, blocks beat attacks**.

Choose which character you want to play. You are picking blind — you do not know what your opponent will pick. Mirror matches (both players picking the same character) are allowed.

## Characters

### Ninja
**Archetype**: Rushdown / mobility. Ninja excels at close range with fast multi-hit attacks and incredible movement options. High damage potential but requires getting in close.

**Strengths**:
- Fastest attacks in the game (NunChukLight, GroundedPunch)
- Excellent mobility (GrapplingHook, DiveKick, SlideKick)
- Very high burst damage combos (PalmStrike, Uppercut, QuickSlash super)
- Sticky Bombs for area denial and setplay

**Weaknesses**:
- Most attacks are short range — struggles at distance
- Relies on getting in close, vulnerable to zoning
- High-damage moves (PalmStrike, Uppercut) are very punishable on whiff

**Key Moves**:
- **NunChukLight** (fast, high damage, short range) — primary pressure tool, beats grabs
- **PalmStrike** (medium speed, very high damage, short range) — biggest punish, huge damage on hit
- **SlideKick** (fast, medium damage, long range) — slides under projectiles, great approach
- **Uppercut** (medium speed, very high damage, short range) — launcher for air combos
- **QuickSlash** (super, fast, very high damage, medium range) — 12-hit super, costs meter

---

### Cowboy
**Archetype**: Versatile / mid-range. Cowboy has strong sword normals, a unique gun stance (Brandish) for fullscreen pressure, and Lasso for long-range grabs. Jack of all trades.

**Strengths**:
- Excellent mid-range sword attacks (Stinger, HSlash2, VSlash)
- Gun stance (Brandish → Shoot/PointBlank) gives fullscreen threat
- Lasso is a long-range grab that beats blocking at distance
- Good defensive tools (SpotDodge, Guntrick, Foresight)

**Weaknesses**:
- Gun moves require spending a turn entering Brandish stance first
- Stinger and other big sword moves are very punishable on block/whiff
- Slower startup on high-damage moves compared to Ninja

**Key Moves**:
- **Pommel** (fast, low damage, short range) — safe pressure starter, combos into 3Combo
- **Stinger** (medium speed, high damage, long range) — best mid-range poke, very punishable on block
- **Lasso** (medium speed, medium damage, long range) — long-range grab, leads to Izuna Drop
- **Brandish** (fast, no damage) — enters Quick Draw stance, unlocks Shoot, PointBlank, PistolWhip
- **LightningSliceNeutral** (fast, high damage, long range) — fast ranged attack, loses to blocking

---

### Wizard
**Archetype**: Zoner / setup. Wizard controls space with projectiles, traps, and the Orb system. Strong at range with versatile elemental attacks.

**Strengths**:
- Best zoning in the game (FlameWave, MagicMissile, Geyser, Sandstorm)
- Orb system creates complex setplay (Orb → SparkBomb, Launch, Teleport)
- Strong damage at mid range (BoltOfMagma, ConjureWeapon)
- Instant teleport via OrbTeleport for repositioning

**Weaknesses**:
- Weaker up close than rushdown characters
- Orb setup requires meter and time investment
- Some zoning tools are slow and punishable at close range

**Key Moves**:
- **BoltOfMagma** (fast, high damage, medium range) — multi-hit blast, chips through block
- **ZephyrThrow** (medium speed, very high damage, short range) — massive damage grab
- **FlameWave** (medium speed, medium damage, long range) — fire projectile for zone control
- **Orb** (super, medium speed) — creates versatile magic orb for setplay
- **Liftoff** (fast, high damage, long range) — fast dash attack, covers distance quickly

---

### Robot
**Archetype**: Grappler / heavy. Robot has powerful grabs, high-damage supers, and a unique Drive (motorcycle) stance. Excels at close range with devastating command grabs.

**Strengths**:
- Multiple grab options (Vacuum, CommandGrab, DisembowelHitGrab, CornerCarryHitGrab)
- Vacuum grab has better range than most grabs
- Devastating supers (KillProcess for 400 damage, LOIC for fullscreen)
- Drive stance unlocks fast mobile attacks
- Strong multi-hit pressure (EyeBeam, Flamethrower)

**Weaknesses**:
- Generally slower than other characters
- Big commitment on many moves — punishable if read
- Drive stance requires a turn to enter
- Struggles against characters that can maintain distance

**Key Moves**:
- **Vacuum** (medium speed, high damage, medium range) — best grab, decent range
- **Slap** (fast, medium damage, short range) — quick poke, beats grabs
- **EyeBeam** (medium speed, medium damage, long range) — multi-hit laser at range
- **KillProcess** (super, slow, very high damage, short range) — devastating close-range super
- **Drive** (fast, no damage) — enters motorcycle stance, unlocks mobile attacks

---

### Mutant
**Archetype**: Aggressive / vortex. Mutant has fast claw attacks, acid damage-over-time, and excellent mix-up potential with cross-ups and multi-hit moves.

**Strengths**:
- Very fast normals (Swipe variants are all fast)
- Acid moves apply damage over time (AcidSlashH, BiteGrab)
- Great mix-ups: overhead (OverheadClaw), low (SwipeDown), cross-up (DashThroughAttack)
- Strong approach tools (AcidSlashJ, WallTrick, Pounce)
- Rebirth super for comeback potential

**Weaknesses**:
- Many attacks are short range
- High-commitment multi-hit moves are punishable on block
- Less effective at fullscreen distance than Wizard or Cowboy

**Key Moves**:
- **Swipe** (fast, medium damage, short range) — reliable fast attack, beats grabs
- **DashThroughAttack** (fast, high damage, long range) — crosses up, confuses blocking
- **AcidSlashJ** (medium speed, high damage, long range) — 10-hit leaping approach
- **BiteGrab** (slow, high damage, short range) — poison grab, damage over time
- **GroundToAirSpin** (medium speed, high damage, short range) — 9-hit combo potential

## Output Format

Return exactly one JSON object (no Markdown fences). Required fields:
- `reasoning` (string): Your strategic reasoning for this character choice. Consider your general fighting game strategy, the character's strengths and weaknesses, and how they match your preferred playstyle.
- `character` (string): One of `Ninja`, `Cowboy`, `Wizard`, `Robot`, `Mutant`.

Example: `{"reasoning": "I want to control space and keep my opponent at range. Wizard has the best zoning tools and Orb setplay gives me complex options.", "character": "Wizard"}`
