extends RefCounted

# Pure data extraction from live game state into a deterministic observation dictionary.
# No side effects. Always emits p1 before p2 for stability.

const MAX_HISTORY_ENTRIES := 10

# Maps known game object types to gameplay-meaningful categories.
const OBJECT_CATEGORY_MAP := {
	"Bullet": "projectile",
	"Arrow": "projectile",
	"StickyBomb": "projectile",
	"Shuriken": "projectile",
	"LoicBeam": "projectile",
	"Zap": "projectile",
	"Fireball": "projectile",
	"WindSlash": "projectile",
	"Geyser": "install",
	"Storm": "install",
	"Trap": "install",
	"Mine": "install",
	"Shield": "effect",
	"Aura": "effect",
}


func build_observation(game, active_fighter, history: Array = []) -> Dictionary:
	var active_player = _fighter_id(active_fighter)
	var bounded_history = history.slice(max(0, history.size() - MAX_HISTORY_ENTRIES), history.size())
	return {
		"tick": int(game.current_tick),
		"frame": int(active_fighter.current_state().current_tick),
		"active_player": active_player,
		"fighters": [_build_fighter_observation(game.p1), _build_fighter_observation(game.p2)],
		"objects": _build_objects(game),
		"stage": _build_stage(game),
		"history": bounded_history,
	}


func _build_fighter_observation(fighter) -> Dictionary:
	var pos = fighter.get_pos()
	var vel = fighter.get_vel()
	var obs = {
		"id": _fighter_id(fighter),
		"character": str(fighter.name),
		"hp": int(fighter.hp),
		"max_hp": int(fighter.MAX_HEALTH),
		"meter": int(fighter.supers_available) * int(fighter.MAX_SUPER_METER) + int(fighter.super_meter),
		"burst": int(fighter.bursts_available),
		"position": {"x": pos.x, "y": pos.y},
		"velocity": {"x": vel.x, "y": vel.y},
		"facing": "right" if int(fighter.get_facing_int()) == 1 else "left",
		"current_state": str(fighter.current_state().state_name),
		"combo_count": int(fighter.combo_count),
		"blockstun": int(fighter.blockstun_ticks),
		"hitlag": int(fighter.hitlag_ticks),
		"state_interruptable": bool(fighter.state_interruptable) if "state_interruptable" in fighter else false,
		"can_feint": bool(fighter.can_feint) if "can_feint" in fighter else false,
		"grounded": pos.y <= 0.0,
	}
	# Optional advanced fighter state - only emitted when available in game
	if "air_actions_remaining" in fighter:
		obs["air_actions_remaining"] = int(fighter.air_actions_remaining)
	if "feints_remaining" in fighter:
		obs["feints_remaining"] = int(fighter.feints_remaining)
	if "initiative" in fighter:
		obs["initiative"] = bool(fighter.initiative)
	if "sadness" in fighter:
		obs["sadness"] = int(fighter.sadness)
	if "wakeup_throw_immune" in fighter:
		obs["wakeup_throw_immune"] = bool(fighter.wakeup_throw_immune)
	if "combo_proration" in fighter:
		obs["combo_proration"] = float(fighter.combo_proration)
	var char_data = _build_character_data(fighter)
	if char_data != null and char_data.size() > 0:
		obs["character_data"] = char_data
	return obs


