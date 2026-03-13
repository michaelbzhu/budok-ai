extends SceneTree

const ACTION_APPLIER_PATH = "res://YomiLLMBridge/bridge/ActionApplier.gd"


class MockCoordinator:
	extends Reference

	var ready_players = {}
	var advance_count = 0

	func mark_ready(player_id: String) -> void:
		ready_players[player_id] = true
		if ready_players.size() >= 2:
			advance_count += 1


class MockFighter:
	extends Reference

	var player_id = ""
	var coordinator = null
	var queued_action = ""
	var queued_data = null
	var queued_extra = null
	var ready_state = false
	var commit_count = 0
	var apply_log = []

	func _init(fighter_player_id: String, ready_coordinator = null) -> void:
		player_id = fighter_player_id
		coordinator = ready_coordinator

	func on_action_selected(action: String, data, extra) -> void:
		queued_action = action
		queued_data = _duplicate_value(data)
		queued_extra = _duplicate_value(extra)
		ready_state = true
		commit_count += 1
		apply_log.append("native_method")
		if coordinator != null:
			coordinator.mark_ready(player_id)

	func snapshot() -> Dictionary:
		return {
			"queued_action": queued_action,
			"queued_data": _duplicate_value(queued_data),
			"queued_extra": _duplicate_value(queued_extra),
			"ready_state": ready_state,
			"commit_count": commit_count,
			"apply_log": apply_log.duplicate(true),
		}

	func _duplicate_value(value):
		if value is Dictionary or value is Array:
			return value.duplicate(true)
		return value


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var action_applier = load(ACTION_APPLIER_PATH).new()
	var comparison = _compare_native_and_bridge_apply(action_applier)
	if not bool(comparison.get("ok", false)):
		printerr("ACTION_APPLY_SMOKE_ERR %s" % to_json(comparison))
		quit(1)
		return

	var simultaneous = _verify_simultaneous_turn_commit(action_applier)
	if not bool(simultaneous.get("ok", false)):
		printerr("ACTION_APPLY_SMOKE_ERR %s" % to_json(simultaneous))
		quit(1)
		return

	print(
		"ACTION_APPLY_SMOKE_OK %s"
		% to_json({"comparison": comparison, "simultaneous": simultaneous})
	)
	quit(0)


func _compare_native_and_bridge_apply(action_applier) -> Dictionary:
	var payload = {
		"action": "Slash",
		"data": {"target": "enemy", "strength": 2},
		"extra": {
			"di": {"x": 12, "y": -7},
			"feint": true,
			"reverse": false,
			"prediction": {"horizon": 2, "style": "aggressive"},
		},
	}

	var native_fighter = MockFighter.new("p1")
	var bridge_fighter = MockFighter.new("p1")

	native_fighter.on_action_selected(
		payload["action"],
		payload["data"].duplicate(true),
		payload["extra"].duplicate(true)
	)
	var apply_result = action_applier.apply_decision(payload, bridge_fighter)

	var bridge_snapshot = bridge_fighter.snapshot()
	var native_snapshot = native_fighter.snapshot()
	var bridge_json = to_json(bridge_snapshot)
	var native_json = to_json(native_snapshot)

	if not bool(apply_result.get("applied", false)):
		return {"ok": false, "reason": "bridge_apply_failed", "apply_result": apply_result}
	if str(apply_result.get("apply_path", "")) != "native_method":
		return {"ok": false, "reason": "bridge_did_not_use_native_method", "apply_result": apply_result}
	if bridge_json != native_json:
		return {
			"ok": false,
			"reason": "bridge_and_native_snapshots_diverged",
			"bridge": bridge_snapshot,
			"native": native_snapshot,
			"bridge_json": bridge_json,
			"native_json": native_json,
		}

	return {
		"ok": true,
		"apply_path": apply_result.get("apply_path", ""),
		"snapshot": bridge_snapshot,
	}


func _verify_simultaneous_turn_commit(action_applier) -> Dictionary:
	var coordinator = MockCoordinator.new()
	var p1 = MockFighter.new("p1", coordinator)
	var p2 = MockFighter.new("p2", coordinator)

	var p1_result = action_applier.apply_decision(
		{
			"action": "Jab",
			"data": null,
			"extra": {
				"di": {"x": 0, "y": 0},
				"feint": false,
				"reverse": false,
				"prediction": null,
			},
		},
		p1
	)
	if coordinator.advance_count != 0:
		return {
			"ok": false,
			"reason": "match_advanced_before_both_players_ready",
			"advance_count": coordinator.advance_count,
		}

	var p2_result = action_applier.apply_decision(
		{
			"action": "Block",
			"data": {"height": "mid"},
			"extra": {
				"di": null,
				"feint": false,
				"reverse": false,
				"prediction": null,
			},
		},
		p2
	)

	if coordinator.advance_count != 1:
		return {
			"ok": false,
			"reason": "match_did_not_advance_after_both_players_ready",
			"advance_count": coordinator.advance_count,
			"p1": p1.snapshot(),
			"p2": p2.snapshot(),
		}

	if str(p1_result.get("apply_path", "")) != "native_method":
		return {"ok": false, "reason": "p1_apply_did_not_use_native_method", "result": p1_result}
	if str(p2_result.get("apply_path", "")) != "native_method":
		return {"ok": false, "reason": "p2_apply_did_not_use_native_method", "result": p2_result}

	return {
		"ok": true,
		"advance_count": coordinator.advance_count,
		"p1": p1.snapshot(),
		"p2": p2.snapshot(),
	}
