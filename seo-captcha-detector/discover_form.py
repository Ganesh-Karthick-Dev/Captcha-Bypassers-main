"""Phase 1A: Non-Destructive HTML Form Discovery Script.

Inspects the server-rendered HTML of the target URL to extract:
1. Form action URL & HTTP method
2. Input fields, hidden CSRF nonces, honeypots
3. CAPTCHA token field association

Does NOT submit any data or make any mutating requests.
"""
import sys
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# Ensure seo-captcha-detector is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from session import CrawlerSession


def discover_forms(target_url: str):
    print("=" * 70)
    print(f"  PHASE 1A: NON-DESTRUCTIVE FORM DISCOVERY")
    print(f"  Target: {target_url}")
    print("=" * 70)

    session = CrawlerSession(timeout=30.0)
    print("\n[*] Fetching server-rendered HTML...")
    try:
        resp = session.fetch(target_url)
    except Exception as exc:
        print(f"[!] Fetch failed: {exc}")
        return

    print(f"[*] Response: HTTP {resp.status_code} ({len(resp.text)} bytes)")

    soup = BeautifulSoup(resp.text, "html.parser")
    forms = soup.find_all("form")

    if not forms:
        print("\n" + "-" * 70)
        print("  RESULT: NO SERVER-RENDERED <form> FOUND")
        print("-" * 70)
        print("  The page does not contain standard HTML <form> tags in raw source.")
        print("  -> Forms are likely rendered dynamically by client-side JavaScript / SPA.")
        print("  -> Phase 1 (Headless HTTP form submit) is NOT APPLICABLE.")
        print("  -> Recommendation: Proceed directly to Phase 2 (In-Browser DOM driving).")
        print("=" * 70)
        return

    print(f"\n[*] Found {len(forms)} <form> element(s) in server-rendered HTML:\n")

    for idx, f in enumerate(forms, start=1):
        raw_action = f.get("action") or ""
        action_url = urljoin(target_url, raw_action)
        method = (f.get("method") or "GET").upper()
        form_id = f.get("id") or "(none)"
        form_name = f.get("name") or "(none)"
        form_class = " ".join(f.get("class", [])) if f.get("class") else "(none)"

        # Check for CAPTCHA association
        has_rc = bool(
            f.find(class_=lambda c: c and "recaptcha" in str(c).lower())
            or f.find(attrs={"data-sitekey": True})
            or "recaptcha" in str(f).lower()
        )

        print("-" * 70)
        print(f"  Form #{idx}:")
        print(f"    ID:                 {form_id}")
        print(f"    Name:               {form_name}")
        print(f"    Class:              {form_class}")
        print(f"    Action URL:         {action_url}")
        print(f"    HTTP Method:        {method}")
        print(f"    CAPTCHA Associated: {'YES' if has_rc else 'No obvious association'}")
        print()

        # Enumerate fields
        fields = []
        hidden_fields = []
        for inp in f.find_all(["input", "textarea", "select"]):
            tag = inp.name
            name = inp.get("name")
            itype = inp.get("type", "text") if tag == "input" else tag
            val = inp.get("value", "")

            if not name:
                continue

            entry = f"name='{name}' (type={itype}, default='{val}')"
            if itype == "hidden":
                hidden_fields.append((name, val))
            else:
                fields.append((name, itype, val))

        print(f"    Standard / Visible Inputs ({len(fields)}):")
        for name, itype, val in fields:
            print(f"      - {name} [{itype}]")

        print(f"    Hidden / CSRF Nonces ({len(hidden_fields)}):")
        for name, val in hidden_fields:
            # truncate long hashes/tokens for clean display
            val_preview = val[:20] + "..." if len(val) > 20 else val
            print(f"      - {name} = '{val_preview}'")

    print("\n" + "=" * 70)
    print("  Discovery Completed.")
    print("=" * 70)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://ermisglobal.com"
    discover_forms(target)
