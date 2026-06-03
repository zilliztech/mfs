# FAQ

## Is MFS a filesystem?

No. MFS exposes file-like commands over indexed sources, but it does not mount
a POSIX filesystem.

## Is the server required?

For v0.4, yes. The CLI talks to the server over HTTP. In local development the
server can run on the same machine.

## What changed from v0.3?

The old version was closer to a pure Python CLI. The current architecture has a
Rust CLI, Python server, OpenAPI protocol, generated SDKs, and optional Rust
server acceleration.

## Should agents use search or grep?

Use search for conceptual queries and grep for exact terms. Use browse commands
to verify context before editing or quoting behavior.
