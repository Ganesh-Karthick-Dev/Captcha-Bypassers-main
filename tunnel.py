"""Standalone ngrok tunnel for SEO & Captcha Microservice.

Usage:
  1. Set your authtoken:
     $env:NGROK_AUTHTOKEN="your_token_here"
  2. Run this script:
     python tunnel.py
"""
import os
import sys
import time
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ngrok-tunnel")

try:
    import ngrok
except ImportError:
    log.error("ngrok SDK not installed. Run: pip install ngrok")
    sys.exit(1)


def connect_ngrok(
    port: int = 5001,
    domain: str = "scheming-squatting-statue.ngrok-free.dev"
):
    token = os.getenv("NGROK_AUTHTOKEN")
    if not token:
        log.error("NGROK_AUTHTOKEN environment variable is not set!")
        print("\n" + "=" * 65)
        print(" [!] Please set your ngrok authtoken first:")
        print("     PowerShell:  $env:NGROK_AUTHTOKEN=\"your_token\"")
        print("     CMD:         set NGROK_AUTHTOKEN=your_token")
        print("     Bash:        export NGROK_AUTHTOKEN=\"your_token\"")
        print("=" * 65 + "\n")
        return None

    target = f"localhost:{port}"
    log.info("Initiating ngrok tunnel: %s -> https://%s", target, domain)

    try:
        forwarder = ngrok.forward(
            target,
            authtoken=token,
            domain=domain,
        )
        url = forwarder.url()
        log.info("App is live on the internet at: %s", url)
        print("\n" + "=" * 65)
        print(f"  Public Microservice URL : {url}")
        print(f"  Local Target Port       : http://localhost:{port}")
        print(f"  Health Check            : {url}/health")
        print(f"  Rank Health             : {url}/api/v1/ranks/health")
        print(f"  Keyword Tracking API    : {url}/keyword-tracking/search")
        print(f"  Batch Tracking API      : {url}/keyword-tracking/batch")
        print("=" * 65 + "\n")
        return forwarder
    except Exception as e:
        log.error("Failed to connect ngrok: %s", e)
        return None


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    domain = os.getenv("NGROK_DOMAIN", "scheming-squatting-statue.ngrok-free.dev")

    forwarder = connect_ngrok(port=port, domain=domain)
    if forwarder:
        try:
            print("Tunnel active. Press Ctrl+C to stop.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Shutting down ngrok tunnel...")
            try:
                ngrok.disconnect()
            except Exception:
                pass
