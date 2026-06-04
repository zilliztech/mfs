# SDK smoke tests

End-to-end checks that each generated SDK can actually drive a live mfs-server
(search→envelope, ls, cat, status, error mapping). These are **test harnesses, not
package entry points or package availability evidence**.

The scripts hard-code `http://127.0.0.1:8765`. That is the generated/smoke
default, not the normal `mfs-server run` / `mfs-server api` default
(`http://127.0.0.1:13619`). Run them only after starting a test server on
`127.0.0.1:8765` and adding a small fixture.

The current scripts set only the generated client base URL. They do not set
`Authorization: Bearer <token>`, so use an intentionally open test server or
extend the harness before running against an auth-protected server.

- python:     `cd python && uv pip install -e ../../python && python smoke_test.py`
- typescript: `(cd ../typescript && npm i && npm run build) && node typescript/smoke_test.cjs`
