extends Node

signal connection_state_changed(state, details)
signal handshake_completed(hello_ack)
signal handshake_failed(reason)
signal daemon_message(envelope)
signal daemon_disconnected(details)

const DEFAULT_HOST := "127.0.0.1"
const DEFAULT_PORT := 8765
const DEFAULT_SCHEMA_VERSION := "v1"
const DEFAULT_SUPPORTED_VERSIONS := ["v1"]

var _socket = WebSocketClient.new()
var _config = {}
var _metadata = {}
var _state = "disconnected"
var _hello_ack = {}
var _last_error = ""
var _last_close = {}


func _ready() -> void:
	_socket.connect("connection_established", self, "_on_connection_established")
	_socket.connect("connection_error", self, "_on_connection_error")
	_socket.connect("connection_closed", self, "_on_connection_closed")
	_socket.connect("server_close_request", self, "_on_server_close_request")
	_socket.connect("data_received", self, "_on_data_received")
	set_process(false)


func _process(_delta: float) -> void:
	if _state == "disconnected":
		return
	_socket.poll()


func configure(config: Dictionary, metadata: Dictionary) -> void:
	_config = config.duplicate(true)
	_metadata = metadata.duplicate(true)


func connect_to_daemon(config := {}, metadata := {}) -> int:
	if config is Dictionary and not config.empty():
		configure(config, metadata if metadata is Dictionary else _metadata)
	elif metadata is Dictionary and not metadata.empty():
		_metadata = metadata.duplicate(true)

	if _config.empty():
		_fail_connection("bridge config has not been loaded")
		return ERR_INVALID_PARAMETER

	var connection_error = _socket.connect_to_url(build_connection_url())
	if connection_error != OK:
		_fail_connection("failed to connect to daemon: %s" % connection_error)
		return connection_error

	_hello_ack = {}
	_last_error = ""
	_transition_to_state("connecting", {"url": build_connection_url()})
	set_process(true)
	return OK


func disconnect_from_daemon(reason := "bridge_disconnect") -> void:
	if _state == "disconnected":
		return
	_last_close = {
		"code": 1000,
		"reason": reason,
		"clean": true,
	}
	_socket.disconnect_from_host(1000, reason)
	_transition_to_state("disconnected", {"reason": reason})
	set_process(false)


func build_connection_url() -> String:
	var transport = _config.get("transport", {})
	var host = str(transport.get("host", DEFAULT_HOST))
	var port = int(transport.get("port", DEFAULT_PORT))
	return "ws://%s:%s" % [host, port]


func build_hello_envelope() -> Dictionary:
	var protocol_config = _config.get("protocol", {})
	var schema_version = str(protocol_config.get("schema_version", DEFAULT_SCHEMA_VERSION))
	var supported_versions = _duplicate_string_array(
		protocol_config.get("supported_versions", DEFAULT_SUPPORTED_VERSIONS)
	)

	return {
		"type": "hello",
		"version": supported_versions[0],
		"ts": _utc_timestamp(),
		"payload": {
			"game_version": str(_metadata.get("game_version", "unknown")),
			"mod_version": str(_metadata.get("version", "0.0.0")),
			"schema_version": schema_version,
			"supported_protocol_versions": supported_versions,
		},
	}


func get_connection_snapshot() -> Dictionary:
	return {
		"state": _state,
		"url": build_connection_url(),
		"hello_ack": _hello_ack.duplicate(true),
		"last_error": _last_error,
		"last_close": _last_close.duplicate(true),
	}


func _on_connection_established(_protocol := "") -> void:
	_transition_to_state("handshaking", {})
	var send_error = _send_json_message(build_hello_envelope())
	if send_error != OK:
		_fail_connection("failed to send hello envelope: %s" % send_error)


func _on_connection_error() -> void:
	_fail_connection("daemon connection failed")


func _on_connection_closed(was_clean: bool = false) -> void:
	var details = _last_close.duplicate(true)
	details["clean"] = was_clean
	_transition_to_state("disconnected", details)
	set_process(false)
	emit_signal("daemon_disconnected", details)


func _on_server_close_request(code: int, reason: String) -> void:
	_last_close = {
		"code": code,
		"reason": reason,
		"clean": false,
	}


