"""
badoo_cdp_interceptor.py — SHIELD v4 (raw CDP, no Playwright)
Connects to Chrome's CDP WebSocket directly — no Playwright, no Runtime/Debugger domains.
Only Fetch domain is enabled: zero automation-detection surface.
No proxy, no TLS fingerprint — Chrome handles Badoo's TLS natively.

Requires: pip install websockets

Run AFTER launching Chromium (bat 3), BEFORE doing anything on Badoo:
  py -3 Solution-2\badoo_cdp_interceptor.py
"""

import asyncio
import base64
import json
import re
import time
import uuid
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:
    print("[ERROR] websockets not installed. Run:  pip install websockets")
    raise SystemExit(1)

# ── Config ─────────────────────────────────────────────────────────────────────
_IDENTITY_FILE = Path(r"C:\Users\MTHG\Desktop\Claude-code-sessions\Working-Corinefresh\session_identity.json")
_LOG_FILE      = Path(r"C:\Users\MTHG\Desktop\Claude-code-sessions\Working-Corinefresh\cdp_live.log")
CDP_HTTP       = "http://localhost:9222"

SESSION_DEVICE_ID  = str(uuid.uuid4())
SESSION_CLOAK_SEED = 0x5A3D9C17

_phone_done       = False
_photo_done       = False
_PHASE            = "pre"
_gate_strips      = 0
_clean_after_gate = 0
_gew_seen         = False

_UUID_RE        = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
_DEVICE_ID_KEYS = {"device_id", "udid"}
_HARD_KILLS     = {"0030-3001-0051"}
_SOFT_STRIPS    = {"0030-3001-0059"}
_CAPTCHA_IDS    = {"captcha_2", "captcha"}
_SCRUB_FIELDS   = {
    "alert_type": {5}, "element": {613}, "permission_type": {6},
    "error_type": {11, 12, 28}, "event_type": {36},
}
_GCM_BAD   = [b"com.googlechromefortesting", b"chrome_for_testing"]
_GCM_GOOD  = b"com.badoo.mobile"
_CLASS3_MT = {36}
_JPEG_MAGIC = b'\xff\xd8\xff'
_PNG_MAGIC  = b'\x89PNG'

_SILENT_HOSTS = {
    # Error/crash reporting — safe to block, not used in signup ML
    "sentry.io", "ingest.sentry.io", "ingest.us.sentry.io",
    # Ad networks — no signup validation role
    "doubleclick.net", "pagead2.googlesyndication.com",
    "tr.snapchat.com", "tr-shadow.snapchat.com",
    # NOTE: googletagmanager, appsflyer, amplitude, mixpanel NOT blocked:
    # Badoo's fraud ML checks for these firing during signup.
    # Blocking them → zero analytics → silent dead at account creation.
}
_SILENT_PATHS = {"/jss/csp_report.phtml"}


# ── Logging ────────────────────────────────────────────────────────────────────

def log(tag, msg):
    line = f"[{time.strftime('%H:%M:%S')}][{tag}] {msg}"
    print(line, flush=True)
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Identity ───────────────────────────────────────────────────────────────────

def _load_identity():
    global SESSION_DEVICE_ID, SESSION_CLOAK_SEED
    if _IDENTITY_FILE.exists():
        try:
            d = json.loads(_IDENTITY_FILE.read_text(encoding="utf-8"))
            SESSION_DEVICE_ID  = d.get("device_id",  SESSION_DEVICE_ID)
            SESSION_CLOAK_SEED = d.get("cloak_seed", SESSION_CLOAK_SEED)
            log("IDENTITY", f"device_id={SESSION_DEVICE_ID[:8]}... seed={SESSION_CLOAK_SEED}")
        except Exception as e:
            log("IDENTITY", f"Read failed: {e}")
    else:
        log("IDENTITY", "session_identity.json not found — using random values!")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _check_phase():
    global _PHASE
    if _phone_done and _photo_done and _PHASE == "pre":
        _PHASE = "post"
        log("PHASE", "=" * 50)
        log("PHASE", "  --> POST-VERIFICATION: full flag patching ACTIVE")
        log("PHASE", "=" * 50)


def _try_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def _ack(mid=0):
    return {
        "$gpb": "badoo.bma.BadooMessage", "message_type": 387, "version": 1,
        "message_id": mid, "body": [{"$gpb": "badoo.bma.MessageBody", "message_type": 387}],
        "responses_count": 1, "is_background": False, "vhost": "",
    }


