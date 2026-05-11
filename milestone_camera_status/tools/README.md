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

1. Run `--once --snapshot rest.json` against a camera with the scene static.
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
   camera in the browser UI). The `.3967.1.1.12.0` config digest's
   trailing 4 bytes should shift; nothing else.

Append findings to `M0_Bosch_SNMP_Walk_Analysis.md` §3 (alarms subtree).

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
