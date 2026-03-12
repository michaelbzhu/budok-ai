"""Replay index helpers for artifact logs."""

from __future__ import annotations

from dataclasses import dataclass, field

from yomi_daemon.logging.schemas import ArtifactPaths
from yomi_daemon.protocol import JsonObject, ProtocolModel


@dataclass(frozen=True, slots=True)
class ReplayTurnIndex(ProtocolModel):
    turn_id: int
    player_id: str
    state_hash: str
    legal_actions_hash: str
    prompt_line: int | None = field(metadata={"serialize_null": True})
    decision_line: int | None = field(metadata={"serialize_null": True})
    action: str | None = field(metadata={"serialize_null": True})


@dataclass(frozen=True, slots=True)
class ReplayIndex(ProtocolModel):
    match_id: str
    created_at: str
    updated_at: str
    replay_path: str | None = field(metadata={"serialize_null": True})
    artifacts: JsonObject = field(default_factory=dict)
    turns: tuple[ReplayTurnIndex, ...] = ()


@dataclass(slots=True)
class ReplayIndexState:
    match_id: str
    artifact_paths: ArtifactPaths
    created_at: str
    updated_at: str
    replay_path: str | None = None
    _turns: dict[int, ReplayTurnIndex] = field(default_factory=dict, init=False, repr=False)

    def record_prompt(
        self,
        *,
        turn_id: int,
        player_id: str,
        state_hash: str,
        legal_actions_hash: str,
        line_number: int,
        updated_at: str,
    ) -> None:
        current = self._turns.get(turn_id)
        prompt_line = line_number
        decision_line = current.decision_line if current is not None else None
        action = current.action if current is not None else None
        self._turns[turn_id] = ReplayTurnIndex(
            turn_id=turn_id,
            player_id=player_id,
            state_hash=state_hash,
            legal_actions_hash=legal_actions_hash,
            prompt_line=prompt_line,
            decision_line=decision_line,
            action=action,
        )
        self.updated_at = updated_at

    def record_decision(
        self,
        *,
        turn_id: int,
        player_id: str,
        state_hash: str,
        legal_actions_hash: str,
        line_number: int,
        updated_at: str,
        action: str | None,
    ) -> None:
        current = self._turns.get(turn_id)
        prompt_line = current.prompt_line if current is not None else None
        self._turns[turn_id] = ReplayTurnIndex(
            turn_id=turn_id,
            player_id=player_id,
            state_hash=state_hash,
            legal_actions_hash=legal_actions_hash,
            prompt_line=prompt_line,
            decision_line=line_number,
            action=action,
        )
        self.updated_at = updated_at

    def finalize(self, *, updated_at: str, replay_path: str | None) -> None:
        self.updated_at = updated_at
        self.replay_path = replay_path

    def snapshot(self) -> ReplayIndex:
        return ReplayIndex(
            match_id=self.match_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            replay_path=self.replay_path,
            artifacts=self.artifact_paths.to_dict(),
            turns=tuple(self._turns[turn_id] for turn_id in sorted(self._turns)),
        )
