extends Node

signal status_changed(snapshot)

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
var _identifier_factory = null
var _runtime_compatibility = null
var _status_snapshot = {
	"state": "waiting_for_game",
	"compatible": null,
	"match_id": "",
	"errors": [],
	"details": {},
	"match_ended": null,
}
var _compatibility_signature = ""
var _match_end_pending = {}
var _match_ended = false
var _replay_snapshot = {}

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
	_identifier_factory = load(_get_script_dir() + "/IdentifierFactory.gd").new()
	_runtime_compatibility = load(_get_script_dir() + "/RuntimeCompatibility.gd").new()

	# Subscribe to daemon messages and disconnect events
	_bridge_client.connect("daemon_message", self, "_on_daemon_message")
	_bridge_client.connect("daemon_disconnected", self, "_on_daemon_disconnected")

	set_process(true)
	_publish_status("waiting_for_game", null, [], {})


func _process(_delta: float) -> void:
	if _game_connected:
		_check_timeouts()
		return
	if not _has_global_game():
		_publish_status("waiting_for_game", null, [], {})
		return

	var compatibility = _runtime_compatibility.check(
		Global.current_game,
		get_tree().get_root(),
		_config
	)
	if not bool(compatibility.get("compatible", false)):
		_publish_compatibility_failure(compatibility)
		return
	_attach_to_game(Global.current_game, compatibility)


func _check_already_actionable() -> void:
	if _game == null or _match_ended:
		return
	var actionable = _get_actionable_players()
	if not actionable.empty():
		print("YomiLLMBridge: players already actionable on attach: %s" % [actionable])
		_on_player_actionable()


func _on_player_actionable() -> void:
	if _game == null or _match_ended:
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
	if _match_ended:
		return
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
	if _match_ended:
		return
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
	if _match_ended:
		return
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


func get_status() -> Dictionary:
	return _status_snapshot.duplicate(true)


func _attach_to_game(game, compatibility: Dictionary) -> void:
	if _game_connected and _game == game:
		return

	_game = game
	_match_id = _identifier_factory.new_match_id()
	_turn_id = 0
	_match_end_pending = {}
	_match_ended = false
	_compatibility_signature = ""
	_pending_requests.clear()
	_pending_timestamps.clear()
	_replay_snapshot = _snapshot_replay_directory("user://replay/autosave")
	_game.connect("player_actionable", self, "_on_player_actionable")
	_game.connect("game_ended", self, "_on_game_ended")
	_game.connect("game_won", self, "_on_game_won")
	_game_connected = true

	# Configure telemetry now that we have a match_id
	_telemetry.configure(_bridge_client, _config, _match_id)

	_publish_status("active", true, [], compatibility.get("details", {}))
	print("YomiLLMBridge TurnHook attached to game, match_id=%s" % _match_id)

	# If players are already actionable (signal fired before we attached), trigger immediately
	call_deferred("_check_already_actionable")


func _publish_compatibility_failure(compatibility: Dictionary) -> void:
	var errors = compatibility.get("errors", [])
	var details = compatibility.get("details", {})
	var signature = to_json({"errors": errors, "details": details})
	_publish_status("compatibility_failed", false, errors, details)
	if signature == _compatibility_signature:
		return
	_compatibility_signature = signature
	for error_message in errors:
		printerr("YomiLLMBridge compatibility check failed: %s" % error_message)


func _publish_status(
	state: String,
	compatible,
	errors: Array,
	details: Dictionary,
	match_ended_payload = null
) -> void:
	var next_snapshot = {
		"state": state,
		"compatible": compatible,
		"match_id": _match_id,
		"errors": errors.duplicate(true),
		"details": details.duplicate(true),
		"match_ended": (
			match_ended_payload.duplicate(true)
			if match_ended_payload is Dictionary
			else match_ended_payload
		),
	}
	if to_json(next_snapshot) == to_json(_status_snapshot):
		return
	_status_snapshot = next_snapshot
	emit_signal("status_changed", get_status())


