#!/usr/bin/env python3
"""Render credibility strip + footer legal block from facts/tahs-public.json.

Humans edit the JSON; this script renders HTML between the
BEGIN/END FACTS_STRIP and FOOTER_LEGAL markers in every *.html.
--check (CI): exit 1 on drift instead of writing. Never scrape TEOS here.
"""
import json, re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FACTS = json.loads((ROOT / "facts/tahs-public.json").read_text())

def strip_html():
    rows = [
        ("Legal name", FACTS["legalName"]),
        ("Public name", f'{FACTS["publicName"]} / {FACTS["publicNameZh"]}'),
        ("EIN", FACTS["ein"]),
        ("IRS status", FACTS["irsStatus"]),
        ("NTEE", FACTS["ntee"]),
        ("BMF foundation code", FACTS["bmfFoundationCode"]),
        ("IRS address on file", FACTS["irsAddressOnFile"]),
        ("Registered office", FACTS["registeredOffice"]),
        ("Filings", FACTS["filings"]),
        ("President", f'{FACTS["president"]["en"]} ({FACTS["president"]["zh"]})'),
        ("Founding president", f'{FACTS["foundingPresident"]["en"]} ({FACTS["foundingPresident"]["zh"]})'),
    ]
    dl = "\n".join(f'    <div><dt>{k}</dt><dd>{v}</dd></div>' for k, v in rows)
    note = (f'  <p class="note small muted">Vendors and license reviewers: confirm the current IRS address on our record at '
            f'<a href="{FACTS["verifyUrl"]}">IRS Tax Exempt Organization Search</a> — search EIN {FACTS["ein"]}.</p>')
    return f"  <dl>\n{dl}\n  </dl>\n{note}"

def footer_html():
    lines = [
        FACTS["legalName"],
        f'EIN {FACTS["ein"]} · 501(c)(3) · {FACTS["determinationShort"]}',
        f'IRS address on file: {FACTS["irsAddressOnFile"]}',
        f'Registered office: {FACTS["registeredOffice"]}',
        f'Echopedia: <a href="{FACTS["echopediaUrl"]}">{FACTS["echopediaUrl"].split("//")[1]}</a>',
        'Contact: ' + ' · '.join(
            f'<a href="mailto:{e}">{e}</a>' for e in FACTS["contactEmails"]
        ) + f' · <a href="tel:{FACTS["contactPhone"]}">{FACTS["contactPhoneDisplay"]}</a>',
        "Determination letter and recent filings: available on request under IRS public-inspection rules.",
        f'© {FACTS["legalName"]}.',
    ]
    return "\n    " + "<br>\n    ".join(lines) + "\n  "

def render(text, name, body):
    pat = re.compile(rf"(<!--BEGIN {name}-->)(.*?)(<!--END {name}-->)", re.S)
    if not pat.search(text):
        return text, False
    new = pat.sub(lambda m: m.group(1) + "\n" + body + m.group(3), text, count=1)
    return new, new != text

def main():
    check = "--check" in sys.argv
    drift = []
    for page in sorted(ROOT.glob("*.html")):
        text = orig = page.read_text()
        for name, body in (("FACTS_STRIP", strip_html()), ("FOOTER_LEGAL", footer_html())):
            text, changed = render(text, name, body)
        if text != orig:
            drift.append(page.name)
            if not check:
                page.write_text(text)
    print(f"render_facts: {'DRIFT' if drift else 'OK'} {drift}")
    sys.exit(1 if (drift and check) else 0)

if __name__ == "__main__":
    main()
