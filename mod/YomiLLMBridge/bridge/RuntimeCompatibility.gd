extends Reference

const SUPPORTED_BUILD_ID := 16151810
const SUPPORTED_GAME_VERSION := "supported-build-16151810"
const SUPPORTED_GLOBAL_VERSION := "1.9.20-steam"
const SUPPORTED_ENGINE_VERSION := "3.5.1"
const REQUIRED_GAME_SIGNALS := ["player_actionable", "game_ended", "game_won"]
const REQUIRED_ACTION_BUTTON_NODES := ["P1ActionButtons", "P2ActionButtons"]
const REQUIRED_GAME_PROPERTIES := ["p1", "p2", "current_tick", "game_end_tick", "time"]
const REQUIRED_FIGHTER_PROPERTIES := [
	"hp",
	"queued_action",
	"queued_data",
	"queued_extra",
	"state_interruptable",
	"game_over",
]
const REQUIRED_FIGHTER_METHODS := ["on_action_selected"]


func check(game, scene_root, config: Dictionary) -> Dictionary:
	var details = {
		"supported_build_id": SUPPORTED_BUILD_ID,
		"supported_game_version": SUPPORTED_GAME_VERSION,
		"supported_global_version": SUPPORTED_GLOBAL_VERSION,
		"supported_engine_version": SUPPORTED_ENGINE_VERSION,
		"expected_game_version": str(config.get("game_version", SUPPORTED_GAME_VERSION)),
		"live_game_version": _read_global_version(),
		"engine_version": _read_engine_version(),
		"game_script_path": _script_path(game),
		"action_button_nodes": [],
	}
	var errors := []

	if details["live_game_version"] != SUPPORTED_GLOBAL_VERSION:
		errors.append(
			"unsupported game version: expected %s, got %s"
			% [SUPPORTED_GLOBAL_VERSION, details["live_game_version"]]
		)

	if not str(details["engine_version"]).begins_with(SUPPORTED_ENGINE_VERSION):
		errors.append(
			"unsupported engine version: expected %s.x, got %s"
			% [SUPPORTED_ENGINE_VERSION, details["engine_version"]]
		)

	if not _script_path(game).ends_with("game.gd"):
		errors.append(
			"unsupported game script path: expected suffix game.gd, got %s"
			% _script_path(game)
		)

	for signal_name in REQUIRED_GAME_SIGNALS:
		if game == null or not game.has_signal(signal_name):
			errors.append("missing required game signal: %s" % signal_name)

	for property_name in REQUIRED_GAME_PROPERTIES:
		if not _has_property(game, property_name):
			errors.append("missing required game property: %s" % property_name)

	for player_id in ["p1", "p2"]:
		var fighter = _safe_get(game, player_id, null)
		if fighter == null:
			errors.append("missing fighter object: %s" % player_id)
			continue
		for property_name in REQUIRED_FIGHTER_PROPERTIES:
			if not _has_property(fighter, property_name):
				errors.append("missing %s fighter property: %s" % [player_id, property_name])
		for method_name in REQUIRED_FIGHTER_METHODS:
			if not fighter.has_method(method_name):
				errors.append("missing %s fighter method: %s" % [player_id, method_name])

	for node_name in REQUIRED_ACTION_BUTTON_NODES:
		var node = null
		if scene_root != null:
			node = scene_root.find_node(node_name, true, false)
		if node == null:
			errors.append("missing required scene node: %s" % node_name)
		else:
			details["action_button_nodes"].append(node_name)

	return {
		"compatible": errors.empty(),
		"state": "compatible" if errors.empty() else "compatibility_failed",
		"errors": errors,
		"details": details,
	}


func _read_global_version() -> String:
	if "VERSION" in Global:
		return str(Global.VERSION)
	return "unknown"


func _read_engine_version() -> String:
	var info = Engine.get_version_info()
	if info is Dictionary and info.has("string"):
		return str(info["string"])
	return "unknown"


func _script_path(target) -> String:
	if target == null:
		return ""
	var script = target.get_script()
	if script == null:
		return ""
	return str(script.resource_path)


func _has_property(target, property_name: String) -> bool:
	if target == null or not target.has_method("get_property_list"):
		return false
	for property_data in target.get_property_list():
		if str(property_data.get("name", "")) == property_name:
			return true
	return false


func _safe_get(target, property_name: String, fallback):
	if target == null or not _has_property(target, property_name):
		return fallback
	return target.get(property_name)
