# The graph holds one Scan: each `ingest` replaces it

The graph is a snapshot of the latest Scan, not a history. By default `ingest` clears the graph and writes the new Scan, so every query answers about one input file at one known time. `ingest --append` keeps what is there and adds or replaces only the Hostnames it scans, for building one graph from several files.

We chose this over keeping Scan history because history adds a time dimension to every node, relationship and Investigation Query, which is beyond the assignment.

We first replaced records per Hostname only. That left Hostnames from an earlier file in the graph, so a query after scanning `list2.txt` could mix in results from `list1.txt`, scanned on another day. Replacing the whole graph removes that mix.

We keep the Neo4j data on a Docker volume rather than discarding it when containers stop: `ingest` and `query` are separate commands that must see the same data, and a restart of Docker should not silently empty the graph. `docker compose down -v` deletes it.

## Consequences

- Ingesting the same file twice yields the same graph.
- The graph records which file was scanned and when, and query output shows it, so stale data is visible.
- "What changed since last week" cannot be answered. Adding history later means changing the graph model and every query, so this is the decision to revisit first if the tool grows.
