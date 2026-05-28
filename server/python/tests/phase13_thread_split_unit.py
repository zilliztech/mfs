"""Phase 13 — message_stream thread_aggregate sub-chunking helper unit. No network/keys.

_split_thread() must respect message boundaries (never split mid-message), produce a
single chunk for short threads, multiple sub-chunks with overlap for long ones, and stay
under the configured size cap on every sub-chunk."""
from mfs_server.engine.engine import _split_thread, _THREAD_MAX_CHARS, _THREAD_OVERLAP_MESSAGES

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def main():
    # 1. empty -> empty
    check("empty thread -> []", _split_thread([]) == [])

    # 2. short thread (well under cap) -> single chunk covering all messages
    short = [f"msg{i}: hello there" for i in range(5)]
    sub = _split_thread(short)
    check("short thread -> 1 sub-chunk", len(sub) == 1)
    check("short thread covers all msgs (0..4)", sub[0][0] == 0 and sub[0][1] == 4)
    check("short thread sub-chunk contains every message body",
          all(f"msg{i}: hello" in sub[0][2] for i in range(5)))

    # 3. long thread -> multiple sub-chunks
    long_msgs = [f"msg{i:03d}: " + ("x" * 400) for i in range(20)]   # each ~408 chars
    sub = _split_thread(long_msgs, max_chars=1500, overlap=2)
    check("long thread splits into multiple sub-chunks", len(sub) >= 4)
    check("every sub-chunk <= max_chars (allowing 1 msg overflow)",
          all(len(t) <= 1500 + 410 for _, _, t in sub))         # 1 msg can push slightly past cap
    check("sub-chunks cover the full thread (start at 0, end at 19)",
          sub[0][0] == 0 and sub[-1][1] == 19)

    # 4. overlap: adjacent sub-chunks share the last N rendered messages
    for i in range(len(sub) - 1):
        prev_end = sub[i][1]
        next_start = sub[i + 1][0]
        check(f"sub-chunk {i+1} starts {_THREAD_OVERLAP_MESSAGES} msgs into the previous one "
              f"(next_start={next_start}, prev_end={prev_end})",
              next_start == prev_end - _THREAD_OVERLAP_MESSAGES + 1)

    # 5. a single huge message that itself exceeds the cap still produces one chunk
    huge = ["BIG: " + ("y" * 5000)]
    sub = _split_thread(huge)
    check("single oversized message -> 1 sub-chunk (no mid-message split)", len(sub) == 1)
    check("oversized message stays intact", sub[0][2].startswith("BIG: y"))

    passed = sum(results)
    print(f"\n{'='*46}\n  thread_split: {passed}/{len(results)} checks passed "
          f"(MAX={_THREAD_MAX_CHARS}, OVERLAP={_THREAD_OVERLAP_MESSAGES})")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
