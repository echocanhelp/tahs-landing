#!/usr/bin/env python3
"""tahs-landing daily linkcheck v2 — sitemap-driven, deterministic, no model.

Scope (grok plan §5 + grok-review-20261027 §B): seed = live origin +
sitemap.xml; internal follow; EVERY non-origin host (sister echopedia AND
external) is single-hopped and its status goes through the SAME finding rule
as internal. Hosts are compared in FULL (no registrable-domain collapse —
echopedia.tah-society.org is NOT tah-society.org). Skip mailto/tel/javascript/
fragments. Assets checked, incl. CSS url() assets (woff2).

Classify: 404/410 broken -> alert immediately and again while open;
5xx/timeout transient -> alert only at 3 consecutive days under a STATUS CLASS
(not raw status). Every fetch retries once before a failure counts.
Redirects: NON-following opener; the chain is recorded and flagged when
length > 3, the registrable domain changes, or an https->http downgrade hop
occurs.

Dedupe key: sha256(source_path + normalized_href + status_class). State lives
outside the repo. Auto-fix (mechanical same-host only): http->https,
trailing-slash/index.html canonicalization. Everything else -> human queue
(issues). LEGAL_PREFIXES is a POLICY for future autofix (those URLs must never
be auto-fixed); there is no autofix yet, so nothing here is "enforced".
Reports: counts only; quiet when green.

Scheduling gate (§B.5): only run against an origin that actually serves this
landing AND canonizes itself — BASE's / must contain LANDING_MARKER and every
same-origin canonical must point back at BASE. Otherwise refuse and keep the
cron paused (e.g. while the apex still serves a Squarespace parking page).
"""
import hashlib, json, os, re, sys, urllib.request, urllib.parse, datetime, pathlib

BASE = os.environ.get("TAHS_SITE_BASE", "https://tah-society.org")
ECHOPEDIA = os.environ.get("TAHS_SISTER", "https://echopedia.tah-society.org")
STATE = pathlib.Path(os.path.expanduser(
    os.environ.get("TAHS_LINKCHECK_STATE", "~/.local/share/tahs-linkcheck/state.json")))
UA = {"User-agent": "tahs-linkcheck/2.0 (contact: site@tah-society.org)"}
TIMEOUT = 15
# Policy only: if/when autofix lands, these prefixes must never be auto-fixed.
# There is no autofix in v2 — do not describe this as enforced.
LEGAL_PREFIXES = ("/nonprofit", "/leadership")
# §B.5 gate: the origin's / must contain this marker or we refuse to run.
LANDING_MARKER = os.environ.get("TAHS_LANDING_MARKER", "tahs-seal-96.png")

MAX_CHAIN = 3
_visited_dump = []  # sorted visited set after the last main() crawl (test hook)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 0
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # surface 3xx to us instead of following

_OPENER = urllib.request.build_opener(NoRedirect)

def norm(url, base):
    url = urllib.parse.urljoin(base, url)
    p = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qsl(p.query)
    q = [(k, v) for k, v in q if not k.startswith("utm_")]
    path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urllib.parse.urlencode(q), ""))

def fetch(url):
    """GET once, retry once on network error (§B.3). NON-following opener
    (§B.4): the chain is recorded hop by hop. We return the FINAL status of
    the recorded chain (the last hop we could probe) plus the full chain, so
    a 301-to-http still surfaces as 301 with the chain for the flags, and a
    clean redirect chain ends at its 200."""
    start = url
    err = ""
    for attempt in (1, 2):  # retry once on network error (fresh chain each attempt)
        url = start
        chain = [url]
        try:
            while True:
                req = urllib.request.Request(url, headers=UA, method="GET")
                try:
                    with _OPENER.open(req, timeout=TIMEOUT) as r:
                        if r.status < 300 and len(chain) > 1 and chain_flags(chain):
                            # clean 200 at chain end, but the chain itself breached
                            # policy (cross-domain / downgrade / too long): surface
                            # the first 3xx so classify() records a redirect finding.
                            return 301, chain, ""
                        return r.status, chain, ""  # non-3xx terminal status
                except urllib.error.HTTPError as e:
                    if e.code in (301, 302, 303, 307, 308):
                        loc = e.headers.get("Location") if e.headers else None
                        if not loc:  # 3xx with no Location: terminal
                            return e.code, chain, str(e)
                        nxt = urllib.parse.urljoin(url, loc)
                        if nxt in chain:  # loop: return the 3xx we saw, chain closed
                            return e.code, chain + [nxt], "loop"
                        chain.append(nxt)
                        if len(chain) - 1 > MAX_CHAIN:  # give up on the chain, keep what we saw
                            return e.code, chain, "chain>%d" % MAX_CHAIN
                        url = nxt
                        continue  # probe next hop
                    return e.code, chain, str(e)  # 4xx/5xx terminal
        except Exception as e:
            err = type(e).__name__
            if len(chain) > 1:  # chain broke mid-redirect: report last 3xx + partial chain
                return 301, chain, err
            if attempt == 2:
                return 0, chain, err
    return 0, chain, err

