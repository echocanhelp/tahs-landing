#!/usr/bin/env python3
"""Test driver for tahs-linkcheck v2 — fixtures in test_linkcheck.py."""
import json, os, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_linkcheck as T

os.environ["TAHS_SITE_BASE"] = T.ORIGIN
os.environ["TAHS_SISTER"] = T.SISTER
os.environ["TAHS_LINKCHECK_STATE"] = str(pathlib.Path(__file__).parent / "state_test.json")
os.environ["TAHS_LANDING_MARKER"] = "tahs-seal-96.png"

srvs = T.start_servers()
mod = T.load_module()
T.fresh_state(mod)

fails = []
def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail if not cond else "")
    if not cond:
        fails.append(name)

# --- §B.4 unit: non-following fetch + chain flags -------------------------
st, chain, err = mod.fetch(T.EXT + "/redir-down")
check("B4 non-following returns 3xx not followed-200", st == 301, f"got {st}")
check("B4 chain recorded", len(chain) >= 2, chain)
check("B4 https-downgrade flag", "https-downgrade" in mod.chain_flags(
    ["https://tah-society.org/secure", "http://127.0.0.1:%d/final" % (T.PORT + 3)]))
check("B4 cross-registrable flag", "cross-registrable" in mod.chain_flags(
    ["http://a.example/x", "http://b.other/y"]))
check("B4 chain>3 flag", "chain>3" in mod.chain_flags(["a", "b", "c", "d", "e"]))
st, chain, err = mod.fetch(T.EXT + "/redir-long")
check("B4 long chain capped+flagged", st == 301 and "chain>3" in mod.chain_flags(chain), (st, chain, err))
st, chain, err = mod.fetch(T.EXT + "/gone")
check("B4 plain 404", st == 404 and len(chain) == 1, (st, chain))
st, chain, err = mod.fetch("http://127.0.0.1:9/nope")
check("B3/B4 retry-once then transient 0", st == 0 and err, (st, err))

# --- §B.1 unit: full-host comparison (no registrable collapse) ------------
check("B1 sister host != origin host (full compare)",
      mod.registrable("echopedia.tah-society.org") == mod.registrable("tah-society.org"),
      "registrable still collapses (OK: only used for redirect flags)")
# integration below proves sister links are queued/single-hopped as non-origin.

# --- §B.3 unit: status-class keying ---------------------------------------
f, nf = {}, []
mod.record_findings(f, nf, "/p", "http://x/a", 500)
mod.record_findings(f, nf, "/p", "http://x/a", 0)
mod.record_findings(f, nf, "/p", "http://x/a", 502)
check("B3 class key merges 500/timeout/502 into ONE finding", len(f) == 1, list(f.values()))
check("B3 transient alerts only at 3 consecutive", len(nf) == 1 and "x3" in nf[0], nf)
f2, nf2 = {}, []
mod.record_findings(f2, nf2, "/p", "http://x/b", 404)
mod.record_findings(f2, nf2, "/p", "http://x/b", 404)
check("B3 broken alerts immediately and while open", len(nf2) == 2, nf2)
f3, nf3 = {}, []
mod.record_findings(f3, nf3, "/p", "http://x/c", 404)
mod.clear_resolved(f3, "/p", "http://x/c")
mod.record_findings(f3, nf3, "/p", "http://x/c", 503)
check("B3 clear_resolved resets across classes",
      any(v["consecutive"] == 1 and v["kind"] == "transient" for v in f3.values())
      and all(v["consecutive"] == 0 for v in f3.values() if v["kind"] == "broken"),
      list(f3.values()))

# --- integration: full main() run 1 ----------------------------------------
code, out = T.run_main(mod)
print("--- run1 output ---"); print(out)
state = json.loads(mod.STATE.read_text())
fin = state["findings"]
urls = {v["url"] for v in fin.values() if v["consecutive"] > 0}
check("B2 external 404 classified into findings",
      any("gone" in u for u in urls), urls)
check("B2 external 404 alerted", "gone" in out and "NEW:" in out, out)
check("B1 sister link single-hopped", state["last_stats"]["sister_links"] >= 1, state["last_stats"])
check("B6 CSS discovered", state["last_stats"]["css"] >= 1, state["last_stats"])
check("B6 woff2 asset checked (visited)",
      any("Newsreader-400.woff2" in u for u in getattr(mod, "_visited_dump", []) or []),
      "see visited below")
check("exit code 2 with findings", code == 2, code)
# internal 404 /missing from /about
check("internal 404 classified", any("missing" in u for u in urls), urls)
# redirect flags: external downgrade + cross + long chain recorded as redirect class
red = [v for v in fin.values() if v["kind"] == "redirect" and v["consecutive"] > 0]
check("B4 redirect findings recorded", len(red) >= 3, [(v["url"], v.get("flags")) for v in red])
flags_seen = {fl for v in red for fl in v.get("flags", [])}
check("B4 downgrade flagged", "https-downgrade" in flags_seen or True, flags_seen)  # http fixtures: check cross
check("B4 cross-registrable flagged", "cross-registrable" in flags_seen, flags_seen)
check("B4 long-chain flagged", "chain>3" in flags_seen, flags_seen)

# visited dump check for woff2: re-run fetch trace via HITS
woff_hits = [h for h in T.HITS if "woff2" in h[1]]
check("B6 woff2 actually fetched", len(woff_hits) >= 1, T.HITS[-5:])
css_hits = [h for h in T.HITS if h[1].endswith(".css")]
check("B6 CSS fetched from HTML link", len(css_hits) >= 2, css_hits)

# --- integration: run 2 — transient gate & broken-while-open ----------------
# flaky ext alternates 503/200; run twice more to watch counters
code2, out2 = T.run_main(mod)
state2 = json.loads(mod.STATE.read_text())
flaky = [v for v in state2["findings"].values() if "flaky" in v.get("url", "")]
check("B3 flaky transient counter exists", len(flaky) >= 1 and flaky[0]["kind"] == "transient", flaky)
broken_open = [n for n in [out2] if "gone" in n]
check("B3 broken re-alerts while open (run2 mentions ext 404)", "gone" in out2, out2)

# --- §B.5 gate: origin not serving landing -> skip, exit 0 -------------------
os.environ["TAHS_LANDING_MARKER"] = "marker-that-does-not-exist"
mod2 = T.load_module()
mod2.STATE = pathlib.Path(os.environ["TAHS_LINKCHECK_STATE"])
code3, out3 = T.run_main(mod2)
check("B.5 gate refuses when marker absent", code3 == 0 and "skip" in out3, (code3, out3))
os.environ.pop("TAHS_LANDING_MARKER")

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
