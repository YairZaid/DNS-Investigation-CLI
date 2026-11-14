from dataclasses import dataclass, field

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable

from dnsinv.config import Settings
from dnsinv.graph.connection import Neo4jAuthError, Neo4jUnavailableError, wait_for_neo4j

SETTINGS = Settings(neo4j_uri="bolt://db:7687", neo4j_user="neo4j", neo4j_password="secret")


@dataclass
class FakeDriver:
    failures: list[Exception]
    closed: bool = False
    attempts: int = 0

    def verify_connectivity(self) -> None:
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeClock:
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def connect(driver, clock, timeout=30.0, on_wait=None):
    created = []

    def factory(uri, auth):
        created.append((uri, auth))
        return driver

    result = wait_for_neo4j(
        SETTINGS,
        timeout=timeout,
        on_wait=on_wait,
        driver_factory=factory,
        sleep=clock.sleep,
        clock=clock.time,
    )
    return result, created


def test_returns_driver_when_neo4j_is_ready():
    driver = FakeDriver(failures=[])

    result, created = connect(driver, FakeClock())

    assert result is driver
    assert created == [("bolt://db:7687", ("neo4j", "secret"))]
    assert not driver.closed


def test_retries_until_ready_and_announces_the_wait_once():
    driver = FakeDriver(failures=[ServiceUnavailable("down")] * 3)
    messages: list[str] = []

    result, _ = connect(driver, FakeClock(), on_wait=messages.append)

    assert result is driver
    assert driver.attempts == 4
    assert len(messages) == 1
    assert "Waiting for Neo4j" in messages[0]


def test_gives_up_after_timeout_and_closes_driver():
    driver = FakeDriver(failures=[ServiceUnavailable("down")] * 1000)
    clock = FakeClock()

    with pytest.raises(Neo4jUnavailableError) as excinfo:
        connect(driver, clock, timeout=5.0)

    assert driver.closed
    assert clock.now >= 5.0
    assert "bolt://db:7687" in str(excinfo.value)


def test_wrong_password_fails_immediately_without_retrying():
    driver = FakeDriver(failures=[AuthError("bad credentials")])
    clock = FakeClock()

    with pytest.raises(Neo4jAuthError):
        connect(driver, clock)

    assert driver.attempts == 1
    assert clock.sleeps == []
    assert driver.closed


def test_malformed_uri_is_reported_as_unavailable():
    def factory(uri, auth):
        raise ValueError("bad scheme")

    with pytest.raises(Neo4jUnavailableError) as excinfo:
        wait_for_neo4j(SETTINGS, timeout=1.0, driver_factory=factory)

    assert "NEO4J_URI" in str(excinfo.value)
