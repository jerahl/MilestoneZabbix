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
    """Fetch camera groups + their child cameras / subgroups.

    Per the Milestone OpenAPI spec (Grouping section):

        GET /cameraGroups                         -> { "array": [<group>, ...] }
        GET /cameraGroups/{id}/cameras            -> { "array": [<camera>, ...] }
        GET /cameraGroups/{id}/cameraGroups       -> { "array": [<group>, ...] }
        GET /cameraGroups/{id}?includeChildren=cameras       -> group w/ cameras inline
        GET /cameraGroups/{id}?includeChildren=cameraGroups  -> group w/ subgroups inline

    The spec documents NO page/size pagination on this endpoint and only
    SINGLE-VALUE includeChildren forms (one of cameras OR cameraGroups, not
    both comma-joined). Earlier versions of this fetcher sent both, which
    on strict installs would 400; on permissive ones it succeeded but
    the unknown size= param was silently dropped.

    Strategy here:
      1. GET /cameraGroups (flat list of every group, ignoring hierarchy).
      2. For each group, GET /cameraGroups/{id}/cameras to enumerate its
         camera members. This is the only spec-blessed way to map cameras
         to groups; the per-group ?includeChildren=cameras form is also
         valid but identical in cost.
      3. We do NOT separately fetch /cameraGroups/{id}/cameraGroups —
         the flat top-level list already contains every group regardless
         of nesting depth, and our flattener preserves parent linkage via
         the path-walking model.

    Older releases shipped before the spec used /deviceGroups or /groups;
    if /cameraGroups 404s we fall back to those names so this works
    cross-version.

    Returns (records, endpoint_name) for diagnostics.
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

    def _unpack(payload: dict | None) -> list[dict]:
        """Per spec the envelope is {"array": [...]}, but pre-spec
        installs used {"data": [...]} so we accept both."""
        if not isinstance(payload, dict):
            return []
        for key in ("array", "data"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
        return []

    # Step 1: find the right endpoint name. Try the spec name first.
    endpoints_to_try = ["cameraGroups", "deviceGroups", "groups"]
    chosen_endpoint = None
    groups: list[dict] = []
    last_status = None
    last_body   = ""
    for endpoint in endpoints_to_try:
        url = f"{base}{api_base}/{endpoint}"
        _log(f"GET {url}  (looking for endpoint)")
        status, payload, body = _get(url)
        _log(f"  -> HTTP {status}; body[:200]={body!r}")
        last_status, last_body = status, body
        if status == 404:
            continue
        if status == 200 and payload is not None:
            arr = _unpack(payload)
            chosen_endpoint = endpoint
            groups = arr
            _log(f"  -> endpoint found: /{endpoint} returned {len(arr)} group(s)")
            break
        # Any other status — let the caller see the message.
        raise RuntimeError(
            f"/{endpoint} returned HTTP {status}: {body!r}"
        )

    if chosen_endpoint is None:
        raise RuntimeError(
            f"no groups endpoint found on this install. Tried "
            f"{', '.join('/' + e for e in endpoints_to_try)} — all 404. "
            f"Last HTTP {last_status}, body: {last_body!r}. "
            f"Check --api-base (currently {api_base}) or your XProtect "
            f"REST API version."
        )

    # Step 2: per-group camera enumeration. Spec endpoint is
    # /cameraGroups/{id}/cameras. Empty group list is a totally legal
    # operational state (the install just hasn't created any groups
    # yet) so we don't error here — return early with the empty list
    # and the caller will surface a clear diagnostic.
    if not groups:
        print(f"[groups] /{chosen_endpoint} returned an EMPTY array — "
              f"this XProtect install has no camera groups configured. "
              f"Create groups in Management Client > Devices > Groups, "
              f"or in Smart Client's device tree.",
              file=sys.stderr)
        return groups, chosen_endpoint

    if include_cameras:
        _log(f"  -> enumerating cameras for {len(groups)} group(s) "
             f"via /{chosen_endpoint}/{{id}}/cameras")
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_group_cameras(gid: str) -> list[dict]:
            url = f"{base}{api_base}/{chosen_endpoint}/{gid}/cameras"
            status, payload, body = _get(url)
            if status != 200 or payload is None:
                _log(f"  -> {gid}: HTTP {status} {body!r}")
                return []
            return _unpack(payload)

        # Bounded parallelism — same heuristic as the cameras fetcher's
        # MAC enrichment, capped at 16 to keep API Gateway load sane.
        workers = max(1, min(len(groups), 16))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_group_cameras, g["id"]): g
                for g in groups if isinstance(g, dict) and g.get("id")
            }
            for fut in as_completed(futures):
                g = futures[fut]
                try:
                    cams = fut.result()
                except Exception as exc:  # noqa: BLE001
                    _log(f"  -> {g.get('id')}: fetch failed: {exc!r}")
                    cams = []
                # Stash on the group record so flatten_groups picks it up.
                g["cameras"] = cams

    return groups, chosen_endpoint


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
    diagnostic = ""
    if len(keyed) == 0:
        diagnostic = (
            "No camera groups configured in this XProtect install. "
            "Create groups in Management Client > Devices > Groups "
            "(or Smart Client > Setup > device tree). Until at least one "
            "group exists, the surveillance dashboard's Sites tab will "
            "fall back to host-as-site bucketing."
        )
    out = {
        "__count": len(keyed),
        "__fetched_at": dt.datetime.now(dt.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "__endpoint": endpoint_used,
        "__diagnostic": diagnostic,
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
