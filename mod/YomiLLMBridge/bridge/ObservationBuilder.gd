extends RefCounted

# Pure data extraction from live game state into a deterministic observation dictionary.
# No side effects. Always emits p1 before p2 for stability.


func build_observation(game, active_fighter) -> Dictionary:
	var active_player = _fighter_id(active_fighter)
	return {
		"tick": int(game.current_tick),
		"frame": int(active_fighter.current_state().current_tick),
		"active_player": active_player,
		"fighters": [_build_fighter_observation(game.p1), _build_fighter_observation(game.p2)],
		"objects": _build_objects(game),
		"stage": _build_stage(game),
		"history": [],
	}


func _build_fighter_observation(fighter) -> Dictionary:
	var pos = fighter.get_pos()
	var vel = fighter.get_vel()
	return {
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
		"hitstun": int(fighter.blockstun_ticks),
		"hitlag": int(fighter.hitlag_ticks),
	}


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
		var entry = {
			"type": str(obj.get_class()),
			"position": {"x": obj_pos.x, "y": obj_pos.y},
		}
		result.append(entry)
	# Sort by type + position for determinism
	result.sort_custom(self, "_sort_objects")
	return result


func _sort_objects(a: Dictionary, b: Dictionary) -> bool:
	var key_a = "%s_%s_%s" % [a["type"], a["position"]["x"], a["position"]["y"]]
	var key_b = "%s_%s_%s" % [b["type"], b["position"]["x"], b["position"]["y"]]
	return key_a < key_b


func _fighter_id(fighter) -> String:
	return "p1" if int(fighter.id) == 1 else "p2"
