extends Node

const DEFAULT_HOST := "127.0.0.1"
const DEFAULT_PORT := 8765
const DEFAULT_SCHEMA_VERSION := "v2"
const DEFAULT_SUPPORTED_VERSIONS := ["v2"]
const DEFAULT_TIMEOUT_PROFILE := "strict_local"
const DEFAULT_DECISION_TIMEOUT_MS := 2500
const DEFAULT_FALLBACK_MODE := "safe_continue"
const DEFAULT_GAME_VERSION := "supported-build-16151810"

var bridge_client: Node = null
var turn_hook: Node = null
var options_ui: Control = null
var bridge_config = {}
var mod_metadata = {}


func _ready() -> void:
	mod_metadata = _load_json_document(_metadata_path())
	bridge_config = _normalize_config(_load_json_document(_config_path()))

	bridge_client = _instantiate_script(_script_base_dir() + "/bridge/BridgeClient.gd")
	if bridge_client == null:
		printerr("YomiLLMBridge failed to load BridgeClient.gd")
		return

	add_child(bridge_client)
	bridge_client.connect("connection_state_changed", self, "_on_bridge_state_changed")
	bridge_client.connect("handshake_completed", self, "_on_handshake_completed")
	bridge_client.connect("handshake_failed", self, "_on_handshake_failed")
	bridge_client.configure(bridge_config, _build_handshake_context())

	options_ui = _instantiate_script(_script_base_dir() + "/ui/ModOptions.gd")
	if options_ui != null:
		add_child(options_ui)
		options_ui.configure(bridge_config)

	print("YomiLLMBridge loaded with transport %s:%s" % [
		bridge_config["transport"]["host"],
		bridge_config["transport"]["port"],
	])

	if bool(bridge_config["transport"].get("connect_on_ready", true)):
		var error = bridge_client.connect_to_daemon()
		if error != OK:
			printerr("YomiLLMBridge failed to start bridge connection: %s" % error)


func get_bridge_status() -> Dictionary:
	var runtime_status = (
		turn_hook.get_status()
		if turn_hook != null and turn_hook.has_method("get_status")
		else {}
	)
	if bridge_client == null:
		return {
			"state": "missing_bridge_client",
			"config": bridge_config.duplicate(true),
			"runtime": runtime_status,
		}
	var snapshot = bridge_client.get_connection_snapshot()
	snapshot["runtime"] = runtime_status
	return snapshot


func get_bridge_config() -> Dictionary:
	return bridge_config.duplicate(true)


func _on_bridge_state_changed(state: String, details: Dictionary) -> void:
	if options_ui != null:
		options_ui.update_bridge_snapshot(get_bridge_status())
	if not bool(bridge_config.get("logging", {}).get("bridge_state", true)):
		return
	print("YomiLLMBridge bridge state=%s details=%s" % [state, details])


func _on_handshake_completed(hello_ack: Dictionary) -> void:
	print("YomiLLMBridge handshake complete: %s" % hello_ack.get("payload", {}))
	_attach_turn_hook()


func _on_handshake_failed(reason: String) -> void:
	printerr("YomiLLMBridge handshake failed: %s" % reason)


func _attach_turn_hook() -> void:
	if turn_hook != null:
		return
	var script = load(_script_base_dir() + "/bridge/TurnHook.gd")
	if script == null:
		printerr("YomiLLMBridge failed to load TurnHook.gd")
		return
	turn_hook = script.new()
	add_child(turn_hook)
	turn_hook.connect("status_changed", self, "_on_turn_hook_status_changed")
	turn_hook.attach(bridge_client, bridge_config)
	_on_turn_hook_status_changed(turn_hook.get_status())


func _on_turn_hook_status_changed(_snapshot: Dictionary) -> void:
	if options_ui != null:
		options_ui.update_bridge_snapshot(get_bridge_status())


func _build_handshake_context() -> Dictionary:
	var metadata = mod_metadata.duplicate(true)
	metadata["game_version"] = str(bridge_config.get("game_version", DEFAULT_GAME_VERSION))
	metadata["schema_version"] = str(
		bridge_config.get("protocol", {}).get("schema_version", DEFAULT_SCHEMA_VERSION)
	)
	metadata["supported_protocol_versions"] = _duplicate_string_array(
		bridge_config.get("protocol", {}).get("supported_versions", DEFAULT_SUPPORTED_VERSIONS)
	)
	return metadata


