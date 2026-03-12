extends Node

# Detects actionable turns and emits DecisionRequest envelopes to the daemon.
# Extends Node (not RefCounted) because it needs _process() and scene tree access.

var _bridge_client = null
var _config = {}
var _game = null
var _game_connected = false
var _match_id = ""
var _turn_id = 0
var _observation_builder = null
var _legal_action_builder = null


func attach(bridge_client, config: Dictionary) -> void:
	_bridge_client = bridge_client
	_config = config
	_observation_builder = load(_get_script_dir() + "/ObservationBuilder.gd").new()
	_legal_action_builder = load(_get_script_dir() + "/LegalActionBuilder.gd").new()
	set_process(true)


func _process(_delta: float) -> void:
	if _game_connected:
		return
	if not _has_global_game():
		return

	_game = Global.current_game
	_match_id = _generate_uuid_v4()
	_turn_id = 0
	_game.connect("player_actionable", self, "_on_player_actionable")
	_game_connected = true
	print("YomiLLMBridge TurnHook attached to game, match_id=%s" % _match_id)


func _on_player_actionable() -> void:
	if _game == null:
		return

	var actionable_players = _get_actionable_players()
	for player_id in actionable_players:
		var fighter = _game.p1 if player_id == "p1" else _game.p2
		var action_buttons = _find_action_buttons(player_id)
		if action_buttons == null:
			printerr("YomiLLMBridge TurnHook could not find action buttons for %s" % player_id)
			continue

		_turn_id += 1
		var observation = _observation_builder.build_observation(_game, fighter)
		var legal_actions = _legal_action_builder.build_legal_actions(_game, fighter, action_buttons)

		var state_hash = _sha256_of_canonical_json(observation)
		var legal_actions_hash = _sha256_of_canonical_json(legal_actions)

		var decision_request = {
			"match_id": _match_id,
			"turn_id": _turn_id,
			"player_id": player_id,
			"deadline_ms": int(_config.get("decision_timeout_ms", 2500)),
			"state_hash": state_hash,
			"legal_actions_hash": legal_actions_hash,
			"decision_type": "turn_action",
			"observation": observation,
			"legal_actions": legal_actions,
		}

		_bridge_client._send_json_message(decision_request)


func _get_actionable_players() -> Array:
	var players = []
	if _game.p1_turn and _is_fighter_interruptable(_game.p1):
		players.append("p1")
	if _game.p2_turn and _is_fighter_interruptable(_game.p2):
		players.append("p2")
	return players


func _is_fighter_interruptable(fighter) -> bool:
	if fighter == null:
		return false
	return bool(fighter.state_interruptable)


func _find_action_buttons(player_id: String):
	var node_name = "P1ActionButtons" if player_id == "p1" else "P2ActionButtons"
	return get_tree().get_root().find_node(node_name, true, false)


func _sha256_of_canonical_json(data) -> String:
	var json_text = to_json(data)
	var ctx = HashingContext.new()
	ctx.start(HashingContext.HASH_SHA256)
	ctx.update(json_text.to_utf8())
	var digest = ctx.finish()
	return _bytes_to_hex(digest)


func _bytes_to_hex(bytes: PoolByteArray) -> String:
	var hex = ""
	for b in bytes:
		hex += "%02x" % b
	return hex


func _has_global_game() -> bool:
	if not ("current_game" in Global):
		return false
	return Global.current_game != null


func _generate_uuid_v4() -> String:
	randomize()
	var hex_chars = "0123456789abcdef"
	var uuid = ""
	for i in range(32):
		if i == 12:
			uuid += "4"
		elif i == 16:
			uuid += hex_chars[8 + (randi() % 4)]
		else:
			uuid += hex_chars[randi() % 16]
		if i == 7 or i == 11 or i == 15 or i == 19:
			uuid += "-"
	return uuid


func _get_script_dir() -> String:
	return get_script().resource_path.get_base_dir()
