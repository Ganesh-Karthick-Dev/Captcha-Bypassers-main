"""Solve the reCAPTCHA v2 IMAGE challenge with a vision model.

The audio fallback is IP-blocked, but the image grid opens normally. We screenshot
the grid, slice it into tiles, ask the Mistral key-pool yes/no per tile, click the
positives, and submit. Handles the three layouts:

  - 3x3 / 4x4 static : classify every tile once, click matches, verify.
  - dynamic (3x3)    : after a match is clicked the tile reloads a new image, so we
                       loop — classify, click new matches, wait for reload — until a
                       round finds nothing new (or a round cap), then verify.

Vision-solve is never 100% (reCAPTCHA images are deliberately ambiguous); success is
judged by reCAPTCHA accepting the submit (checkbox -> checked / token appears), not by
our own confidence. Caller retries on failure.
"""
import asyncio
import base64
import io
import logging
import time

from PIL import Image
from .timing import timed

log = logging.getLogger(__name__)

_BFRAME = "/bframe"
_VERIFY = "#recaptcha-verify-button"
_MAX_DYNAMIC_ROUNDS = 6
_CLASSIFY_CONCURRENCY = 9  # classify full 3x3 grid concurrently for 2-3x faster round completion


async def _find_bframe(page):
    t0 = time.perf_counter()
    try:
        for fr in page.frames:
            if _BFRAME in (fr.url or ""):
                return fr
        return None
    finally:
        elapsed = time.perf_counter() - t0
        log.info("[TIMING] bframe_lookup                      %.3fs", elapsed)


async def _challenge_meta(bf) -> dict:
    t0 = time.perf_counter()
    try:
        return await bf.evaluate("""() => {
            const t = document.querySelector('table');
            const desc = document.querySelector(
                '.rc-imageselect-desc-no-canonical, .rc-imageselect-desc');
            const strong = desc?.querySelector('strong')?.innerText;
            const rows = t ? t.rows.length : 0;
            const cols = t && t.rows[0] ? t.rows[0].cells.length : 0;
            return {
                target: strong || (desc ? desc.innerText.split('\\n')[0] : ''),
                rows, cols,
                dynamic: !!document.querySelector(
                    '.rc-imageselect-dynamic-selected') ||
                    /click verify once there are none/i.test(desc?.innerText || ''),
                has_table: !!t,
            };
        }""")
    finally:
        elapsed = time.perf_counter() - t0
        log.info("[TIMING] challenge_meta_extract             %.3fs", elapsed)


async def _classify_grid(bf, keypool, target: str, n: int) -> list:
    """Screenshot the grid, slice into n*n tiles, classify each concurrently.

    Returns the list of tile indices (row-major) classified as containing target.
    """
    t0_ss = time.perf_counter()
    table = await bf.query_selector("table")
    if not table:
        return []
    try:
        await asyncio.wait_for(table.wait_for_element_state("stable", timeout=3000),
                               timeout=5)
    except Exception:
        pass
    try:
        grid_png = await asyncio.wait_for(table.screenshot(), timeout=10)
    except asyncio.TimeoutError:
        log.warning("grid screenshot timed out, retrying with frame screenshot")
        try:
            grid_png = await asyncio.wait_for(bf.screenshot(), timeout=10)
        except Exception:
            log.warning("frame screenshot also failed")
            return []
    log.info("[TIMING] grid_screenshot                    %.3fs", time.perf_counter() - t0_ss)

    t0_cls = time.perf_counter()
    img = Image.open(io.BytesIO(grid_png)).convert("RGB")
    W, H = img.size
    tw, th = W // n, H // n

    sem = asyncio.Semaphore(_CLASSIFY_CONCURRENCY)

    async def judge(idx, r, c):
        tile = img.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th))
        buf = io.BytesIO(); tile.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        async with sem:  # bound concurrent Mistral calls (thread-pool + rate)
            yes = await asyncio.to_thread(keypool.classify, b64, target)
        return idx if yes else None

    tasks = [judge(r * n + c, r, c) for r in range(n) for c in range(n)]
    results = await asyncio.gather(*tasks)
    res = [i for i in results if i is not None]
    log.info("[TIMING] grid_tile_classification (%d tiles) %.3fs", n * n, time.perf_counter() - t0_cls)
    return res


