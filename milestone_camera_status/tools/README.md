# M0 pilot validation tools

Operator-side helpers used during the M0 vendor-survey track. Not invoked by
Zabbix; not part of the runtime data path. Each script is stdlib-only and
shells out to `snmpget` / `snmpwalk` so the Zabbix proxy already has the
required binaries.

## `bosch_motion_probe.py`

Tail-style watcher for the Bosch private MIB branch
(`.1.3.6.1.4.1.3967.1`). Prints a line every time a watched OID changes
value — point a Bosch FLEXIDOME / DINION / AUTODOME at a static scene, run
the script, wave a hand in the FoV, watch which OIDs blink.

**Why it exists.** The walk-analysis docs in this directory decoded the
*structure* of the Bosch private MIB across three independent SNMP walks
(5100i pre-update, 5100i post-update, 7000i) but couldn't label the actual
semantics of the per-imager alarm matrices (`.3967.1.3.{1,2,3}.1.1.X`),
the 16-slot device-wide alarm matrix (`.3967.1.3.4.1.1.{1..16}`), the two
analytics counters (`.3967.1.4.{1,2}`), or the slow-moving sensor scalars
(`.3967.1.1.{7,9}.1.1.1`) — they were all zero or near-static in the
snapshots. The motion-probe deltas across known stimuli are what assign
names to those OIDs.

### Quick start

```sh
# 1. Watch one camera forever, default community 'public', 1 Hz poll
./bosch_motion_probe.py 10.24.18.22

# 2. Tighter cadence for very brief events
./bosch_motion_probe.py 10.24.18.22 --interval 0.25

# 3. SNMPv3 (preferred for production-pilot)
./bosch_motion_probe.py 10.112.18.48 --version 3 \
    --v3-user zbx_monitor \
    --v3-auth-proto SHA --v3-auth-pass 'authpw' \
    --v3-priv-proto AES --v3-priv-pass 'privpw'
```

### Before / after testing (no live SNMP — replays JSON snapshots)

```sh
./bosch_motion_probe.py 10.24.18.22 --once --snapshot before.json
#   ... trigger an event (motion, tamper, configure change, …) ...
./bosch_motion_probe.py 10.24.18.22 --once --snapshot after.json

./bosch_motion_probe.py --diff before.json after.json
```

### What it watches

Auto-discovers imager instances via `.3967.1.1.1.3.1.1` (returns 1 row on
a single-imager FLEXIDOME, N rows on a multi-imager housing), then polls:

- **Per-imager** (multiplied by N): the three alarm-state OIDs, the two
  per-imager counters, and the 8 encoder-slot blobs.
- **Per-device** (one set regardless of imager count): the 16-slot
  device-wide alarm matrix, the two analytics counters, the unlabelled
  sensor scalars, the config digest, and the state blobs in `.3967.1.1.5.*`.

The full OID list (with provisional labels) is at the top of the script,
in `PER_IMAGER_TEMPLATES` and `PER_DEVICE_OIDS`.

### Suggested test plan (M0 pilot validation)

**Prerequisite — enable VCA in the camera UI first.** The Bosch private MIB's
alarm matrices (`.3967.1.3.*`) only flip when a configured Video Content
Analysis (VCA) rule actually trips. The 5100i ships with Essential Video
Analytics (EVA) included but **default-off**. In the Bosch browser UI:
**Configuration → Alarm → VCA**, enable an analytics mode (EVA / IVA / Motion+),
and add a rule that covers the whole frame so any movement trips it. Without
this step the alarm matrices stay at `0` forever and the watcher will
correctly report "no alarm signals" no matter how much motion is in the FoV.

1. Run `--once --snapshot rest.json` against a camera with the scene static
   and the EVA rule enabled but un-tripped.
2. Walk in front of the camera (cover ~50 % of the frame for ~3 s).
3. While motion is happening, watch the live diff output; record which
   imager-level OIDs (`.3967.1.3.{1,2,3}.1.1.X`) and which device-level
   slots (`.3967.1.3.4.1.1.{1..16}`) toggled.
