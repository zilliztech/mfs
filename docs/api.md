# HTTP API

The HTTP API is the protocol boundary between the CLI, SDKs, and server.
The source of truth is `protocol/openapi.yaml`.

Major endpoint groups:

| Group | Examples |
|---|---|
| Server | `/v1/server/info`, `/v1/status` |
| Ingest | `/v1/add`, `/v1/upload`, job APIs |
| Connectors | probe, estimate, inspect, remove |
| Retrieval | search and grep |
| Browse | ls and cat |

The OpenAPI file is also used to generate the Python and TypeScript SDKs.
When the server API changes, update the spec and regenerate SDKs before
publishing.
