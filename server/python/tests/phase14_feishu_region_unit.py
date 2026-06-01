"""Phase 14 — feishu connector region (feishu/lark) endpoint resolution.

Offline unit. Verifies the host-string substitution for Feishu (国内) vs Lark
(海外) and that the SDK client builder gets the right `.domain()`. Does NOT
make any real HTTP call — Lark is unverified live (we have no Lark tenant),
this just proves the wiring is symmetric.
"""

import lark_oapi as lark

from mfs_server.connectors.feishu.oauth import endpoints
from mfs_server.connectors.feishu.plugin import FeishuPlugin

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def main():
    # 1. endpoints("feishu") and endpoints("lark") return distinct, plausible host pairs
    f = endpoints("feishu")
    l = endpoints("lark")
    check("feishu accounts host", f["accounts"] == "https://accounts.feishu.cn")
    check("feishu open host", f["open"] == "https://open.feishu.cn")
    check("lark accounts host", l["accounts"] == "https://accounts.larksuite.com")
    check("lark open host", l["open"] == "https://open.larksuite.com")
    check("feishu != lark (sanity)", f["open"] != l["open"])

    # 2. default endpoints() with no arg uses feishu
    check("default region = feishu", endpoints()["open"] == "https://open.feishu.cn")

    # 3. unknown region raises
    try:
        endpoints("not-a-region")
        check("unknown region raises", False)
    except ValueError as e:
        check(f"unknown region raises: {str(e)[:50]}...", True)

    # 4. SDK domain mapping matches what lark_oapi exposes
    check(
        "_sdk_domain('feishu') = FEISHU_DOMAIN",
        FeishuPlugin._sdk_domain("feishu") == lark.FEISHU_DOMAIN,
    )
    check("_sdk_domain('lark') = LARK_DOMAIN", FeishuPlugin._sdk_domain("lark") == lark.LARK_DOMAIN)
    check(
        "unknown region falls back to feishu (not strict here)",
        FeishuPlugin._sdk_domain("xyz") == lark.FEISHU_DOMAIN,
    )

    # 5. consistency: the SDK constant matches our endpoints() open host
    check("lark.FEISHU_DOMAIN == endpoints('feishu')['open']", lark.FEISHU_DOMAIN == f["open"])
    check("lark.LARK_DOMAIN == endpoints('lark')['open']", lark.LARK_DOMAIN == l["open"])

    passed = sum(results)
    print(f"\n{'=' * 46}\n  feishu region unit: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
