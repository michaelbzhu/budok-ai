extends SceneTree

# End-to-end live match smoke test.
#
# Requires a running daemon on 127.0.0.1:8765 (default).
# Loads BridgeClient directly (no Global needed), connects to the daemon,
# sends simulated decision requests for a multi-turn match including a
# parameterized action, receives action decisions, and emits match_ended.
#
# Usage:
#   godot3 --no-window --path mod --script res://BridgeLiveMatchSmoke.gd
#
# Output markers:
#   LIVE_MATCH_SMOKE_OK   — all turns and match end succeeded
#   LIVE_MATCH_SMOKE_ERR  — a failure occurred

const MAX_CONNECT_FRAMES = 300
const MAX_RESPONSE_FRAMES = 200
const TOTAL_TURNS = 4


var _received_messages = []


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var BridgeClient = load("res://YomiLLMBridge/bridge/BridgeClient.gd")
	var ActionApplier = load("res://YomiLLMBridge/bridge/ActionApplier.gd")

	if BridgeClient == null or ActionApplier == null:
		printerr("LIVE_MATCH_SMOKE_ERR failed to load bridge scripts")
		quit(1)
		return

	var applier = ActionApplier.new()
	var client = BridgeClient.new()
	root.add_child(client)

	# Connect daemon_message signal to collect responses
	client.connect("daemon_message", self, "_on_daemon_message")

	var config = {
		"transport": {"host": "127.0.0.1", "port": 8765, "connect_on_ready": false},
		"protocol": {"schema_version": "v2", "supported_versions": ["v2"]},
		"game_version": "1.9.20-steam",
		"decision_timeout_ms": 5000,
		"fallback_mode": "safe_continue",
		"logging": {"events": true, "bridge_state": false, "raw_messages": false},
	}
	var metadata = {"version": "0.0.1", "name": "YomiLLMBridge", "author": "yomi-ai"}
	client.configure(config, metadata)
	client.connect_to_daemon()

	# Wait for handshake
	var connected = false
	for _i in range(MAX_CONNECT_FRAMES):
		yield(self, "idle_frame")
		var snapshot = client.get_connection_snapshot()
		if str(snapshot.get("state", "")) == "connected":
			connected = true
			break
		if str(snapshot.get("last_error", "")) != "":
			printerr("LIVE_MATCH_SMOKE_ERR handshake failed: %s" % str(snapshot.get("last_error")))
			quit(1)
			return

	if not connected:
		printerr("LIVE_MATCH_SMOKE_ERR handshake timeout")
		quit(1)
		return

	# Run multi-turn match
	var match_id = "smoke-%d" % OS.get_unix_time()
	var decisions = []
	var errors = []

	for turn_idx in range(1, TOTAL_TURNS + 1):
		var player_id = "p1" if turn_idx % 2 == 1 else "p2"

		var observation = _build_observation(turn_idx, player_id)
		var legal_actions = _build_legal_actions(turn_idx)

		var request_payload = {
			"match_id": match_id,
			"turn_id": turn_idx,
			"player_id": player_id,
			"deadline_ms": 5000,
			"state_hash": "state-%d" % turn_idx,
			"legal_actions_hash": "legal-%d" % turn_idx,
			"decision_type": "turn_action",
			"observation": observation,
			"legal_actions": legal_actions,
		}
		var request_envelope = _build_envelope("decision_request", request_payload)
		client._send_json_message(request_envelope)

		# Wait for action_decision response via signal
		_received_messages.clear()
		var got_response = false
		for _w in range(MAX_RESPONSE_FRAMES):
			yield(self, "idle_frame")
			for msg in _received_messages:
				if str(msg.get("type", "")) == "action_decision":
					var payload = msg.get("payload", {})
					if payload is Dictionary:
						decisions.append({
							"turn": turn_idx,
							"player": player_id,
							"action": str(payload.get("action", "")),
						})
						# Apply the decision to verify ActionApplier works
						var mock_fighter = _MockFighter.new(player_id)
						applier.apply_decision(payload, mock_fighter)
						got_response = true
			if got_response:
				break

		if not got_response:
			errors.append("turn %d: no response from daemon" % turn_idx)

	# Send match_ended
	var match_ended_payload = {
		"match_id": match_id,
		"winner": "p1",
		"end_reason": "ko",
		"total_turns": TOTAL_TURNS,
		"end_tick": 500,
		"end_frame": 60,
		"errors": [],
	}
	var ended_envelope = _build_envelope("match_ended", match_ended_payload)
	client._send_json_message(ended_envelope)

	# Brief settle for daemon to finalize artifacts
	for _s in range(10):
		yield(self, "idle_frame")

	# Report results
	if errors.size() > 0:
		printerr("LIVE_MATCH_SMOKE_ERR %s" % to_json({"errors": errors, "decisions": decisions}))
		quit(1)
		return

	if decisions.size() != TOTAL_TURNS:
		printerr("LIVE_MATCH_SMOKE_ERR expected %d decisions, got %d" % [TOTAL_TURNS, decisions.size()])
		quit(1)
		return

	print("LIVE_MATCH_SMOKE_OK %s" % to_json({
		"match_id": match_id,
		"turns": TOTAL_TURNS,
		"decisions": decisions,
	}))
	quit(0)


