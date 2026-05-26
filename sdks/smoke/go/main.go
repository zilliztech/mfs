package main

import (
	"context"
	"fmt"
	"os"
	"strings"

	mfssdk "github.com/zilliztech/mfs-sdk-go"
)

var pass, total int

func check(name string, cond bool) {
	total++
	mark := "FAIL"
	if cond {
		pass++
		mark = "OK"
	}
	fmt.Printf("  [%s] %s\n", mark, name)
}

func main() {
	cfg := mfssdk.NewConfiguration()
	cfg.Servers = mfssdk.ServerConfigurations{{URL: "http://127.0.0.1:8765"}}
	c := mfssdk.NewAPIClient(cfg)
	ctx := context.Background()

	info, _, err := c.ServerAPI.GetServerInfo(ctx).Execute()
	check("getServerInfo version 0.4.0", err == nil && info.GetVersion() == "0.4.0")

	st, _, err := c.ServerAPI.Status(ctx).Execute()
	check("status lists >=1 connector", err == nil && len(st.Connectors) >= 1)

	res, _, err := c.RetrievalAPI.Search(ctx).Q("single sign-on login").TopK(3).Execute()
	check("search returns results", err == nil && len(res.Results) >= 1)
	if len(res.Results) >= 1 {
		top := res.Results[0]
		check("envelope: source is auth.md", strings.HasSuffix(top.GetSource(), "auth.md"))
		check("envelope: lines [start,end]", len(top.GetLines()) == 2)
		check("envelope: content non-empty", top.GetContent() != "")
	}

	ls, _, err := c.BrowseAPI.Ls(ctx).Path("/tmp/mfs_sdk_fixture").Execute()
	names := map[string]bool{}
	if err == nil {
		for _, e := range ls.Entries {
			names[e.Name] = true
		}
	}
	check("ls lists auth.md + billing.md", names["auth.md"] && names["billing.md"])

	cat, _, err := c.BrowseAPI.Cat(ctx).Path("/tmp/mfs_sdk_fixture/auth.md").Execute()
	check("cat returns SSO content", err == nil && cat != nil)

	_, httpResp, err := c.BrowseAPI.Cat(ctx).Path("/tmp/mfs_sdk_fixture").Execute()
	check("cat dir -> 400", err != nil && httpResp != nil && httpResp.StatusCode == 400)

	fmt.Printf("\n  Go SDK: %d/%d checks passed\n", pass, total)
	if pass != total {
		os.Exit(1)
	}
}