4. Trigger ONVIF motion events from the Bosch browser UI — compare the
   diff. If `.3967.1.4.x` (the two zeroed counters) increment when motion
   *types* fire (vs the alarm-state OIDs which flip on / off), then those
   counters are the **IVA rule trip counters** and the alarm-state OIDs
   are the **current-state booleans**.
5. Cover the lens (tamper). Identify which device-wide slot in
   `.3967.1.3.4.1.1.{1..16}` toggles — that's the tamper slot.
6. If the camera supports a relay output, trigger it from the UI; the
   relay-output state slot should be visible in the same device-wide
   matrix.
7. Save a snapshot before and after a known config change (rename the
   camera in the browser UI). The `.3967.1.1.12.0` field is a per-second
   tick counter (NOT a config digest); look at `.3967.1.1.5.{13,14}` for
   any config-related changes.

Append findings to `M0_Bosch_SNMP_Walk_Analysis.md` §3 (alarms subtree).

### Tip: encoder slot 1 (`.3967.1.2.2.1.1.1`) shows live bitrate

Bytes 4–7 of the slot blob carry the imager's current encoded bitrate in
kbps. On a static scene it sits around 700–800 kbps; on an active scene
(real motion, ambient lighting changes) it climbs to 5–6 Mbps. Watching
this alone is a cheap "scene activity" signal even when no alarm rule is
configured — useful for confirming the camera is actually seeing
something during a probe session. Bytes 0–3 of the same slot are a
per-second counter that drifts +1/s independent of scene content.

---

## `bosch_fleet_compat.py`

Fleet-shape probe: given a list of camera IPs, GETs eight schema-defining
OIDs per camera in one round trip and classifies the result. Used during
M0 closeout to confirm whether the SNMP schema decoded from the 5100i +
7000i pilots (per `M0_Bosch_Findings.md`) holds across older Bosch model
families in the fleet — see `M0_Bosch_Fleet_Compatibility.md` for the
full probe campaign rationale.

### What it returns

A one-line-per-camera markdown row, classified into:

- **A** — full compatibility. Alarm and encoder OIDs return the expected
  shapes (integer-valued alarm boolean + ≥20-byte hex encoder blob). The
  sub-template's full item list applies to this generation of camera.
- **B** — identity-only. Bosch private identity branch present, but the
  alarm/encoder OIDs are absent or wrong-shaped. Sub-template auto-gates
  its full-MIB items off for this generation.
- **C** — minimal. Only standard MIB-II is present; no `.1.3.6.1.4.1.3967`
  branch. Sub-template emits only standard-MIB items.
- **X** — unreachable. SNMP error printed in the row.

### Usage

```sh
# IPs as args
./bosch_fleet_compat.py 10.24.18.83 10.24.18.84 10.24.18.85

# From a file (one IP per line, '#' comments allowed)
cat > pilot_targets.txt <<EOF
# CPP7.3 pilots (already-validated reference)
10.24.18.83        # FLEXIDOME indoor 5100i IR - 5MP
10.112.18.48       # FLEXIDOME multi 7000i IR
# 'i'-generation probe targets (high impact — 1,488 cameras in this group)
10.24.18.84        # FLEXIDOME IP 5000i IR
10.24.18.85        # FLEXIDOME IP 4000i
10.24.18.86        # FLEXIDOME IP micro 3000i
# Pre-'i' 5000 HD generation probe targets (442 cameras)
10.24.18.87        # FLEXIDOME IP indoor 5000 HD
10.24.18.88        # FLEXIDOME IP outdoor 5000 HD
10.24.18.89        # DINION IP starlight 6000 HD
10.24.18.90        # FLEXIDOME IP panoramic 5000 MP
10.24.18.91        # BOSCH FLEXIDOME HD 720p VR IVA
EOF
./bosch_fleet_compat.py --hosts-file pilot_targets.txt
```

For SNMPv3 the same flags as `bosch_motion_probe.py` apply
(`--version 3 --v3-user ...` etc.).

### Workflow

1. Pick one camera per row in `M0_Bosch_Fleet_Compatibility.md` §5 (about
   11 cameras spanning every distinct platform generation in the fleet).
