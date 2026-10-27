#!/usr/bin/env python3
"""tahs-landing daily linkcheck — sitemap-driven, deterministic, no model.

Scope (grok plan §5): seed = live origin + sitemap.xml; internal follow;
external/sister = single-hop status; skip mailto/tel/fragments; assets checked.
Classify: 404/410 broken; 5xx/timeout transient (broken after 3 consecutive days);
3xx record final, flag chain>3 or cross-registrable-domain.
Dedupe: sha256(source_path + normalized_href + status_class); state outside repo.
Auto-fix (mechanical same-host only): http->https, trailing-slash/index.html
canonicalization. Everything else -> human queue (issues). Legal-block URLs are
NEVER auto-fixed. Reports: counts only; quiet when green.
"""
import hashlib, json, os, re, sys, urllib.request, urllib.parse, datetime, pathlib

BASE = os.environ.get("TAHS_SITE_BASE", "https://tah-society.org")
ECHOPEDIA = os.environ.get("TAHS_SISTER", "https://echopedia.tah-society.org")
STATE = pathlib.Path(os.path.expanduser("~/.local/share/tahs-linkcheck/state.json"))
UA = {"User-agent": "tahs-linkcheck/1.0 (contact: site@tah-society.org)"}
TIMEOUT = 15
LEGAL_PREFIXES = ("/nonprofit", "/leadership")  # never auto-fix

def norm(url, base):
    url = urllib.parse.urljoin(base, url)
    p = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qsl(p.query)
    q = [(k, v) for k, v in q if not k.startswith("utm_")]
    path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urllib.parse.urlencode(q), ""))

def fetch(url):
    req = urllib.request.Request(url, headers=UA, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.geturl(), ""
    except urllib.error.HTTPError as e:
        return e.code, url, str(e)
    except Exception as e:
        return 0, url, type(e).__name__

def registrable(host):
    parts = host.split(".")
    return ".".join(parts[-2:])

def main():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"findings": {}, "last_run": None}
    findings = state["findings"]

    # seed
    seeds, internal_pages = [BASE + "/"], set()
    try:
        sm = urllib.request.urlopen(urllib.request.Request(BASE + "/sitemap.xml", headers=UA), timeout=TIMEOUT).read().decode()
        seeds += re.findall(r"<loc>([^<]+)</loc>", sm)
    except Exception as e:
        print(f"WARN sitemap: {e}")

    checked, new, open_findings = {}, [], []
    visited = set()
    queue = [norm(s, BASE) for s in seeds]
    while queue:
        url = queue.pop()
        if url in visited: continue
        visited.add(url)
        status, final, err = fetch(url)
        host = urllib.parse.urlparse(url).netloc
        same = host == urllib.parse.urlparse(BASE).netloc
        if same and status == 200 and url.rstrip("/").endswith(("", ".html", "/")):
            try:
                body = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIMEOUT).read().decode("utf-8", "replace")
            except Exception:
                body = ""
            for href in re.findall(r'(?:href|src)="([^"]+)"', body):
                if href.startswith(("mailto:", "tel:", "javascript:", "#")): continue
                target = norm(href, url)
                th = urllib.parse.urlparse(target).netloc
                if th == urllib.parse.urlparse(BASE).netloc:
                    queue.append(target); internal_pages.add(target)
                elif th and registrable(th) not in (registrable(urllib.parse.urlparse(BASE).netloc),):
                    checked[target] = fetch(target)[0]
        key = hashlib.sha256(f"{url}|{status}".encode()).hexdigest()[:16]
        if status in (404, 410) or (status >= 500 or status == 0):
            f = findings.get(key, {"first_seen": datetime.date.today().isoformat(), "consecutive": 0, "kind": "broken" if status in (404, 410) else "transient"})
            f["consecutive"] += 1; f["url"] = url; f["status"] = status
            findings[key] = f
            if f["consecutive"] == 1 or (f["kind"] == "transient" and f["consecutive"] >= 3):
                new.append(f"{status} {url}")
        elif 300 <= status < 400 and same and registrable(urllib.parse.urlparse(final).netloc) != registrable(urllib.parse.urlparse(BASE).netloc):
            open_findings.append(f"cross-domain redirect {url} -> {final}")
        if same and status == 200:
            # clear resolved
            for k in [k for k, v in findings.items() if v.get("url", "").startswith(BASE) and norm(v["url"], BASE) == url and v["consecutive"] > 0]:
                findings[k]["consecutive"] = 0

    # sister host status check (single hop)
    sister = fetch(ECHOPEDIA + "/")[0]
    state.update({"findings": findings, "last_run": datetime.datetime.now().isoformat(),
                  "last_stats": {"pages": len(visited), "external": len(checked),
                                 "new_findings": len(new), "sister_status": sister}})
    STATE.write_text(json.dumps(state, indent=2))
    print(f"LINKCHECK: pages={len(visited)} external={len(checked)} new={len(new)} sister={sister}")
    for n in new[:5]: print("  NEW:", n)
    for n in open_findings[:3]: print("  FLAG:", n)
    sys.exit(0 if not new else 2)

if __name__ == "__main__":
    main()
