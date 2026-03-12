extends SceneTree

const MAX_IDLE_FRAMES = 180
const MOD_MAIN_PATH = "res://YomiLLMBridge/ModMain.gd"

var _mod_main: Node = null


func _init() -> void:
	var script = load(MOD_MAIN_PATH)
	if script == null:
		printerr("BRIDGE_SMOKE failed to load %s" % MOD_MAIN_PATH)
		quit(1)
		return

	_mod_main = script.new()
	root.call_deferred("add_child", _mod_main)
	call_deferred("_run")


func _run() -> void:
	for _index in range(MAX_IDLE_FRAMES):
		yield(self, "idle_frame")

		if not is_instance_valid(_mod_main):
			printerr("BRIDGE_SMOKE ModMain node was freed before handshake completed")
			quit(1)
			return

		var status = _mod_main.get_bridge_status()
		var state = str(status.get("state", "unknown"))
		if state == "connected":
			print("BRIDGE_SMOKE_OK %s" % to_json(status))
			quit(0)
			return
		if str(status.get("last_error", "")) != "":
			printerr("BRIDGE_SMOKE_ERR %s" % to_json(status))
			quit(1)
			return

	printerr("BRIDGE_SMOKE_TIMEOUT %s" % to_json(_mod_main.get_bridge_status()))
	quit(1)
