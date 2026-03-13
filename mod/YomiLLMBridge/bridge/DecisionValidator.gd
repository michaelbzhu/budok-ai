extends RefCounted

# Validates incoming action decisions against the pending request and legal action set.
# Mirrors daemon-side validation in daemon/src/yomi_daemon/validation.py.
# Returns "" on success or a fallback reason string on failure.

const DI_MIN := -100
const DI_MAX := 100
const CURRENT_PROTOCOL_VERSION := "v2"
const SCHEMA_DESCRIPTOR_KEYS := [
	"additionalProperties",
	"choices",
	"const",
	"default",
	"enum",
	"items",
	"maximum",
	"maxItems",
	"max_items",
	"maxLength",
	"minimum",
	"minItems",
	"min_items",
	"minLength",
	"properties",
	"required",
	"semantic_hint",
	"type",
	"ui_kind",
]


func validate_decision(decision: Dictionary, request: Dictionary) -> String:
	# Envelope shape: must be action_decision type
	var envelope_error = _validate_envelope_shape(decision)
	if envelope_error != "":
		return envelope_error

	var payload = decision["payload"]

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
	if not extra.has("feint") or not extra.has("reverse") or not extra.has("prediction"):
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
	# Live v2 traffic must always use the canonical envelope contract.
	if str(decision.get("type", "")) != "action_decision":
		return "malformed_output"
	if str(decision.get("version", "")) != CURRENT_PROTOCOL_VERSION:
		return "malformed_output"
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
	var prediction = extra.get("prediction")
	if prediction != null and not bool(supports.get("prediction", false)):
		return "illegal_output"
	if prediction != null:
		if not (prediction is Dictionary):
			return "malformed_output"
		var prediction_spec = legal_action.get("prediction_spec", _default_prediction_spec())
		if not (prediction_spec is Dictionary):
			prediction_spec = _default_prediction_spec()
		var prediction_error = _validate_payload_value(
			prediction,
			prediction_spec,
			"extra.prediction",
			false
		)
		if prediction_error != "":
			return prediction_error

	return ""


func _validate_payload_data(data, legal_action: Dictionary) -> String:
	var payload_spec = legal_action.get("payload_spec", {})
	if not (payload_spec is Dictionary) or payload_spec.empty():
		# No payload expected but data was provided
		if data is Dictionary and not data.empty():
			return "illegal_output"
		return ""

	if data == null:
		return "illegal_output"

	if not (data is Dictionary):
		return "illegal_output"

	return _validate_payload_value(data, payload_spec, "data", _looks_like_field_map(payload_spec))


func _looks_like_field_map(spec: Dictionary) -> bool:
	for key in SCHEMA_DESCRIPTOR_KEYS:
		if spec.has(key):
			return false
	return true


func _validate_payload_value(value, spec: Dictionary, context: String, field_map_mode: bool) -> String:
	if field_map_mode:
		if not (value is Dictionary):
			return "illegal_output"
		for key in value.keys():
			if not spec.has(key):
				return "illegal_output"
		for key in value.keys():
			var descriptor = spec.get(key)
			if descriptor is Dictionary:
				var nested_error = _validate_payload_value(
					value[key],
					descriptor,
					context + "." + str(key),
					_looks_like_field_map(descriptor)
				)
				if nested_error != "":
					return nested_error
		for key in spec.keys():
			var required_descriptor = spec.get(key)
			if required_descriptor is Dictionary and bool(required_descriptor.get("required", false)) and not value.has(key):
				return "illegal_output"
		return ""

	var type_error = _validate_descriptor_type(value, spec)
	if type_error != "":
		return type_error

	if spec.has("const") and value != spec["const"]:
		return "illegal_output"

	var enum_values = spec.get("enum", spec.get("choices"))
	if enum_values is Array and enum_values.find(value) == -1:
		return "illegal_output"

	if value is Dictionary:
		var properties = spec.get("properties", {})
		if properties is Dictionary:
			var additional_properties = spec.get("additionalProperties", true)
			for key in value.keys():
				if properties.has(key):
					var descriptor = properties.get(key)
					if descriptor is Dictionary:
						var property_error = _validate_payload_value(
							value[key],
							descriptor,
							context + "." + str(key),
							_looks_like_field_map(descriptor)
						)
						if property_error != "":
							return property_error
					continue
				if additional_properties == false:
					return "illegal_output"
			var required = spec.get("required", [])
			if required is Array:
				for required_key in required:
					if required_key is String and not value.has(required_key):
						return "illegal_output"

	if value is Array:
		var items_spec = spec.get("items", null)
		if items_spec is Dictionary:
			for item in value:
				var item_error = _validate_payload_value(
					item,
					items_spec,
					context + "[]",
					_looks_like_field_map(items_spec)
				)
				if item_error != "":
					return item_error
		var min_items = _optional_int(spec.get("minItems", spec.get("min_items", null)))
		if min_items != null and value.size() < min_items:
			return "illegal_output"
		var max_items = _optional_int(spec.get("maxItems", spec.get("max_items", null)))
		if max_items != null and value.size() > max_items:
			return "illegal_output"

	if _is_number(value):
		var minimum = _optional_number(spec.get("minimum", null))
		if minimum != null and float(value) < minimum:
			return "illegal_output"
		var maximum = _optional_number(spec.get("maximum", null))
		if maximum != null and float(value) > maximum:
			return "illegal_output"

	if value is String:
		var min_length = _optional_int(spec.get("minLength", null))
		if min_length != null and value.length() < min_length:
			return "illegal_output"
		var max_length = _optional_int(spec.get("maxLength", null))
		if max_length != null and value.length() > max_length:
			return "illegal_output"

	return ""


func _validate_descriptor_type(value, spec: Dictionary) -> String:
	var normalized_type = str(spec.get("type", "")).to_lower()
	if normalized_type == "":
		return ""
	if normalized_type == "string":
		return "" if value is String else "illegal_output"
	if normalized_type == "integer":
		return "" if (typeof(value) == TYPE_INT) else "illegal_output"
	if normalized_type == "number":
		return "" if _is_number(value) else "illegal_output"
	if normalized_type == "boolean":
		return "" if typeof(value) == TYPE_BOOL else "illegal_output"
	if normalized_type == "object":
		return "" if value is Dictionary else "illegal_output"
	if normalized_type == "array":
		return "" if value is Array else "illegal_output"
	return "" if value is String else "illegal_output"


func _optional_int(value):
	return value if typeof(value) == TYPE_INT else null


func _optional_number(value):
	if _is_number(value):
		return float(value)
	return null


func _is_number(value) -> bool:
	return typeof(value) == TYPE_INT or typeof(value) == TYPE_REAL


func _default_prediction_spec() -> Dictionary:
	return {
		"type": "object",
		"additionalProperties": false,
		"required": ["horizon"],
		"properties": {
			"horizon": {
				"type": "integer",
				"minimum": 1,
				"maximum": 3,
				"default": 1,
				"semantic_hint": "prediction_horizon_turns",
			},
			"opponent_action": {
				"type": "string",
				"default": "",
				"semantic_hint": "predicted_opponent_action",
			},
			"confidence": {
				"type": "string",
				"enum": ["low", "medium", "high"],
				"default": "medium",
				"semantic_hint": "prediction_confidence",
			},
		},
	}
