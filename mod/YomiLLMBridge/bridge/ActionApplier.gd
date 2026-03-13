extends Reference

# Injects validated decisions into the game's native decision pipeline.
# Prefer the fighter's native action-selection hook when available so ready-state
# and downstream commit logic stay aligned with the game's own lifecycle.

const NATIVE_APPLY_METHOD := "on_action_selected"
const ACTION_SELECTED_SIGNAL := "action_selected"


func apply_decision(decision_payload: Dictionary, fighter) -> Dictionary:
	# Returns a result dict with "applied" bool and optional "error" string.
	if fighter == null:
		return {"applied": false, "error": "fighter is null"}

	var action_name = str(decision_payload.get("action", ""))
	if action_name == "":
		return {"applied": false, "error": "empty action name"}

	var queued_data = _resolve_queued_data(decision_payload.get("data"))
	var queued_extra = _resolve_queued_extra(decision_payload.get("extra"))

	if fighter.has_method(NATIVE_APPLY_METHOD):
		fighter.call(NATIVE_APPLY_METHOD, action_name, queued_data, queued_extra)
		return _build_apply_result(action_name, fighter, "native_method")

	if fighter.has_signal(ACTION_SELECTED_SIGNAL):
		fighter.emit_signal(ACTION_SELECTED_SIGNAL, action_name, queued_data, queued_extra)
		return _build_apply_result(action_name, fighter, "signal_emit")

	if not _can_write_queued_fields(fighter):
		return {"applied": false, "error": "fighter is missing native apply hooks"}

	# Retain queued-field mutation only as a compatibility fallback for harnesses.
	fighter.queued_action = action_name
	fighter.queued_data = queued_data
	fighter.queued_extra = queued_extra

	return _build_apply_result(action_name, fighter, "queued_fields")


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
	resolved["prediction"] = _resolve_prediction(extra.get("prediction"))

	return resolved


func _resolve_prediction(prediction):
	if prediction == null:
		return null
	if prediction is Dictionary:
		return prediction.duplicate(true)
	return null


func _build_apply_result(action_name: String, fighter, apply_path: String) -> Dictionary:
	return {
		"applied": true,
		"action": action_name,
		"apply_path": apply_path,
		"queued_action": _maybe_get_property(fighter, "queued_action"),
		"queued_data": _duplicate_value(_maybe_get_property(fighter, "queued_data")),
		"queued_extra": _duplicate_value(_maybe_get_property(fighter, "queued_extra")),
	}


func _can_write_queued_fields(fighter) -> bool:
	return (
		_has_property(fighter, "queued_action")
		and _has_property(fighter, "queued_data")
		and _has_property(fighter, "queued_extra")
	)


func _maybe_get_property(target, property_name: String):
	if not _has_property(target, property_name):
		return null
	return target.get(property_name)


func _has_property(target, property_name: String) -> bool:
	if target == null:
		return false
	for property_info in target.get_property_list():
		if str(property_info.get("name", "")) == property_name:
			return true
	return false


func _duplicate_value(value):
	if value is Dictionary or value is Array:
		return value.duplicate(true)
	return value