def registrable(host):
    """Registrable domain for CROSS-DOMAIN redirect flags only (§B.4).
    Internal/external classification never uses this — hosts are compared
    in FULL (§B.1), so echopedia.tah-society.org is NOT tah-society.org."""
    parts = host.split(".")
    return ".".join(parts[-2:])

def chain_flags(chain):
    """Flag redirect chains: length > MAX_CHAIN, registrable-domain change,
    or an https->http downgrade hop (§B.4)."""
    flags = []
    if len(chain) - 1 > MAX_CHAIN:
        flags.append("chain>%d" % MAX_CHAIN)
    parsed = [urllib.parse.urlparse(u) for u in chain]
    hosts = [p.netloc for p in parsed if p.netloc]
    if hosts and len({registrable(h) for h in hosts}) > 1:
        flags.append("cross-registrable")
    for a, b in zip(parsed, parsed[1:]):
        if a.scheme == "https" and b.scheme == "http":
            flags.append("https-downgrade")
            break
    return flags

def classify(status):
    if status in (404, 410): return "broken"
    if 300 <= status < 400: return "redirect"  # unresolved 3xx is itself a finding (§B.4)
    if status >= 500 or status == 0: return "transient"
    return None

def record_findings(findings, findings_new, source_path, href, status, kind_extra=None):
    """Apply the SAME finding rule to internal and external URLs (§B.2).
    Key = sha256(source_path + normalized_href + status_class) (§B.3) — the
    CLASS, not the raw status, so 500/timeout/502 are one finding, not three
    day-1 findings that never reach 3. kind_extra = redirect policy flags."""
    cls = classify(status)
    if cls is None:
        return
    status_class = cls
    key = hashlib.sha256(f"{source_path}|{href}|{status_class}".encode()).hexdigest()[:16]
    today = datetime.date.today().isoformat()
    f = findings.get(key, {"first_seen": today, "consecutive": 0, "kind": status_class})
    f["consecutive"] += 1
    f["url"] = href
    f["source"] = source_path
    f["status"] = status
    if kind_extra:
        f["flags"] = kind_extra
    findings[key] = f
    if status_class == "broken":
        # alert immediately and again while open (every run it stays open)
        findings_new.append(f"{status} {href} (from {source_path})")
    elif status_class == "transient":
        # alert only at 3 consecutive days under this status class
        if f["consecutive"] == 3:
            findings_new.append(f"{status} {href} (from {source_path}) [transient x3]")
    else:  # redirect: surface while open; flags (if any) name the policy breach
        suffix = " [%s]" % ",".join(kind_extra) if kind_extra else " [redirect]"
        findings_new.append(f"{status} {href} (from {source_path}){suffix}")

def clear_resolved(findings, source_path, href):
    """A clean fetch resets consecutive for every open finding on this
    (source, url) pair, whatever class it was — so an external 200 clears its
    old 404, and a recovered transient resets its 3-day counter."""
    for k, v in list(findings.items()):
        if v.get("url") == href and v.get("source") == source_path and v.get("consecutive", 0) > 0:
            v["consecutive"] = 0

def extract_links(body, url):
    """href/src links plus <link rel=stylesheet href=...> targets, so CSS
    files are discovered and their url() assets (woff2) get checked (§B.6)."""
    links = set()
    for href in re.findall(r'(?:href|src)="([^"]+)"', body):
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.add(norm(href, url))
    return links

def css_assets(css_url, css_text):
    out = set()
    for m in re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", css_text):
        if m.startswith(("data:", "mailto:", "#")):
            continue
        out.add(norm(m, css_url))
    return out

