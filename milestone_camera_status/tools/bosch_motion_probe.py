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
BOSCH = "1.3.6.1.4.1.3967"

# OIDs whose value moves every poll on an idle, undisturbed camera. Suppressed
# from the change-print stream by default (--ignore-noise, on by default) so
# real motion / alarm signals stand out. The classifications come from the
# pilot 5100i live watch sessions in May 2026 — see
# M0_Bosch_SNMP_Walk_5100i_PostUpdate.md §5 for the per-OID evidence.
NOISY_OIDS = {
    f"{BOSCH}.1.1.12.0":         "tick counter (+1 per second; not a config digest)",
    f"{BOSCH}.1.1.7.1.1.1":      "sensor scalar A (slow drift — colour-temp / sensor-temp)",
    f"{BOSCH}.1.1.9.1.1.1":      "auto-exposure scalar (byte 0 of .9.1.4.1)",
    f"{BOSCH}.1.1.9.1.4.1":      "auto-exposure tuple (iris/?/shutter/?)",
}

# Per-imager — one row per logical imager (1 on a 5100i, 4 on a 7000i multi)
PER_IMAGER_TEMPLATES: dict[str, str] = {
    f"{BOSCH}.1.1.4.1.1.1.{{idx}}":   "per-imager counter #1 (firmware-internal, near-static)",
    f"{BOSCH}.1.1.4.1.1.2.{{idx}}":   "per-imager motion-trip indicator (0 idle / 1 with active VCA — possibly lifetime counter, TBD)",
    f"{BOSCH}.1.3.1.1.1.{{idx}}":     "per-imager alarm state #1 (reserved — not toggled by VCA motion; candidate input-contact / tamper)",
    f"{BOSCH}.1.3.2.1.1.{{idx}}":     "per-imager VCA motion-active boolean (0 idle / 1 alarm)",
    f"{BOSCH}.1.3.3.1.1.{{idx}}":     "per-imager alarm-detail bitmap (5 bytes: [0]=flags, [3]=alarm-type, [4]=rule-index)",
    f"{BOSCH}.1.2.2.1.1.{{slot}}":    "encoder slot blob (bytes 4-7=current bitrate kbps, 20-27=WxH, 28=codec)",
}

# Per-device — fixed regardless of imager count
PER_DEVICE_OIDS: dict[str, str] = {
    # 16-slot device-wide alarm matrix
    **{f"{BOSCH}.1.3.4.1.1.{i}": f"device alarm slot {i:>2} (housing-wide)"
       for i in range(1, 17)},
    # Two device-wide counters
    f"{BOSCH}.1.4.1.1.1.1.1":      "device counter #1 (IVA / analytics device-wide)",
    f"{BOSCH}.1.4.2.1.1.1.1":      "device counter #2 (IVA / analytics device-wide)",
    # Dynamic-ish scalars whose semantics are now partially understood
    f"{BOSCH}.1.1.7.1.1.1":        NOISY_OIDS[f"{BOSCH}.1.1.7.1.1.1"],
    f"{BOSCH}.1.1.9.1.1.1":        NOISY_OIDS[f"{BOSCH}.1.1.9.1.1.1"],
    f"{BOSCH}.1.1.9.1.4.1":        NOISY_OIDS[f"{BOSCH}.1.1.9.1.4.1"],
    f"{BOSCH}.1.1.10.0":           "scalar (often static at 130 — could be config preset)",
    # Per-second tick counter — moves every poll on an idle camera
    f"{BOSCH}.1.1.12.0":           NOISY_OIDS[f"{BOSCH}.1.1.12.0"],
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
# Strip everything after the first non-printable byte. Applied to printable text
# values only — hex-strings (which are space-separated 2-char nibble pairs and
# would otherwise be passed through whole) are detected by the type-prefix and
# left alone.
G29_STRIP = re.compile(rb"^([\x20-\x7E]*).*", re.DOTALL)
HEX_VALUE_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}\s*)+$")


def strip_g29(raw: bytes) -> str:
    m = G29_STRIP.match(raw)
    return (m.group(1) if m else raw).decode("ascii", errors="replace").rstrip()


