# Troubleshooting

## The CLI cannot reach the server

Check that the server is running and that the CLI has the right endpoint:

```bash
mfs status
echo "$MFS_API_URL"
echo "$MFS_API_TOKEN"
```

## Local paths work but client/server upload does not

If the server runs in Docker or on another host, it cannot read the client's
local filesystem directly. Use upload mode:

```bash
mfs add --upload --wait ./some-folder
```

## First indexing run is slow

The default local embedding backend may download an ONNX model on first use.
Keep the server cache or Docker volume if you want later runs to reuse it.

## Documentation build fails

Run the docs build from the repo root:

```bash
uv run --group docs mkdocs build --strict
```

Strict mode is intentional. Broken links and missing pages should fail early.
