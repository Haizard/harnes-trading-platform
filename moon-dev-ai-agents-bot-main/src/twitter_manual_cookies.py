"""
Manual Twitter cookie extraction - fallback if auto-login fails.

Opens Chrome to x.com, you log in manually, then paste the cookies.

Usage:
  1. Make sure VPN is ON
  2. Run: python src/twitter_manual_cookies.py
  3. Log in to Twitter in the Chrome window that opens
  4. Follow the on-screen instructions to paste cookies
"""

import json
import os
import subprocess
import platform

def find_chrome():
    system = platform.system()
    if system == "Windows":
        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in paths:
            if os.path.exists(p):
                return p
    elif system == "Darwin":
        p = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(p):
            return p
    else:
        for p in ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]:
            if os.path.exists(p):
                return p
    return None


def main():
    print("=" * 60)
    print("TWITTER COOKIE SETUP (Manual)")
    print("=" * 60)
    print()

    # Check existing
    if os.path.exists("cookies.json"):
        with open("cookies.json") as f:
            try:
                data = json.load(f)
                if any(c.get("name") == "auth_token" for c in data):
                    print("[OK] cookies.json already exists with auth_token!")
                    print("Delete cookies.json and run again to refresh.")
                    return
            except json.JSONDecodeError:
                pass

    chrome = find_chrome()
    if not chrome:
        print("[ERROR] Chrome not found. Install Google Chrome.")
        return

    print("Step 1: Opening Chrome to x.com...")
    subprocess.Popen([chrome, "https://x.com"])

    print()
    print("Step 2: Log in to Twitter in the Chrome window.")
    print("         (Make sure VPN is ON)")
    print()
    print("Step 3: After logging in, press F12 to open DevTools.")
    print()
    print("Step 4: Go to Console tab and paste this EXACT code:")
    print()
    print('   fetch("https://x.com/i/flow/login")')
    print('     .then(r => r.headers)')
    print('     .catch(() => document.cookie)')
    print()
    print("   Or just type this simpler version:")
    print()
    print('   document.cookie.split(";").map(c=>c.trim()).filter(c=>c.startsWith("auth_token")||c.startsWith("ct0")).join("\\n")')
    print()
    print("Step 5: Copy the output and paste below.")
    print()

    # Get auth_token
    auth_line = input("Paste auth_token line (or just the value): ").strip()
    if "=" in auth_line:
        auth_token = auth_line.split("=", 1)[1].strip()
    else:
        auth_token = auth_line.strip()

    # Get ct0
    ct0_line = input("Paste ct0 line (or just the value): ").strip()
    if "=" in ct0_line:
        ct0 = ct0_line.split("=", 1)[1].strip()
    else:
        ct0 = ct0_line.strip()

    if not auth_token:
        print("[ERROR] auth_token is required!")
        return

    # Save
    twikit_cookies = [
        {"name": "auth_token", "value": auth_token, "domain": ".x.com", "path": "/"},
        {"name": "ct0", "value": ct0, "domain": ".x.com", "path": "/"},
    ]
    with open("cookies.json", "w") as f:
        json.dump(twikit_cookies, f, indent=2)

    print()
    print(f"[SUCCESS] Saved! auth_token={auth_token[:10]}... ct0={ct0[:10]}...")
    print("Redeploy on Northflank to activate Twitter sentiment.")
    print("Cookies last ~2-6 months.")


if __name__ == "__main__":
    main()