# Symbolic OID prefixes net-snmp emits when MIBs are loaded in /etc/snmp/snmp.conf.
# `-Oqn` *should* force numeric output, but on some builds it's not enough —
# normalise both forms here so the script works regardless of the host's snmp.conf.
OID_PREFIX_REWRITES = [
    ("SNMPv2-SMI::enterprises.", "1.3.6.1.4.1."),
    ("SNMPv2-SMI::mib-2.", "1.3.6.1.2.1."),
    ("SNMPv2-SMI::private.", "1.3.6.1.4.1."),
    ("iso.org.dod.internet.private.enterprises.", "1.3.6.1.4.1."),
    ("iso.org.dod.internet.mgmt.mib-2.", "1.3.6.1.2.1."),
    ("iso.3.6.1.4.1.", "1.3.6.1.4.1."),
    ("iso.3.6.1.2.1.", "1.3.6.1.2.1."),
    ("iso.", "1."),                # last-ditch — covers raw `iso.X.X…` output
]


def normalize_oid(oid: str) -> str:
    oid = oid.lstrip(".")
    for sym, num in OID_PREFIX_REWRITES:
        if oid.startswith(sym):
            return num + oid[len(sym):]
    return oid


def _strip_type_prefix(raw: str) -> str:
    """`STRING: "Camera 1"` → `"Camera 1"`. Pass-through if no prefix."""
    m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", raw, re.DOTALL)
    return m.group(2) if m else raw


