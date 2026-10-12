"""SEO CAPTCHA Detection & Resolution CLI.

Uses the full architecture: ChallengeClassifier -> ChallengeHandler -> SolverAdapters.
Run with:  python detect_captcha.py "https://target-url.com"
"""
import logging
import sys

from models import AccessState, ChallengeType
from fetcher import SEOFetcher

# Enable structured logging so every pipeline step is visible
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def run_detection(
    target_url: str,
    solver_url: str = "http://127.0.0.1:8877",
    max_retries: int = 2,
    timeout: float = 300.0,
    solve_dom_widgets: bool = True,
    print_output: bool = True,
):
    """Executes the full CAPTCHA detection and resolution pipeline for a target URL."""
    if print_output:
        print("=" * 60)
        print(f"  Target: {target_url}")
        print("=" * 60)

    fetcher = SEOFetcher(
        solver_url=solver_url,
        max_retries=max_retries,
        timeout=timeout,
        solver_timeout=timeout,
        solve_dom_widgets=solve_dom_widgets,
    )
    result = fetcher.fetch_page(target_url)

    if print_output:
        # -- Classification --
        print()
        print("-" * 60)
        print("  CLASSIFICATION")
        print("-" * 60)

        if result.classification:
            c = result.classification
            print(f"  Access:      {c.access.value}")
            print(f"  Status Code: {c.status_code}")
            print(f"  Final URL:   {c.final_url}")
            ch = c.challenge
            print(f"  Challenge:")
            print(f"    present:           {ch.present}")
            print(f"    type:              {ch.type.value}")
            print(f"    layer:             {ch.layer.value if ch.layer else 'n/a'}")
            print(f"    adapter_available: {ch.adapter_available}")
            print(f"    supported:         {ch.supported}")
            if ch.sitekey:
                print(f"    sitekey:   {ch.sitekey}")
            if ch.note:
                print(f"    note:      {ch.note}")
            if ch.action_required:
                print(f"    action:    {ch.action_required}")
        else:
            print("  (no classification -- network error)")

        # -- Resolution --
        if result.resolution:
            print()
            print("-" * 60)
            print("  RESOLUTION")
            print("-" * 60)
            r = result.resolution
            print(f"  Success:        {r.success}")
            print(f"  Status:         {r.status}")
            print(f"  Challenge Type: {r.challenge_type.value}")
            if r.elapsed is not None:
                print(f"  Elapsed:        {r.elapsed}s")
            print(f"  Has Token:      {r.has_token}")
            print(f"  Has Clearance:  {r.has_clearance}")
            if r.error:
                print(f"  Error:          {r.error}")

        # -- Final Result --
        print()
        print("-" * 60)
        print("  RESULT")
        print("-" * 60)
        print(f"  Pipeline Status: {result.status}")
        print(f"  Retries Used:    {result.retries_used}")
        print(f"  Total Elapsed:   {result.elapsed_total}s")

        if result.html:
            preview = result.html[:200].replace("\n", " ").strip()
            print(f"  HTML Preview:    {preview}...")

        # -- Session State (Sanitized) --
        print()
        print("-" * 60)
        print("  SESSION STATE (sanitized)")
        print("-" * 60)
        domain = fetcher.session.extract_domain(target_url)
        state = fetcher.session.get_sanitized_state(domain)
        for k, v in state.items():
            print(f"  {k}: {v}")
        stored_token = fetcher.session.get_token(target_url)
        if stored_token:
            print(f"  stored_token:   {stored_token[:30]}... ({len(stored_token)} chars)")

        print()
        print("=" * 60)

    return result


def main():
    # [TEMPORARILY DISABLED PER USER REQUEST]: --skip-dom CLI flag
    skip_dom = False
    if "--skip-dom" in sys.argv:
        logging.getLogger("seo.detector").warning(
            "--skip-dom is temporarily disabled; proceeding to solve in-DOM widgets."
        )
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target_url = args[0] if args else "https://peet.ws/turnstile-test/non-interactive.html"

    result = run_detection(target_url, solve_dom_widgets=not skip_dom)
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
