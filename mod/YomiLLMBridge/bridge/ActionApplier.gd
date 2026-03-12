extends RefCounted

# Injects validated decisions into the game's native decision pipeline.
# Writes queued_action, queued_data, and queued_extra on the fighter node,
# mirroring the pattern used by _AIOpponents in the base game.


func apply_decision(decision_payload: Dictionary, fighter) -> Dictionary:
	# Returns a result dict with "applied" bool and optional "error" string.
	if fighter == null:
		return {"applied": false, "error": "fighter is null"}

	var action_name = str(decision_payload.get("action", ""))
	if action_name == "":
		return {"applied": false, "error": "empty action name"}

	# Write the queued fields that the native engine reads during turn resolution
	fighter.queued_action = action_name
	fighter.queued_data = _resolve_queued_data(decision_payload.get("data"))
	fighter.queued_extra = _resolve_queued_extra(decision_payload.get("extra"))

	return {"applied": true, "action": action_name}


func _resolve_queued_data(data):
	# Pass through dict or null; the engine accepts both
	if data == null:
		return null
	if data is Dictionary:
		return data.duplicate(true)
	return null


func _resolve_queued_extra(extra):
	# Normalize the extra dict into the shape the engine expects
	if extra == null:
		return null
	if not (extra is Dictionary):
		return null

	var resolved = {}

	var di = extra.get("di")
	if di != null and di is Dictionary:
		resolved["di"] = {
			"x": int(di.get("x", 0)),
			"y": int(di.get("y", 0)),
		}
	else:
		resolved["di"] = null

	resolved["feint"] = bool(extra.get("feint", false))
	resolved["reverse"] = bool(extra.get("reverse", false))

	return resolved
