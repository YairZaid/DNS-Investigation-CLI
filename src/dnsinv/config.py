"""Settings read from environment variables, with defaults that match docker-compose.yml."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = field(default="dnsinv-local", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        defaults = cls()
        return cls(
            neo4j_uri=env.get("NEO4J_URI", defaults.neo4j_uri),
            neo4j_user=env.get("NEO4J_USER", defaults.neo4j_user),
            neo4j_password=env.get("NEO4J_PASSWORD", defaults.neo4j_password),
        )
