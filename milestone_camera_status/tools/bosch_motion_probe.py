#!/usr/bin/env python3
"""
bosch_motion_probe.py
=====================
Tail-style watcher for the Bosch private MIB branch (.1.3.6.1.4.1.3967.1)
on a single FLEXIDOME / DINION / AUTODOME camera. Prints a line every time
a watched OID changes value, so an operator can wave a hand at the camera
and immediately see which OIDs correspond to motion / tamper / IVA / IR.

Why this exists
---------------
The M0 walk-analysis docs (M0_Bosch_SNMP_Walk_Analysis.md and the two
follow-ups) decoded the cardinality and per-device/per-imager layout of
.3967.1.* but couldn't label the actual semantics of the alarm-state
matrices (.3967.1.3.{1,2,3}.1.1.X, .3967.1.3.4.1.1.{1..16}) or the slow-
moving sensor scalars (.3967.1.1.{7,9}.1.1.1) because they were all zero
or near-static in the snapshot walks. This script polls those branches at
~1 Hz and prints diffs — point the camera at a static scene, run the
script, wave a hand, watch which OIDs blink.

The script also writes a JSON snapshot of all watched OIDs at intervals
so a long unattended run can be replayed.

Requires
--------
- Python 3.8+ (stdlib only)
- net-snmp `snmpget` + `snmpwalk` in $PATH (the operator already has these
  on the Zabbix proxy host — same binaries Zabbix uses internally)
- SNMP credentials for the camera (community for v1/v2c, or v3 user)

Usage
-----
    # Simplest — public community, watch one camera, print diffs forever
    ./bosch_motion_probe.py 10.24.18.22

    # SNMPv3 (auth + priv)
    ./bosch_motion_probe.py 10.112.18.48 \\
        --version 3 --v3-user zbx_monitor \\
        --v3-auth-proto SHA --v3-auth-pass 'authpw' \\
        --v3-priv-proto AES --v3-priv-pass 'privpw'

    # One-shot snapshot (no watch loop) — write current state to a file
    ./bosch_motion_probe.py 10.24.18.22 --snapshot before.json --once

    # ... wave a hand at the camera ...

    ./bosch_motion_probe.py 10.24.18.22 --snapshot after.json --once
    ./bosch_motion_probe.py 10.24.18.22 --diff before.json after.json

    # Faster poll (default 1.0s — for very brief events bump to 0.25s)
    ./bosch_motion_probe.py 10.24.18.22 --interval 0.25

Exit codes
----------
    0  clean exit (Ctrl-C in watch mode, or normal completion)
    2  snmpget/snmpwalk failure (credentials wrong, camera unreachable, etc.)
    3  invalid arguments
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# OID catalogue — every OID we want to watch, with a human-readable label.
# Per-imager OIDs use the placeholder {idx} which gets expanded at discovery
# time based on how many imagers the camera reports in .3967.1.1.1.3.1.1.
# ─────────────────────────────────────────────────────────────────────────────
BOSCH = "1.3.6.1.4.1.3967.1"

# Per-imager — one row per logical imager (1 on a 5100i, 4 on a 7000i multi)
PER_IMAGER_TEMPLATES: dict[str, str] = {
    f"{BOSCH}.1.1.4.1.1.1.{{idx}}":   "per-imager counter #1 (firmware-internal, near-static)",
    f"{BOSCH}.1.1.4.1.1.2.{{idx}}":   "per-imager counter #2 (zeroed at rest, candidate motion-trip lifetime)",
    f"{BOSCH}.1.3.1.1.1.{{idx}}":     "per-imager alarm state #1",
    f"{BOSCH}.1.3.2.1.1.{{idx}}":     "per-imager alarm state #2",
    f"{BOSCH}.1.3.3.1.1.{{idx}}":     "per-imager alarm state #3 (5-byte bitmap)",
    f"{BOSCH}.1.2.2.1.1.{{slot}}":    "encoder slot blob (8 per imager, idx (i-1)*8+1 .. i*8)",
}

# Per-device — fixed regardless of imager count
PER_DEVICE_OIDS: dict[str, str] = {
    # 16-slot device-wide alarm matrix
    **{f"{BOSCH}.1.3.4.1.1.{i}": f"device alarm slot {i:>2} (housing-wide)"
       for i in range(1, 17)},
    # Two device-wide counters
    f"{BOSCH}.1.4.1.1.1.1.1":      "device counter #1 (IVA / analytics device-wide)",
    f"{BOSCH}.1.4.2.1.1.1.1":      "device counter #2 (IVA / analytics device-wide)",
    # Dynamic-ish scalars whose semantics are still unlabelled
    f"{BOSCH}.1.1.7.1.1.1":        "sensor scalar A (varies in 10s; candidate ambient / temp)",
    f"{BOSCH}.1.1.9.1.1.1":        "sensor scalar B (varies slowly; candidate IR intensity)",
    f"{BOSCH}.1.1.9.1.4.1":        "sensor scalar C (4-byte blob; candidate IR state)",
    f"{BOSCH}.1.1.10.0":           "scalar (often static at 130 — could be config preset)",
    # Config digest — moves only on config change
    f"{BOSCH}.1.1.12.0":           "device config digest (16 bytes; trailing 4 = checksum)",
    # State blobs in .1.1.5 — slow-moving but useful change detectors
    f"{BOSCH}.1.1.5.12.0":         "persistent counter (survives reboot; candidate operating-hours)",
    f"{BOSCH}.1.1.5.13.0":         "encoded state #1 (contains IP-last-3-octets)",
    f"{BOSCH}.1.1.5.14.0":         "encoded state #2 (long hex blob)",
    f"{BOSCH}.1.1.5.15.1.1.1":     "encoded state #3 (contains 'Unknown' label-table fragment)",
}

# Per-imager NAME table — drives discovery
IMAGER_NAME_TABLE_ROOT = f"{BOSCH}.1.1.1.3.1.1"

# Identity OIDs printed once at startup so the operator sees what they're watching
IDENTITY_OIDS: dict[str, str] = {
    f"{BOSCH}.1.1.1.7.0":          "vendor",
    f"{BOSCH}.1.1.5.1.0":          "model",
    f"{BOSCH}.1.1.1.5.0":          "firmware short code",
    f"{BOSCH}.1.1.1.6.0":          "board fingerprint",
    f"{BOSCH}.1.5.1.1.0":          "mac",
    f"{BOSCH}.1.5.1.2.0":          "ip",
}


# G29 — net-snmpd on Bosch returns octet strings with trailing binary garbage.
# Strip everything after the first non-printable byte.
G29_STRIP = re.compile(rb"^([\x20-\x7E]*).*", re.DOTALL)


def strip_g29(raw: bytes) -> str:
    m = G29_STRIP.match(raw)
    return (m.group(1) if m else raw).decode("ascii", errors="replace").rstrip()


# ─────────────────────────────────────────────────────────────────────────────
# snmpget / snmpwalk wrappers
# ─────────────────────────────────────────────────────────────────────────────
class SnmpClient:
    """Thin wrapper around net-snmp snmpget/snmpwalk. Returns OID → raw value."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.host = args.host
        self.timeout = args.timeout
        self.retries = args.retries

        # Build the common arg list for v1/v2c vs v3
        if args.version in ("1", "2c"):
            self._auth = ["-v", args.version, "-c", args.community]
        elif args.version == "3":
            if not args.v3_user:
                fatal("--v3-user required for SNMPv3")
            self._auth = ["-v", "3", "-u", args.v3_user, "-l", args.v3_level]
            if args.v3_level in ("authNoPriv", "authPriv"):
                self._auth += ["-a", args.v3_auth_proto, "-A", args.v3_auth_pass]
            if args.v3_level == "authPriv":
                self._auth += ["-x", args.v3_priv_proto, "-X", args.v3_priv_pass]
        else:
            fatal(f"unknown SNMP version {args.version!r}")

        for tool in ("snmpget", "snmpwalk"):
            if shutil.which(tool) is None:
                fatal(f"required binary {tool!r} not found in $PATH "
                      f"(install net-snmp / net-snmp-utils)")

    def _common(self) -> list[str]:
        return self._auth + [
            "-Oqn",                       # numeric OID + quick value (one line each)
            "-OU",                        # don't strip OID instances
            "-t", str(self.timeout),
            "-r", str(self.retries),
        ]

    def get_many(self, oids: list[str]) -> dict[str, str]:
        """Batch GET. Returns {oid: value-stripped}. Missing rows are absent."""
        if not oids:
            return {}
        out: dict[str, str] = {}
        # net-snmp accepts many OIDs in one snmpget invocation. Cap at 60 per call
        # to stay well within the camera's response-size budget.
        batch = 60
        for i in range(0, len(oids), batch):
            chunk = oids[i:i + batch]
            cmd = ["snmpget", *self._common(), self.host, *chunk]
            rc, stdout, stderr = self._run(cmd)
            if rc != 0:
                # snmpget exits non-zero on any failure (camera down, auth bad,
                # at least one OID returns noSuchObject). Try to salvage rows.
                pass
            for line in stdout.splitlines():
                self._parse_line(line, out)
        return out

    def walk(self, root_oid: str) -> dict[str, str]:
        """Walk a sub-tree. Returns {oid: value-stripped}."""
        cmd = ["snmpwalk", *self._common(), self.host, root_oid]
        rc, stdout, _stderr = self._run(cmd)
        if rc != 0:
            return {}
        out: dict[str, str] = {}
        for line in stdout.splitlines():
            self._parse_line(line, out)
        return out

    @staticmethod
    def _parse_line(line: bytes | str, into: dict[str, str]) -> None:
        # `snmpget -Oqn` produces lines like:
        #   .1.3.6.1.4.1.3967.1.1.5.1.0 "FLEXIDOME indoor 5100i IR - 5MP"
        # or, for integers / counters:
        #   .1.3.6.1.4.1.3967.1.1.4.1.1.1.1 659211
        # or, on missing rows:
        #   .1.3.6.1.4.1.3967.1.99.99.0 = No Such Object available
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.strip()
        if not line or line.startswith("End of MIB"):
            return
        # Drop the noSuchObject / noSuchInstance markers — they're absences
        if "No Such" in line or "= No more" in line:
            return
        # Split into OID + value on the first whitespace run
        m = re.match(r"^\.?(\S+)\s+(.*)$", line)
        if not m:
            return
        oid, raw = m.group(1), m.group(2)
        # If snmp returned a quoted string, unquote — net-snmp uses double-quotes
        if len(raw) >= 2 and raw[0] == '"' and raw.endswith('"'):
            raw = raw[1:-1]
        # G29 — strip trailing binary garbage
        clean = strip_g29(raw.encode("utf-8", errors="replace"))
        into[oid] = clean

    @staticmethod
    def _run(cmd: list[str]) -> tuple[int, bytes, bytes]:
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=30)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, b"", b"timeout"
        except FileNotFoundError as e:
            fatal(f"binary not found: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Imager-instance discovery + OID-list assembly
# ─────────────────────────────────────────────────────────────────────────────
def discover_imagers(snmp: SnmpClient) -> list[tuple[int, str]]:
    """Walk .3967.1.1.1.3.1.1 and return [(idx, name), ...]."""
    rows = snmp.walk(IMAGER_NAME_TABLE_ROOT)
    if not rows:
        # The walk failing usually means camera unreachable or auth wrong —
        # discovered_imagers == [] would silently produce an empty watch list.
        fatal(f"could not walk {IMAGER_NAME_TABLE_ROOT} — check host / auth")
    out: list[tuple[int, str]] = []
    for oid, value in rows.items():
        # OID looks like .1.3.6.1.4.1.3967.1.1.1.3.1.1.<idx>
        tail = oid[len(IMAGER_NAME_TABLE_ROOT) + 1:]
        if tail.isdigit():
            out.append((int(tail), value))
    out.sort(key=lambda t: t[0])
    return out


def build_watch_oids(imagers: list[tuple[int, str]]) -> dict[str, str]:
    """Returns {oid: label} merging per-imager (expanded) + per-device OIDs."""
    watched: dict[str, str] = {}
    # Per-imager OIDs
    for idx, name in imagers:
        for template, label in PER_IMAGER_TEMPLATES.items():
            if "{slot}" in template:
                # 8 encoder slots per imager, row index (idx-1)*8+1 .. idx*8
                for slot in range((idx - 1) * 8 + 1, idx * 8 + 1):
                    oid = template.format(slot=slot)
                    watched[oid] = f"imager {idx} ({name}) · {label} · row {slot}"
            elif "{idx}" in template:
                oid = template.format(idx=idx)
                watched[oid] = f"imager {idx} ({name}) · {label}"
    # Per-device OIDs
    for oid, label in PER_DEVICE_OIDS.items():
        watched[oid] = f"device · {label}"
    return watched


# ─────────────────────────────────────────────────────────────────────────────
# Output formatting
# ─────────────────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def print_banner(identity: dict[str, str], imagers: list[tuple[int, str]],
                 watched: dict[str, str], args: argparse.Namespace) -> None:
    print(f"# bosch_motion_probe.py  ·  host={args.host}  ·  started {now_iso()}")
    print(f"#   model           : {identity.get(f'{BOSCH}.1.1.5.1.0', '?')}")
    print(f"#   vendor          : {identity.get(f'{BOSCH}.1.1.1.7.0', '?')}")
    print(f"#   firmware short  : {identity.get(f'{BOSCH}.1.1.1.5.0', '?')}")
    print(f"#   board fingerprt : {identity.get(f'{BOSCH}.1.1.1.6.0', '?')}")
    print(f"#   mac / ip        : {identity.get(f'{BOSCH}.1.5.1.1.0', '?')} / "
          f"{identity.get(f'{BOSCH}.1.5.1.2.0', '?')}")
    print(f"#   imagers         : {len(imagers)}  →  "
          + ", ".join(f"{idx}={name!r}" for idx, name in imagers))
    print(f"#   watching        : {len(watched)} OIDs at {args.interval}s interval")
    print(f"#   tip: wave a hand at the camera; only diffs are printed below.")
    print(f"#")
    print(f"#   timestamp                  scope          oid                                  old → new")


