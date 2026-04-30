# CLI Reference

## `mfs add`

Index local files and directories.

```bash
mfs add <path...> [--sync] [--force] [--watch] [--exclude PATTERN]
```

Common options:

| Option | Meaning |
| --- | --- |
| `--sync` | embed in the foreground |
| `--force` | force full hash comparison, skipping mtime shortcuts |
| `--watch` | watch for changes and reindex |
| `--interval` | debounce interval such as `1500ms`, `10s`, `1m` |
| `--exclude` | glob pattern to exclude; can be repeated |
| `--summarize` | auto-generate LLM summaries for text files |
| `--describe` | auto-generate VLM descriptions for images |

## `mfs search`

Semantic search across indexed files.

```bash
mfs search <query> <path>
mfs search <query> --all
```

Options:

| Option | Meaning |
| --- | --- |
| `--top-k` | number of results, default `10` |
| `--mode` | `hybrid`, `semantic`, or `keyword` |
| `--json` | emit structured Hit output |
| `--quiet` | show one line per result |
| `--path` | compatibility alias for positional path |

Plain stdin is searched as temporary text. Headered `mfs cat` output scopes the
search to the original source file.

## `mfs grep`

Full-text search with smart routing.

```bash
mfs grep <pattern> <path>
mfs grep <pattern> --all
```

Options:

| Option | Meaning |
| --- | --- |
| `-C` | context lines before and after |
| `-i` | case-insensitive search |
| `--json` | emit structured Hit output |
| `--quiet` | compact output |

Line numbers are shown by default. `-n` is accepted for compatibility and emits
a deprecation warning.

## `mfs ls`

List directory contents with compact summaries.

```bash
mfs ls [path] [--peek|--skim|--deep] [-W N] [-H N] [-D N]
```

Default preset is `--skim`.

## `mfs tree`

Show a recursive directory tree with optional summaries.

```bash
mfs tree [path] [-L N] [--peek|--skim|--deep] [-W N] [-H N] [-D N]
```

`-L` controls directory recursion depth.

## `mfs cat`

Read a file or show a density-controlled overview.

```bash
mfs cat <file>
mfs cat --skim <file>
mfs cat -n 40:90 <file>
```

Options:

| Option | Meaning |
| --- | --- |
| `--peek`, `--skim`, `--deep` | density preset |
| `-W`, `-H`, `-D` | custom density budget |
| `-n` | line range, such as `40:90` |
| `--no-frontmatter` | strip YAML/TOML frontmatter |
| `--meta` | force `::mfs:` pipe headers |
| `--no-meta` | omit pipe headers |
| `--json` | emit structured Hit output |
| `--no-line-numbers` | omit line numbers in density views |

## `mfs status`

Show indexing status and progress.

```bash
mfs status
mfs status --json
mfs status --needs-summary
```

## `mfs remove`

Remove a file or directory from the index.

```bash
mfs remove <target>
```

## `mfs config`

Manage `~/.mfs/config.toml`.

```bash
mfs config path
mfs config init
mfs config show
mfs config get embedding.provider
mfs config set embedding.provider onnx
```
