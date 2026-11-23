"""Launch Chrome with remote debugging so chrome-devtools-mcp can extract a Costco refresh token.

Usage: `costco-auth-browser` (console script). Cross-platform: macOS, Linux, Windows.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from costco_mcp_server.auth import REGION_CONFIG, get_region_config

DEFAULT_PORT = 9223
PROFILE_DIR = Path.home() / ".costco-mcp" / "chrome-profile"


def find_chrome() -> str | None:
    system = platform.system()

    if system == "Darwin":
        path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if path.exists():
            return str(path)

    elif system == "Windows":
        for env_var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
            if base := os.environ.get(env_var):
                path = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if path.exists():
                    return str(path)

    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if path := shutil.which(name):
            return path

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch Chrome for Costco MCP login")
    parser.add_argument(
        "--region",
        choices=sorted(REGION_CONFIG),
        default=os.environ.get("COSTCO_REGION", "us").strip().lower() or "us",
        help="Costco region to open ('us' for costco.com, 'ca' for costco.ca)",
    )
    args = parser.parse_args()
    cfg = get_region_config(args.region)

    port = int(os.environ.get("COSTCO_AUTH_PORT", DEFAULT_PORT))
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    chrome = find_chrome()
    if chrome is None:
        print(
            "Chrome/Chromium not found. Install Google Chrome "
            "(https://www.google.com/chrome/) or Chromium.",
            file=sys.stderr,
        )
        return 1

    print(f"Launching {chrome} ({args.region}: {cfg['base_url']})")
    print(f"  --remote-debugging-port={port}")
    print(f"  --user-data-dir={PROFILE_DIR}")
    print()
    print(f"Log into {cfg['base_url']} in the browser window, then ask Claude to refresh your token.")
    print(f"(chrome-devtools-mcp should be registered against the debugger on port {port}.)")
    print()

    return subprocess.call([
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        cfg["base_url"],
    ])


if __name__ == "__main__":
    sys.exit(main())
