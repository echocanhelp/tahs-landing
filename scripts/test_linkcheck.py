#!/usr/bin/env python3
"""Fixtures for tahs-linkcheck v2 (grok-review-20261027 §B). Run: python3 test_linkcheck.py"""
import http.server, json, os, sys, threading, urllib.parse, pathlib, importlib.util

PORT = 8731
ORIGIN = f"http://127.0.0.1:{PORT}"
SISTER = f"http://127.0.0.1:{PORT + 1}"
EXT = f"http://127.0.0.1:{PORT + 2}"
HITS = []  # (port, path)

PAGES = {
    "/": """<html><head><link rel="canonical" href="{O}/">
<link rel="stylesheet" href="/assets/css/styles.css"></head>
<body><img src="/assets/img/tahs-seal-96.png">
<a href="/about">a</a><a href="{S}/people/x.html">sister</a>
<a href="{E}/ok">ext ok</a><a href="{E}/gone">ext 404</a>
<a href="{E}/flaky">ext flaky</a><a href="{E}/redir-down">downgrade</a>
<a href="{E}/redir-cross">cross</a><a href="{E}/redir-long">long</a>
<a href="mailto:x@y.z">m</a><a href="#f">f</a></body></html>""",
    "/about": """<html><head><link rel="canonical" href="{O}/about"></head>
<body><a href="/missing">gone</a><a href="/redir-same">same redirect</a></body></html>""",
    "/assets/css/styles.css": "@font-face{font-family:A;src:url('../fonts/Newsreader-400.woff2') format('woff2')} .x{background:url('/assets/img/bg.png')}",
    "/assets/fonts/Newsreader-400.woff2": "woff2bytes",
    "/assets/img/bg.png": "pngbytes",
    "/assets/img/tahs-seal-96.png": "sealbytes",
    "/redir-same": "moved",
}
SISTER_PAGES = {"/people/x.html": "<html>sister ok</html>", "/gone": "gone-sister"}
EXT_PAGES = {"/ok": "ext ok", "/redir-down": "rd", "/redir-cross": "rd", "/redir-long": "rd", "/other2": "other2"}
FLAKY = {"n": 0}

class Handler(http.server.BaseHTTPRequestHandler):
    port_tag = None
    def log_message(self, *a):
        pass
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        HITS.append((self.port_tag, path))
        base = {"origin": ORIGIN, "sister": SISTER, "ext": EXT}[self.port_tag]
        if self.port_tag == "origin":
            if path == "/sitemap.xml":
                body = "<urlset><url><loc>%s/about</loc></url></urlset>" % base
                return self._send(200, body, "application/xml")
            if path == "/missing":
                return self._send(404, "nope")
            if path == "/redir-same":
                self.send_response(301); self.send_header("Location", base + "/about")
                self.send_header("Content-Length", "0"); self.end_headers(); return
            if path in PAGES:
                body = PAGES[path].replace("{O}", base).replace("{S}", SISTER).replace("{E}", EXT)
                ct = "text/css" if path.endswith(".css") else "text/html"
                return self._send(200, body, ct)
            return self._send(404, "nf")
        if self.port_tag == "sister":
            if path in SISTER_PAGES:
                return self._send(200, SISTER_PAGES[path])
            return self._send(404, "nf-sister")
        # ext
        if path == "/gone":
            return self._send(404, "ext gone")
        if path == "/flaky":
            FLAKY["n"] += 1
            return self._send(503, "flaky") if FLAKY["n"] % 2 == 1 else self._send(200, "recovered")
        if path == "/redir-down":
            self.send_response(301); self.send_header("Location", "http://127.0.0.1:%d/final" % (PORT + 3))
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if path == "/redir-cross":
            # different port on 127.0.0.1 => different registrable() => cross-registrable flag
            self.send_response(301); self.send_header("Location", "http://127.0.0.1:%d/other2" % (PORT + 3))
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if path == "/redir-long":  # 4 hops then 200
            self.send_response(301); self.send_header("Location", EXT + "/l1")
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if path in ("/l1", "/l2", "/l3"):
            nxt = {"/l1": "/l2", "/l2": "/l3", "/l3": "/l4"}[path]
            self.send_response(301); self.send_header("Location", EXT + nxt)
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if path == "/l4":
            return self._send(200, "end")
        if path == "/other":
            return self._send(200, "other")
        if path == "/final":
            return self._send(200, "final")
        if path in EXT_PAGES:  # /ok and friends: the plain-200 ext pages
            return self._send(200, EXT_PAGES[path])
        return self._send(404, "nf")
    def _send(self, code, body, ct="text/html"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

def serve(port, tag):
    class H(Handler):
        port_tag = "ext" if tag == "ext4" else tag
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def load_module():
    spec = importlib.util.spec_from_file_location("linkcheck", pathlib.Path(__file__).parent / "linkcheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def run_main(mod):
    import io, contextlib
    buf = io.StringIO()
    code = 0
    def fake_exit(c=0):
        raise SystemExit(c)
    mod.sys.exit = fake_exit
    with contextlib.redirect_stdout(buf):
        try:
            mod.main()
        except SystemExit as e:
            code = e.code or 0
    return code, buf.getvalue()

def start_servers():
    return [serve(PORT, "origin"), serve(PORT + 1, "sister"), serve(PORT + 2, "ext"), serve(PORT + 3, "ext4")]

def fresh_state(mod):
    p = pathlib.Path(mod.STATE)
    if p.exists():
        p.unlink()
    return p
