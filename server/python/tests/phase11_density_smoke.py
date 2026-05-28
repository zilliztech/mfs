"""Phase 11 — cat density modes (--peek/--skim). Markdown-only unit smoke.
Renamed from phase11_filter_density_smoke after index_filter (restricted AST) was deleted
along with src/mfs_server/common/filter_ast.py. No network/keys required."""
from mfs_server.engine.engine import _density_view

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def main():
    md = "# Title\nintro line\n\n## Section A\ndetails a\n\n## Section B\ndetails b\n"
    peek = _density_view(md, ".md", "peek")
    check("peek = headings only", peek == "# Title\n## Section A\n## Section B")
    skim = _density_view(md, ".md", "skim")
    check("skim adds one-line summaries", "intro line" in skim and "details a" in skim)
    passed = sum(results)
    print(f"\n{'='*46}\n  density: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