func _build_character_data(fighter) -> Dictionary:
	var char_name = str(fighter.name)
	if char_name == "Cowboy":
		return {
			"bullets_left": int(fighter.bullets_left) if "bullets_left" in fighter else 0,
			"has_gun": bool(fighter.has_gun) if "has_gun" in fighter else false,
			"consecutive_shots": int(fighter.consecutive_shots) if "consecutive_shots" in fighter else 0,
		}
	elif char_name == "Robot":
		var data = {}
		if "loic_meter" in fighter:
			data["loic_meter"] = int(fighter.loic_meter)
		if "loic_meter_max" in fighter:
			data["loic_meter_max"] = int(fighter.loic_meter_max)
		if "can_loic" in fighter:
			data["can_loic"] = bool(fighter.can_loic)
		if "armor_pips" in fighter:
			data["armor_pips"] = int(fighter.armor_pips)
		if "armor_active" in fighter:
			data["armor_active"] = bool(fighter.armor_active)
		return data
	elif char_name == "Ninja":
		var data = {}
		if "momentum_stores" in fighter:
			data["momentum_stores"] = int(fighter.momentum_stores)
		if "sticky_bombs_left" in fighter:
			data["sticky_bombs_left"] = int(fighter.sticky_bombs_left)
		if "juke_pips" in fighter:
			data["juke_pips"] = int(fighter.juke_pips)
		if "juke_pips_max" in fighter:
			data["juke_pips_max"] = int(fighter.juke_pips_max)
		return data
	elif char_name == "Mutant":
		var data = {}
		if "juke_pips" in fighter:
			data["juke_pips"] = int(fighter.juke_pips)
		if "juke_pips_max" in fighter:
			data["juke_pips_max"] = int(fighter.juke_pips_max)
		if "install_ticks" in fighter:
			data["install_ticks"] = int(fighter.install_ticks)
		if "bc_charge" in fighter:
			data["bc_charge"] = int(fighter.bc_charge)
		return data
	elif char_name == "Wizard":
		var data = {}
		if "hover_left" in fighter:
			data["hover_left"] = int(fighter.hover_left)
		if "hover_max" in fighter:
			data["hover_max"] = int(fighter.hover_max)
		if "geyser_charge" in fighter:
			data["geyser_charge"] = int(fighter.geyser_charge)
		if "gusts_in_combo" in fighter:
			data["gusts_in_combo"] = int(fighter.gusts_in_combo)
		return data
	return {}


func _build_stage(game) -> Dictionary:
	return {
		"width": int(game.stage_width),
		"ceiling_height": int(game.ceiling_height),
		"has_ceiling": bool(game.has_ceiling),
	}


func _build_objects(game) -> Array:
	var result = []
	if not game.has("objects") and not ("objects" in game):
		return result
	var objects = game.objects
	if objects == null:
		return result
	for obj in objects:
		if obj == null:
			continue
		if not is_instance_valid(obj):
			continue
		var obj_pos = obj.get_pos() if obj.has_method("get_pos") else Vector2.ZERO
		var raw_type = _resolve_object_type(obj)
		var entry = {
			"type": raw_type,
			"position": {"x": obj_pos.x, "y": obj_pos.y},
		}
		var category = _classify_object(raw_type)
		if category != "unknown":
			entry["category"] = category
		if "owner_id" in obj:
			entry["owner"] = "p1" if int(obj.owner_id) == 1 else "p2"
		result.append(entry)
	# Sort by type + position for determinism
	result.sort_custom(self, "_sort_objects")
	return result


func _resolve_object_type(obj) -> String:
	# Prefer the node name (usually gameplay-meaningful like "Bullet", "StickyBomb")
	if obj.name != null and str(obj.name) != "":
		var node_name = str(obj.name)
		# Strip trailing digits added by Godot for duplicate nodes (e.g. "Bullet2" -> "Bullet")
		while node_name.length() > 0 and node_name[-1] >= "0" and node_name[-1] <= "9":
			node_name = node_name.substr(0, node_name.length() - 1)
		if node_name != "":
			return node_name
	# Fall back to script resource filename
	var script = obj.get_script()
	if script != null and script.resource_path != "":
		var path = str(script.resource_path)
		var filename = path.get_file().get_basename()
		if filename != "":
			return filename
	# Final fallback: engine class
	return str(obj.get_class())


func _classify_object(type_name: String) -> String:
	if type_name in OBJECT_CATEGORY_MAP:
		return OBJECT_CATEGORY_MAP[type_name]
	return "unknown"


func _sort_objects(a: Dictionary, b: Dictionary) -> bool:
	var key_a = "%s_%s_%s" % [a["type"], a["position"]["x"], a["position"]["y"]]
	var key_b = "%s_%s_%s" % [b["type"], b["position"]["x"], b["position"]["y"]]
	return key_a < key_b


func _fighter_id(fighter) -> String:
	return "p1" if int(fighter.id) == 1 else "p2"
