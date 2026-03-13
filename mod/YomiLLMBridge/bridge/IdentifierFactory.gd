extends Reference

var _rng := RandomNumberGenerator.new()


func _init(rng_seed = null) -> void:
	if rng_seed == null:
		_rng.randomize()
	else:
		_rng.seed = int(rng_seed)


func new_match_id(prefix := "match") -> String:
	return _prefixed_identifier(prefix, _random_hex_token(16))


func _prefixed_identifier(prefix: String, token: String) -> String:
	return "%s-%s" % [prefix.strip_edges().to_lower(), token.strip_edges().to_lower()]


func _random_hex_token(byte_count: int) -> String:
	var hex = ""
	for _index in range(byte_count):
		hex += "%02x" % _rng.randi_range(0, 255)
	return hex
