#!/usr/bin/env python3
"""
milestone_groups_state.py
-------------------------
One-shot fetcher for Milestone XProtect *camera groups* (the logical
folders Smart Client uses to organise cameras into sites / buildings /
zones). One row per group, with the group's camera GUID list and a
de-duplicated parent-hardware GUID list so downstream consumers can
bucket cameras and devices by group in O(1).

Why this exists
    XProtect's organisational hierarchy is built on cameraGroups —
    every site / campus / building / floor users see in the Smart
    Client tree is a camera group. The Zabbix dashboard wants those
    groups as the "Site" axis instead of treating every Zabbix host as
    a site, so we pull /api/rest/v1/cameraGroups with the child cameras
    inlined and emit a snapshot keyed by group GUID.

    Doing this in a sibling script (rather than extending cameras_state.py)
    keeps responsibilities clear: cameras_state knows about hardware and
    cameras, groups_state knows about organisational folders. Both feed
    independent Zabbix items and refresh on their own cadence.

Output (default):
    JSON with this shape (mirrors the cameras_state.py file layout —
    a root-level by-GUID lookup plus an __array for LLD iteration):

        {
          "__count": N,
          "__fetched_at": "2026-04-29T12:34:56Z",
          "__array": [
              {
                  "id": "<group-guid>",
                  "name": "Bryant HS",
                  "description": "...",
                  "path": "Bryant HS",
                  "parentGroupId": null,
                  "cameraCount": 224,
                  "hardwareCount": 87,
                  "cameraIds": ["<cam-guid>", ...],
                  "hardwareIds": ["<hw-guid>", ...]
              },
              ...
          ],
          "<group-guid>": { ... same shape ... },
          ...
        }

    Per Plan v1.2 §A.5 the __-prefixed diagnostic keys (__count,
    __fetched_at, __array) can never collide with a real group GUID,
    so Zabbix can JSONPath-lookup any group at $["<guid>"] with no
    preprocessing required.

Usage:
    milestone_groups_state.py <host> <username> <password>
                              [--scheme https] [--verify-tls]
                              [--timeout 60] [--client-id GrantValidatorClient]
                              [--idp-path /IDP/connect/token]
                              [--api-base /api/rest/v1]
                              [--page-size 500]
                              [--no-cameras]

Exit codes:
    0  success (JSON on stdout)
    2  authentication failure
    3  HTTP / API error
    4  timeout
    1  other error

Requires: python3.8+ (urllib only — no third-party deps).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


# ---------------------------------------------------------------------------
# HTTP helpers (urllib only — same vocabulary as cameras_state.py so
# operators only have to learn one shape).
# ---------------------------------------------------------------------------
def _ssl_ctx(verify_tls: bool) -> ssl.SSLContext | None:
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def get_token(base: str, idp_path: str, user: str, password: str,
              client_id: str, ctx: ssl.SSLContext | None,
              timeout: float) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "password",
        "username": user,
        "password": password,
        "client_id": client_id,
    }).encode()
    req = urllib.request.Request(
        base + idp_path,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"IDP HTTP {e.code}: {body}") from None
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(
            f"IDP response missing access_token: {str(payload)[:500]}"
        )
    return token


# ---------------------------------------------------------------------------
# Group fetching
#
# /api/rest/v1/cameraGroups returns the logical group hierarchy. With
# ?includeChildren=cameras every group record carries its direct camera
# children inline. Nested subgroups would normally appear under
# children.cameraGroups; we walk that tree depth-first so a flat output
# row exists for every group regardless of nesting depth.
# ---------------------------------------------------------------------------
def fetch_camera_groups(
    base: str, token: str, ctx: ssl.SSLContext | None,
    timeout: float, api_base: str, page_size: int,
    include_cameras: bool, debug: bool = False,
) -> tuple[list[dict], str]:
    """Find the groups endpoint and return (records, endpoint_name).

    XProtect's REST surface has shifted the groups path across versions:
        /api/rest/v1/cameraGroups      — older / Smart Client native
        /api/rest/v1/deviceGroups      — newer canonical name
        /api/rest/v1/groups            — legacy fallback

    Some installs only expose one of the three; we probe each in turn,
    try with includeChildren first, then without, then strip the param
    entirely. Whichever returns a non-empty array wins.

    Returns the raw records plus the endpoint name that succeeded so
    operators can see which path their install uses (logged to stderr).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    def _log(msg: str) -> None:
        if debug:
            print(f"[groups] {msg}", file=sys.stderr)

    def _get(url: str) -> tuple[int, dict | None, str]:
        """GET url. Returns (status, parsed_json, raw_body_snippet)."""
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
                raw = r.read().decode()
            try:
                return (r.status, json.loads(raw), raw[:200])
            except json.JSONDecodeError:
                return (r.status, None, raw[:200])
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            return (e.code, None, body)
        except urllib.error.URLError as e:
            return (0, None, repr(e)[:200])

    # Group records can land under different envelope keys. Try them all.
    def _unpack(payload: dict | None) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        for key in ("array", "data", "items", "result", "groups"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                # Some installs nest the array under data.array
                inner = v.get("array") or v.get("items")
                if isinstance(inner, list):
                    return inner
        # Last resort: if the whole payload looks like a single group
        # (has 'id' + 'name'), wrap it in a list.
        if "id" in payload and ("name" in payload or "displayName" in payload):
            return [payload]
        return []

    # Endpoint × includeChildren-shape combinations to try. The
    # _children=None entry strips the param entirely; useful for
    # installs that don't accept includeChildren at all.
    if include_cameras:
        children_shapes = ["cameras,cameraGroups", "cameras", None]
    else:
        children_shapes = ["cameraGroups", None]
    endpoints = ["cameraGroups", "deviceGroups", "groups"]

    last_status = None
    last_body   = ""
    for endpoint in endpoints:
        for children in children_shapes:
            params = ["page=0", "size=10000"]
            if children:
                params.append(f"includeChildren={children}")
            url = f"{base}{api_base}/{endpoint}?" + "&".join(params)
            _log(f"trying {url}")
            status, payload, body = _get(url)
            _log(f"  -> HTTP {status}; body[:200]={body!r}")
            last_status, last_body = status, body
            if status == 200 and payload is not None:
                arr = _unpack(payload)
                _log(f"  -> unpacked {len(arr)} record(s) from {endpoint}")
                if arr:
                    print(f"[groups] success: {endpoint} "
                          f"({len(arr)} record{'s' if len(arr) != 1 else ''})",
                          file=sys.stderr)
                    # If the endpoint succeeded but children weren't
                    # asked for / weren't returned, fan out per-group
                    # to pick them up so cameraIds are populated.
                    if include_cameras and not _any_has_cameras(arr):
                        _log("  -> no inline cameras; fanning out per group")
                        for g in arr:
                            gid = g.get("id")
                            if not gid:
                                continue
                            detail_url = (
                                f"{base}{api_base}/{endpoint}/{gid}"
                                f"?includeChildren=cameras"
                            )
                            st, pl, _ = _get(detail_url)
                            if st == 200 and isinstance(pl, dict):
                                # Drill past 'data' envelope if present.
                                detail = (pl.get("data")
                                          if isinstance(pl.get("data"), dict)
                                          else pl)
                                # Cameras can live under several shapes.
                                cams = (detail.get("cameras")
                                        or (detail.get("children", {}) or {}).get("cameras")
                                        or [])
                                if isinstance(cams, list) and cams:
                                    g["cameras"] = cams
                    return arr, endpoint

    raise RuntimeError(
        f"no groups endpoint found "
        f"(last HTTP {last_status}, last body: {last_body!r}). "
        f"Run with --debug to see every URL tried; you may need "
        f"to point --api-base at a different prefix or upgrade your "
        f"XProtect REST API."
    )


def _any_has_cameras(records: list[dict]) -> bool:
    """True if any record in the list looks like it carries child cameras."""
    for r in records or []:
        if not isinstance(r, dict):
            continue
        v = r.get("cameras")
        if isinstance(v, list) and v:
            return True
        children = r.get("children")
        if isinstance(children, dict):
            v = children.get("cameras")
            if isinstance(v, list) and v:
                return True
    return False


# ---------------------------------------------------------------------------
# Flatten the group tree
#
# A group in the API response has roughly:
#   { id, displayName / name, description,
#     children: { cameras: [...], cameraGroups: [...] }   (older shape)
#     cameras: [...], cameraGroups: [...]                  (newer shape)
#   }
#
# We walk depth-first, emitting one row per group with the cameras
# directly attached AT THAT LEVEL (we do NOT roll subgroup cameras up
# into the parent — operators should see "Bryant HS" with the cameras
# directly under it, and each child subgroup as its own row).
#
# If you want roll-up behaviour, do it downstream by walking parentGroupId.
# ---------------------------------------------------------------------------
def _kids(node: dict, key: str) -> list:
    """Get child list 'key' tolerantly: newer API exposes it flat, older
    nests it under .children. Always returns a list (possibly empty)."""
    v = node.get(key)
    if isinstance(v, list):
        return v
    children = node.get("children")
    if isinstance(children, dict):
        v2 = children.get(key)
        if isinstance(v2, list):
            return v2
    return []


def flatten_groups(
    root_groups: list[dict],
) -> list[dict]:
    """Walk the group tree, emit one flat row per group.

    Each row carries:
        id, name, description, path (slash-separated lineage),
        parentGroupId,
        cameraIds[], hardwareIds[] (deduped from this group's cameras),
        cameraCount, hardwareCount
    """
    out: list[dict] = []

    def visit(node: dict, parent_id: str | None, parent_path: str) -> None:
        if not isinstance(node, dict):
            return
        gid = node.get("id")
        if not gid:
            return
        name = (
            node.get("displayName")
            or node.get("name")
            or node.get("description")
            or gid
        )
        path = f"{parent_path}/{name}" if parent_path else name

        # Cameras directly under this group.
        cameras = _kids(node, "cameras")
        cam_ids: list[str] = []
        hw_ids: list[str] = []
        for cam in cameras:
            if not isinstance(cam, dict):
                continue
            cid = cam.get("id")
            if cid:
                cam_ids.append(cid)
            # Camera record carries its hardware via relations.parent.id;
            # tolerate the older flat 'parentId' shape too.
            rel = cam.get("relations") or {}
            parent = rel.get("parent") if isinstance(rel, dict) else None
            hwid = (
                parent.get("id") if isinstance(parent, dict) else None
            ) or cam.get("hardwareId") or cam.get("parentId")
            if hwid:
                hw_ids.append(hwid)

        # De-dup hardware ids (one hardware can host several cameras).
        hw_unique = list(dict.fromkeys(hw_ids))

        out.append({
            "id": gid,
            "name": name,
            "description": node.get("description") or "",
            "path": path,
            "parentGroupId": parent_id,
            "cameraCount": len(cam_ids),
            "hardwareCount": len(hw_unique),
            "cameraIds": cam_ids,
            "hardwareIds": hw_unique,
        })

        # Recurse into subgroups.
        for sub in _kids(node, "cameraGroups"):
            visit(sub, gid, path)

    for g in root_groups or []:
        visit(g, None, "")

    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("host",
                    help="API Gateway host (no scheme), "
                         "e.g. milestone.example.com")
    ap.add_argument("username")
    ap.add_argument("password")
    ap.add_argument("--scheme", default="https",
                    choices=("http", "https"))
    ap.add_argument("--verify-tls", action="store_true",
                    help="Enforce TLS cert validation (default: off)")
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="Per-request timeout in seconds (default: 60)")
    ap.add_argument("--client-id", default="GrantValidatorClient")
    ap.add_argument("--idp-path", default="/IDP/connect/token",
                    help="IDP token endpoint path. Default "
                         "/IDP/connect/token; some installs use "
                         "/API/IDP/connect/token.")
    ap.add_argument("--api-base", default="/api/rest/v1",
                    help="REST API base path (default /api/rest/v1)")
    ap.add_argument("--page-size", type=int, default=500,
                    help="Pagination page size for fallback path "
                         "(default 500)")
    ap.add_argument("--no-cameras", action="store_true",
                    help="Skip camera enumeration per group. Groups "
                         "still get a row with cameraCount=0; useful "
                         "for diagnosing just the group tree shape.")
    ap.add_argument("--debug", action="store_true",
                    help="Log every endpoint URL tried, the HTTP "
                         "status, and the first 200 bytes of the "
                         "response body to stderr. Use this to "
                         "figure out which groups endpoint your "
                         "XProtect version exposes when the snapshot "
                         "comes back empty.")
    args = ap.parse_args()

    base = f"{args.scheme}://{args.host}"
    ctx = _ssl_ctx(args.verify_tls) if args.scheme == "https" else None

    # Step 1: token.
    try:
        token = get_token(
            base, args.idp_path, args.username, args.password,
            args.client_id, ctx, args.timeout,
        )
    except RuntimeError as e:
        msg = str(e)
        if any(s in msg for s in ("IDP HTTP 400", "IDP HTTP 401",
                                   "invalid_username_or_password",
                                   "LockedOut")):
            print(json.dumps({"error": "auth_failed", "detail": msg}),
                  file=sys.stderr)
            return 2
        print(json.dumps({"error": "idp_error", "detail": msg}),
              file=sys.stderr)
        return 3
    except urllib.error.URLError as e:
        print(json.dumps({"error": "network_error",
                          "detail": repr(e)}),
              file=sys.stderr)
        return 3
    except (TimeoutError, OSError) as e:
        print(json.dumps({"error": "timeout", "detail": repr(e)}),
              file=sys.stderr)
        return 4

    # Step 2: camera groups.
    endpoint_used = ""
    try:
        groups_raw, endpoint_used = fetch_camera_groups(
            base, token, ctx, args.timeout,
            args.api_base, args.page_size,
            include_cameras=not args.no_cameras,
            debug=args.debug,
        )
    except RuntimeError as e:
        print(json.dumps({"error": "api_error", "detail": str(e)}),
              file=sys.stderr)
        return 3
    except urllib.error.URLError as e:
        print(json.dumps({"error": "network_error",
                          "detail": repr(e)}),
              file=sys.stderr)
        return 3
    except (TimeoutError, OSError) as e:
        print(json.dumps({"error": "timeout", "detail": repr(e)}),
              file=sys.stderr)
        return 4
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": "unexpected", "detail": repr(e)}),
              file=sys.stderr)
        return 1

    # Step 3: flatten the tree.
    flat = flatten_groups(groups_raw)
    keyed: dict[str, dict] = {row["id"]: row for row in flat}

    # Diagnostic top-level fields (same __-prefix convention as
    # cameras_state.py so they can never collide with a group GUID).
    out = {
        "__count": len(keyed),
        "__fetched_at": dt.datetime.now(dt.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "__endpoint": endpoint_used,
        "__root_count": len(groups_raw),
        "__total_cameras": sum(r["cameraCount"] for r in flat),
        "__total_hardware": sum(r["hardwareCount"] for r in flat),
        "__array": flat,
    }
    out.update(keyed)

    sys.stdout.write(json.dumps(out, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
