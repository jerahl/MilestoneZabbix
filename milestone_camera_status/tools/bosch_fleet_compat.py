#!/usr/bin/env python3
"""
bosch_fleet_compat.py
=====================
Probe a list of Bosch IP cameras and emit a one-line schema-fingerprint per
camera. Used during M0 closeout to confirm whether the SNMP schema decoded
from the 5100i + 7000i pilots (per M0_Bosch_Findings.md) holds across older
model families in the fleet.

For each camera, fetches eight critical OIDs in a single snmpget batch and
classifies the result into one of three buckets:

    A   full compatibility — every fingerprint OID returns the expected
        shape (model string, alarm boolean as int, 44-byte encoder blob)
    B   identity-only — standard MIB + Bosch private identity branch work,
        but the alarm/encoder OIDs are missing or wrong-shaped
    C   minimal — only standard MIB present, no Bosch private branch

The output is markdown — paste into M0_Bosch_Fleet_Compatibility.md §9 to
record fleet probe results.

Usage
-----
    # IPs on argv
    ./bosch_fleet_compat.py 10.24.18.83 10.24.18.84 10.24.18.85

    # IPs from a file (one per line)
    ./bosch_fleet_compat.py --hosts-file pilot_targets.txt

    # SNMPv1/v2c with non-default community
    ./bosch_fleet_compat.py --community public2 10.24.18.83

    # SNMPv3
    ./bosch_fleet_compat.py --version 3 --v3-user zbx_monitor \\
        --v3-auth-proto SHA --v3-auth-pass '…' \\
        --v3-priv-proto AES --v3-priv-pass '…' \\
        --hosts-file pilot_targets.txt

Requires
--------
- Python 3.8+ (stdlib only)
- net-snmp `snmpget` in $PATH
"""
from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


BOSCH = "1.3.6.1.4.1.3967"

# Eight schema-fingerprint OIDs. The order matters for the printed table.
FINGERPRINT_OIDS: list[tuple[str, str]] = [
    ("sysDescr",        "1.3.6.1.2.1.1.1.0"),
    ("vendor",          f"{BOSCH}.1.1.1.7.0"),
    ("model",           f"{BOSCH}.1.1.5.1.0"),
    ("fw_short",        f"{BOSCH}.1.1.1.5.0"),
    ("platform",        f"{BOSCH}.1.1.1.4.0"),
    ("imager1.name",    f"{BOSCH}.1.1.1.3.1.1.1"),
    ("imager1.motion",  f"{BOSCH}.1.3.2.1.1.1"),
    ("imager1.enc",     f"{BOSCH}.1.2.2.1.1.1"),
]


# G29 — trailing binary garbage strip on octet strings.
G29_STRIP = re.compile(rb"^([\x20-\x7E]*).*", re.DOTALL)


def strip_g29(raw: bytes) -> str:
    m = G29_STRIP.match(raw)
    return (m.group(1) if m else raw).decode("ascii", errors="replace").rstrip()


OID_PREFIX_REWRITES = [
    ("SNMPv2-SMI::enterprises.", "1.3.6.1.4.1."),
    ("SNMPv2-SMI::mib-2.", "1.3.6.1.2.1."),
    ("iso.org.dod.internet.private.enterprises.", "1.3.6.1.4.1."),
    ("iso.org.dod.internet.mgmt.mib-2.", "1.3.6.1.2.1."),
    ("iso.3.6.1.4.1.", "1.3.6.1.4.1."),
    ("iso.3.6.1.2.1.", "1.3.6.1.2.1."),
    ("iso.", "1."),
]


def normalize_oid(oid: str) -> str:
    oid = oid.lstrip(".")
    for sym, num in OID_PREFIX_REWRITES:
        if oid.startswith(sym):
            return num + oid[len(sym):]
    return oid


