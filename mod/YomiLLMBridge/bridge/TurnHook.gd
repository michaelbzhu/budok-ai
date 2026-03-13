extends Node

# Detects actionable turns, emits DecisionRequest envelopes to the daemon,
# receives and validates responses, applies decisions or triggers fallback,
# and emits telemetry events throughout the lifecycle.
# Extends Node (not RefCounted) because it needs _process() and scene tree access.

var _bridge_client = null
var _config = {}
var _game = null
var _game_connected = false
var _match_id = ""
var _turn_id = 0
var _observation_builder = null
var _legal_action_builder = null
var _decision_validator = null
var _action_applier = null
var _fallback_handler = null
var _telemetry = null
var _protocol_codec = null

# Pending requests keyed by "p1_turnid" or "p2_turnid" for correlation
var _pending_requests = {}
# Timestamps (OS.get_ticks_msec) for pending requests, same keys
var _pending_timestamps = {}


func attach(bridge_client, config: Dictionary) -> void:
	_bridge_client = bridge_client
	_config = config
	_observation_builder = load(_get_script_dir() + "/ObservationBuilder.gd").new()
	_legal_action_builder = load(_get_script_dir() + "/LegalActionBuilder.gd").new()
	_decision_validator = load(_get_script_dir() + "/DecisionValidator.gd").new()
	_action_applier = load(_get_script_dir() + "/ActionApplier.gd").new()
	_fallback_handler = load(_get_script_dir() + "/FallbackHandler.gd").new()
	_telemetry = load(_get_script_dir() + "/Telemetry.gd").new()
	_protocol_codec = load(_get_script_dir() + "/ProtocolCodec.gd").new()

	# Subscribe to daemon messages and disconnect events
	_bridge_client.connect("daemon_message", self, "_on_daemon_message")
	_bridge_client.connect("daemon_disconnected", self, "_on_daemon_disconnected")

	set_process(true)