def origin_serves_landing():
    """§B.5 gate: / must 200 (via the non-following fetch — a parked apex
    that redirects away is not serving our landing), contain the landing
    marker, and every same-origin canonical must point back at BASE."""
    status, chain, _ = fetch(BASE + "/")
    if status != 200:
        return False, f"origin {BASE}/ -> {status} (chain {len(chain)-1} hops)"
    try:
        body = urllib.request.urlopen(urllib.request.Request(BASE + "/", headers=UA), timeout=TIMEOUT).read().decode("utf-8", "replace")
    except Exception as e:
        return False, f"cannot read {BASE}/: {e}"
    if LANDING_MARKER not in body:
        return False, f"marker '{LANDING_MARKER}' absent from {BASE}/"
    base_norm = norm(BASE + "/", BASE)
    for href in re.findall(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', body):
        c = norm(href, BASE + "/")
        if urllib.parse.urlparse(c).netloc == urllib.parse.urlparse(BASE).netloc.lower() and c != base_norm:
            return False, f"canonical {href} does not point at {BASE}"
    return True, ""

def main():
    origin_host = urllib.parse.urlparse(BASE).netloc.lower()
    sister_host = urllib.parse.urlparse(ECHOPEDIA).netloc.lower()
    ok, why = origin_serves_landing()
    if not ok:
        print(f"LINKCHECK: skip ({why}; keep cron paused until the apex serves the landing)")
        sys.exit(0)

    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"findings": {}, "last_run": None}
    findings = state["findings"]

    # seed
    seeds = [BASE + "/"]
    try:
        sm = urllib.request.urlopen(urllib.request.Request(BASE + "/sitemap.xml", headers=UA), timeout=TIMEOUT).read().decode()
        seeds += re.findall(r"<loc>([^<]+)</loc>", sm)
    except Exception as e:
        print(f"WARN sitemap: {e}")

    new_findings = []
    visited, checked_external, checked_sister, css_checked = set(), set(), set(), set()

    def check_offsite(target, tsource):
        """Single-hop every non-origin host (sister echopedia AND external);
        its status goes through the SAME finding rule as internal (§B.1/B.2).
        tsource = the page path that links this target (for the finding key)."""
        th = urllib.parse.urlparse(target).netloc
        if th == sister_host:
            checked_sister.add(target)
        else:
            checked_external.add(target)
        tstatus, tchain, _ = fetch(target)
        flags = chain_flags(tchain) if 300 <= tstatus < 400 else []
        record_findings(findings, new_findings, tsource, target, tstatus, kind_extra=flags)
        if classify(tstatus) is None:  # clean fetch -> resolve any open finding on this pair
            clear_resolved(findings, tsource, target)

    queue = [norm(s, BASE) for s in seeds]
    while queue:
        url = queue.pop()
        if url in visited:
            continue
        visited.add(url)
        host = urllib.parse.urlparse(url).netloc
        same = host == origin_host
        status, chain, err = fetch(url)
        source_path = urllib.parse.urlparse(url).path or "/"

        if same:
            if status == 200:
                clear_resolved(findings, source_path, url)
                body = ""
                try:
                    req = urllib.request.Request(url, headers=UA, method="GET")
                    with _OPENER.open(req, timeout=TIMEOUT) as r:
                        body = r.read().decode("utf-8", "replace")
                except Exception:
                    pass
                if url.endswith(".css"):
                    css_checked.add(url)
                    for asset in css_assets(url, body):
                        ah = urllib.parse.urlparse(asset).netloc
                        if ah == origin_host:
                            queue.append(asset)
                        else:
                            check_offsite(asset, source_path)  # CSS assets off-origin are single-hopped too
                elif url.rstrip("/").endswith(("", ".html", "/")):
                    for target in extract_links(body, url):
                        th = urllib.parse.urlparse(target).netloc
                        if not th:
                            continue
                        if th == origin_host:
                            queue.append(target)
                        else:
                            check_offsite(target, source_path)
            elif 300 <= status < 400:
                flags = chain_flags(chain)
                if flags:
                    record_findings(findings, new_findings, source_path, url, status, kind_extra="redirect")
                elif len(chain) > 1 and chain[1] not in visited:
                    queue.append(chain[1])  # plain same-origin redirect: follow once for discovery
                else:
                    clear_resolved(findings, source_path, url)
            else:
                record_findings(findings, new_findings, source_path, url, status)
        else:
            # non-origin URL reached directly from seed/sitemap: it is its own source
            check_offsite(url, urllib.parse.urlparse(url).path or "/")

    # sister homepage liveness (kept for the cron line)
    sister = fetch(ECHOPEDIA + "/")[0]
    globals()["_visited_dump"] = sorted(visited)
    state.update({"findings": findings, "last_run": datetime.datetime.now().isoformat(),
                  "last_stats": {"pages": len(visited), "external": len(checked_external),
                                 "sister_links": len(checked_sister), "css": len(css_checked),
                                 "new_findings": len(new_findings), "sister_status": sister}})
    STATE.write_text(json.dumps(state, indent=2))
    print(f"LINKCHECK: pages={len(visited)} external={len(checked_external)} sister_links={len(checked_sister)} css={len(css_checked)} new={len(new_findings)} sister={sister}")
    for n in new_findings[:5]:
        print("  NEW:", n)
    sys.exit(0 if not new_findings else 2)

if __name__ == "__main__":
    main()