def parse_snmp_text(text: str) -> dict[str, str]:
    """Same parser shape as bosch_motion_probe.py — handles quick + verbose +
    symbolic + multi-line hex output. Returns {numeric-oid: cleaned-value}."""
    rows: dict[str, str] = {}
    current_oid: str | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_oid, current_parts
        if current_oid is None:
            return
        raw = " ".join(p.strip() for p in current_parts if p.strip())
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", raw, re.DOTALL)
        if m:
            raw = m.group(2).strip()
        if len(raw) >= 2 and raw[0] == '"' and raw.endswith('"'):
            raw = raw[1:-1]
        # G29 strip on text, leave hex strings alone
        if re.match(r"^(?:[0-9A-Fa-f]{2}\s*)+$", raw):
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
        if ("No Such" in s) or s.startswith("End of MIB") or ("No more variables" in s):
            continue
        m = re.match(r"^\.?([A-Za-z0-9_:.-]+?)\s*(=|\s)\s*(.*)$", s)
        if m and ("." in m.group(1) or "::" in m.group(1)):
            flush()
            current_oid = normalize_oid(m.group(1))
            current_parts = [m.group(3)]
        else:
            if current_oid is not None:
                current_parts.append(s.strip())
    flush()
    return rows


@dataclass
class ProbeResult:
    host: str
    bucket: str = "?"                 # A / B / C / X (unreachable)
    error: str | None = None
    values: dict[str, str] = field(default_factory=dict)


def build_snmp_args(args: argparse.Namespace) -> list[str]:
    if args.version in ("1", "2c"):
        auth = ["-v", args.version, "-c", args.community]
    elif args.version == "3":
        if not args.v3_user:
            sys.exit("--v3-user required for SNMPv3")
        auth = ["-v", "3", "-u", args.v3_user, "-l", args.v3_level]
        if args.v3_level in ("authNoPriv", "authPriv"):
            auth += ["-a", args.v3_auth_proto, "-A", args.v3_auth_pass]
        if args.v3_level == "authPriv":
            auth += ["-x", args.v3_priv_proto, "-X", args.v3_priv_pass]
    else:
        sys.exit(f"unknown SNMP version {args.version!r}")
    return auth + ["-OqnU", "-m", "", "-t", str(args.timeout), "-r", str(args.retries)]


def probe(host: str, snmp_args: list[str]) -> ProbeResult:
    result = ProbeResult(host=host)
    oids = [o for (_, o) in FINGERPRINT_OIDS]
    cmd = ["snmpget", *snmp_args, host, *oids]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        result.bucket = "X"
        result.error = "subprocess timeout"
        return result
    if p.returncode != 0 and not p.stdout.strip():
        result.bucket = "X"
        err = p.stderr.decode("utf-8", errors="replace").strip()
        result.error = err.splitlines()[0] if err else f"rc={p.returncode}"
        return result
    parsed = parse_snmp_text(p.stdout.decode("utf-8", errors="replace"))
    # Map parsed OIDs back to friendly names
    by_name: dict[str, str] = {}
    for name, oid in FINGERPRINT_OIDS:
        by_name[name] = parsed.get(oid, "")
    result.values = by_name

    # Classify
    if not by_name.get("sysDescr"):
        result.bucket = "X"
        result.error = "no sysDescr — SNMP not responding"
        return result
    has_private = bool(by_name.get("vendor")) and bool(by_name.get("model"))
    motion_val = by_name.get("imager1.motion", "")
    motion_int_ok = motion_val.isdigit() or motion_val in ("0", "1")
    enc_val = by_name.get("imager1.enc", "")
    # Encoder slot blob is 44 bytes = 88 hex digits + spaces; loose check: >= 60 chars hex-like
    enc_hex_ok = bool(re.match(r"^(?:[0-9A-Fa-f]{2}\s*){20,}$", enc_val))
    if has_private and motion_int_ok and enc_hex_ok:
        result.bucket = "A"
    elif has_private:
        result.bucket = "B"
    else:
        result.bucket = "C"
    return result