func _process(_delta: float) -> void:
	if _game_connected:
		_check_timeouts()
		return
	if not _has_global_game():
		return

	_game = Global.current_game
	_match_id = _generate_uuid_v4()
	_turn_id = 0
	_game.connect("player_actionable", self, "_on_player_actionable")
	_game_connected = true

	# Configure telemetry now that we have a match_id
	_telemetry.configure(_bridge_client, _config, _match_id)

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

		var state_hash = _protocol_codec.sha256_hex(observation)
		var legal_actions_hash = _protocol_codec.sha256_hex(legal_actions)

		var decision_request_payload = {
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
		var decision_request_envelope = _protocol_codec.build_envelope(
			"decision_request",
			decision_request_payload
		)

		# Track pending request for correlation
		var pending_key = _pending_key(player_id, _turn_id)
		_pending_requests[pending_key] = decision_request_payload
		_pending_timestamps[pending_key] = OS.get_ticks_msec()

		_bridge_client._send_json_message(decision_request_envelope)

		_telemetry.emit_turn_requested(
			_turn_id, player_id, state_hash, legal_actions_hash,
			OS.get_ticks_msec()
		)


func _on_daemon_message(envelope: Dictionary) -> void:
	if str(envelope.get("type", "")) != "action_decision":
		return
	if not envelope.has("payload") or not (envelope["payload"] is Dictionary):
		return
	var payload: Dictionary = envelope["payload"]

	var player_id = _find_player_for_decision(payload)
	if player_id == "":
		return

	var pending_key = _pending_key(player_id, int(payload.get("turn_id", -1)))
	var request = _pending_requests.get(pending_key)
	if request == null:
		# No pending request for this decision; could be stale
		return

	var sent_time = _pending_timestamps.get(pending_key, OS.get_ticks_msec())
	var latency_ms = OS.get_ticks_msec() - sent_time

	# Validate the decision against the pending request
	var validation_error = _decision_validator.validate_decision(envelope, request)

	if validation_error == "":
		# Valid decision: apply it
		_apply_valid_decision(payload, request, player_id, latency_ms)
	else:
		# Invalid decision: use fallback
		_apply_fallback(request, player_id, validation_error, latency_ms)


func _apply_valid_decision(
	payload: Dictionary,
	request: Dictionary,
	player_id: String,
	latency_ms: int
) -> void:
	var fighter = _game.p1 if player_id == "p1" else _game.p2

	var apply_result = _action_applier.apply_decision(payload, fighter)
	if not bool(apply_result.get("applied", false)):
		# Application failed, use fallback
		_apply_fallback(request, player_id, "malformed_output", latency_ms)
		return

	var action_name = str(payload.get("action", ""))
	var policy_id = str(payload.get("policy_id", ""))

	# Record as last valid decision for potential replay
	_fallback_handler.record_valid_decision(player_id, payload)

	_telemetry.emit_decision_received(
		int(request.get("turn_id", 0)), player_id, action_name, latency_ms, policy_id
	)
	_telemetry.emit_decision_applied(
		int(request.get("turn_id", 0)), player_id, action_name, latency_ms
	)

	# Clear pending request
	var pending_key = _pending_key(player_id, int(request.get("turn_id", 0)))
	_clear_pending(pending_key)


func _apply_fallback(
	request: Dictionary,
	player_id: String,
	fallback_reason: String,
	latency_ms: int
) -> void:
	var fallback_mode = str(_config.get("fallback_mode", "safe_continue"))
	var fallback_decision = _fallback_handler.choose_fallback(
		request, fallback_reason, fallback_mode
	)

	var fighter = _game.p1 if player_id == "p1" else _game.p2
	var apply_result = _action_applier.apply_decision(fallback_decision, fighter)

	var fallback_action = str(fallback_decision.get("action", ""))
	var strategy = str(fallback_decision.get("policy_id", "fallback/safe_continue"))
	# Strip "fallback/" prefix for the strategy name
	if strategy.begins_with("fallback/"):
		strategy = strategy.substr(9)

	_telemetry.emit_decision_fallback(
		int(request.get("turn_id", 0)),
		player_id,
		fallback_reason,
		fallback_action,
		strategy,
		latency_ms
	)

	# Clear pending request
	var pending_key = _pending_key(player_id, int(request.get("turn_id", 0)))
	_clear_pending(pending_key)


func _on_daemon_disconnected(_details: Dictionary) -> void:
	# Resolve all pending requests as disconnected
	var keys_to_resolve = _pending_requests.keys().duplicate()
	for pending_key in keys_to_resolve:
		var request = _pending_requests.get(pending_key)
		if request == null:
			continue
		var player_id = str(request.get("player_id", ""))
		var sent_time = _pending_timestamps.get(pending_key, OS.get_ticks_msec())
		var latency_ms = OS.get_ticks_msec() - sent_time
		_apply_fallback(request, player_id, "disconnect", latency_ms)


func _check_timeouts() -> void:
	var timeout_ms = int(_config.get("decision_timeout_ms", 2500))
	var now = OS.get_ticks_msec()
	var keys_to_resolve = []

	for pending_key in _pending_timestamps:
		var sent_time = _pending_timestamps[pending_key]
		if now - sent_time >= timeout_ms:
			keys_to_resolve.append(pending_key)

	for pending_key in keys_to_resolve:
		var request = _pending_requests.get(pending_key)
		if request == null:
			continue
		var player_id = str(request.get("player_id", ""))
		var latency_ms = now - _pending_timestamps.get(pending_key, now)
		_apply_fallback(request, player_id, "timeout", latency_ms)


func _find_player_for_decision(payload: Dictionary) -> String:
	# Match decision to player via turn_id and match_id correlation
	var decision_match_id = str(payload.get("match_id", ""))
	var decision_turn_id = int(payload.get("turn_id", -1))

	for player_id in ["p1", "p2"]:
		var key = _pending_key(player_id, decision_turn_id)
		if _pending_requests.has(key):
			var request = _pending_requests[key]
			if str(request.get("match_id", "")) == decision_match_id:
				return player_id

	return ""


func _pending_key(player_id: String, turn_id: int) -> String:
	return "%s_%d" % [player_id, turn_id]


func _clear_pending(pending_key: String) -> void:
	_pending_requests.erase(pending_key)
	_pending_timestamps.erase(pending_key)


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