def print_change(oid: str, old: str, new: str, label: str) -> None:
    # Truncate long blobs for readability
    def shorten(s: str, n: int = 40) -> str:
        return s if len(s) <= n else s[:n - 1] + "…"
    scope = "imager" if "imager" in label else "device"
    print(f"  {now_iso():<27} {scope:<12} {oid:<38} {shorten(old, 28)!r:>30} → {shorten(new, 28)!r}")
    print(f"  {'':<27} {'':<12} └─ {label}")


# ─────────────────────────────────────────────────────────────────────────────
# Modes
# ─────────────────────────────────────────────────────────────────────────────
def cmd_watch(snmp: SnmpClient, args: argparse.Namespace) -> int:
    identity = snmp.get_many(list(IDENTITY_OIDS.keys()))
    imagers = discover_imagers(snmp)
    watched = build_watch_oids(imagers)
    print_banner(identity, imagers, watched, args)

    oids = list(watched.keys())
    last: dict[str, str] = snmp.get_many(oids)

    # Optional periodic snapshot
    next_snapshot_at = time.monotonic() + args.snapshot_every if args.snapshot_every else None

    # Graceful Ctrl-C
    stop = {"flag": False}
    def _sigint(*_): stop["flag"] = True
    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    while not stop["flag"]:
        time.sleep(args.interval)
        current = snmp.get_many(oids)
        for oid in oids:
            old = last.get(oid)
            new = current.get(oid)
            if old != new and new is not None:
                print_change(oid, str(old), str(new), watched[oid])
        if current:
            last = current
        if next_snapshot_at and time.monotonic() >= next_snapshot_at:
            path = f"{args.snapshot_prefix}-{int(time.time())}.json"
            write_snapshot(path, args.host, identity, imagers, watched, last)
            print(f"# snapshot written: {path}", flush=True)
            next_snapshot_at = time.monotonic() + args.snapshot_every

    print(f"# stopped at {now_iso()}", flush=True)
    return 0