func _on_daemon_message(envelope: Dictionary) -> void:
	_received_messages.append(envelope.duplicate(true))


# Build envelopes without ProtocolCodec to avoid RefCounted compatibility issues
func _build_envelope(message_type: String, payload: Dictionary) -> Dictionary:
	return {
		"type": message_type,
		"version": "v2",
		"ts": _utc_now(),
		"payload": payload,
	}


func _utc_now() -> String:
	var dt = OS.get_datetime_from_unix_time(OS.get_unix_time())
	return "%04d-%02d-%02dT%02d:%02d:%02dZ" % [
		dt["year"], dt["month"], dt["day"],
		dt["hour"], dt["minute"], dt["second"],
	]


class _MockFighter:
	extends Reference
	var player_id = ""
	var queued_action = ""
	var queued_data = null
	var queued_extra = null

	func _init(pid = "p1"):
		player_id = pid

	func on_action_selected(action, data, extra):
		queued_action = action
		queued_data = data
		queued_extra = extra


func _build_observation(turn_idx: int, active_player: String) -> Dictionary:
	return {
		"tick": 100 + turn_idx,
		"frame": 12,
		"active_player": active_player,
		"fighters": [
			{
				"id": "p1", "character": "Cowboy",
				"hp": 1000, "max_hp": 1000, "meter": 1, "burst": 1,
				"position": {"x": -5.0, "y": 0.0},
				"velocity": {"x": 0.0, "y": 0.0},
				"facing": "right", "current_state": "neutral",
				"combo_count": 0, "blockstun": 0, "hitlag": 0,
				"state_interruptable": true, "can_feint": true, "grounded": true,
			},
			{
				"id": "p2", "character": "Ninja",
				"hp": 1000, "max_hp": 1000, "meter": 1, "burst": 1,
				"position": {"x": 5.0, "y": 0.0},
				"velocity": {"x": 0.0, "y": 0.0},
				"facing": "left", "current_state": "neutral",
				"combo_count": 0, "blockstun": 0, "hitlag": 0,
				"state_interruptable": true, "can_feint": false, "grounded": true,
			},
		],
		"objects": [],
		"stage": {"id": "training_room"},
		"history": [],
	}


func _build_legal_actions(turn_idx: int) -> Array:
	var actions = [
		{
			"action": "block", "label": "Block",
			"payload_spec": {},
			"supports": {"di": false, "feint": false, "reverse": false, "prediction": false},
		},
		{
			"action": "attack_a", "label": "Attack A",
			"payload_spec": {},
			"supports": {"di": true, "feint": false, "reverse": false, "prediction": false},
			"damage": 100.0, "startup_frames": 5,
		},
		{
			"action": "move_forward", "label": "Move Forward",
			"payload_spec": {},
			"supports": {"di": false, "feint": false, "reverse": false, "prediction": false},
		},
	]

	# Turn 3 adds a parameterized action (cowboy directional shot)
	if turn_idx == 3:
		actions.append({
			"action": "directional_shot", "label": "Directional Shot",
			"payload_spec": {
				"type": "object",
				"additionalProperties": false,
				"properties": {
					"angle": {
						"type": "integer",
						"minimum": 0, "maximum": 360,
						"default": 90,
						"semantic": "Direction angle in degrees",
					},
				},
			},
			"supports": {"di": true, "feint": false, "reverse": false, "prediction": false},
			"damage": 80.0, "startup_frames": 8,
		})

	return actions
