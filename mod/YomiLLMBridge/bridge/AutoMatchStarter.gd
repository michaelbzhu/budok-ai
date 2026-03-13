extends Reference

# Programmatically starts a singleplayer match after daemon handshake,
# bypassing manual menu navigation.  Constructs the match_data dictionary
# expected by main._on_match_ready() and emits the CharacterSelect.match_ready
# signal so the game follows its normal setup path.

const BUILTIN_CHARACTERS := ["Ninja", "Cowboy", "Wizard", "Robot", "Mutant"]
const DEFAULT_STAGE_WIDTH := 1100

var _started := false


func start_match(hello_ack: Dictionary) -> void:
	if _started:
		printerr("AutoMatchStarter: match already started, ignoring duplicate call")
		return
	_started = true

	var config = _extract_config(hello_ack)
	var characters = _resolve_characters(config)
	if characters.empty():
		printerr("AutoMatchStarter: failed to resolve characters from config")
		return

	var match_data = _build_match_data(characters, config)

	# Emit match_ready on CharacterSelect, which main.gd listens to.
	# This triggers main._on_match_ready() -> setup_game() -> game.start_game()
	# and sets Global.current_game, which TurnHook polls for.
	var root = Engine.get_main_loop().root if Engine.get_main_loop() != null else null
	if root == null:
		printerr("AutoMatchStarter: no scene tree root available")
		return

	var css = _find_character_select(root)
	if css != null:
		print("AutoMatchStarter: emitting match_ready via CharacterSelect — %s vs %s" % [
			characters["p1"], characters["p2"]
		])
		css.emit_signal("match_ready", match_data)
		return

	# Fallback: call main._on_match_ready directly
	var main_node = root.get_node_or_null("Main")
	if main_node != null and main_node.has_method("_on_match_ready"):
		print("AutoMatchStarter: calling main._on_match_ready directly — %s vs %s" % [
			characters["p1"], characters["p2"]
		])
		main_node._on_match_ready(match_data)
		return

	# Last resort: call setup_game directly
	if main_node != null and main_node.has_method("setup_game"):
		print("AutoMatchStarter: calling main.setup_game directly — %s vs %s" % [
			characters["p1"], characters["p2"]
		])
		main_node.hide_main_menu(true)
		main_node.setup_game(true, match_data)
		return

	printerr("AutoMatchStarter: could not find CharacterSelect or Main node to start match")


func _extract_config(hello_ack: Dictionary) -> Dictionary:
	var payload = hello_ack.get("payload", {})
	if not (payload is Dictionary):
		return {}
	var config = payload.get("config", {})
	if not (config is Dictionary):
		return {}
	return config


func _resolve_characters(config: Dictionary) -> Dictionary:
	var char_selection = config.get("character_selection", {})
	if not (char_selection is Dictionary):
		char_selection = {}

	var mode = str(char_selection.get("mode", "mirror"))
	var available = _get_available_characters()

	if available.empty():
		printerr("AutoMatchStarter: no characters available in Global.name_paths")
		return {}

	match mode:
		"assigned":
			return _resolve_assigned(char_selection, available)
		"random_from_pool":
			return _resolve_random_from_pool(char_selection, available)
		"mirror", _:
			return _resolve_mirror(available)


func _resolve_assigned(char_selection: Dictionary, available: Array) -> Dictionary:
	var assignments = char_selection.get("assignments", {})
	if not (assignments is Dictionary):
		printerr("AutoMatchStarter: mode=assigned but no assignments provided")
		return {}

	var p1_name = str(assignments.get("p1", ""))
	var p2_name = str(assignments.get("p2", ""))

	if p1_name == "" or p2_name == "":
		printerr("AutoMatchStarter: mode=assigned but assignments incomplete: p1=%s p2=%s" % [
			p1_name, p2_name
		])
		return {}

	if not (p1_name in available):
		printerr("AutoMatchStarter: assigned p1 character '%s' not in available: %s" % [
			p1_name, available
		])
		return {}

	if not (p2_name in available):
		printerr("AutoMatchStarter: assigned p2 character '%s' not in available: %s" % [
			p2_name, available
		])
		return {}

	return {"p1": p1_name, "p2": p2_name}


func _resolve_mirror(available: Array) -> Dictionary:
	randomize()
	var idx = randi() % available.size()
	var name = available[idx]
	print("AutoMatchStarter: mirror mode selected '%s'" % name)
	return {"p1": name, "p2": name}


func _resolve_random_from_pool(char_selection: Dictionary, available: Array) -> Dictionary:
	var pool = char_selection.get("pool", [])
	if not (pool is Array) or pool.empty():
		printerr("AutoMatchStarter: mode=random_from_pool but pool is empty")
		return {}

	# Filter pool to only characters that are actually available
	var valid_pool = []
	for entry in pool:
		var name = str(entry)
		if name in available:
			valid_pool.append(name)

	if valid_pool.empty():
		printerr("AutoMatchStarter: no pool characters available. pool=%s available=%s" % [
			pool, available
		])
		return {}

	randomize()
	var p1_idx = randi() % valid_pool.size()
	var p2_idx = randi() % valid_pool.size()
	return {"p1": valid_pool[p1_idx], "p2": valid_pool[p2_idx]}


func _get_available_characters() -> Array:
	# Read from Global.name_paths at runtime (includes mod characters)
	if "name_paths" in Global and Global.name_paths is Dictionary:
		return Global.name_paths.keys()
	# Fallback to built-in list
	return BUILTIN_CHARACTERS.duplicate()


func _build_match_data(characters: Dictionary, config: Dictionary) -> Dictionary:
	randomize()
	var data = {
		"singleplayer": true,
		"selected_characters": {
			1: {"name": characters["p1"]},
			2: {"name": characters["p2"]},
		},
		"selected_styles": {1: null, 2: null},
		"seed": randi(),
		"stage_width": DEFAULT_STAGE_WIDTH,
		"p2_dummy": false,
	}
	return data


func _find_character_select(root: Node) -> Node:
	# CharacterSelect is at /root/Main/UILayer/.../CharacterSelect
	# Try the unique name path first (Godot % syntax)
	var main = root.get_node_or_null("Main")
	if main == null:
		return null

	# Walk the UILayer subtree looking for a node with the match_ready signal
	var ui_layer = main.get_node_or_null("UILayer")
	if ui_layer == null:
		return null

	return _find_node_with_signal(ui_layer, "match_ready")


func _find_node_with_signal(node: Node, signal_name: String) -> Node:
	if node.has_signal(signal_name):
		return node
	for child in node.get_children():
		var found = _find_node_with_signal(child, signal_name)
		if found != null:
			return found
	return null
