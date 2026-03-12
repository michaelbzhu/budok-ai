extends RefCounted

# Validates incoming action decisions against the pending request and legal action set.
# Mirrors daemon-side validation in daemon/src/yomi_daemon/validation.py.
# Returns "" on success or a fallback reason string on failure.

const DI_MIN := -100
const DI_MAX := 100


func validate_decision(decision: Dictionary, request: Dictionary) -> String:
	# Envelope shape: must be action_decision type
	var envelope_error = _validate_envelope_shape(decision)
	if envelope_error != "":
		return envelope_error

	var payload = decision.get("payload", decision)

	# Match ID check
	if str(payload.get("match_id", "")) != str(request.get("match_id", "")):
		return "stale_response"

	# Turn ID check
	if int(payload.get("turn_id", -1)) != int(request.get("turn_id", -1)):
		return "stale_response"

	# Action must be a non-empty string
	var action_name = str(payload.get("action", ""))
	if action_name == "":
		return "malformed_output"

	# Extra must be a dict with feint and reverse bools
	var extra = payload.get("extra")
	if extra == null or not (extra is Dictionary):
		return "malformed_output"
	if not extra.has("feint") or not extra.has("reverse"):
		return "malformed_output"

	# Find matching legal action
	var legal_actions = request.get("legal_actions", [])
	var matched_action = _find_legal_action(action_name, legal_actions)
	if matched_action == null:
		return "illegal_output"

	# Validate extras against supports
	var extras_error = _validate_extras(extra, matched_action)
	if extras_error != "":
		return extras_error

	# Validate payload data against payload_spec
	var data_error = _validate_payload_data(payload.get("data"), matched_action)
	if data_error != "":
		return data_error

	return ""


func is_replayable(decision_payload: Dictionary, request: Dictionary) -> bool:
	# Check if a prior decision can be replayed under the current legal set.
	# Skip match_id and turn_id checks.
	var action_name = str(decision_payload.get("action", ""))
	if action_name == "":
		return false

	var extra = decision_payload.get("extra")
	if extra == null or not (extra is Dictionary):
		return false

	var legal_actions = request.get("legal_actions", [])
	var matched_action = _find_legal_action(action_name, legal_actions)
	if matched_action == null:
		return false

	if _validate_extras(extra, matched_action) != "":
		return false

	if _validate_payload_data(decision_payload.get("data"), matched_action) != "":
		return false

	return true


func _validate_envelope_shape(decision: Dictionary) -> String:
	# Accept both enveloped and bare payload forms
	if decision.has("type"):
		var msg_type = str(decision.get("type", ""))
		if msg_type == "action_decision":
			if not decision.has("payload") or not (decision["payload"] is Dictionary):
				return "malformed_output"
	return ""


func _find_legal_action(action_name: String, legal_actions: Array):
	for legal_action in legal_actions:
		if not (legal_action is Dictionary):
			continue
		if str(legal_action.get("action", "")) == action_name:
			return legal_action
	return null


func _validate_extras(extra: Dictionary, legal_action: Dictionary) -> String:
	var supports = legal_action.get("supports", {})
	if not (supports is Dictionary):
		return "malformed_output"

	# DI validation
	var di = extra.get("di")
	if di != null:
		if not bool(supports.get("di", false)):
			return "illegal_output"
		if not (di is Dictionary):
			return "malformed_output"
		var di_x = di.get("x")
		var di_y = di.get("y")
		if di_x == null or di_y == null:
			return "malformed_output"
		if int(di_x) < DI_MIN or int(di_x) > DI_MAX:
			return "illegal_output"
		if int(di_y) < DI_MIN or int(di_y) > DI_MAX:
			return "illegal_output"

	# Feint validation
	if bool(extra.get("feint", false)) and not bool(supports.get("feint", false)):
		return "illegal_output"

	# Reverse validation
	if bool(extra.get("reverse", false)) and not bool(supports.get("reverse", false)):
		return "illegal_output"

	return ""


func _validate_payload_data(data, legal_action: Dictionary) -> String:
	if data == null:
		return ""

	var payload_spec = legal_action.get("payload_spec", {})
	if not (payload_spec is Dictionary) or payload_spec.empty():
		# No payload expected but data was provided
		if data is Dictionary and not data.empty():
			return "illegal_output"
		return ""

	if not (data is Dictionary):
		return "illegal_output"

	return ""