func _normalize_config(raw_config: Dictionary) -> Dictionary:
	var normalized = {
		"transport": {
			"host": DEFAULT_HOST,
			"port": DEFAULT_PORT,
			"connect_on_ready": true,
		},
		"protocol": {
			"schema_version": DEFAULT_SCHEMA_VERSION,
			"supported_versions": DEFAULT_SUPPORTED_VERSIONS.duplicate(),
		},
		"game_version": DEFAULT_GAME_VERSION,
		"timeout_profile": DEFAULT_TIMEOUT_PROFILE,
		"decision_timeout_ms": DEFAULT_DECISION_TIMEOUT_MS,
		"fallback_mode": DEFAULT_FALLBACK_MODE,
		"logging": {
			"events": true,
			"bridge_state": true,
			"raw_messages": false,
		},
	}

	if raw_config.empty():
		return normalized

	if raw_config.has("transport") and raw_config["transport"] is Dictionary:
		var transport = raw_config["transport"]
		normalized["transport"]["host"] = str(transport.get("host", DEFAULT_HOST))
		normalized["transport"]["port"] = int(transport.get("port", DEFAULT_PORT))
		normalized["transport"]["connect_on_ready"] = bool(
			transport.get("connect_on_ready", true)
		)

	if raw_config.has("protocol") and raw_config["protocol"] is Dictionary:
		var protocol = raw_config["protocol"]
		normalized["protocol"]["schema_version"] = str(
			protocol.get("schema_version", DEFAULT_SCHEMA_VERSION)
		)
		normalized["protocol"]["supported_versions"] = _duplicate_string_array(
			protocol.get("supported_versions", DEFAULT_SUPPORTED_VERSIONS)
		)

	normalized["game_version"] = str(raw_config.get("game_version", DEFAULT_GAME_VERSION))
	normalized["timeout_profile"] = str(
		raw_config.get("timeout_profile", DEFAULT_TIMEOUT_PROFILE)
	)
	normalized["decision_timeout_ms"] = int(
		raw_config.get("decision_timeout_ms", DEFAULT_DECISION_TIMEOUT_MS)
	)
	normalized["fallback_mode"] = str(raw_config.get("fallback_mode", DEFAULT_FALLBACK_MODE))

	if raw_config.has("logging") and raw_config["logging"] is Dictionary:
		var logging_config = raw_config["logging"]
		normalized["logging"]["events"] = bool(logging_config.get("events", true))
		normalized["logging"]["bridge_state"] = bool(
			logging_config.get("bridge_state", true)
		)
		normalized["logging"]["raw_messages"] = bool(
			logging_config.get("raw_messages", false)
		)

	return normalized


func _load_json_document(path: String) -> Dictionary:
	var file = File.new()
	if not file.file_exists(path):
		printerr("YomiLLMBridge missing JSON document: %s" % path)
		return {}

	var open_error = file.open(path, File.READ)
	if open_error != OK:
		printerr("YomiLLMBridge failed to open %s: %s" % [path, open_error])
		return {}

	var raw_text = file.get_as_text()
	file.close()

	var parse_result = JSON.parse(raw_text)
	if parse_result.error != OK:
		printerr(
			"YomiLLMBridge failed to parse %s at line %s: %s" % [
				path,
				parse_result.error_line,
				parse_result.error_string,
			]
		)
		return {}
	if not (parse_result.result is Dictionary):
		printerr("YomiLLMBridge expected %s to contain a JSON object" % path)
		return {}
	return parse_result.result


func _instantiate_script(path: String):
	var script = load(path)
	if script == null:
		return null
	return script.new()


func _duplicate_string_array(raw_value) -> Array:
	var result = []
	if not (raw_value is Array):
		return DEFAULT_SUPPORTED_VERSIONS.duplicate()
	for item in raw_value:
		result.append(str(item))
	if result.empty():
		return DEFAULT_SUPPORTED_VERSIONS.duplicate()
	return result


func _script_base_dir() -> String:
	return get_script().resource_path.get_base_dir()


func _config_path() -> String:
	return _script_base_dir() + "/config/default_config.json"


func _metadata_path() -> String:
	return _script_base_dir() + "/_metadata"
