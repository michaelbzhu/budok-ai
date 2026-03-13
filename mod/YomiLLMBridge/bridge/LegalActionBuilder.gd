extends Reference

# Reads the action-button UI state to enumerate all legal actions for a fighter.


func build_legal_actions(game, fighter, action_buttons) -> Array:
	var result = []

	# Refresh button visibility from current game state
	action_buttons.update_buttons(false)

	for button in action_buttons.buttons:
		if button == null:
			continue
		if not button.visible:
			continue
		if button.disabled:
			continue

		var action_entry = {
			"action": str(button.action_name),
			"label": _get_button_label(button),
			"payload_spec": _build_payload_spec(button),
			"supports": {
				"di": true,
				"feint": _supports_feint(button, fighter),
				"reverse": _supports_reverse(button),
				"prediction": _supports_prediction(button),
			},
		}
		var prediction_spec = _build_prediction_spec(button)
		if prediction_spec != null:
			action_entry["prediction_spec"] = prediction_spec
		result.append(action_entry)

	return result


func _get_button_label(button) -> String:
	if button.state != null and button.state.has("title"):
		return str(button.state.title)
	return str(button.action_name)


func _supports_feint(button, fighter) -> bool:
	if button.state == null:
		return false
	if not button.state.has_method("can_feint"):
		return false
	if not button.state.can_feint():
		return false
	if fighter.has("can_feint") and not fighter.can_feint:
		return false
	return true


func _supports_reverse(button) -> bool:
	if button.has_method("is_reversible"):
		return bool(button.is_reversible())
	if "reversible" in button:
		return bool(button.reversible)
	return false


func _build_payload_spec(button) -> Dictionary:
	var data_node = _read_property(button, "data_node", null)
	if data_node == null:
		return {}

	var properties = {}
	var required = []
	for child in _data_children(data_node):
		var field_name = str(_read_property(child, "name", ""))
		if field_name == "":
			continue
		var descriptor = _build_field_descriptor(child)
		if descriptor.empty():
			continue
		properties[field_name] = descriptor
		if bool(_read_property(child, "required", true)):
			required.append(field_name)

	if properties.empty():
		return {}

	var payload_spec = {
		"type": "object",
		"additionalProperties": false,
		"properties": properties,
	}
	if not required.empty():
		payload_spec["required"] = required
	return payload_spec


func _supports_prediction(button) -> bool:
	var explicit_support = _read_property(button, "supports_prediction", null)
	if explicit_support != null:
		return bool(explicit_support)
	var state = _read_property(button, "state", null)
	if state != null:
		var state_support = _read_property(state, "supports_prediction", null)
		if state_support != null:
			return bool(state_support)
	return (
		_read_property(button, "prediction_spec", null) is Dictionary
		or _read_property(state, "prediction_spec", null) is Dictionary
	)


func _build_prediction_spec(button):
	var direct_spec = _read_property(button, "prediction_spec", null)
	if direct_spec is Dictionary:
		return direct_spec.duplicate(true)
	var state = _read_property(button, "state", null)
	var state_spec = _read_property(state, "prediction_spec", null)
	if state_spec is Dictionary:
		return state_spec.duplicate(true)
	if _supports_prediction(button):
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
	return null


func _data_children(data_node) -> Array:
	if data_node == null:
		return []
	if data_node is Dictionary:
		var children = data_node.get("children", [])
		return children if children is Array else []
	if data_node.has_method("get_children"):
		return data_node.get_children()
	return []