func _on_game_ended() -> void:
	if _match_ended:
		return
	_match_end_pending = _build_match_ended_payload(null)


func _on_game_won(winner) -> void:
	if _match_ended:
		return
	var payload = (
		_match_end_pending.duplicate(true)
		if not _match_end_pending.empty()
		else _build_match_ended_payload(winner)
	)
	payload["winner"] = _normalize_winner(winner)
	_finalize_match(payload)


func _finalize_match(payload: Dictionary) -> void:
	if _match_ended:
		return
	_match_ended = true
	_match_end_pending = {}
	_pending_requests.clear()
	_pending_timestamps.clear()
	_telemetry.emit_match_ended(payload)
	_publish_status("match_ended", true, [], _status_snapshot.get("details", {}), payload)
	print("YomiLLMBridge match ended: %s" % payload)


func _build_match_ended_payload(winner_override) -> Dictionary:
	var payload = {
		"match_id": _match_id,
		"winner": _normalize_winner(winner_override),
		"end_reason": _derive_end_reason(),
		"total_turns": _turn_id,
		"end_tick": _derive_end_tick(),
		"end_frame": _derive_end_frame(),
		"errors": [],
	}
	var replay_path = _find_replay_path()
	if replay_path != null:
		payload["replay_path"] = replay_path
	return payload


func _normalize_winner(winner_override):
	if winner_override == 1 or str(winner_override).to_lower() == "p1":
		return "p1"
	if winner_override == 2 or str(winner_override).to_lower() == "p2":
		return "p2"
	if _game == null:
		return null
	var p1_hp = int(_game.p1.hp)
	var p2_hp = int(_game.p2.hp)
	if p1_hp > p2_hp:
		return "p1"
	if p2_hp > p1_hp:
		return "p2"
	return null


func _derive_end_reason() -> String:
	if _game == null:
		return "resolved"
	if int(_game.current_tick) > int(_game.time):
		return "timeout"
	if int(_game.p1.hp) <= 0 or int(_game.p2.hp) <= 0:
		return "ko"
	return "resolved"


func _derive_end_tick() -> int:
	if _game == null:
		return 0
	if _has_property(_game, "game_end_tick"):
		return int(_game.game_end_tick)
	return int(_game.current_tick)


func _derive_end_frame() -> int:
	if _game == null:
		return 0
	return int(_game.current_tick)


func _find_replay_path():
	var after = _snapshot_replay_directory("user://replay/autosave")
	var best_path = ""
	var best_time = -1
	for path in after:
		var modified_at = int(after[path])
		if not str(path).ends_with(".replay"):
			continue
		if modified_at <= int(_replay_snapshot.get(path, -1)):
			continue
		if modified_at > best_time:
			best_time = modified_at
			best_path = str(path)
	if best_path != "":
		return ProjectSettings.globalize_path(best_path)

	for path in after:
		var modified_at = int(after[path])
		if not str(path).ends_with(".replay"):
			continue
		if modified_at > best_time:
			best_time = modified_at
			best_path = str(path)
	if best_path == "":
		return null
	return ProjectSettings.globalize_path(best_path)


func _snapshot_replay_directory(path: String) -> Dictionary:
	var snapshot = {}
	var directory = Directory.new()
	if directory.open(path) != OK:
		return snapshot
	directory.list_dir_begin(true, true)
	while true:
		var file_name = directory.get_next()
		if file_name == "":
			break
		if directory.current_is_dir():
			continue
		if not file_name.ends_with(".replay"):
			continue
		var file_path = path.plus_file(file_name)
		snapshot[file_path] = directory.get_modified_time(file_path)
	directory.list_dir_end()
	return snapshot


func _has_property(target, property_name: String) -> bool:
	if target == null or not target.has_method("get_property_list"):
		return false
	for property_data in target.get_property_list():
		if str(property_data.get("name", "")) == property_name:
			return true
	return false


func _get_script_dir() -> String:
	return get_script().resource_path.get_base_dir()
