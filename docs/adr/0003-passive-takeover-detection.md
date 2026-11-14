# Takeover detection is DNS-only by default; HTTP verification is opt-in

Some Providers (S3, GitHub Pages, Heroku) keep resolving in DNS after a resource is deleted, and only an HTTP response reveals that it is gone. By default the tool still uses DNS only and reports such Hostnames as Takeover Candidates with confidence `needs-verification`. A `--verify-http` flag sends one GET request per candidate and checks the Fingerprint's response marker.

We rejected always verifying because the tool would then contact third-party servers that the user never asked it to contact, and rejected DNS-only because it cannot confirm the S3 case at all.

## Consequences

- The tool never tries to claim a resource. It only reports.
- Results without `--verify-http` contain false positives by design, and the output says so through the confidence column.
