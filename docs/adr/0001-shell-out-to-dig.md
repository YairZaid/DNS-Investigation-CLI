# Shell out to `dig` and parse its text output

The assignment requires that DNS records are collected with `dig`, so every Lookup runs the `dig` binary as a subprocess and parses its text output, instead of using a DNS library such as dnspython. The cost is a text parser that depends on dig's output format and a hard dependency on the binary; we contain both behind a single collection module and pin the dig version through the Docker image.

## Consequences

- The tool is run through Docker by default, because `dig` is not present on Windows.
- The runner is injected, so tests replace it with recorded dig output and never touch the network.
- Hostnames are validated before they reach the command line, and arguments are passed as a list, never through a shell.
