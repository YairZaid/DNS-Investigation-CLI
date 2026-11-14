# Recorded dig output

Every file here is real, unedited output of `dig 9.20` for the exact command the tool runs:

```
dig @SERVER +noall +comments +question +answer +time=3 +tries=2 NAME A NAME AAAA NAME MX NAME TXT NAME CNAME
```

| Files | Source |
|---|---|
| `ok`, `alias`, `goodchain1`, `dangling-in`, `loop1`, `mail`, `nullmx`, `txt`, `nodata`, `missing` | BIND 9.20, authoritative for a lab zone `test.lab` |
| `chain1`, `dangling-out` | Same BIND; the CNAME target is outside its zone and it does not chase it |
| `unbound-*` | Unbound (a real recursive resolver) in front of two separate authoritative servers, so the CNAME crosses zones and the resolver must chase it |
| `public-example-com` | `1.1.1.1`, `example.com` |
| `docker-resolver-duplicates` | Docker Desktop's built-in resolver, `www.microsoft.com`: every record appears more than once |
| `unreachable` | An address that never answers (`192.0.2.1`), two questions |
