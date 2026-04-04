extends Node

# Loads a saved .replay file on startup, plays it back in-engine, and quits
# after a short grace period so an external recorder can capture the full replay.

const DEFAULT_EXIT_GRACE_SECONDS := 5.0
const DEFAULT_PLAYBACK_SPEED_MOD := 2
const STARTUP_SETTLE_MS := 1000

var _replay_path := ""
var _exit_grace_ms := int(DEFAULT_EXIT_GRACE_SECONDS * 1000.0)
var _playback_speed_mod := DEFAULT_PLAYBACK_SPEED_MOD
var _replay_loaded := false
var _load_requested := false
var _playback_started := false
var _replay_game = null
var _connected_game = null
var _replay_finished := false
var _replay_end_grace_started_ms := 0
var _last_wait_reason := ""
var _ready_at_ms := 0
var _watch_mode_requested := false
var _watch_mode_transition_pending := false
var _last_status_log_ms := 0
var _replay_match_data := {}


func configure(
	replay_path: String,
	exit_grace_seconds: float = DEFAULT_EXIT_GRACE_SECONDS,
	playback_speed_mod: int = DEFAULT_PLAYBACK_SPEED_MOD
) -> void:
	_replay_path = replay_path
	_exit_grace_ms = int(max(exit_grace_seconds, 0.0) * 1000.0)
	_playback_speed_mod = max(playback_speed_mod, 1)
	set_process(true)


func _ready() -> void:
	_log("AutoReplayRecorder: _ready")
	_ready_at_ms = OS.get_ticks_msec()
	set_process(true)


func _process(_delta: float) -> void:
	if not _replay_loaded:
		if not _load_requested:
			var wait_reason = _replay_wait_reason()
			if wait_reason == "":
				_load_requested = true
				call_deferred("_deferred_load_replay")
			elif wait_reason != _last_wait_reason:
				_last_wait_reason = wait_reason
				_log("AutoReplayRecorder: waiting for %s" % wait_reason)
		return
	_monitor_replay()


func _replay_wait_reason() -> String:
	var main_node = _get_main_node()
	if main_node == null or not main_node.has_method("_on_match_ready"):
		return "main node with _on_match_ready"
	if not ("mods_loaded" in Global) or not bool(Global.mods_loaded):
		return "Global.mods_loaded"
	if _ready_at_ms > 0 and OS.get_ticks_msec() - _ready_at_ms < STARTUP_SETTLE_MS:
		return "startup settle"
	return ""


func _deferred_load_replay() -> void:
	if _replay_wait_reason() != "":
		_log("AutoReplayRecorder: replay prerequisites not ready yet")
		_load_requested = false
		return
	_try_load_replay()


func _try_load_replay() -> void:
	var main_node = _get_main_node()
	if main_node == null or not main_node.has_method("_on_loaded_replay"):
		_load_requested = false
		return

	if _replay_path == "":
		_fail_and_quit("AutoReplayRecorder: missing replay path")
		return

	var file = File.new()
	if not file.file_exists(_replay_path):
		_fail_and_quit("AutoReplayRecorder: replay file not found: %s" % _replay_path)
		return

	_log("AutoReplayRecorder: loading replay %s" % _replay_path)
	print("AutoReplayRecorder: loading replay %s" % _replay_path)
	var match_data = ReplayManager.load_replay(_replay_path)
	if not (match_data is Dictionary) or match_data.empty():
		_fail_and_quit("AutoReplayRecorder: failed to load replay: %s" % _replay_path)
		return

	Global.playback_speed_mod = _playback_speed_mod
	ReplayManager.play_full = false
	ReplayManager.replaying_ingame = false
	if main_node.has_method("hide_main_menu"):
		main_node.hide_main_menu(true)
	_replay_match_data = match_data.duplicate(true)

	var p1_name = str(match_data.get("selected_characters", {}).get(1, {}).get("name", ""))
	var p2_name = str(match_data.get("selected_characters", {}).get(2, {}).get("name", ""))
	if Global.name_paths.has(p1_name) and Global.name_paths.has(p2_name):
		match_data["replay"] = true
		_log("AutoReplayRecorder: starting built-in replay directly")
		main_node._on_match_ready(match_data)
	else:
		if not main_node.has_method("_on_loaded_replay"):
			_fail_and_quit(
				"AutoReplayRecorder: replay requires _on_loaded_replay for character loading"
			)
			return
		if not ("css_instance" in Global) or Global.css_instance == null:
			_log("AutoReplayRecorder: waiting for Global.css_instance for replay character loading")
			_load_requested = false
			return
		_log("AutoReplayRecorder: starting replay via _on_loaded_replay")
		main_node._on_loaded_replay(match_data)
	_replay_loaded = true


