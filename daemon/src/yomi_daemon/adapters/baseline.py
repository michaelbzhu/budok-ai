"""Deterministic baseline policy adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random
from typing import TYPE_CHECKING, cast

from yomi_daemon.adapters.base import (
    AdapterConstructionError,
    BasePolicyAdapter,
    metadata_from_policy_config,
)
from yomi_daemon.protocol import DecisionRequest, JsonObject, JsonValue, LegalAction

if TYPE_CHECKING:
    from yomi_daemon.config import PolicyConfig


_UNRESOLVED = object()

_DEFENSIVE_KEYWORDS = (
    "block",
    "guard",
    "parry",
    "defend",
    "shield",
    "wait",
    "idle",
    "evade",
    "backdash",
)
_OFFENSIVE_KEYWORDS = (
    "slash",
    "attack",
    "strike",
    "kick",
    "punch",
    "shoot",
    "blast",
    "smash",
    "throw",
    "stab",
)
_LOW_COMMITMENT_KEYWORDS = (
    "block",
    "guard",
    "wait",
    "backdash",
    "jab",
    "poke",
    "dash",
    "step",
    "move",
)
_HIGH_COMMITMENT_KEYWORDS = (
    "super",
    "burst",
    "charge",
    "launcher",
    "throw",
    "teleport",
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    index: int
    action: LegalAction
    payload: JsonObject | None
    payload_resolved: bool


class RandomBaselineAdapter(BasePolicyAdapter):
    async def decide(self, request: DecisionRequest):
        candidates = _build_candidates(request, rng=self.rng_for_request(request, salt="payload"))
        selectable = _prefer_resolved_candidates(candidates)
        rng = self.rng_for_request(request, salt="random-choice")
        chosen = selectable[rng.randrange(len(selectable))]
        return self.build_decision(
            request,
            chosen.action,
            data=chosen.payload,
            notes="Deterministic seeded random baseline.",
        )


class BlockAlwaysBaselineAdapter(BasePolicyAdapter):
    async def decide(self, request: DecisionRequest):
        candidates = _build_candidates(request, rng=self.rng_for_request(request, salt="payload"))
        chosen = max(
            candidates,
            key=lambda candidate: (
                candidate.payload_resolved,
                _defensive_score(candidate.action),
                _safe_score(candidate.action),
                -candidate.index,
            ),
        )
        return self.build_decision(
            request,
            chosen.action,
            data=chosen.payload,
            notes="Prefers guard-like actions and conservative fallbacks.",
        )


class GreedyDamageBaselineAdapter(BasePolicyAdapter):
    async def decide(self, request: DecisionRequest):
        candidates = _build_candidates(request, rng=self.rng_for_request(request, salt="payload"))
        chosen = max(
            candidates,
            key=lambda candidate: (
                candidate.payload_resolved,
                _damage_score(candidate.action),
                -_startup_value(candidate.action),
                _range_value(candidate.action),
                -_meter_cost(candidate.action),
                -candidate.index,
            ),
        )
        return self.build_decision(
            request,
            chosen.action,
            data=chosen.payload,
            notes="Uses tactical damage metadata with deterministic fallbacks.",
        )


class ScriptedSafeBaselineAdapter(BasePolicyAdapter):
    async def decide(self, request: DecisionRequest):
        candidates = _build_candidates(request, rng=self.rng_for_request(request, salt="payload"))
        chosen = max(
            candidates,
            key=lambda candidate: (
                candidate.payload_resolved,
                _safe_score(candidate.action),
                -_meter_cost(candidate.action),
                -_startup_value(candidate.action),
                -candidate.index,
            ),
        )
        return self.build_decision(
            request,
            chosen.action,
            data=chosen.payload,
            notes="Conservative legality-preserving scripted baseline.",
        )


def build_baseline_adapter(
    policy_id: str,
    policy: "PolicyConfig",
    *,
    default_trace_seed: int = 0,
) -> BasePolicyAdapter:
    metadata = metadata_from_policy_config(policy_id, policy)
    if policy_id == "baseline/random":
        return RandomBaselineAdapter(metadata=metadata, default_trace_seed=default_trace_seed)
    if policy_id == "baseline/block_always":
        return BlockAlwaysBaselineAdapter(metadata=metadata, default_trace_seed=default_trace_seed)
    if policy_id == "baseline/greedy_damage":
        return GreedyDamageBaselineAdapter(metadata=metadata, default_trace_seed=default_trace_seed)
    if policy_id == "baseline/scripted_safe":
        return ScriptedSafeBaselineAdapter(metadata=metadata, default_trace_seed=default_trace_seed)
    raise AdapterConstructionError(f"unsupported baseline policy id: {policy_id!r}")


def _build_candidates(request: DecisionRequest, *, rng: Random) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for index, action in enumerate(request.legal_actions):
        payload = _resolve_payload_object(action.payload_spec, rng=rng)
        resolved_payload = None if payload is _UNRESOLVED else cast(JsonObject, payload)
        candidates.append(
            _Candidate(
                index=index,
                action=action,
                payload=resolved_payload,
                payload_resolved=payload is not _UNRESOLVED,
            )
        )
    return candidates


def _prefer_resolved_candidates(candidates: Sequence[_Candidate]) -> Sequence[_Candidate]:
    resolved = [candidate for candidate in candidates if candidate.payload_resolved]
    return resolved or candidates


def _action_text(action: LegalAction) -> str:
    parts = [action.action]
    if action.label:
        parts.append(action.label)
    if action.description:
        parts.append(action.description)
    return " ".join(parts).lower()


def _keyword_hits(text: str, keywords: Sequence[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _defensive_score(action: LegalAction) -> int:
    text = _action_text(action)
    return _keyword_hits(text, _DEFENSIVE_KEYWORDS)


def _safe_score(action: LegalAction) -> float:
    text = _action_text(action)
    no_payload_bonus = 3.0 if not action.payload_spec else 0.0
    no_extra_bonus = 1.0 if not action.supports.di else 0.0
    defensive_bonus = float(_keyword_hits(text, _DEFENSIVE_KEYWORDS) * 4)
    low_commitment_bonus = float(_keyword_hits(text, _LOW_COMMITMENT_KEYWORDS) * 2)
    high_commitment_penalty = float(_keyword_hits(text, _HIGH_COMMITMENT_KEYWORDS) * 3)
    startup_penalty = min(_startup_value(action), 30) / 10.0
    meter_penalty = float(_meter_cost(action) * 2)
    return (
        no_payload_bonus
        + no_extra_bonus
        + defensive_bonus
        + low_commitment_bonus
        - high_commitment_penalty
        - startup_penalty
        - meter_penalty
    )


def _damage_score(action: LegalAction) -> float:
    if action.damage is not None:
        return action.damage

    text = _action_text(action)
    offensive_hits = _keyword_hits(text, _OFFENSIVE_KEYWORDS)
    defensive_hits = _keyword_hits(text, _DEFENSIVE_KEYWORDS)
    payload_bonus = 5.0 if action.payload_spec else 0.0
    startup_bonus = max(0.0, 12.0 - min(_startup_value(action), 12))
    range_bonus = min(_range_value(action), 25.0) / 5.0
    meter_penalty = float(_meter_cost(action) * 4)
    return (
        offensive_hits * 25.0
        - defensive_hits * 10.0
        + payload_bonus
        + startup_bonus
        + range_bonus
        - meter_penalty
    )


def _startup_value(action: LegalAction) -> int:
    return action.startup_frames if action.startup_frames is not None else 999


def _range_value(action: LegalAction) -> float:
    return action.range if action.range is not None else 0.0


def _meter_cost(action: LegalAction) -> int:
    return action.meter_cost if action.meter_cost is not None else 0


def _resolve_payload_object(spec: JsonObject, *, rng: Random) -> JsonObject | object:
    if not spec:
        return {}
    if _looks_like_schema_descriptor(spec):
        resolved = _resolve_schema_value(spec, rng=rng)
        return resolved if isinstance(resolved, dict) else _UNRESOLVED

    payload: JsonObject = {}
    for key, raw_value in spec.items():
        resolved = _resolve_schema_value(raw_value, rng=rng)
        if resolved is _UNRESOLVED:
            return _UNRESOLVED
        payload[key] = cast(JsonValue, resolved)
    return payload


def _looks_like_schema_descriptor(spec: JsonObject) -> bool:
    return bool(
        {"type", "enum", "const", "default", "properties", "items", "minimum", "maximum"}
        & set(spec)
    )


def _resolve_schema_value(spec: object, *, rng: Random) -> JsonValue | object:
    if spec is None or isinstance(spec, bool | int | float | str):
        return cast(JsonValue, spec)
    if not isinstance(spec, Mapping):
        return _UNRESOLVED

    mapping = cast(Mapping[str, object], spec)

    if "const" in mapping:
        return _coerce_json_value(mapping["const"])
    if "default" in mapping:
        return _coerce_json_value(mapping["default"])

    enum_values = mapping.get("enum", mapping.get("choices"))
    if isinstance(enum_values, Sequence) and not isinstance(enum_values, str | bytes | bytearray):
        choices = [_coerce_json_value(item) for item in enum_values]
        if choices:
            return choices[rng.randrange(len(choices))]

    raw_type = mapping.get("type")
    if isinstance(raw_type, str):
        normalized_type = raw_type.lower()
        if normalized_type == "object":
            return _resolve_object_value(mapping, rng=rng)
        if normalized_type == "array":
            return _resolve_array_value(mapping, rng=rng)
        if normalized_type == "integer":
            return _coerce_numeric_bound(mapping.get("minimum", mapping.get("min", 0)), as_int=True)
        if normalized_type == "number":
            return _coerce_numeric_bound(
                mapping.get("minimum", mapping.get("min", 0.0)),
                as_int=False,
            )
        if normalized_type == "boolean":
            return False
        if normalized_type == "string":
            return str(mapping.get("example", mapping.get("placeholder", "")))
        if normalized_type in {"enemy", "opponent", "target_enemy"}:
            return "enemy"
        if normalized_type in {"self", "ally", "target_self"}:
            return "self"
        if normalized_type in {"direction", "facing"}:
            return "forward"
        if normalized_type in {"vector2", "vec2", "point", "position"}:
            return {"x": 0, "y": 0}

    if "properties" in mapping:
        return _resolve_object_value(mapping, rng=rng)
    if "items" in mapping:
        return _resolve_array_value(mapping, rng=rng)

    nested_payload: JsonObject = {}
    for key, value in mapping.items():
        resolved = _resolve_schema_value(value, rng=rng)
        if resolved is _UNRESOLVED:
            return _UNRESOLVED
        nested_payload[key] = cast(JsonValue, resolved)
    return nested_payload


def _resolve_object_value(spec: Mapping[str, object], *, rng: Random) -> JsonObject | object:
    properties = spec.get("properties")
    if not isinstance(properties, Mapping):
        return {}

    resolved: JsonObject = {}
    for key, value in cast(Mapping[str, object], properties).items():
        item = _resolve_schema_value(value, rng=rng)
        if item is _UNRESOLVED:
            return _UNRESOLVED
        resolved[str(key)] = cast(JsonValue, item)
    return resolved


def _resolve_array_value(spec: Mapping[str, object], *, rng: Random) -> list[JsonValue] | object:
    items_spec = spec.get("items")
    min_items = spec.get("min_items", spec.get("minItems", 0))
    if not isinstance(min_items, int):
        min_items = 0
    if items_spec is None:
        return []

    resolved_item = _resolve_schema_value(items_spec, rng=rng)
    if resolved_item is _UNRESOLVED:
        return _UNRESOLVED
    return [cast(JsonValue, resolved_item) for _ in range(max(0, min_items))]


def _coerce_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return cast(JsonValue, value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [cast(JsonValue, _coerce_json_value(item)) for item in value]
    if isinstance(value, Mapping):
        result: JsonObject = {}
        for key, item in value.items():
            result[str(key)] = _coerce_json_value(item)
        return result
    return str(value)


def _coerce_numeric_bound(value: object, *, as_int: bool) -> int | float:
    if isinstance(value, bool):
        return 0 if as_int else 0.0
    if isinstance(value, int | float):
        return int(value) if as_int else float(value)
    if isinstance(value, str):
        try:
            return int(value) if as_int else float(value)
        except ValueError:
            return 0 if as_int else 0.0
    return 0 if as_int else 0.0