def render_markdown(results: list[ProbeResult]) -> str:
    out: list[str] = []
    out.append("## §9 Fleet probe results")
    out.append("")
    out.append("| Host | Bucket | Model | FW | Platform | Imagers | Alarm-OID | Enc-OID | sysDescr (truncated) |")
    out.append("|---|:--:|---|---|---|:---:|:---:|:---:|---|")
    for r in results:
        if r.bucket == "X":
            out.append(f"| `{r.host}` | **X** | — | — | — | — | — | — | `{r.error or 'unreachable'}` |")
            continue
        v = r.values
        sysd = v.get("sysDescr", "")
        sysd_short = sysd[:55] + ("…" if len(sysd) > 55 else "")
        motion = v.get("imager1.motion", "")
        enc_len = len(v.get("imager1.enc", "").replace(" ", "")) // 2
        # Count imagers — we only fetched imager 1's name, but a non-empty value
        # implies the LLD table starts there. Note: this script doesn't walk the
        # whole table, just probes whether row 1 exists.
        imager_present = "yes" if v.get("imager1.name") else "—"
        alarm_ok = "✓" if motion in ("0", "1") or (motion.isdigit() and int(motion) < 256) else ("·" if motion else "—")
        enc_ok = "✓" if enc_len >= 20 else ("·" if v.get("imager1.enc") else "—")
        out.append(
            f"| `{r.host}` | **{r.bucket}** | `{v.get('model', '')}` | `{v.get('fw_short', '')}` | "
            f"`{v.get('platform', '')}` | {imager_present} | {alarm_ok} | {enc_ok} | `{sysd_short}` |"
        )
    out.append("")
    # Bucket summary
    counts: dict[str, int] = {}
    for r in results:
        counts[r.bucket] = counts.get(r.bucket, 0) + 1
    out.append("**Bucket summary**: " + " · ".join(
        f"{b}={counts.get(b, 0)}" for b in ("A", "B", "C", "X")))
    out.append("")
    out.append("Legend: **A** = full compatibility (alarm + encoder OIDs return expected shapes), "
               "**B** = identity-only (Bosch private branch present but alarm/encoder OIDs missing/wrong-shaped), "
               "**C** = minimal (only standard MIB, no Bosch private branch), "
               "**X** = unreachable (SNMP error, see column).")
    return "\n".join(out)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Probe a list of Bosch IP cameras for SNMP schema compatibility.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[1] if "Usage" in __doc__ else "",
    )
    p.add_argument("hosts", nargs="*", help="camera IPs (omit if using --hosts-file)")
    p.add_argument("--hosts-file", help="file with one IP per line; '#' comments allowed")
    p.add_argument("--workers", type=int, default=8,
                   help="parallel probes (default 8 — be gentle on small fleets)")

    p.add_argument("--version", default="2c", choices=["1", "2c", "3"])
    p.add_argument("--community", default="public")
    p.add_argument("--v3-user", default=None)
    p.add_argument("--v3-level", default="authPriv",
                   choices=["noAuthNoPriv", "authNoPriv", "authPriv"])
    p.add_argument("--v3-auth-proto", default="SHA", choices=["MD5", "SHA"])
    p.add_argument("--v3-auth-pass", default="")
    p.add_argument("--v3-priv-proto", default="AES", choices=["DES", "AES"])
    p.add_argument("--v3-priv-pass", default="")
    p.add_argument("--timeout", type=int, default=2)
    p.add_argument("--retries", type=int, default=1)

    return p.parse_args(argv)


def load_hosts(args: argparse.Namespace) -> list[str]:
    hosts: list[str] = list(args.hosts)
    if args.hosts_file:
        with open(args.hosts_file) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    hosts.append(line)
    if not hosts:
        sys.exit("no hosts — pass IPs as args or use --hosts-file")
    return hosts


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if shutil.which("snmpget") is None:
        sys.exit("snmpget not in $PATH (install net-snmp / net-snmp-utils)")
    hosts = load_hosts(args)
    snmp_args = build_snmp_args(args)

    print(f"# probing {len(hosts)} host(s) with {args.workers} workers …",
          file=sys.stderr)

    results: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        # Preserve input order in output
        results = list(ex.map(lambda h: probe(h, snmp_args), hosts))

    print(render_markdown(results))
    # Exit non-zero if any host is in bucket X — useful for CI/scripted runs
    if any(r.bucket == "X" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
