# The graph holds only the latest Scan of each Hostname

Re-ingesting a Hostname replaces its DNS Records instead of adding a new timestamped snapshot. We chose this over keeping Scan history because history would add a time dimension to every node, relationship and Investigation Query, which is beyond the assignment. Ingesting the same file twice therefore yields the same graph.

## Consequences

- "What changed since last week" cannot be answered. Adding history later means changing the graph model and every query, so this is the decision to revisit first if the tool grows.
- Each Hostname carries a `last_scanned` timestamp so stale data is at least visible.