def _fake_selfie(mid=0):
    return {
        "$gpb": "badoo.bma.BadooMessage", "message_type": 263, "version": 1,
        "message_id": mid, "object_type": 0,
        "body": [{"$gpb": "badoo.bma.MessageBody", "message_type": 263}],
        "responses_count": 1, "is_background": False, "vhost": "",
    }


def _hlist_to_dict(lst):
    if isinstance(lst, dict):
        return {k.lower(): v for k, v in lst.items()}
    return {h["name"].lower(): h["value"] for h in (lst or [])}


def _dict_to_hlist(d):
    return [{"name": k, "value": v} for k, v in d.items()]


def _is_gew_url(url):
    # Matches gew.badoo.com, gew3.badoo.com, gew12.badoo.com etc.
    return bool(re.search(r'://gew\d*\.badoo\.com', url))

def _check_cdn(url, body=None):
    global _gew_seen
    if _is_gew_url(url) and not _gew_seen:
        _gew_seen = True
        log("GEW!!!", f"SHADOW BAN -- {url[:80]}")
        log("GEW!!!", "Abort and start fresh identity.")
    if body and not _gew_seen and re.search(r'gew\d*\.badoo', body) and "badoocdn" in body:
        _gew_seen = True
        log("GEW!!!", "SHADOW BAN CDN URL in response body!")
        log("GEW!!!", "Abort this session -- start fresh identity.")


def _patch_cookie(s):
    if "device_id=" not in s:
        return s, False
    n = re.sub(r'device_id=[0-9a-f\-]+', f'device_id={SESSION_DEVICE_ID}', s, flags=re.IGNORECASE)
    return n, n != s