func _build_field_descriptor(child) -> Dictionary:
	var inline_schema = _read_property(child, "schema", null)
	if inline_schema is Dictionary:
		return inline_schema.duplicate(true)

	var kind = str(_read_property(child, "kind", _classify_data_child(child))).to_lower()
	var semantic_hint = _read_property(child, "semantic_hint", null)

	if kind == "slider":
		var slider_descriptor = {
			"type": "integer",
			"minimum": int(_read_property(child, "minimum", _read_property(child, "min", 0))),
			"maximum": int(_read_property(child, "maximum", _read_property(child, "max", 100))),
			"default": int(_read_property(child, "default", _read_property(child, "minimum", _read_property(child, "min", 0)))),
			"ui_kind": "slider",
		}
		if semantic_hint != null:
			slider_descriptor["semantic_hint"] = str(semantic_hint)
		return slider_descriptor

	if kind == "option" or kind == "enum":
		var choices = _coerce_array(_read_property(child, "choices", []))
		var option_descriptor = {
			"type": "string",
			"enum": choices,
			"default": str(_read_property(child, "default", choices[0] if not choices.empty() else "")),
			"ui_kind": "enum",
		}
		if semantic_hint != null:
			option_descriptor["semantic_hint"] = str(semantic_hint)
		return option_descriptor

	if kind == "check" or kind == "checkbox":
		var checkbox_descriptor = {
			"type": "boolean",
			"default": bool(_read_property(child, "default", false)),
			"ui_kind": "checkbox",
		}
		if semantic_hint != null:
			checkbox_descriptor["semantic_hint"] = str(semantic_hint)
		return checkbox_descriptor

	if kind == "8way" or kind == "direction" or kind == "direction8":
		var direction_choices = _coerce_array(
			_read_property(
				child,
				"choices",
				["neutral", "up", "down", "forward", "back", "up_forward", "up_back", "down_forward", "down_back"]
			)
		)
		var direction_descriptor = {
			"type": "direction",
			"enum": direction_choices,
			"default": str(_read_property(child, "default", direction_choices[0] if not direction_choices.empty() else "neutral")),
			"ui_kind": "direction8",
		}
		if semantic_hint != null:
			direction_descriptor["semantic_hint"] = str(semantic_hint)
		return direction_descriptor

	if kind == "xy_plot" or kind == "xy" or kind == "vector2":
		var x_bounds = _coerce_dictionary(_read_property(child, "x", {}))
		var y_bounds = _coerce_dictionary(_read_property(child, "y", {}))
		var xy_descriptor = {
			"type": "object",
			"additionalProperties": false,
			"required": ["x", "y"],
			"ui_kind": "xy",
			"properties": {
				"x": {
					"type": "number",
					"minimum": float(x_bounds.get("minimum", x_bounds.get("min", 0.0))),
					"maximum": float(x_bounds.get("maximum", x_bounds.get("max", 0.0))),
					"default": float(x_bounds.get("default", x_bounds.get("minimum", x_bounds.get("min", 0.0)))),
				},
				"y": {
					"type": "number",
					"minimum": float(y_bounds.get("minimum", y_bounds.get("min", 0.0))),
					"maximum": float(y_bounds.get("maximum", y_bounds.get("max", 0.0))),
					"default": float(y_bounds.get("default", y_bounds.get("minimum", y_bounds.get("min", 0.0)))),
				},
			},
		}
		if semantic_hint != null:
			xy_descriptor["semantic_hint"] = str(semantic_hint)
		return xy_descriptor

	return {}


func _classify_data_child(child) -> String:
	var class_name_lower = str(child.get_class()).to_lower() if child != null and child.has_method("get_class") else ""
	var child_name = str(_read_property(child, "name", "")).to_lower()
	if class_name_lower.find("slider") != -1:
		return "slider"
	if class_name_lower.find("option") != -1:
		return "option"
	if class_name_lower.find("check") != -1:
		return "check"
	if child_name.find("8way") != -1:
		return "8way"
	if child_name.find("xy") != -1 or child_name.find("plot") != -1:
		return "xy_plot"
	return "unknown"


func _read_property(subject, property_name: String, default_value):
	if subject == null:
		return default_value
	if subject is Dictionary:
		return subject.get(property_name, default_value)
	if subject is Object:
		for property_info in subject.get_property_list():
			if str(property_info.get("name", "")) == property_name:
				return subject.get(property_name)
	return default_value


func _coerce_array(value) -> Array:
	return value if value is Array else []


func _coerce_dictionary(value) -> Dictionary:
	return value if value is Dictionary else {}
