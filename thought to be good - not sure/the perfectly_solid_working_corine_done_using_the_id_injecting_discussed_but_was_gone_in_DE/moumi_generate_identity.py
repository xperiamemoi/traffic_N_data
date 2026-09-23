"""
Badoo session identity generator — run once before each signup attempt.

Writes session_identity.json which is read by:
  - badoo_mitm_addon.py  (device_id, cloak_seed)
  - badoo_signup.py      (user_agent, locale, timezone)
  - badoo_cloak_photos.py (cloak_seed)

Usage:
  py -3 badoo_generate_identity.py
"""

import json
import random
import uuid
from pathlib import Path

IDENTITY_FILE = Path(r"C:\Users\MTHG\Desktop\Claude-code-sessions\Solution-2\session_identity.json")


def generate() -> dict:
    identity = {
        "device_id":            str(uuid.uuid4()),
        "user_agent":           (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/144.0.0.0 Safari/537.36"
        ),
        "screen_width":         1920,
        "screen_height":        1080,
        "timezone":             "Europe/Zurich",
        "locale":               "fr-CH",
        "hardware_concurrency": 8,
        "device_memory":        8,
        "platform":             "Win32",
        "cloak_seed":           random.randint(0, 2**31 - 1),
    }

    IDENTITY_FILE.write_text(json.dumps(identity, indent=2), encoding="utf-8")

    print("[IDENTITY] Fresh session generated:")
    for k, v in identity.items():
        val = str(v)
        if len(val) > 60:
            val = val[:57] + "..."
        print(f"  {k:25s}: {val}")
    print(f"\n[IDENTITY] Saved → {IDENTITY_FILE}")
    print("[IDENTITY] Next steps:")
    print("  1. py -3 badoo_cloak_photos.py photo1.jpg photo2.jpg  (uses this seed)")
    print("  2. Start mitmdump (addon reads this file at load)")
    print("  3. Run 3_launch_clean_chromium.bat")
    return identity


if __name__ == "__main__":
    generate()
