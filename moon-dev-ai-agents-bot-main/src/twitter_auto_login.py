"""
Twitter auto-login via nodriver.

Strategy: Load x.com homepage (passes Cloudflare), then use the ON-PAGE
login form (modal) without navigating to a new URL.

Setup:
  1. Make sure VPN is ON
  2. Set TWITTER_USERNAME, TWITTER_EMAIL, TWITTER_PASSWORD in .env
  3. Run: python src/twitter_auto_login.py
"""

import asyncio
import json
import os
import sys
import traceback

from dotenv import load_dotenv

load_dotenv()


async def se(page, js, default=""):
    """Safe evaluate."""
    try:
        return await page.evaluate(js)
    except Exception:
        return default


async def main():
    import nodriver as uc

    username = os.environ.get("TWITTER_USERNAME", "")
    email = os.environ.get("TWITTER_EMAIL", "")
    password = os.environ.get("TWITTER_PASSWORD", "")

    if not all([username, email, password]):
        print("[ERROR] Set TWITTER_USERNAME, TWITTER_EMAIL, TWITTER_PASSWORD in .env")
        return False

    print(f"[LOGIN] Starting for user: {username}")
    browser = await uc.start(headless=False, browser_args=["--no-sandbox"])

    try:
        # ── Step 1: Load x.com homepage (Cloudflare-friendly) ──
        print("[1/7] Loading x.com homepage...")
        page = await browser.get("https://x.com")

        for i in range(30):
            await asyncio.sleep(3)
            body_len = await se(page, "document.body ? document.body.innerText.length : 0", 0)
            if isinstance(body_len, str):
                body_len = 0
            if body_len > 100:
                print(f"[1/7] Homepage loaded ({body_len} chars) after {(i+1)*3}s")
                break
            print(f"[WAIT] {(i+1)*3}s...")
        else:
            print("[ERROR] Homepage failed to load. Is VPN on?")
            return False

        await asyncio.sleep(2)

        # ── Step 2: Accept cookie consent if present ──
        print("[2/7] Checking for cookie consent...")
        await se(page, """
            (function() {
                var btns = document.querySelectorAll('button');
                for (var b of btns) {
                    var t = b.textContent.toLowerCase().trim();
                    if (t.includes('accept all') || t.includes('accept') && t.includes('cookie')) {
                        b.click(); return 'clicked';
                    }
                }
                return 'none';
            })()
        """)
        await asyncio.sleep(3)

        # ── Step 3: Take screenshot of what we see ──
        body = await se(page, "document.body.innerText.substring(0, 3000)")
        print(f"[3/7] Page content:\n{body[:400]}\n...")

        # ── Step 4: Click "Sign in" or "Log in" on the homepage ──
        print("[4/7] Looking for Sign in button...")
        clicked = await se(page, """
            (function() {
                // Method 1: Look for links/buttons with "sign in" text
                var els = document.querySelectorAll('a, button, div[role="button"]');
                for (var e of els) {
                    var t = e.textContent.toLowerCase().trim();
                    if (t === 'sign in' || t === 'log in' || t === 'get the app') continue;
                    if (t.includes('sign in') || t.includes('log in')) {
                        e.click();
                        return 'clicked: ' + t;
                    }
                }
                // Method 2: Look for the link href
                var links = document.querySelectorAll('a[href*="login"], a[href*="flow/login"]');
                for (var l of links) {
                    l.click();
                    return 'link: ' + l.href;
                }
                return 'not_found';
            })()
        """)
        print(f"[4/7] Sign in result: {clicked}")
        await asyncio.sleep(5)

        # ── Step 5: Wait for login form ──
        print("[5/7] Waiting for login form...")
        form_found = False
        for i in range(20):
            await asyncio.sleep(3)
            body = await se(page, "document.body.innerText.substring(0, 3000)")
            lower = body.lower()
            if "email or username" in lower:
                print(f"[5/7] Login form found after {(i+1)*3}s")
                form_found = True
                break
            # Check if we're on a new page with login options
            if "continue with phone" in lower or "continue with google" in lower:
                print(f"[5/7] Login options found after {(i+1)*3}s")
                form_found = True
                break
            if i % 5 == 0:
                print(f"[WAIT] Form... {(i+1)*3}s (body: {len(body)} chars)")

        if not form_found:
            url = await se(page, "window.location.href", "unknown")
            print(f"[ERROR] Login form not found. URL: {url}")
            print(f"[DEBUG] Body: {body[:300]}")
            return False

        # ── Step 6: Handle "Email or username" option ──
        body = await se(page, "document.body.innerText.substring(0, 3000)")
        if "continue with phone" in body.lower() or "continue with google" in body.lower():
            print("[6/7] Clicking 'Email or username' option...")
            await se(page, """
                (function() {
                    var els = document.querySelectorAll('div[role="button"], button, span, a');
                    for (var e of els) {
                        if (e.textContent.trim() === 'Email or username') {
                            e.click(); return 'clicked';
                        }
                    }
                    return 'not_found';
                })()
            """)
            await asyncio.sleep(5)

        # ── Step 7: Fill username ──
        print("[7/7] Entering username...")
        for attempt in range(10):
            result = await se(page, f"""
                (function() {{
                    var inputs = document.querySelectorAll('input');
                    for (var i = 0; i < inputs.length; i++) {{
                        var inp = inputs[i];
                        var r = inp.getBoundingClientRect();
                        var s = window.getComputedStyle(inp);
                        // Must be visible
                        if (r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden') {{
                            if (inp.name === 'username_or_email' || inp.autocomplete === 'username' || (inp.type === 'text' && inp.placeholder && inp.placeholder.toLowerCase().includes('user'))) {{
                                // Native setter for React
                                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                                setter.call(inp, '{username}');
                                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                inp.focus();
                                inp.dispatchEvent(new Event('focus', {{ bubbles: true }}));
                                return 'ok:' + inp.name + ':' + inp.placeholder;
                            }}
                        }}
                    }}
                    // List all inputs for debug
                    var info = [];
                    for (var j = 0; j < inputs.length; j++) {{
                        var x = inputs[j];
                        var rb = x.getBoundingClientRect();
                        info.push(x.name + '|' + x.type + '|' + x.autocomplete + '|' + Math.round(rb.width) + 'x' + Math.round(rb.height));
                    }}
                    return 'inputs:' + info.join('; ');
                }})()
            """)
            if result and result.startswith("ok:"):
                print(f"[7/7] Username entered ({result})")
                break
            print(f"[WAIT] Username input... {(attempt+1)*2}s ({result})")
            await asyncio.sleep(2)
        else:
            print(f"[ERROR] No username input found: {result}")
            return False

        await asyncio.sleep(1)

        # Click Next
        print("[NEXT] Clicking Next...")
        await se(page, """
            (function() {
                var btns = document.querySelectorAll('button, div[role="button"]');
                for (var b of btns) {
                    if (b.textContent.trim() === 'Next' && !b.disabled) { b.click(); return 'ok'; }
                }
                return 'not_found';
            })()
        """)
        await asyncio.sleep(5)

        body = await se(page, "document.body.innerText.substring(0, 3000)")
        print(f"[AFTER NEXT] {body[:200]}")

        # Handle email verification if needed
        if "email" in body.lower() and ("verify" in body.lower() or "enter your" in body.lower()):
            print("[EMAIL] Verification step...")
            await se(page, f"""
                (function() {{
                    var inputs = document.querySelectorAll('input[type="text"], input[name="text"]');
                    for (var inp of inputs) {{
                        var r = inp.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {{
                            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                            setter.call(inp, '{email}');
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            return 'ok';
                        }}
                    }}
                    return 'not_found';
                }})()
            """)
            await asyncio.sleep(1)
            await se(page, """
                var btns = document.querySelectorAll('button, div[role="button"]');
                for (var b of btns) {
                    if (b.textContent.trim() === 'Next' && !b.disabled) { b.click(); break; }
                }
            """)
            await asyncio.sleep(5)

        # Enter password
        print("[PASSWORD] Entering password...")
        for attempt in range(10):
            result = await se(page, """
                (function() {
                    var inputs = document.querySelectorAll('input[type="password"], input[name="password"]');
                    for (var inp of inputs) {
                        var r = inp.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            inp.focus();
                            return 'found';
                        }
                    }
                    return 'not_found';
                })()
            """)
            if result == "found":
                await se(page, f"""
                    (function() {{
                        var inp = document.querySelector('input[type="password"]');
                        if (inp) {{
                            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                            setter.call(inp, '{password}');
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            return 'ok';
                        }}
                        return 'no_inp';
                    }})()
                """)
                print("[PASSWORD] Entered")
                break
            print(f"[WAIT] Password... {(attempt+1)*2}s")
            await asyncio.sleep(2)
        else:
            body = await se(page, "document.body.innerText.substring(0, 500)")
            print(f"[ERROR] No password input. Page: {body[:300]}")
            return False

        await asyncio.sleep(1)

        # Click Log in
        print("[LOGIN] Clicking Log in...")
        await se(page, """
            (function() {
                var btns = document.querySelectorAll('button, div[role="button"]');
                for (var b of btns) {
                    var t = b.textContent.trim().toLowerCase();
                    if (t === 'log in' && !b.disabled) { b.click(); return 'ok'; }
                }
                return 'not_found';
            })()
        """)

        # Wait for login
        print("[LOGIN] Waiting for login to complete...")
        for i in range(20):
            await asyncio.sleep(3)
            url = await se(page, "window.location.href", "")
            body = await se(page, "document.body.innerText.substring(0, 500)", "")
            if url and "login" not in url and "flow" not in url and "onboarding" not in url:
                print(f"[LOGIN] Success! URL: {url}")
                break
            if any(w in body.lower() for w in ["wrong password", "incorrect", "suspended", "locked"]):
                print(f"[ERROR] Login failed: {body[:200]}")
                return False
            if i % 5 == 0:
                print(f"[WAIT] Login... {(i+1)*3}s")
        else:
            print("[WARN] Timeout, checking cookies anyway...")

        # Extract cookies
        await asyncio.sleep(2)
        cookies_data = await page.send(uc.cdp.network.get_all_cookies())
        cookie_dict = {c.name: c.value for c in cookies_data}
        auth_token = cookie_dict.get("auth_token", "")
        ct0 = cookie_dict.get("ct0", "")

        if auth_token:
            twikit_cookies = [
                {"name": "auth_token", "value": auth_token, "domain": ".x.com", "path": "/"},
                {"name": "ct0", "value": ct0, "domain": ".x.com", "path": "/"},
            ]
            with open("cookies.json", "w") as f:
                json.dump(twikit_cookies, f, indent=2)
            print(f"\n[SUCCESS] Cookies saved! auth_token={auth_token[:10]}...")
            return True
        else:
            print(f"[ERROR] No auth_token. Cookies: {list(cookie_dict.keys())}")
            return False

    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("TWITTER AUTO-LOGIN")
    print("Make sure VPN is ON if Twitter is blocked in your country")
    print("=" * 60)
    success = asyncio.run(main())
    if success:
        print("\nDone! The sentiment agent is now active.")
    else:
        print("\nFailed. Try the manual method: python src/twitter_manual_cookies.py")