async def _tiles(bf):
    return await bf.query_selector_all("td[role=button], .rc-imageselect-tile")


async def solve_image_challenge(page, keypool, max_rounds: int = _MAX_DYNAMIC_ROUNDS) -> bool:
    """Run the image challenge to a submit. Returns True if we pressed verify
    without an obvious error (caller confirms via token/checkbox)."""
    bf = await _find_bframe(page)
    if not bf:
        return False
    meta = await _challenge_meta(bf)
    if not meta.get("has_table") or not meta.get("target"):
        log.info("no image grid present"); return False

    n = meta["rows"]
    if not n:
        await asyncio.sleep(1.5)
        meta = await _challenge_meta(bf)
        n = meta.get("rows") or 0
        if not n:
            log.warning("grid rows unreadable — skipping to avoid mis-slice")
            return False
    target = meta["target"]
    log.info("image challenge: target=%r grid=%dx%d dynamic=%s",
             target, n, meta["cols"], meta["dynamic"])

    rounds = max_rounds if meta["dynamic"] else 1
    clicked_any = False
    for rnd in range(rounds):
        round_start = time.perf_counter()
        log.info("[ROUND %d/%d] START", rnd + 1, rounds)

        positives = await _classify_grid(bf, keypool, target, n)
        log.info("round %d/%d: %d/%d tiles match %r -> %s",
                 rnd + 1, rounds, len(positives), n * n, target, positives)
        if not positives:
            log.info("[ROUND %d/%d] COMPLETE in %.3fs (no matching tiles)", rnd + 1, rounds, time.perf_counter() - round_start)
            break

        t0_interact = time.perf_counter()
        tiles = await _tiles(bf)
        for idx in positives:
            if idx < len(tiles):
                try:
                    await tiles[idx].click(timeout=1000)
                    clicked_any = True
                except Exception:
                    try:
                        await tiles[idx].dispatch_event("click")
                        clicked_any = True
                    except Exception as e:
                        log.debug("tile %d click: %s", idx, str(e).splitlines()[0])
        log.info("[TIMING] tile_interaction_clicks          %.3fs", time.perf_counter() - t0_interact)

        if not meta["dynamic"]:
            log.info("[ROUND %d/%d] COMPLETE in %.3fs (static layout)", rnd + 1, rounds, time.perf_counter() - round_start)
            break

        settle_start = time.perf_counter()
        await asyncio.sleep(0.7)  # let reloaded tiles settle before re-classifying
        log.info("[TIMING] challenge_settling                 %.3fs", time.perf_counter() - settle_start)

        bf = await _find_bframe(page) or bf
        log.info("[ROUND %d/%d] TOTAL                         %.3fs", rnd + 1, rounds, time.perf_counter() - round_start)

    if not clicked_any:
        log.info("no tiles matched %r; attempting reload for a fresh challenge", target)
        bf = await _find_bframe(page) or bf
        if bf:
            try:
                reloaded = await bf.evaluate("""() => {
                    const r = document.querySelector('#recaptcha-reload-button');
                    if (r) { r.click(); return true; }
                    return false;
                }""")
                if reloaded:
                    log.info("Reload button clicked successfully")
                    await asyncio.sleep(0.8)
                    return False
            except Exception as e:
                log.debug("reload click: %s", e)

    t0_verify = time.perf_counter()
    bf = await _find_bframe(page) or bf
    if bf:
        try:
            # Immediate evaluate click is instantaneous (<5ms) and avoids Playwright cross-origin iframe actionability check hangs
            clicked = await bf.evaluate("""() => {
                const b = document.querySelector('#recaptcha-verify-button');
                if (b) { b.click(); return true; }
                return false;
            }""")
            if not clicked:
                await bf.locator(_VERIFY).click(timeout=1000)
        except Exception:
            try:
                await bf.locator(_VERIFY).dispatch_event("click")
            except Exception as e2:
                log.warning("verify click failed: %s", str(e2).splitlines()[0])
                return False
    log.info("[TIMING] verify_button_click               %.3fs", time.perf_counter() - t0_verify)

    t0_settle_final = time.perf_counter()
    await asyncio.sleep(0.6)
    log.info("[TIMING] post_verify_settle                 %.3fs", time.perf_counter() - t0_settle_final)
    return True
