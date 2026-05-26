import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.api.ServerApi;
import io.zilliz.mfs.api.RetrievalApi;
import io.zilliz.mfs.api.BrowseApi;
import io.zilliz.mfs.model.*;
import java.util.*;

public class Smoke {
    static int pass = 0, total = 0;
    static void check(String name, boolean cond) {
        total++; if (cond) pass++;
        System.out.println("  [" + (cond ? "OK" : "FAIL") + "] " + name);
    }
    public static void main(String[] args) throws Exception {
        ApiClient client = new ApiClient();
        client.setBasePath("http://127.0.0.1:8765");
        ServerApi server = new ServerApi(client);
        RetrievalApi retrieval = new RetrievalApi(client);
        BrowseApi browse = new BrowseApi(client);

        ServerInfo info = server.getServerInfo();
        check("getServerInfo version 0.4.0", "0.4.0".equals(info.getVersion()));

        StatusResponse st = server.status();
        check("status lists >=1 connector", st.getConnectors().size() >= 1);

        SearchResponse res = retrieval.search("single sign-on login", null, "hybrid", 3, false);
        check("search returns results", res.getResults().size() >= 1);
        ResultEnvelope top = res.getResults().get(0);
        check("envelope: source is auth.md", top.getSource().endsWith("auth.md"));
        check("envelope: lines [start,end]", top.getLines() != null && top.getLines().size() == 2);
        check("envelope: content non-empty", top.getContent() != null && !top.getContent().isEmpty());

        LsResponse ls = browse.ls("/tmp/mfs_sdk_fixture");
        Set<String> names = new HashSet<>();
        for (LsEntry e : ls.getEntries()) names.add(e.getName());
        check("ls lists auth.md + billing.md", names.contains("auth.md") && names.contains("billing.md"));

        CatResponse cat = browse.cat("/tmp/mfs_sdk_fixture/auth.md", null, false);
        check("cat returns SSO content", cat.getContent().contains("Single sign-on"));

        try {
            browse.cat("/tmp/mfs_sdk_fixture", null, false);
            check("cat dir -> error", false);
        } catch (ApiException e) {
            check("cat dir -> 400", e.getCode() == 400);
        }

        System.out.println("\n  Java SDK: " + pass + "/" + total + " checks passed");
        System.exit(pass == total ? 0 : 1);
    }
}
