// TypeScript SDK smoke test (compiled CJS build) vs live mfs-server (127.0.0.1:8765).
const { Configuration, ServerApi, RetrievalApi, BrowseApi } = require("../../typescript/dist/index.js");

let pass = 0, total = 0;
function check(name, cond) { total++; if (cond) pass++; console.log(`  [${cond ? "OK" : "FAIL"}] ${name}`); }

(async () => {
  const cfg = new Configuration({ basePath: "http://127.0.0.1:8765" });
  const server = new ServerApi(cfg), retrieval = new RetrievalApi(cfg), browse = new BrowseApi(cfg);

  const info = await server.getServerInfo();
  check("getServerInfo version 0.4.0", info.version === "0.4.0");
  const st = await server.status();
  check("status lists >=1 connector", st.connectors.length >= 1);
  const res = await retrieval.search({ q: "single sign-on login", topK: 3 });
  check("search returns results", res.results.length >= 1);
  const top = res.results[0];
  check("envelope: source is auth.md", top.source.endsWith("auth.md"));
  check("envelope: lines [start,end]", Array.isArray(top.lines) && top.lines.length === 2);
  check("envelope: content non-empty", !!top.content);
  const ls = await browse.ls({ path: "/tmp/mfs_sdk_fixture" });
  const names = new Set(ls.entries.map((e) => e.name));
  check("ls lists auth.md + billing.md", names.has("auth.md") && names.has("billing.md"));
  const cat = await browse.cat({ path: "/tmp/mfs_sdk_fixture/auth.md" });
  check("cat returns SSO content", JSON.stringify(cat).includes("Single sign-on"));
  try { await browse.cat({ path: "/tmp/mfs_sdk_fixture" }); check("cat dir -> error", false); }
  catch (e) { check("cat dir -> error (4xx)", (e?.response?.status || 400) === 400); }

  console.log(`\n  TypeScript SDK: ${pass}/${total} checks passed`);
  process.exitCode = pass === total ? 0 : 1;
})();
