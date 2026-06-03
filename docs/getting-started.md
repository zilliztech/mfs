# Quickstart

Install the CLI from the published release:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/zilliztech/mfs/releases/download/v0.4.0-beta.2/mfs-cli-installer.sh | sh
```

Or install from crates.io:

```bash
cargo install mfs-cli --version 0.4.0-beta.2
```

Run the server from source during the beta:

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs/server/python
uv sync
uv run mfs-server setup
uv run mfs-server run
```

In another shell, point the CLI at the server and try a small corpus:

```bash
mfs status
mfs add --wait ./some-folder
mfs search "how is this project configured" ./some-folder --top-k 5
mfs ls ./some-folder
```

For true client/server behavior where the server cannot read host paths,
use upload mode:

```bash
mfs add --upload --wait ./some-folder
```
