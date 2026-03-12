extends RefCounted

# Chooses legal fallback actions for timeout, disconnect, or invalid responses.
# Mirrors daemon-side fallback logic in daemon/src/yomi_daemon/fallback.py.

var _validator = null
var _last_valid_decisions = {}  # player_id -> decision payload dict


func _init() -> void:
	_validator = load(get_script().resource_path.get_base_dir() + "/DecisionValidator.gd").new()


func record_valid_decision(player_id: String, decision_payload: Dictionary) -> void:
	_last_valid_decisions[player_id] = decision_payload.duplicate(true)


func choose_fallback(
	request: Dictionary,
	fallback_reason: String,
	fallback_mode: String
) -> Dictionary:
	var legal_actions = request.get("legal_actions", [])
	if legal_actions.empty():
		return _empty_fallback(request, fallback_reason)

	var player_id = str(request.get("player_id", ""))

	# Try last_valid_replayable first
	if fallback_mode == "last_valid_replayable":
		var last_decision = _last_valid_decisions.get(player_id)
		if last_decision != null and _validator.is_replayable(last_decision, request):
			return _build_fallback_from_prior(request, last_decision, fallback_reason)

	# Try heuristic_guard
	if fallback_mode == "heuristic_guard" or fallback_mode == "last_valid_replayable":
		var guard_action = _select_guard_action(legal_actions)
		if guard_action != null:
			return _build_fallback_from_action(
				request, guard_action, fallback_reason, "heuristic_guard"
			)

	# Always fall back to safe_continue
	var safe_action = _select_safe_action(legal_actions)
	return _build_fallback_from_action(
		request, safe_action, fallback_reason, "safe_continue"
	)


func _build_fallback_from_prior(
	request: Dictionary,
	prior_decision: Dictionary,
	fallback_reason: String
) -> Dictionary:
	return {
		"match_id": request.get("match_id", ""),
		"turn_id": request.get("turn_id", 0),
		"action": prior_decision.get("action", ""),
		"data": prior_decision.get("data"),
		"extra": prior_decision.get("extra", {"di": null, "feint": false, "reverse": false}),
		"policy_id": "fallback/last_valid_replayable",
		"notes": "Replayed the last request-compatible decision after upstream failure.",
		"fallback_reason": fallback_reason,
	}


func _build_fallback_from_action(
	request: Dictionary,
	action: Dictionary,
	fallback_reason: String,
	strategy: String
) -> Dictionary:
	var supports = action.get("supports", {})
	var di_value = null
	if bool(supports.get("di", false)):
		di_value = {"x": 0, "y": 0}

	return {
		"match_id": request.get("match_id", ""),
		"turn_id": request.get("turn_id", 0),
		"action": str(action.get("action", "")),
		"data": null,
		"extra": {
			"di": di_value,
			"feint": false,
			"reverse": false,
		},
		"policy_id": "fallback/" + strategy,
		"notes": "Selected fallback action after upstream failure.",
		"fallback_reason": fallback_reason,
	}


func _empty_fallback(request: Dictionary, fallback_reason: String) -> Dictionary:
	return {
		"match_id": request.get("match_id", ""),
		"turn_id": request.get("turn_id", 0),
		"action": "",
		"data": null,
		"extra": {"di": null, "feint": false, "reverse": false},
		"policy_id": "fallback/safe_continue",
		"notes": "No legal actions available for fallback.",
		"fallback_reason": fallback_reason,
	}


# --- Action scoring ---

const DEFENSIVE_KEYWORDS = [
	"block", "guard", "parry", "defend", "shield",
	"wait", "idle", "evade", "backdash",
]

const LOW_COMMITMENT_KEYWORDS = [
	"block", "guard", "wait", "backdash",
	"jab", "poke", "dash", "step", "move",
]

const HIGH_COMMITMENT_KEYWORDS = [
	"super", "burst", "charge", "launcher", "throw", "teleport",
]


func _select_guard_action(legal_actions: Array):
	var best_action = null
	var best_score = 0.0
	for i in range(legal_actions.size()):
		var action = legal_actions[i]
		var g_score = _guard_score(action)
		if g_score <= 0.0:
			continue
		var s_score = _safe_score(action)
		var tie_break = -i
		var combined = [g_score, s_score, tie_break]
		if best_action == null or _compare_scores(combined, best_score) > 0:
			best_action = action
			best_score = combined
	return best_action


func _select_safe_action(legal_actions: Array) -> Dictionary:
	var best_action = legal_actions[0]
	var best_score = [_safe_score(best_action), 0]
	for i in range(1, legal_actions.size()):
		var action = legal_actions[i]
		var s_score = _safe_score(action)
		var combined = [s_score, -i]
		if _compare_scores(combined, best_score) > 0:
			best_action = action
			best_score = combined
	return best_action


func _compare_scores(a, b) -> int:
	# Compare two score arrays element by element
	var len_a = a.size() if a is Array else 0
	var len_b = b.size() if b is Array else 0
	var max_len = max(len_a, len_b)
	for i in range(max_len):
		var va = a[i] if i < len_a else 0.0
		var vb = b[i] if i < len_b else 0.0
		if va > vb:
			return 1
		elif va < vb:
			return -1
	return 0


func _action_text(action: Dictionary) -> String:
	var parts = [str(action.get("action", ""))]
	var label = action.get("label")
	if label != null and str(label) != "":
		parts.append(str(label))
	var description = action.get("description")
	if description != null and str(description) != "":
		parts.append(str(description))
	return PoolStringArray(parts).join(" ").to_lower()


func _keyword_hits(text: String, keywords: Array) -> int:
	var hits = 0
	for keyword in keywords:
		if text.find(keyword) != -1:
			hits += 1
	return hits


func _guard_score(action: Dictionary) -> float:
	var text = _action_text(action)
	return float(_keyword_hits(text, DEFENSIVE_KEYWORDS) * 4) + _safe_score(action)


func _safe_score(action: Dictionary) -> float:
	var text = _action_text(action)
	var payload_spec = action.get("payload_spec", {})
	var supports = action.get("supports", {})

	var no_payload_bonus = 3.0 if (payload_spec is Dictionary and payload_spec.empty()) or payload_spec == null else 0.0
	var no_extra_bonus = 1.0 if not bool(supports.get("di", false)) else 0.0
	var defensive_bonus = float(_keyword_hits(text, DEFENSIVE_KEYWORDS) * 4)
	var low_commitment_bonus = float(_keyword_hits(text, LOW_COMMITMENT_KEYWORDS) * 2)
	var high_commitment_penalty = float(_keyword_hits(text, HIGH_COMMITMENT_KEYWORDS) * 3)

	var startup_frames = action.get("startup_frames")
	var startup_value = int(startup_frames) if startup_frames != null else 999
	var startup_penalty = min(startup_value, 30) / 10.0

	var meter_cost = action.get("meter_cost")
	var meter_penalty = float(int(meter_cost) * 2) if meter_cost != null else 0.0

	return (
		no_payload_bonus
		+ no_extra_bonus
		+ defensive_bonus
		+ low_commitment_bonus
		- high_commitment_penalty
		- startup_penalty
		- meter_penalty
	)
