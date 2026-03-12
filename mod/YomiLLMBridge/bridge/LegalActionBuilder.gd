extends RefCounted

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
			},
		}
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
	if button.data_node == null:
		return {}

	var spec = {}
	for child in button.data_node.get_children():
		if child == null:
			continue
		var child_type = _classify_data_child(child)
		spec[str(child.name)] = child_type
	return spec


func _classify_data_child(child) -> String:
	var class_name_lower = str(child.get_class()).to_lower()
	if "slider" in class_name_lower:
		return "slider"
	if "option" in class_name_lower:
		return "option"
	if "check" in class_name_lower:
		return "check"
	if "8way" in str(child.name).to_lower():
		return "8way"
	if "xy" in str(child.name).to_lower() or "plot" in str(child.name).to_lower():
		return "xy_plot"
	return "unknown"
