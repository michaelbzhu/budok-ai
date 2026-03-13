extends RefCounted

# Emits auditable lifecycle events around request, apply, fallback, and match end.
# Events are sent to the daemon via the bridge client as protocol event envelopes.

var _bridge_client = null
var _config = {}
var _match_id = ""
var _protocol_codec = null


func configure(bridge_client, config: Dictionary, match_id: String) -> void:
	_bridge_client = bridge_client
	_config = config
	_match_id = match_id
	_protocol_codec = load(get_script().resource_path.get_base_dir() + "/ProtocolCodec.gd").new()


func emit_turn_requested(
	turn_id: int,
	player_id: String,
	state_hash: String,
	legal_actions_hash: String,
	request_time_ms: int
) -> void:
	_emit_event("TurnRequested", {
		"turn_id": turn_id,
		"player_id": player_id,
		"details": {
			"state_hash": state_hash,
			"legal_actions_hash": legal_actions_hash,
		},
	})


func emit_decision_received(
	turn_id: int,
	player_id: String,
	action: String,
	latency_ms: int,
	policy_id: String
) -> void:
	_emit_event("DecisionReceived", {
		"turn_id": turn_id,
		"player_id": player_id,
		"latency_ms": latency_ms,
		"details": {
			"action": action,
			"policy_id": policy_id,
		},
	})


func emit_decision_applied(
	turn_id: int,
	player_id: String,
	action: String,
	latency_ms: int
) -> void:
	_emit_event("DecisionApplied", {
		"turn_id": turn_id,
		"player_id": player_id,
		"latency_ms": latency_ms,
		"details": {
			"action": action,
		},
	})


func emit_decision_fallback(
	turn_id: int,
	player_id: String,
	fallback_reason: String,
	fallback_action: String,
	strategy: String,
	latency_ms: int
) -> void:
	_emit_event("DecisionFallback", {
		"turn_id": turn_id,
		"player_id": player_id,
		"fallback_reason": fallback_reason,
		"latency_ms": latency_ms,
		"details": {
			"action": fallback_action,
			"strategy": strategy,
			"fallback_reason": fallback_reason,
		},
	})


func emit_match_ended(
	payload: Dictionary
) -> void:
	var details = {
		"winner": payload.get("winner", null),
		"end_reason": str(payload.get("end_reason", "")),
		"total_turns": int(payload.get("total_turns", 0)),
		"end_tick": int(payload.get("end_tick", 0)),
		"end_frame": int(payload.get("end_frame", 0)),
	}
	if payload.has("replay_path"):
		details["replay_path"] = payload.get("replay_path", null)
	_emit_event("MatchEnded", {
		"details": details,
	})
	_emit_envelope("match_ended", payload)


func emit_error(
	error_message: String,
	turn_id: int = -1,
	player_id: String = ""
) -> void:
	var overrides = {
		"details": {"error": error_message},
	}
	if turn_id >= 0:
		overrides["turn_id"] = turn_id
	if player_id != "":
		overrides["player_id"] = player_id
	_emit_event("Error", overrides)


func _emit_event(event_name: String, overrides: Dictionary) -> void:
	if not _is_events_enabled():
		return

	var payload = {
		"match_id": _match_id,
		"event": event_name,
	}

	# Merge overrides into payload
	for key in overrides:
		payload[key] = overrides[key]

	_emit_envelope("event", payload)


func _emit_envelope(message_type: String, payload: Dictionary) -> void:
	var envelope = _protocol_codec.build_envelope(message_type, payload)

	if _bridge_client != null:
		_bridge_client._send_json_message(envelope)


func _is_events_enabled() -> bool:
	var logging = _config.get("logging", {})
	if not (logging is Dictionary):
		return true
	return bool(logging.get("events", true))
