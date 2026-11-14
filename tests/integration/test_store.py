"""The graph store against a real Neo4j, fed with scans built from recorded dig output."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dnsinv.collection.model import HostnameScan, ScanTarget
from dnsinv.collection.resolver import ResolverChoice, ResolverSource
from dnsinv.collection.scanner import scan_hostname
from dnsinv.config import Settings
from dnsinv.graph.store import GraphStore, ScanInfo, open_store
from tests.fakes import FakeRunner, ok

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dig"
RESOLVER = ResolverChoice("1.1.1.1", ResolverSource.DEFAULT)
DAY_1 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
DAY_2 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def scanned(fixture, name, *, is_input=True, apex=None):
    text = (FIXTURES / f"{fixture}.txt").read_text(encoding="utf-8")
    return scan_hostname(ScanTarget(name, is_input, apex), FakeRunner(lambda a: ok(text)), RESOLVER)


def answered(name: str, status: str) -> HostnameScan:
    """A scan of `name` whose A lookup returned `status` and no records."""
    text = (
        f";; ->>HEADER<<- opcode: QUERY, status: {status}, id: 1\n"
        f";; QUESTION SECTION:\n;{name}.\t\tIN\tA\n"
    )
    return scan_hostname(ScanTarget(name, True, None), FakeRunner(lambda a: ok(text)), RESOLVER)


def info(source="urls.txt", at=DAY_1, hostnames=1):
    return ScanInfo(source=source, scanned_at=at, resolver="1.1.1.1 (default)", hostnames=hostnames)


@pytest.fixture
def store() -> Iterator[GraphStore]:
    with open_store(Settings.from_env(os.environ), timeout=30.0) as graph:
        graph.ensure_schema()
        graph.clear()
        yield graph


def rows(store, cypher, **params):
    return [record.data() for record in store.query(cypher, **params)]


def single(store, cypher, **params):
    (row,) = rows(store, cypher, **params)
    return row


# ---- what gets written --------------------------------------------------------------


def test_addresses_become_ip_nodes(store):
    store.save_scan(info(), [scanned("ok", "ok.test.lab")], replace=True)

    ips = rows(
        store,
        "MATCH (:Hostname {name: 'ok.test.lab'})-[:RESOLVES_TO]->(ip:IP) "
        "RETURN ip.address AS address, ip.version AS version ORDER BY address",
    )

    assert ips == [
        {"address": "192.0.2.10", "version": 4},
        {"address": "192.0.2.11", "version": 4},
        {"address": "2001:db8::10", "version": 6},
    ]


def test_hostname_properties(store):
    store.save_scan(info(), [scanned("ok", "ok.test.lab")], replace=True)

    host = single(
        store,
        "MATCH (h:Hostname {name: 'ok.test.lab'}) "
        "RETURN h.is_input AS is_input, h.status AS status, h.scanned_at AS at, "
        "h.failed_lookups AS failed, h.dangling AS dangling",
    )

    assert host["is_input"] is True
    assert host["status"] == "NOERROR"
    assert host["at"].to_native() == DAY_1
    assert host["failed"] == []
    assert host["dangling"] is False


def test_cname_chain_records_belong_to_their_owner(store):
    store.save_scan(info(), [scanned("goodchain1", "goodchain1.test.lab")], replace=True)

    chain = single(
        store,
        "MATCH p = (:Hostname {name: 'goodchain1.test.lab'})-[:CNAME_TO*]->(end:Hostname) "
        "WHERE NOT (end)-[:CNAME_TO]->() "
        "RETURN [n IN nodes(p) | n.name] AS names, "
        "       [(end)-[:RESOLVES_TO]->(ip) | ip.address] AS ips",
    )

    assert chain["names"] == ["goodchain1.test.lab", "goodchain2.test.lab", "ok.test.lab"]
    assert sorted(chain["ips"]) == ["192.0.2.10", "192.0.2.11", "2001:db8::10"]
    # The addresses belong to the end of the chain, not to the name that was asked.
    assert (
        rows(
            store, "MATCH (:Hostname {name: 'goodchain1.test.lab'})-[:RESOLVES_TO]->(ip) RETURN ip"
        )
        == []
    )


def test_dangling_cname_keeps_the_status_of_every_link(store):
    store.save_scan(info(), [scanned("unbound-xdangling", "xdangling.test.lab")], replace=True)

    links = rows(
        store,
        "MATCH (h:Hostname {name: 'xdangling.test.lab'})-[:CNAME_TO]->(t:Hostname) "
        "RETURN h.status AS source, h.dangling AS dangling, t.name AS target, t.status AS status",
    )

    assert links == [
        {"source": "NOERROR", "dangling": True, "target": "gone.other.lab", "status": "NXDOMAIN"}
    ]


def test_mail_exchanges_keep_priority(store):
    store.save_scan(info(), [scanned("mail", "mail.test.lab")], replace=True)

    mx = rows(
        store,
        "MATCH (:Hostname {name: 'mail.test.lab'})-[r:MAIL_HANDLED_BY]->(m:Hostname) "
        "RETURN m.name AS name, r.priority AS priority ORDER BY priority",
    )

    assert mx == [
        {"name": "mx1.test.lab", "priority": 10},
        {"name": "mx2.test.lab", "priority": 20},
    ]


def test_null_mx_is_a_property_not_a_relationship(store):
    store.save_scan(info(), [scanned("nullmx", "nullmx.test.lab")], replace=True)

    host = single(
        store,
        "MATCH (h:Hostname {name: 'nullmx.test.lab'}) "
        "RETURN h.null_mx AS null_mx, count{ (h)-[:MAIL_HANDLED_BY]->() } AS mx",
    )

    assert host == {"null_mx": True, "mx": 0}


def test_txt_records_are_stored_raw(store):
    store.save_scan(info(), [scanned("txt", "txt.test.lab")], replace=True)

    values = {
        row["value"]
        for row in rows(
            store,
            "MATCH (:Hostname {name: 'txt.test.lab'})-[:HAS_TXT]->(t) RETURN t.value AS value",
        )
    }

    assert "v=spf1 include:_spf.example.com ~all" in values
    assert "part one part two" in values
    assert len(values) == 5


def test_subdomain_is_linked_to_its_apex(store):
    store.save_scan(
        info(hostnames=2),
        [
            scanned("ok", "ok.test.lab", apex="test.lab"),
            scanned("nodata", "test.lab", is_input=False, apex="test.lab"),
        ],
        replace=True,
    )

    link = single(
        store,
        "MATCH (h:Hostname)-[:SUBDOMAIN_OF]->(a:Hostname) "
        "RETURN h.name AS name, a.name AS apex, a.is_apex AS is_apex, a.is_input AS apex_is_input",
    )

    assert link == {
        "name": "ok.test.lab",
        "apex": "test.lab",
        "is_apex": True,
        "apex_is_input": False,
    }
    # The apex is also a property, so queries can group hostnames by organisation.
    assert single(store, "MATCH (h:Hostname {name: 'ok.test.lab'}) RETURN h.apex AS apex") == {
        "apex": "test.lab"
    }


def test_failed_lookups_are_listed(store):
    store.save_scan(info(), [scanned("dangling-out", "dangling-out.test.lab")], replace=True)

    host = single(
        store,
        "MATCH (h:Hostname {name: 'dangling-out.test.lab'}) RETURN h.failed_lookups AS failed",
    )

    assert host["failed"] == ["A:SERVFAIL"]


def test_cname_loop_is_marked(store):
    store.save_scan(info(), [scanned("loop1", "loop1.test.lab")], replace=True)

    host = single(
        store,
        "MATCH (h:Hostname {name: 'loop1.test.lab'}) "
        "RETURN h.cname_loop AS loop, h.dangling AS dangling",
    )

    assert host == {"loop": True, "dangling": False}


def test_shared_address_is_one_node(store):
    store.save_scan(
        info(hostnames=2),
        [scanned("ok", "ok.test.lab"), scanned("alias", "alias.test.lab")],
        replace=True,
    )

    assert single(store, "MATCH (ip:IP {address: '192.0.2.10'}) RETURN count(ip) AS n") == {"n": 1}


def test_scan_node_records_source_time_and_resolver(store):
    store.save_scan(info(source="list.txt"), [scanned("ok", "ok.test.lab")], replace=True)

    (scan,) = store.scans()

    assert scan == ScanInfo("list.txt", DAY_1, "1.1.1.1 (default)", 1)


# ---- running again ----------------------------------------------------------------------


def test_same_scan_twice_gives_the_same_graph(store):
    scans = [
        scanned("ok", "ok.test.lab"),
        scanned("alias", "alias.test.lab"),
        scanned("txt", "txt.test.lab"),
    ]

    store.save_scan(info(), scans, replace=True)
    first = store.counts()
    store.save_scan(info(), scans, replace=True)

    assert store.counts() == first


def test_new_ingest_replaces_the_previous_file(store):
    store.save_scan(info(source="list1.txt"), [scanned("ok", "ok.test.lab")], replace=True)
    store.save_scan(
        info(source="list2.txt", at=DAY_2), [scanned("mail", "mail.test.lab")], replace=True
    )

    names = [
        row["name"]
        for row in rows(store, "MATCH (h:Hostname {is_input: true}) RETURN h.name AS name")
    ]

    assert names == ["mail.test.lab"]
    assert [s.source for s in store.scans()] == ["list2.txt"]


def test_append_keeps_both_files(store):
    store.save_scan(info(source="list1.txt"), [scanned("ok", "ok.test.lab")], replace=True)
    store.save_scan(
        info(source="list2.txt", at=DAY_2), [scanned("mail", "mail.test.lab")], replace=False
    )

    hosts = rows(
        store,
        "MATCH (h:Hostname {is_input: true}) "
        "RETURN h.name AS name, h.scanned_at AS at ORDER BY name",
    )

    assert [(h["name"], h["at"].to_native()) for h in hosts] == [
        ("mail.test.lab", DAY_2),
        ("ok.test.lab", DAY_1),
    ]
    assert [s.source for s in store.scans()] == ["list1.txt", "list2.txt"]


def test_rescan_removes_records_that_disappeared_and_their_orphans(store):
    # Day 1: the name resolves to two addresses. Day 2: it is gone.
    store.save_scan(info(), [scanned("ok", "ok.test.lab")], replace=True)
    store.save_scan(info(at=DAY_2), [answered("ok.test.lab", "NXDOMAIN")], replace=False)

    host = single(
        store,
        "MATCH (h:Hostname {name: 'ok.test.lab'}) "
        "RETURN h.status AS status, count{ (h)-->() } AS outgoing",
    )

    assert host == {"status": "NXDOMAIN", "outgoing": 0}
    assert store.counts().nodes.get("IP", 0) == 0


def test_rescan_keeps_nodes_other_hostnames_still_point_to(store):
    store.save_scan(
        info(hostnames=2),
        [scanned("ok", "ok.test.lab"), scanned("alias", "alias.test.lab")],
        replace=True,
    )
    # alias.test.lab is rescanned and no longer exists; ok.test.lab still owns the IPs.
    store.save_scan(info(at=DAY_2), [answered("alias.test.lab", "NXDOMAIN")], replace=False)

    ips = rows(
        store, "MATCH (:Hostname {name: 'ok.test.lab'})-[:RESOLVES_TO]->(ip) RETURN ip.address AS a"
    )

    assert len(ips) == 3


@pytest.mark.parametrize("own_scan_first", [True, False])
def test_a_name_scanned_itself_keeps_its_own_status_when_it_is_also_a_chain_link(
    store, own_scan_first
):
    # The chain from xdangling.test.lab says gone.other.lab is NXDOMAIN. Scanned directly
    # it answered NOERROR; its own scan wins, whichever is written first.
    own = answered("gone.other.lab", "NOERROR")
    chain = scanned("unbound-xdangling", "xdangling.test.lab")
    scans = [own, chain] if own_scan_first else [chain, own]

    store.save_scan(info(hostnames=2), scans, replace=True)

    assert single(store, "MATCH (h:Hostname {name: 'gone.other.lab'}) RETURN h.status AS s") == {
        "s": "NOERROR"
    }


def test_schema_can_be_created_again(store):
    store.ensure_schema()
    store.ensure_schema()

    names = {row["name"] for row in rows(store, "SHOW CONSTRAINTS YIELD name RETURN name")}

    assert {"hostname_name", "ip_address", "txt_key"} <= names


def test_counts_by_label_and_relationship_type(store):
    store.save_scan(info(), [scanned("alias", "alias.test.lab")], replace=True)

    counts = store.counts()

    assert counts.nodes == {"Hostname": 2, "IP": 3, "Scan": 1}
    assert counts.relationships == {"CNAME_TO": 1, "RESOLVES_TO": 3}