2. Run `./bosch_fleet_compat.py --hosts-file <list> > probe_results.md`.
3. Paste the output as §9 of `M0_Bosch_Fleet_Compatibility.md`.
4. For each generation, set the bucket (A/B/C) — that's the sub-template
   gating outcome.
5. M1 ships `Milestone Camera vendor — Bosch.yaml` with the bucket logic
   baked into its calc items (the `bosch.dev.has_full_mib` flag — see
   `M0_Bosch_Fleet_Compatibility.md` §6).

### Performance + safety

- Probes in parallel via a thread pool (default 8 workers; tune with
  `--workers`). 11 cameras complete in ~3 seconds; a hundred in ~30
  seconds.
- Each probe is a single batched `snmpget` of 8 OIDs — minimal load on
  the cameras.
- Credentials redacted from `--debug`-style printing (same redactor as
  `bosch_motion_probe.py`); the tool doesn't print the snmp command line
  in non-debug runs.
- Exits non-zero if any camera lands in bucket X — useful for shell
  pipelines that want to gate on full fleet reachability.

### Troubleshooting

The script runs a **pre-flight** before doing anything Bosch-specific: it
`snmpget`s `sysDescr.0` (`.1.3.6.1.2.1.1.1.0`) to confirm basic SNMP works.

- **Pre-flight fails** → SNMP itself is broken. The error message includes
  the underlying `snmpwalk`/`snmpget` stderr plus a one-line hint matched
  against common net-snmp error strings (`Timeout`, `unknown user`,
  `authentication failure`, `wrong digest`, `unknown community`, etc.).
- **Pre-flight passes but the Bosch private-MIB walk returns nothing** →
  SNMP works but `.1.3.6.1.4.1.3967.1.1.1.3.1.1` is empty. Either this
  isn't a Bosch camera, or the Bosch SNMP module is disabled. Confirm
  vendor with `snmpget -v2c -c <comm> <host> 1.3.6.1.2.1.1.1.0` — the
  `sysDescr.0` string should mention `Bosch`, or kernel codename
  `arc-cam` (FLEXIDOME family) / `co-cam` (multi-imager / 7000i family).

For the noisy case — full `snmpwalk`/`snmpget` command lines and rc/stderr
on every call:

```sh
./bosch_motion_probe.py 10.24.18.22 --debug --once 2>&1 | less
```

Common Bosch-specific footguns:

| Symptom | Likely cause |
|---|---|
| Pre-flight times out | SNMP not enabled in the Bosch UI under Configuration → Service → SNMP, OR camera ACL blocks the proxy host |
| `unknown community` / `noAccess` | Recent Bosch firmware ships with SNMPv1/v2c DISABLED. Use `--version 3` with an operator-created v3 user. |
| v3 `authenticationFailure` | The default `service` account does NOT have SNMP access. Create a dedicated read-only v3 user in the Bosch UI. |
| v3 `wrong digest` or `decryption error` | Auth/priv passwords or protocols wrong. Bosch typically defaults to **SHA + AES**. |

### Exit codes

| Code | Meaning |
|------|---------|
| 0    | clean exit (Ctrl-C in watch mode, or `--once` / `--diff` completed) |
| 2    | snmpget/snmpwalk failure (camera unreachable, auth wrong) |
| 3    | invalid arguments |

### Known caveats

- Applies the G29 string-strip uniformly — any binary octet-string OID
  will appear truncated at its first non-printable byte. That's fine for
  all the OIDs we watch (they're either integers or printable strings) but
  if you point this at a different MIB region with genuinely-binary
  payloads you'll lose data.
- Polls all watched OIDs in batches of 60 per `snmpget` call to stay
  within the camera's response-size budget — at 1 Hz that's ~2–3 round
  trips per second per camera. Don't run multiple instances of this
  watcher against the same camera or you'll start to see snmpd-side
  rate-limiting.
- `sysUpTime.0` (.1.3.0) is *not* in the watch list because per **G28**
  it tracks snmpd uptime, not camera uptime — it would change every time
  the agent restarts (~daily on some cameras) and add noise. Use
  `hrSystemUptime.0` (.25.1.1.0) in your own watch list if needed.
