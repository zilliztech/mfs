# SDK smoke tests (not published)

End-to-end checks that each generated SDK can actually drive a live mfs-server
(search→envelope, ls, cat, status, error mapping). These are **test harnesses, not
shipped artifacts** — only `sdks/{python,typescript,go,java}` are published.

Run against a server on `127.0.0.1:8765` (start one + `mfs add` a small dir first):

- python:     `cd python && uv pip install -e ../../python && python smoke_test.py`
- typescript: `(cd ../typescript && npm i && npm run build) && node typescript/smoke_test.cjs`
- go:         `cd go && go run .`            # go.mod `replace`s the SDK at ../../go
- java:       `(cd ../java && mvn -q -DskipTests install) && cd java && mvn -q compile && \`
              `  java -cp "target/classes:$(mvn -q dependency:build-classpath -Dmdep.outputFile=/dev/stdout)" Smoke`