def parse_snmp_text(text: str) -> dict[str, str]:
    """Parse snmpwalk / snmpget output. Handles:
      - quick (`-Oq`)  : '.1.2.3 "value"'
      - verbose default: 'OID = TYPE: value'
      - symbolic OIDs  : 'SNMPv2-SMI::enterprises.3967.X = …'
      - multi-line hex : continuation lines for wrapped Hex-STRING values.
    Returns {numeric-oid (no leading dot): cleaned-value-string}.
    """
    rows: dict[str, str] = {}
    current_oid: str | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_oid, current_parts
        if current_oid is None:
            return
        raw = " ".join(p.strip() for p in current_parts if p.strip())
        raw = _strip_type_prefix(raw).strip()
        # Unquote outer double-quotes
        if len(raw) >= 2 and raw[0] == '"' and raw.endswith('"'):
            raw = raw[1:-1]
        # Apply G29 strip only to free text — leave hex-strings alone.
        if HEX_VALUE_RE.match(raw):
            cleaned = raw
        else:
            cleaned = strip_g29(raw.encode("utf-8", errors="replace"))
        rows[current_oid] = cleaned
        current_oid = None
        current_parts = []

    for line in text.splitlines():
        s = line.rstrip()
        if not s:
            continue
        # Drop noSuchObject / End-of-MIB markers — these aren't data.
        if ("No Such" in s) or s.startswith("End of MIB") or ("No more variables" in s):
            continue
        # A new OID line starts with optional '.', then a token containing at
        # least one '.' or '::' (so 'eth0' continuation lines don't trigger).
        # The token is followed by either ' = ' (verbose) or whitespace (quick).
        m = re.match(r"^\.?([A-Za-z0-9_:.-]+?)\s*(=|\s)\s*(.*)$", s)
        if m and ("." in m.group(1) or "::" in m.group(1)):
            flush()
            current_oid = normalize_oid(m.group(1))
            current_parts = [m.group(3)]
        else:
            # Continuation of previous (e.g. multi-line Hex-STRING)
            if current_oid is not None:
                current_parts.append(s.strip())
    flush()
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# snmpget / snmpwalk wrappers
# ─────────────────────────────────────────────────────────────────────────────
class SnmpClient:
    """Thin wrapper around net-snmp snmpget/snmpwalk. Returns OID → raw value."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.host = args.host
        self.timeout = args.timeout
        self.retries = args.retries
        self.debug = getattr(args, "debug", False)
        self.last_stderr: str = ""           # most recent snmp stderr — surfaced in error paths

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

    def preflight(self) -> None:
        """Confirm basic SNMP works before doing the imager discovery walk.

        Tries to GET sysDescr.0 (.1.3.6.1.2.1.1.1.0) — every camera serves this.
        Failure here means SNMP itself is broken (wrong version, wrong community,
        unreachable, blocked port, etc.). Failure later (on the imager-name walk)
        means SNMP works but the Bosch private branch isn't there — i.e. wrong
        vendor, or SNMP enabled but Bosch MIB module disabled.
        """
        cmd = ["snmpget", *self._common(), self.host, "1.3.6.1.2.1.1.1.0"]
        rc, stdout, stderr = self._run(cmd)
        if rc != 0 or not stdout.strip():
            err = (stderr.decode("utf-8", errors="replace").strip()
                   or stdout.decode("utf-8", errors="replace").strip()
                   or "(no output)")
            hint = self._diagnose(err)
            fatal(
                f"SNMP pre-flight failed against {self.host}\n"
                f"  command : {redact_cmd(cmd)}\n"
                f"  stderr  : {err}\n"
                f"  {hint}"
            )
        # Pre-flight succeeded — surface the camera identity so the operator knows
        # they reached *something*, even if it turns out not to be a Bosch.
        text = stdout.decode("utf-8", errors="replace").strip()
        if self.debug:
            print(f"# preflight ok: {text}", file=sys.stderr)

    @staticmethod
    def _diagnose(stderr: str) -> str:
        """Translate a net-snmp error string into an operator-actionable hint."""
        s = stderr.lower()
        if "timeout" in s or "no response" in s:
            return ("hint: camera didn't respond on UDP/161. Check (a) ICMP reachability, "
                    "(b) SNMP is enabled in the Bosch browser UI under "
                    "Configuration → General → Network → SNMP, "
                    "(c) the proxy isn't blocked by an ACL.")
        if "authentication" in s or "authenticationfailure" in s or "auth failure" in s:
            return ("hint: SNMPv3 auth failed. Check --v3-user, --v3-auth-proto "
                    "(MD5 vs SHA), --v3-auth-pass, --v3-priv-proto (DES vs AES), "
                    "--v3-priv-pass. Bosch v3 users are configured per-camera; the "
                    "default 'service' account does NOT have SNMP access — create a "
                    "dedicated read-only user.")
        if "unknown user" in s or "usmstatsunknownusernames" in s:
            return ("hint: SNMPv3 user not configured on the camera. Create one in the "
                    "Bosch UI under Configuration → Service → SNMP.")
        if "unknown community" in s or "no access" in s or "noaccess" in s:
            return ("hint: SNMPv1/v2c community wrong, or v1/v2c disabled. Recent Bosch "
                    "firmware ships with v1/v2c DISABLED — try --version 3 with an "
                    "operator-created v3 user.")
        if "wrong digest" in s or "decryption" in s or "snmpv3 message processing" in s:
            return ("hint: v3 auth or priv password wrong, or wrong protocol "
                    "(MD5 vs SHA, DES vs AES). Bosch typically defaults to SHA+AES.")
        if "unknown host" in s or "name or service not known" in s or "nodename nor servname" in s:
            return "hint: DNS lookup failed — pass an IP address instead, or fix /etc/hosts."
        if "permission denied" in s:
            return "hint: local sandboxing blocking outbound UDP/161. Run from the proxy host."
        return ("hint: re-run with --debug to see the full snmp command, "
                "or try a manual `snmpwalk -v2c -c <community> <host> 1.3.6.1.2.1.1` "
                "to isolate whether it's an SNMP problem or a Bosch-private-MIB problem.")

    def _common(self) -> list[str]:
        return self._auth + [
            # -O qnU as a single argument (some net-snmp builds don't merge
            # multiple -O flags reliably). q=quick (no '=' / type prefix),
            # n=numeric OID (override any MIB loaded in snmp.conf), U=no
            # units suffix on values.
            "-OqnU",
            # -m '' disables MIB loading entirely on this invocation — bullet-proofs
            # the parser against /etc/snmp/snmp.conf having `mibs all` set.
            "-m", "",
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
            self.last_stderr = stderr.decode("utf-8", errors="replace").strip()
            if self.debug:
                print(f"# get cmd: {redact_cmd(cmd)}", file=sys.stderr)
                print(f"# get rc={rc} stdout={len(stdout)}B stderr={self.last_stderr!r}",
                      file=sys.stderr)
                if stdout:
                    print(f"# get stdout (first 400B): {stdout[:400]!r}", file=sys.stderr)
            text = stdout.decode("utf-8", errors="replace")
            out.update(parse_snmp_text(text))
        return out

    def walk(self, root_oid: str) -> dict[str, str]:
        """Walk a sub-tree. Returns {oid: value-stripped}."""
        cmd = ["snmpwalk", *self._common(), self.host, root_oid]
        rc, stdout, stderr = self._run(cmd)
        self.last_stderr = stderr.decode("utf-8", errors="replace").strip()
        if self.debug:
            print(f"# walk cmd: {redact_cmd(cmd)}", file=sys.stderr)
            print(f"# walk rc={rc} stdout={len(stdout)}B stderr={self.last_stderr!r}",
                  file=sys.stderr)
            if stdout:
                print(f"# walk stdout (first 400B): {stdout[:400]!r}", file=sys.stderr)
        if rc != 0:
            return {}
        return parse_snmp_text(stdout.decode("utf-8", errors="replace"))

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
        # SNMP basic reachability already validated via preflight(), so an empty
        # walk here means: camera *responds* but doesn't expose the Bosch private
        # branch. Either it's not a Bosch, or SNMP is on but the Bosch MIB module
        # is disabled.
        hint = snmp._diagnose(snmp.last_stderr) if snmp.last_stderr else (
            "hint: SNMP works (preflight passed) but .3967.1.1.1.3.1.1 returned "
            "no rows. Either this isn't a Bosch camera, or the Bosch SNMP module "
            "is disabled. Confirm vendor with: snmpget -v2c -c <comm> <host> "
            "1.3.6.1.2.1.1.1.0  → should mention 'Bosch' or 'arc-cam'/'co-cam'."
        )
        fatal(
            f"could not walk {IMAGER_NAME_TABLE_ROOT}\n"
            f"  stderr  : {snmp.last_stderr or '(empty — likely no Bosch branch on this device)'}\n"
            f"  {hint}"
        )
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


def redact_cmd(cmd: list[str]) -> str:
    """Render a command-line for printing, scrubbing SNMP credentials.

    -c <community>, -A <auth-pass>, -X <priv-pass> get replaced with '***' so
    that --debug output is safe to paste into a bug report.
    """
    REDACT_AFTER = {"-c", "-A", "-X"}
    out: list[str] = []
    skip_next = False
    for tok in cmd:
        if skip_next:
            out.append("***")
            skip_next = False
            continue
        out.append(tok)
        if tok in REDACT_AFTER:
            skip_next = True
    return shlex.join(out)


def render_diff(old: str, new: str, width: int = 30) -> tuple[str, str]:
    """For long values where most of the bytes are shared, return ellipsised
    forms that emphasise the *changing* portion of each side.

    Bosch hex-blob OIDs (config digest, encoder slot, sensor tuple) all carry
    their entropy in a small region of an otherwise-static blob — head
    truncation would hide exactly the part the operator wants to see.
    """
    if old == new:
        return old, new
    if len(old) <= width and len(new) <= width:
        return old, new
    # Longest common prefix
    lcp = 0
    for a, b in zip(old, new):
        if a == b:
            lcp += 1
        else:
            break
    # Longest common suffix (on what remains after the LCP, both sides)
    o_rest, n_rest = old[lcp:], new[lcp:]
    lcs = 0
    for a, b in zip(reversed(o_rest), reversed(n_rest)):
        if a == b:
            lcs += 1
        else:
            break
    # Display window: 4 chars of context before the divergence, and the full
    # changing section, and 4 chars after (or however much suffix is shared).
    ctx = 4
    start = max(0, lcp - ctx)
    o_end = len(old) - max(0, lcs - ctx)
    n_end = len(new) - max(0, lcs - ctx)
    o = ("…" if start > 0 else "") + old[start:o_end] + ("…" if o_end < len(old) else "")
    n = ("…" if start > 0 else "") + new[start:n_end] + ("…" if n_end < len(new) else "")
    return o, n


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
    # Smart-diff display: for long blobs that share most of their bytes, show
    # only the differing portion + a few chars of context. The naive head-cut
    # would hide the digest tail bytes that actually moved.
    o_disp, n_disp = render_diff(old, new, width=32)
    scope = "imager" if "imager" in label else "device"
    print(f"  {now_iso():<27} {scope:<12} {oid:<38} {o_disp!r:>34} → {n_disp!r}")
    print(f"  {'':<27} {'':<12} └─ {label}")


# ─────────────────────────────────────────────────────────────────────────────
# Modes
# ─────────────────────────────────────────────────────────────────────────────
def cmd_watch(snmp: SnmpClient, args: argparse.Namespace) -> int:
    snmp.preflight()
    identity = snmp.get_many(list(IDENTITY_OIDS.keys()))
    imagers = discover_imagers(snmp)
    watched = build_watch_oids(imagers)
    print_banner(identity, imagers, watched, args)

    # Build the noise filter — OIDs whose value moves every poll on an idle
    # camera (tick counter, auto-exposure tuple, slow sensor drift). Hidden by
    # default so the alarm matrix and counter signals stand out during motion
    # testing. --include-noise reverses for full-fidelity diff streams.
    suppress: set[str] = set() if args.include_noise else set(NOISY_OIDS.keys())
    if suppress:
        print(f"#   noise filter    : suppressing {len(suppress)} always-changing OIDs "
              f"({', '.join(sorted(suppress))}). Override with --include-noise.")
    print()

    oids = list(watched.keys())
    last: dict[str, str] = snmp.get_many(oids)
    suppressed_counts: dict[str, int] = {oid: 0 for oid in suppress}

    # Print the initial state for any non-suppressed OID that's already at a
    # non-default value. Catches the "motion was active when the watcher
    # started" case — without this, the operator would never see the alarm
    # OIDs because subsequent polls return the same (non-zero) value, so the
    # diff stream stays silent.
    DEFAULT_VALUES = {"0", "", "00 00 00 00 00", "0.0", "0.0.0.0"}
    nonzero_at_start = sorted(
        (oid for oid, v in last.items()
         if oid not in suppress and v not in DEFAULT_VALUES and oid in watched),
        key=lambda o: watched[o],
    )
    if nonzero_at_start:
        print(f"#   initial non-default values (alarm OIDs may already be active):")
        for oid in nonzero_at_start:
            v = last[oid]
            v_disp = v if len(v) <= 50 else v[:47] + "…"
            print(f"#     {oid:<40} {v_disp!r}   {watched[oid]}")
        print(f"#   ↳ to see transitions to/from these values, wait for the camera state "
              f"to settle to defaults before triggering test events.")
        print()

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
                if oid in suppress:
                    suppressed_counts[oid] = suppressed_counts.get(oid, 0) + 1
                    continue
                print_change(oid, str(old), str(new), watched[oid])
        if current:
            last = current
        if next_snapshot_at and time.monotonic() >= next_snapshot_at:
            path = f"{args.snapshot_prefix}-{int(time.time())}.json"
            write_snapshot(path, args.host, identity, imagers, watched, last)
            print(f"# snapshot written: {path}", flush=True)
            next_snapshot_at = time.monotonic() + args.snapshot_every

    # On exit, summarise how many noise-changes were filtered so the operator
    # sees that the suppression was working and the camera was being polled.
    if suppress and any(suppressed_counts.values()):
        print(f"# noise summary:", flush=True)
        for oid, n in sorted(suppressed_counts.items()):
            if n:
                print(f"#   {oid}  {n} change(s) suppressed   ({NOISY_OIDS.get(oid, '')})",
                      flush=True)
    print(f"# stopped at {now_iso()}", flush=True)
    return 0


def cmd_once(snmp: SnmpClient, args: argparse.Namespace) -> int:
    snmp.preflight()
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
    p.add_argument("--debug", action="store_true",
                   help="print every snmp command and its stderr (for troubleshooting)")

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
    p.add_argument("--include-noise", action="store_true",
                   help="don't suppress always-changing OIDs (tick counter, auto-exposure, "
                        "sensor drift). Off by default so motion-test signals stand out.")

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
