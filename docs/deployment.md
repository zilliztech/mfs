# Deployment

During the v0.4 beta, the CLI is the published artifact and the server is
typically run from source or from a local Docker image.

## Development server

```bash
cd server/python
uv sync --extra all-connectors
uv run mfs-server run
```

## Docker server with host CLI

A useful smoke-test topology is:

- Docker runs `mfs-server`
- the host runs the Rust CLI
- server state lives in a Docker volume
- the CLI talks to the server over HTTP

This avoids polluting the host Python environment and exercises the real
client/server upload path.

## Kubernetes and manifests

Deployment assets live under:

- `deployments/docker/`
- `deployments/compose/`
- `deployments/helm/`

These pages will be expanded once the server packaging and stable release flow
settle.