func _on_data_received() -> void:
	var peer = _socket.get_peer(1)
	if peer == null:
		_fail_connection("received data without a websocket peer")
		return

	var packet_text = peer.get_packet().get_string_from_utf8()
	var parse_result = JSON.parse(packet_text)
	if parse_result.error != OK:
		_fail_connection("received malformed JSON from daemon")
		return
	if not (parse_result.result is Dictionary):
		_fail_connection("received non-object envelope from daemon")
		return

	var envelope: Dictionary = parse_result.result
	if _state == "handshaking":
		_handle_hello_ack(envelope)
		return
	emit_signal("daemon_message", envelope.duplicate(true))


func _handle_hello_ack(envelope: Dictionary) -> void:
	var validation_error = validate_hello_ack(envelope)
	if validation_error != "":
		_last_close = {
			"code": 1002,
			"reason": validation_error,
			"clean": false,
		}
		_socket.disconnect_from_host(1002, validation_error)
		_fail_connection(validation_error)
		return

	_hello_ack = envelope.duplicate(true)
	_transition_to_state(
		"connected",
		{
			"accepted_protocol_version": envelope["payload"]["accepted_protocol_version"],
			"daemon_version": envelope["payload"]["daemon_version"],
		}
	)
	emit_signal("handshake_completed", _hello_ack.duplicate(true))


func validate_hello_ack(envelope: Dictionary) -> String:
	if str(envelope.get("type", "")) != "hello_ack":
		return "expected hello_ack envelope"

	var supported_versions = _duplicate_string_array(
		_config.get("protocol", {}).get("supported_versions", DEFAULT_SUPPORTED_VERSIONS)
	)
	if not (str(envelope.get("version", "")) in supported_versions):
		return "daemon selected unsupported envelope version"

	if not envelope.has("payload") or not (envelope["payload"] is Dictionary):
		return "hello_ack payload must be an object"

	var payload: Dictionary = envelope["payload"]
	var accepted_protocol_version = str(payload.get("accepted_protocol_version", ""))
	if accepted_protocol_version == "":
		return "hello_ack missing accepted_protocol_version"
	if not (accepted_protocol_version in supported_versions):
		return "daemon selected unsupported protocol version"

	var expected_schema_version = str(
		_config.get("protocol", {}).get("schema_version", DEFAULT_SCHEMA_VERSION)
	)
	if str(payload.get("accepted_schema_version", "")) != expected_schema_version:
		return "daemon selected unsupported schema version"

	if str(payload.get("daemon_version", "")) == "":
		return "hello_ack missing daemon_version"
	if not payload.has("policy_mapping") or not (payload["policy_mapping"] is Dictionary):
		return "hello_ack missing policy_mapping"

	var policy_mapping: Dictionary = payload["policy_mapping"]
	if str(policy_mapping.get("p1", "")) == "":
		return "hello_ack missing policy_mapping.p1"
	if str(policy_mapping.get("p2", "")) == "":
		return "hello_ack missing policy_mapping.p2"

	if payload.has("config") and payload["config"] != null and not (payload["config"] is Dictionary):
		return "hello_ack config must be an object when present"

	return ""


func _send_json_message(message: Dictionary) -> int:
	var peer = _socket.get_peer(1)
	if peer == null:
		return ERR_DOES_NOT_EXIST
	peer.set_write_mode(WebSocketPeer.WRITE_MODE_TEXT)
	return peer.put_packet(to_json(message).to_utf8())


func _transition_to_state(state: String, details: Dictionary) -> void:
	_state = state
	emit_signal("connection_state_changed", state, details.duplicate(true))


func _fail_connection(reason: String) -> void:
	_last_error = reason
	transition_to_disconnected(reason)
	if _hello_ack.empty():
		emit_signal("handshake_failed", reason)
	else:
		emit_signal(
			"daemon_disconnected",
			{
				"reason": reason,
				"clean": false,
			}
		)


func transition_to_disconnected(reason: String) -> void:
	_transition_to_state("disconnected", {"reason": reason})
	set_process(false)


func _utc_timestamp() -> String:
	var now = OS.get_datetime(true)
	return "%04d-%02d-%02dT%02d:%02d:%02dZ" % [
		int(now["year"]),
		int(now["month"]),
		int(now["day"]),
		int(now["hour"]),
		int(now["minute"]),
		int(now["second"]),
	]


func _duplicate_string_array(raw_value, fallback := DEFAULT_SUPPORTED_VERSIONS) -> Array:
	var result = []
	if raw_value is Array:
		for item in raw_value:
			result.append(str(item))
	if result.empty():
		return fallback.duplicate()
	return result