func _monitor_replay() -> void:
	var main_node = _get_main_node()
	if main_node == null:
		return

	var game = main_node.get("game")
	if game == null or not is_instance_valid(game):
		return
	_bind_game(game)
	if not _playback_started:
		if not (game.match_data is Dictionary) or not game.match_data.has("replay"):
			return
		if not ReplayManager.playback:
			return
		_playback_started = true
		_replay_game = game
		_log("AutoReplayRecorder: replay playback started")
		print("AutoReplayRecorder: replay playback started")
		return

	_replay_game = game
	_log_status()

	if (
		not _watch_mode_requested
		and not ReplayManager.playback
		and _replay_game.has_method("is_waiting_on_player")
		and _replay_game.is_waiting_on_player()
		and _replay_game.current_tick > 0
	):
		_watch_mode_requested = true
		_watch_mode_transition_pending = true
		_log("AutoReplayRecorder: requesting watch replay mode")
		_replay_game.start_playback()
		return

	if not _replay_finished:
		return

	if OS.get_ticks_msec() - _replay_end_grace_started_ms >= _exit_grace_ms:
		_log("AutoReplayRecorder: replay capture complete, quitting")
		print("AutoReplayRecorder: replay capture complete, quitting")
		get_tree().quit()


func _bind_game(game) -> void:
	if game == _connected_game:
		return
	_connected_game = game
	_apply_replay_options_to_game(game)
	if not game.is_connected("game_ended", self, "_on_game_ended"):
		game.connect("game_ended", self, "_on_game_ended")
	if not game.is_connected("playback_requested", self, "_on_playback_requested"):
		game.connect("playback_requested", self, "_on_playback_requested")


func _on_game_ended() -> void:
	_mark_replay_finished("AutoReplayRecorder: replay finished")


func _on_playback_requested() -> void:
	if not _playback_started:
		return
	if _watch_mode_transition_pending:
		_watch_mode_transition_pending = false
		_log("AutoReplayRecorder: watch replay transition acknowledged")
		return
	_mark_replay_finished("AutoReplayRecorder: replay loop requested")


func _mark_replay_finished(message: String) -> void:
	if _replay_finished:
		return
	_replay_finished = true
	_replay_end_grace_started_ms = OS.get_ticks_msec()
	_log("%s, waiting %.2fs before quitting" % [message, _exit_grace_ms / 1000.0])
	print("%s, waiting %.2fs before quitting" % [message, _exit_grace_ms / 1000.0])
	_prevent_replay_loop()


func _log_status() -> void:
	var now = OS.get_ticks_msec()
	if now - _last_status_log_ms < 2000:
		return
	_last_status_log_ms = now
	if _replay_game == null or not is_instance_valid(_replay_game):
		return
	_log(
		"AutoReplayRecorder: status tick=%s playback=%s replaying_ingame=%s waiting=%s finished=%s"
		% [
			str(_replay_game.current_tick),
			str(ReplayManager.playback),
			str(ReplayManager.replaying_ingame),
			str(_replay_game.is_waiting_on_player()),
			str(_replay_game.game_finished),
		]
	)


func _apply_replay_options_to_game(game) -> void:
	if game == null or not is_instance_valid(game):
		return
	if not (_replay_match_data is Dictionary) or _replay_match_data.empty():
		return
	var starting_hp = _replay_match_data.get("starting_hp", null)
	if starting_hp == null:
		return
	var hp_value = int(starting_hp)
	if hp_value <= 0:
		return
	for fighter in [game.p1, game.p2]:
		if fighter == null:
			continue
		fighter.MAX_HEALTH = hp_value
		fighter.hp = hp_value
		fighter.trail_hp = hp_value
	var hud = _find_hud()
	if hud != null:
		_sync_hud_health_bars(hud, hp_value)
	_log("AutoReplayRecorder: applied starting_hp=%d to replay game" % hp_value)


func _find_hud() -> Node:
	var root = get_tree().get_root() if get_tree() != null else null
	if root == null:
		return null
	return root.find_node("HudLayer", true, false)


func _sync_hud_health_bars(hud, max_hp: int) -> void:
	for bar_name in [
		"P1HealthBar",
		"P2HealthBar",
		"P1HealthBarTrail",
		"P2HealthBarTrail",
		"P1GhostHealthBar",
		"P2GhostHealthBar",
		"P1GhostHealthBarTrail",
		"P2GhostHealthBarTrail",
	]:
		var bar = hud.find_node(bar_name, true, false)
		if bar != null:
			bar.max_value = max_hp
			bar.value = max_hp


func _prevent_replay_loop() -> void:
	if "play_full" in ReplayManager:
		ReplayManager.play_full = false
	if _replay_game != null and is_instance_valid(_replay_game):
		_replay_game.game_started = false


func _get_main_node():
	var main_loop = Engine.get_main_loop()
	if main_loop == null or main_loop.root == null:
		return null
	return main_loop.root.get_node_or_null("Main")


func _fail_and_quit(message: String) -> void:
	_log(message)
	printerr(message)
	if get_tree() != null:
		get_tree().quit(1)


func _log(message: String) -> void:
	var file = File.new()
	var path = "/tmp/yomi_autoreplay_debug.log"
	var mode = File.READ_WRITE if file.file_exists(path) else File.WRITE
	var open_error = file.open(path, mode)
	if open_error != OK:
		return
	file.seek_end()
	file.store_line(message)
	file.close()