def cmd_once(snmp: SnmpClient, args: argparse.Namespace) -> int:
    identity = snmp.get_many(list(IDENTITY_OIDS.keys()))
    imagers = discover_imagers(snmp)
    watched = build_watch_oids(imagers)
    values = snmp.get_many(list(watched.keys()))
    if args.snapshot:
        write_snapshot(args.snapshot, args.host, identity, imagers, watched, values)
        print(f"snapshot written: {args.snapshot}  ·  {len(values)} OIDs captured")
    else:
        # No --snapshot → pretty-print to stdout
        print(f"# {args.host}  ·  {now_iso()}")
        for oid in sorted(watched):
            v = values.get(oid, "(absent)")
            print(f"  {oid:<42} {v!r:>30}   {watched[oid]}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    before = json.loads(open(args.before).read())
    after = json.loads(open(args.after).read())
    bv = before["values"]
    av = after["values"]
    labels = {**before.get("labels", {}), **after.get("labels", {})}
    keys = sorted(set(bv) | set(av))
    diffs = 0
    print(f"# diff  before={args.before}  after={args.after}")
    print(f"# before captured: {before.get('captured_at', '?')}")
    print(f"# after  captured: {after.get('captured_at', '?')}")
    for k in keys:
        if bv.get(k) != av.get(k):
            diffs += 1
            print(f"  {k:<42} {str(bv.get(k, '(absent)'))!r:>30} → {str(av.get(k, '(absent)'))!r}")
            if k in labels:
                print(f"  {'':<42} └─ {labels[k]}")
    print(f"# {diffs} OID(s) changed")
    return 0 if diffs >= 0 else 1


def write_snapshot(path: str, host: str, identity: dict[str, str],
                   imagers: list[tuple[int, str]], watched: dict[str, str],
                   values: dict[str, str]) -> None:
    payload = {
        "captured_at": now_iso(),
        "host": host,
        "identity": identity,
        "imagers": [{"idx": i, "name": n} for i, n in imagers],
        "labels": watched,
        "values": values,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Tail-watch the Bosch private MIB for motion / alarm / sensor changes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[1] if "Usage" in __doc__ else "",
    )
    p.add_argument("host", nargs="?", help="camera IP or hostname")

    # SNMP auth
    p.add_argument("--version", default="2c", choices=["1", "2c", "3"], help="SNMP version")
    p.add_argument("--community", default="public", help="v1/v2c community (default: public)")
    p.add_argument("--v3-user", default=None)
    p.add_argument("--v3-level", default="authPriv", choices=["noAuthNoPriv", "authNoPriv", "authPriv"])
    p.add_argument("--v3-auth-proto", default="SHA", choices=["MD5", "SHA"])
    p.add_argument("--v3-auth-pass", default="")
    p.add_argument("--v3-priv-proto", default="AES", choices=["DES", "AES"])
    p.add_argument("--v3-priv-pass", default="")
    p.add_argument("--timeout", type=int, default=2, help="snmpget timeout in seconds (default 2)")
    p.add_argument("--retries", type=int, default=1, help="snmpget retry count (default 1)")

    # Mode
    p.add_argument("--once", action="store_true",
                   help="poll once and exit (default: watch forever)")
    p.add_argument("--snapshot", default=None,
                   help="write JSON snapshot to this path (use with --once)")
    p.add_argument("--snapshot-every", type=int, default=0, metavar="SECONDS",
                   help="in watch mode, additionally write a snapshot every N seconds")
    p.add_argument("--snapshot-prefix", default="bosch-snapshot",
                   help="prefix for periodic snapshots (default: bosch-snapshot)")

    # Watch tuning
    p.add_argument("--interval", type=float, default=1.0,
                   help="poll interval in seconds (default 1.0; 0.25 for fast motion testing)")

    # Diff mode (alternative to host-based modes)
    p.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"), default=None,
                   help="compare two snapshot JSONs and print differences (no SNMP traffic)")

    return p.parse_args(argv)


def fatal(msg: str) -> None:
    print(f"bosch_motion_probe: error: {msg}", file=sys.stderr)
    sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.diff:
        args.before, args.after = args.diff
        return cmd_diff(args)

    if not args.host:
        fatal("host argument required (or use --diff BEFORE AFTER)")

    snmp = SnmpClient(args)
    if args.once:
        return cmd_once(snmp, args)
    return cmd_watch(snmp, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
