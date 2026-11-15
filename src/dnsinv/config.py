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
    neo4j_browser_url: str = "http://localhost:7474"
    """Where the user's own browser reaches Neo4j Browser, for the --cypher link. Inside
    Docker this differs from NEO4J_URI, so docker-compose.yml sets it from NEO4J_HTTP_PORT."""
    neo4j_browser_bolt_url: str | None = None
    """The Connection URL to type into Neo4j Browser's login form, when it is not the
    default localhost:7687 (inside GitHub Codespaces). None means the default works."""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        defaults = cls()
        browser_url = env.get("NEO4J_BROWSER_URL", defaults.neo4j_browser_url).rstrip("/")
        bolt_url: str | None = None

        # GitHub Codespaces publishes every forwarded port at its own HTTPS address, and
        # Neo4j Browser reaches Bolt through the forwarded 7687 over TLS on port 443.
        # Locally docker-compose passes these variables through empty, so this is skipped.
        codespace = env.get("CODESPACE_NAME", "")
        domain = env.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "")
        if codespace and domain:
            browser_url = f"https://{codespace}-7474.{domain}"
            bolt_url = f"neo4j+s://{codespace}-7687.{domain}:443"

        return cls(
            neo4j_uri=env.get("NEO4J_URI", defaults.neo4j_uri),
            neo4j_user=env.get("NEO4J_USER", defaults.neo4j_user),
            neo4j_password=env.get("NEO4J_PASSWORD", defaults.neo4j_password),
            neo4j_browser_url=browser_url,
            neo4j_browser_bolt_url=bolt_url,
        )