def _inject_device_id(obj, depth=0):
    if depth > 10:
        return False
    changed = False
    if isinstance(obj, dict):
        for k in list(obj):
            v = obj[k]
            if k in _DEVICE_ID_KEYS and isinstance(v, str) and _UUID_RE.match(v) and v != SESSION_DEVICE_ID:
                log("DEVICE", f"{k}: {v[:8]}... -> {SESSION_DEVICE_ID[:8]}...")
                obj[k] = SESSION_DEVICE_ID
                changed = True
            else:
                changed |= _inject_device_id(v, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            changed |= _inject_device_id(item, depth + 1)
    return changed


def _scrub_gcm(b):
    changed = False
    for bad in _GCM_BAD:
        if bad in b:
            b = b.replace(bad, _GCM_GOOD)
            changed = True
    if changed:
        log("GCM", "Chrome-for-Testing app ID scrubbed")
    return b, changed


def _is_verify(post_text, ct):
    if "json" not in ct:
        return False, 0
    d = _try_json(post_text)
    if not d:
        return False, 0
    for item in d.get("body", []):
        if item.get("message_type") == 262 and "server_user_verify" in item:
            return True, d.get("message_id", 0)
    return False, 0


def _scrub_hotpanel(b):
    try:
        d = json.loads(b)
    except Exception:
        return b, False
    clean, dropped = [], 0
    for ev in d.get("events", []):
        drop = any(
            isinstance(s, dict) and s.get(f) in bv
            for s in ev.get("body", {}).values()
            for f, bv in _SCRUB_FIELDS.items()
        )
        if drop:
            dropped += 1
            log("SCRUB", f"event {ev.get('name')} dropped")
        else:
            clean.append(ev)
    if dropped:
        d["events"] = clean
        return json.dumps(d).encode(), True
    return b, False


def _neutralise(data, mid):
    global _gate_strips, _clean_after_gate, _phone_done
    body = data.get("body", [])
    if not body:
        return False
    for item in body:
        err  = item.get("server_error_message", {})
        eid  = str(err.get("error_id", ""))
        emsg = str(err.get("error_message", "")).lower()
        if any(k in eid for k in _HARD_KILLS) or "session not found" in emsg:
            log("KILL", f"{eid!r} --> ACK")
            data.clear(); data.update(_ack(mid))
            return False
    gate = False
    clean = []
    for item in body:
        err = item.get("server_error_message", {})
        eid = str(err.get("error_id", ""))
        if any(k in eid for k in _SOFT_STRIPS):
            log("GATE", f"Phone gate {eid!r} stripped"); gate = True; continue
        if any(k in eid.lower() for k in _CAPTCHA_IDS):
            log("CAPTCHA", f"{eid!r} stripped"); continue
        clean.append(item)
    if len(clean) != len(body):
        data["body"] = clean; data["responses_count"] = len(clean)
    for item in data.get("body", []):
        n = item.get("client_notification", {})
        if n.get("id") == "requested_verify_phone" and n.get("blocking"):
            n["blocking"] = False; log("PHONE", "Notification made non-blocking")
    return gate


def _suppress_class3(data):
    for item in data.get("body", []):
        if item.get("message_type") in _CLASS3_MT:
            n = item.get("client_notification", {})
            if n:
                n["blocking"] = False; log("CLASS3", "Optional verify non-blocking")


def _patch_photos(obj, depth=0):
    if depth > 10: return 0
    patched = 0
    if isinstance(obj, dict):
        if "is_photo_of_me" in obj or "is_pending_moderation" in obj:
            ch = False
            for k, w in [("is_photo_of_me", True), ("is_pending_moderation", False),
                         ("can_set_as_profile_photo", True)]:
                if obj.get(k) != w:
                    obj[k] = w; ch = True
            if ch:
                patched += 1; log("PHOTO", f"patched id={obj.get('id','?')}")
        for k, w in [("requires_moderation", False), ("is_upload_forbidden", False)]:
            if obj.get(k) is True:
                obj[k] = False; log("ALBUM", f"cleared {k}")
        for k in ("is_suspended", "is_blocked", "is_deleted"):
            if obj.get(k) is True:
                obj[k] = False; log("ACCOUNT", f"cleared {k}")
        for v in obj.values():
            patched += _patch_photos(v, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            patched += _patch_photos(item, depth + 1)
    return patched


def _detect_photo_ok(data, depth=0):
    global _photo_done
    if _photo_done or depth > 10: return
    if isinstance(data, dict):
        if data.get("is_photo_of_me") is True:
            photo_id = data.get("id", "")
            if photo_id:
                _photo_done = True
                log("PHASE", f"is_photo_of_me:True on real photo id={photo_id} -- confirmed server-side")
                _check_phase(); return
            else:
                log("PHASE", "is_photo_of_me:True ignored -- no photo id (default/placeholder, not real upload)")
        for v in data.values():
            _detect_photo_ok(v, depth + 1)
    elif isinstance(data, list):
        for item in data:
            _detect_photo_ok(item, depth + 1)


# ── CDP messaging ──────────────────────────────────────────────────────────────

_pending: dict[int, asyncio.Future] = {}
_id_counter = [100]
_ws_ref = [None]


async def _send_raw(method, params=None):
    """Fire-and-forget CDP send."""
    mid = _id_counter[0]; _id_counter[0] += 1
    msg = json.dumps({"id": mid, "method": method, "params": params or {}})
    await _ws_ref[0].send(msg)


async def _send_await(method, params=None, timeout=10.0):
    """Send CDP command and await response."""
    mid = _id_counter[0]; _id_counter[0] += 1
    fut = asyncio.get_event_loop().create_future()
    _pending[mid] = fut
    msg = json.dumps({"id": mid, "method": method, "params": params or {}})
    await _ws_ref[0].send(msg)
    return await asyncio.wait_for(fut, timeout=timeout)


# ── Event handlers ─────────────────────────────────────────────────────────────

_PASSTHROUGH_TYPES = {"Document", "Image", "StyleSheet", "Font", "Media", "Manifest", "Ping", "Preflight", "Other"}


async def _on_request(event):
    global _photo_done
    rid           = event["requestId"]
    req           = event["request"]
    url           = req["url"]
    method        = req.get("method", "GET")
    resource_type = event.get("resourceType", "")

    # Always check for gew domain first, even on page navigations
    _check_cdn(url)

    # Pass through page navigations and static assets immediately — no delay, no modification
    if resource_type in _PASSTHROUGH_TYPES:
        await _send_raw("Fetch.continueRequest", {"requestId": rid})
        return

    hdrs_raw = event.get("requestHeaders") or req.get("headers", {})
    hdrs = _hlist_to_dict(hdrs_raw)
    if isinstance(hdrs_raw, dict):
        hdrs_list = [{"name": k, "value": v} for k, v in hdrs_raw.items()]
    else:
        hdrs_list = list(hdrs_raw)

    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    path = parsed.path
    is_badoo = "badoo.com" in host or "badoocdn.com" in host
    is_gcm   = "fcm.googleapis" in url or "firebase" in url or "googleapis.com/gcm" in url

    # Silent block
    if any(h in host for h in _SILENT_HOSTS) or any(p in path for p in _SILENT_PATHS):
        log("SILENT", f"{host}{path[:50]}")
        await _send_raw("Fetch.fulfillRequest", {
            "requestId": rid, "responseCode": 200,
            "responseHeaders": [{"name": "content-type", "value": "application/json"}],
            "body": base64.b64encode(b"{}").decode(),
        })
        return

    # GCM
    if is_gcm:
        raw = req.get("postData", "")
        if raw:
            try: pb = base64.b64decode(raw)
            except Exception: pb = raw.encode("latin-1", errors="replace")
            nb, ch = _scrub_gcm(pb)
            if ch:
                await _send_raw("Fetch.continueRequest", {"requestId": rid,
                    "postData": base64.b64encode(nb).decode()})
                return
        await _send_raw("Fetch.continueRequest", {"requestId": rid})
        return

    if not is_badoo:
        await _send_raw("Fetch.continueRequest", {"requestId": rid})
        return

    # ── Badoo request ─────────────────────────────────────────────────────────
    log("REQ", f"{method} {host}{path[:60]}")
    ct = hdrs.get("content-type", "")
    raw_post = req.get("postData", "")
    post_bytes = b""
    post_text  = ""
    if raw_post:
        try: post_bytes = base64.b64decode(raw_post)
        except Exception: post_bytes = raw_post.encode("utf-8", errors="replace")
        try: post_text = post_bytes.decode("utf-8", errors="replace")
        except Exception: pass

    # [G] selfie verify block
    if method == "POST" and "json" in ct:
        ok, vmid = _is_verify(post_text, ct)
        if ok:
            log("BLOCK", f"server_user_verify mid={vmid} --> fake success")
            if not _photo_done:
                _photo_done = True
                log("PHASE", "photo_done=True (selfie verify intercepted)")
                _check_phase()
            rb = json.dumps(_fake_selfie(vmid)).encode()
            await _send_raw("Fetch.fulfillRequest", {
                "requestId": rid, "responseCode": 200,
                "responseHeaders": [{"name": "content-type", "value": "application/json; charset=utf-8"}],
                "body": base64.b64encode(rb).decode(),
            })
            return

    # [H] log uploads
    if "multipart/form-data" in ct:
        if _JPEG_MAGIC in post_bytes or _PNG_MAGIC in post_bytes:
            log("UPLOAD", f"Image upload -> {url[:80]}")

    # [C] hotpanel scrub
    if post_bytes and ("hotpanel" in url or ("analytics" in url and "badoo" in url)):
        nb, ch = _scrub_hotpanel(post_bytes)
        if ch:
            await _send_raw("Fetch.continueRequest", {"requestId": rid,
                "postData": base64.b64encode(nb).decode()})
            return

    # [A] device_id in JSON body
    if method in ("POST", "PUT", "PATCH") and "json" in ct and post_text:
        d = _try_json(post_text)
        if d and _inject_device_id(d):
            await _send_raw("Fetch.continueRequest", {"requestId": rid,
                "postData": base64.b64encode(json.dumps(d).encode()).decode()})
            return

    await _send_raw("Fetch.continueRequest", {"requestId": rid})


async def _on_response(event):
    global _gate_strips, _clean_after_gate, _phone_done
    rid           = event["requestId"]
    url           = event["request"]["url"]
    status        = event.get("responseStatusCode", 200)
    resource_type = event.get("resourceType", "")
    rhdrs         = event.get("responseHeaders", [])
    hdrs          = _hlist_to_dict(rhdrs)
    ct            = hdrs.get("content-type", "")

    # Pass through static assets immediately
    if resource_type in _PASSTHROUGH_TYPES:
        await _send_raw("Fetch.continueResponse", {"requestId": rid})
        return

    _check_cdn(url)

    if "json" not in ct:
        await _send_raw("Fetch.continueResponse", {"requestId": rid})
        return

    try:
        br = await _send_await("Fetch.getResponseBody", {"requestId": rid})
        body_bytes = base64.b64decode(br["body"]) if br.get("base64Encoded") else br["body"].encode()
        body_text  = body_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        log("ERR", f"getResponseBody: {e}")
        await _send_raw("Fetch.continueResponse", {"requestId": rid})
        return

    _check_cdn(url, body_text)

    if "SERVER_GET_USER" in url and "LIST" not in url:
        log("DUMP", f"GET_USER: {body_text[:500]}")

    data = _try_json(body_text)
    if not data:
        await _send_raw("Fetch.continueResponse", {"requestId": rid})
        return

    mid = data.get("message_id", 0)

    gate = _neutralise(data, mid)
    if gate:
        _gate_strips += 1; _clean_after_gate = 0
        log("PHASE", f"Phone gate stripped (total: {_gate_strips})")
    elif _gate_strips > 0 and not _phone_done:
        _clean_after_gate += 1
        if _clean_after_gate >= 4:
            _phone_done = True
            log("PHASE", "Phone verified -- 4 clean round-trips")
            _check_phase()

    if data.get("message_type") != 387:
        _suppress_class3(data)
        if _PHASE == "post":
            n = _patch_photos(data)
            if n: log("PHOTO", f"{n} patched -- {url[:60]}")
        else:
            _detect_photo_ok(data)

    for item in data.get("body", []):
        err = item.get("server_error_message", {})
        if err and err.get("error_id"):
            log("UNHANDLED_ERR", f"mt={item.get('message_type')} err={err['error_id']}")

    new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    new_hdrs = [h for h in rhdrs if h["name"].lower() not in ("content-type", "content-length")]
    new_hdrs += [
        {"name": "content-type",   "value": "application/json; charset=utf-8"},
        {"name": "content-length", "value": str(len(new_body))},
    ]
    await _send_raw("Fetch.fulfillRequest", {
        "requestId": rid, "responseCode": status,
        "responseHeaders": new_hdrs,
        "body": base64.b64encode(new_body).decode(),
    })


async def _dispatch(event):
    is_resp = "responseStatusCode" in event
    try:
        if is_resp:
            await _on_response(event)
        else:
            await _on_request(event)
    except Exception as e:
        url = event.get("request", {}).get("url", "?")
        log("ERR", f"dispatch: {e} -- {url[:60]}")
        try:
            rid = event["requestId"]
            if is_resp:
                await _send_raw("Fetch.continueResponse", {"requestId": rid})
            else:
                await _send_raw("Fetch.continueRequest", {"requestId": rid})
        except Exception:
            pass


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    _load_identity()
    log("INIT", "=" * 60)
    log("INIT", "  SHIELD v4 -- raw CDP (no proxy, no Playwright, no devtools detection)")
    log("INIT", f"  device_id  = {SESSION_DEVICE_ID}")
    log("INIT", f"  cloak_seed = {SESSION_CLOAK_SEED}")
    log("INIT", "=" * 60)

    # Find Chromium page via CDP HTTP API
    with urllib.request.urlopen(f"{CDP_HTTP}/json") as r:
        tabs = json.loads(r.read().decode())

    page_tab = None
    for tab in tabs:
        if tab.get("type") == "page":
            if page_tab is None:
                page_tab = tab
            if "badoo" in tab.get("url", "").lower():
                page_tab = tab
                break

    if not page_tab:
        log("ERR", "No browser page found. Is Chromium running with --remote-debugging-port=9222?")
        return

    ws_url = page_tab["webSocketDebuggerUrl"]
    log("INIT", f"Page: {page_tab.get('url','?')[:60]}")
    log("INIT", f"CDP WebSocket: {ws_url}")

    try:
        ws_connect = websockets.connect(ws_url, max_size=100 * 1024 * 1024)
    except AttributeError:
        ws_connect = websockets.legacy.client.connect(ws_url, max_size=100 * 1024 * 1024)

    async with ws_connect as ws:
        _ws_ref[0] = ws

        # Enable ONLY Fetch domain — no Runtime, no Debugger, no detection surface.
        # Use https://* (not *) to avoid intercepting chrome:// and data: internal URLs.
        await _send_raw("Fetch.enable", {
            "patterns": [
                {"urlPattern": "https://*",        "requestStage": "Request"},
                {"urlPattern": "*badoo.com*",       "requestStage": "Response"},
                {"urlPattern": "*badoocdn.com*",    "requestStage": "Response"},
            ]
        })

        log("LIVE", "CDP Fetch active (raw WebSocket). Monitoring Badoo traffic...")

        last_hb = time.time()

        async for raw in ws:
            msg = json.loads(raw)

            # Route response to awaiting future
            if "id" in msg:
                fut = _pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg.get("result", {}))
                continue

            # Handle Fetch events
            if msg.get("method") == "Fetch.requestPaused":
                asyncio.ensure_future(_dispatch(msg["params"]))

            # Heartbeat log
            if time.time() - last_hb >= 30:
                last_hb = time.time()
                log("LIVE", f"Phase: {_PHASE} | phone={_phone_done} photo={_photo_done}")


if __name__ == "__main__":
    asyncio.run(main())
