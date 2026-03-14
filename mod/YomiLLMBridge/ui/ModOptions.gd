extends Control

signal config_changed(updated_config)

var _config = {}
var _bridge_snapshot = {}


func _ready() -> void:
	visible = false


func configure(config: Dictionary) -> void:
	_config = config.duplicate(true)
	emit_signal("config_changed", _config.duplicate(true))


func update_bridge_snapshot(snapshot: Dictionary) -> void:
	_bridge_snapshot = snapshot.duplicate(true)


func get_config() -> Dictionary:
	return _config.duplicate(true)


func get_bridge_snapshot() -> Dictionary:
	return _bridge_snapshot.duplicate(true)


func set_transport_host(host: String) -> void:
	_ensure_transport()
	_config["transport"]["host"] = host.strip_edges()
	emit_signal("config_changed", _config.duplicate(true))


func set_transport_port(port: int) -> void:
	_ensure_transport()
	_config["transport"]["port"] = max(port, 0)
	emit_signal("config_changed", _config.duplicate(true))


func set_logging_toggle(toggle_name: String, enabled: bool) -> void:
	_ensure_logging()
	_config["logging"][toggle_name] = enabled
	emit_signal("config_changed", _config.duplicate(true))


func get_summary() -> Dictionary:
	_ensure_transport()
	_ensure_logging()
	return {
		"host": str(_config["transport"].get("host", "127.0.0.1")),
		"port": int(_config["transport"].get("port", 8765)),
		"logging": _config["logging"].duplicate(true),
		"state": str(_bridge_snapshot.get("state", "disconnected")),
	}


func _ensure_transport() -> void:
	if not _config.has("transport") or not _config["transport"] is Dictionary:
		_config["transport"] = {}


func _ensure_logging() -> void:
	if not _config.has("logging") or not _config["logging"] is Dictionary:
		_config["logging"] = {}
