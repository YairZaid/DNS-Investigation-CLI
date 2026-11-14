"""Connecting to Neo4j, waiting for it to start when needed."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from neo4j import Driver, GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired

from dnsinv.config import Settings
from dnsinv.errors import DnsInvError

RETRY_INTERVAL_SECONDS = 1.0


class Neo4jUnavailableError(DnsInvError):
    pass


class Neo4jAuthError(DnsInvError):
    pass


class _Connectable(Protocol):
    def verify_connectivity(self) -> None: ...
    def close(self) -> None: ...


DriverFactory = Callable[..., Any]
WaitCallback = Callable[[str], None]


def wait_for_neo4j(
    settings: Settings,
    timeout: float,
    on_wait: WaitCallback | None = None,
    *,
    driver_factory: DriverFactory = GraphDatabase.driver,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Return a connected driver, retrying until `timeout` seconds have passed.

    Wrong credentials fail at once: waiting will not fix them.
    """
    uri = settings.neo4j_uri
    try:
        driver: _Connectable = driver_factory(
            uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
    except ValueError as exc:
        raise Neo4jUnavailableError(f"NEO4J_URI {uri!r} is not a valid Neo4j URI: {exc}") from exc

    deadline = clock() + timeout
    announced = False
    while True:
        try:
            driver.verify_connectivity()
            return driver
        except AuthError as exc:
            driver.close()
            raise Neo4jAuthError(
                f"Neo4j at {uri} rejected user {settings.neo4j_user!r}: check NEO4J_PASSWORD"
            ) from exc
        except (ServiceUnavailable, SessionExpired, OSError) as exc:
            if clock() >= deadline:
                driver.close()
                raise Neo4jUnavailableError(
                    f"Neo4j is not reachable at {uri} after {timeout:g}s"
                ) from exc
            if on_wait is not None and not announced:
                on_wait(f"Waiting for Neo4j at {uri} to become ready...")
                announced = True
            sleep(RETRY_INTERVAL_SECONDS)


def check_neo4j(settings: Settings, timeout: float, on_wait: WaitCallback | None = None) -> str:
    """Connect, return a short description of the server, and disconnect."""
    driver: Driver = wait_for_neo4j(settings, timeout, on_wait)
    try:
        records, _, _ = driver.execute_query(
            "CALL dbms.components() YIELD name, versions, edition "
            "RETURN name, versions[0] AS version, edition"
        )
        first = records[0]
        return f"{first['name']} {first['version']} {first['edition']}"
    finally:
        driver.close()
