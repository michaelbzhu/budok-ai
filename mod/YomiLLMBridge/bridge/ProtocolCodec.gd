extends Reference

const CURRENT_PROTOCOL_VERSION := "v2"


func build_envelope(message_type: String, payload: Dictionary, version := CURRENT_PROTOCOL_VERSION) -> Dictionary:
	return {
		"type": message_type,
		"version": str(version),
		"ts": utc_timestamp(),
		"payload": payload.duplicate(true),
	}


func canonical_json(value) -> String:
	return _canonical_json_value(value)


func sha256_hex(value) -> String:
	var ctx = HashingContext.new()
	ctx.start(HashingContext.HASH_SHA256)
	ctx.update(canonical_json(value).to_utf8())
	return _bytes_to_hex(ctx.finish())


func utc_timestamp() -> String:
	var now = OS.get_datetime(true)
	return "%04d-%02d-%02dT%02d:%02d:%02dZ" % [
		int(now["year"]),
		int(now["month"]),
		int(now["day"]),
		int(now["hour"]),
		int(now["minute"]),
		int(now["second"]),
	]


func _canonical_json_value(value) -> String:
	if value == null:
		return "null"
	if value is Dictionary:
		var keys = value.keys()
		keys.sort()
		var parts := []
		for key in keys:
			parts.append("%s:%s" % [JSON.print(str(key)), _canonical_json_value(value[key])])
		return "{%s}" % PoolStringArray(parts).join(",")
	if value is Array:
		var parts := []
		for item in value:
			parts.append(_canonical_json_value(item))
		return "[%s]" % PoolStringArray(parts).join(",")
	return JSON.print(value)


func _bytes_to_hex(bytes: PoolByteArray) -> String:
	var hex = ""
	for byte_value in bytes:
		hex += "%02x" % byte_value
	return hex
