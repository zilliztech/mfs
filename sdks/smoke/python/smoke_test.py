"""Python SDK smoke test against a live mfs-server (127.0.0.1:8765).
Exercises the generated typed client end-to-end: server info, add, search
(envelope), ls, cat, status. Run after starting the server + adding the fixture.
"""
import sys

import mfs_sdk
from mfs_sdk.rest import ApiException

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")


cfg = mfs_sdk.Configuration(host="http://127.0.0.1:8765")
with mfs_sdk.ApiClient(cfg) as api:
    server = mfs_sdk.ServerApi(api)
    retrieval = mfs_sdk.RetrievalApi(api)
    browse = mfs_sdk.BrowseApi(api)

    info = server.get_server_info()
    check("getServerInfo version 0.4.0", info.version == "0.4.0")

    st = server.status()
    check("status lists >=1 connector", len(st.connectors) >= 1)

    res = retrieval.search(q="single sign-on login", top_k=3)
    check("search returns results", len(res.results) >= 1)
    top = res.results[0]
    check("envelope: source is auth.md", top.source.endswith("auth.md"))
    check("envelope: locator carries {'lines':[start,end]} for body chunks",
          isinstance(top.locator, dict) and isinstance(top.locator.get("lines"), list)
          and len(top.locator["lines"]) == 2)
    check("envelope: content non-empty", bool(top.content))
    check("envelope: metadata typed dict", isinstance(top.metadata, dict))

    ls = browse.ls(path="/tmp/mfs_sdk_fixture")
    names = {e.name for e in ls.entries}
    check("ls lists auth.md + billing.md", {"auth.md", "billing.md"} <= names)

    cat = browse.cat(path="/tmp/mfs_sdk_fixture/auth.md")
    check("cat returns SSO content", "Single sign-on" in cat.actual_instance.content
          if hasattr(cat, "actual_instance") else "Single sign-on" in cat.content)

    try:
        browse.cat(path="/tmp/mfs_sdk_fixture")
        check("cat dir -> error", False)
    except ApiException as e:
        check("cat dir -> 400 is_directory", e.status == 400)

passed = sum(results)
print(f"\n  Python SDK: {passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
