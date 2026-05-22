# M0 closeout — Bosch FLEXIDOME findings & sub-template input

**Status:** done. Inputs ready for M1's `Milestone Camera vendor — Bosch` sub-template task.

This is the consolidated reference for the Bosch vendor track. Supersedes the iterative analyses for the purposes of building the M1 sub-template; the iterative documents remain on disk as the evidence trail.

**Evidence trail (read for "how we got here"):**

- `M0_Vendor_Audit_Bosch.md` — initial desk audit of the four direct-camera channels (RCP+ over CGI, ONVIF, SNMP, ICMP) before any pilot data.
- `M0_Bosch_SNMP_Walk_Analysis.md` — first real walk (5100i, FW `10020900`, Linux 5.4.238). Established that the agent is net-snmpd and exposes the full standard MIB suite + a Bosch private branch.
- `M0_Bosch_SNMP_Walk_7000i_Comparison.md` — second pilot device, 4-imager FLEXIDOME multi 7000i IR. Decoded the per-imager vs per-device cardinality of every sub-branch by holding both walks side-by-side.
- `M0_Bosch_SNMP_Walk_5100i_PostUpdate.md` — same 5100i, new firmware (`06010980`, Linux 5.15.173). Discovered G32 (ifIndex/hrStorage renumbering on FW update) + UCD-SNMP-MIB removal + new `Available memory` row.
- `tools/bosch_motion_probe.py` + walk transcripts from May 2026 pilot sessions — decoded the alarm-OID semantics in the Bosch private branch by triggering a VCA motion event with `Configuration → Alarm → VCA → Essential Video Analytics` enabled.

---

## 1 · Identity & gating

These are the OIDs the host_prototype in `Milestone Camera by Direct Polling` uses to decide *whether* to link the Bosch sub-template, and which model-specific tunables apply.

| Item | OID | Pilot value (5100i-5MP) | Notes |
|---|---|---|---|
| Vendor | `.1.3.6.1.4.1.3967.1.1.1.7.0` | `Bosch` | Gating value #1. Non-empty + matches `Bosch` exactly. |
| Model | `.1.3.6.1.4.1.3967.1.1.5.1.0` | `FLEXIDOME indoor 5100i IR - 5MP` | **Gating value #2.** Regex: `^FLEXIDOME` (covers all variants — `multi 7000i IR`, `indoor 5100i IR`, `indoor 5100i IR - 5MP`, etc.). Don't anchor on the `- \dMP` suffix — older firmware doesn't emit it. |
| Hardware platform | `.1.3.6.1.4.1.3967.1.1.1.4.0` | `F000B543` | **NOT a model discriminator** — identical across 5100i + 7000i. Use for "which CPP platform" reporting only (`F000B543` = CPP7.3 family). |
| Board fingerprint | `.1.3.6.1.4.1.3967.1.1.1.6.0` | `04 00 04 07 05 04 09 04 03 08 00 05 00 05 00 00 07 03` | Per-board hash. Stable across firmware updates (validated 5100i pre/post FW flash). Different per model family. |
| Firmware short code | `.1.3.6.1.4.1.3967.1.1.1.5.0` | `06010980` | Informational only — **does not sort numerically** (5100i FWs `10020900` and `06010980` are in different version namespaces; different models use entirely different number spaces). No "stale firmware" trigger possible across model families. |
| MAC | `.1.3.6.1.4.1.3967.1.5.1.1.0` | `00 07 5F F7 59 76` | Bosch OUI `00:07:5F`. Cross-check against Milestone's `hardware.mac` field. |
| Sensor codename | `sysName.0` = `.1.3.6.1.2.1.1.5.0` | `arc-cam` (5100i) / `co-cam-48` (7000i) | Internal SoC codename — different per model family. Useful for "which firmware track is this on". |

---

## 2 · Imager discovery (LLD root)

Walk `.1.3.6.1.4.1.3967.1.1.1.3.1.1` for one row per logical imager.

| Pilot box | Rows discovered |
|---|---|
| FLEXIDOME indoor 5100i IR (single-imager) | 1 row: `Camera 1` |
| FLEXIDOME multi 7000i IR (4-imager) | 4 rows: `co-cam-48-1`, `co-cam-48-2`, `co-cam-48-3`, `co-cam-48-4` |

Sub-template LLD: `{#IMAGER.IDX}` = the integer suffix of the OID, `{#IMAGER.NAME}` = the value (with G29 strip). Single-imager FLEXIDOMEs produce N=1; multi-imager housings produce N=4. Per Plan v1.2 §B G2, the **Zabbix host_prototype dedups on `{#CAM.HW.ID}`** so a multi-imager unit becomes one Zabbix host with N imager-LLD rows inside it; the corresponding Milestone-side camera GUIDs join via Milestone's `camera.channel` field (NOT via displayName — independently operator-set, no enforced parity).

---

## 3 · v1 Bosch sub-template — definitive item list

These ship in the first cut of `Milestone Camera vendor — Bosch.yaml`. Every item gets tags `cam_id={#CAM.ID}`, `cam_hw_id={#CAM.HW.ID}`, `rs_id={#CAM.RS.ID}`, `vendor=bosch`, `site={$MILESTONE.SITE.NAME}`, `src=zbx` (per Plan v1.2 §C7 + G27).

### 3a — Device-level (one item per Zabbix host)

| Item key | Source OID | Cadence | Trigger | Notes |
|---|---|---|---|---|
| `bosch.dev.model[{#HOST.NAME}]` | `.3967.1.1.5.1.0` (G29 strip) | 1 d, `DISCARD_UNCHANGED_HEARTBEAT 7d` | — | drives the host_prototype's vendor-sub gating |
| `bosch.dev.fw.short[{#HOST.NAME}]` | `.3967.1.1.1.5.0` (G29 strip) | 1 d, heartbeat 7d | — | informational only; do NOT trigger numeric comparison |
| `bosch.dev.board.fingerprint[{#HOST.NAME}]` | `.3967.1.1.1.6.0` | once | — | hardware identity audit |
| `bosch.dev.platform.code[{#HOST.NAME}]` | `.3967.1.1.1.4.0` (G29 strip) | once | — | CPP platform family code |
| `bosch.dev.vms.endpoint[{#HOST.NAME}]` | `.3967.1.5.2.1.1.1` (G29 strip) | 5 m | `≠ {$MILESTONE.HOST}` → INFORMATION | catches "camera registered to wrong VMS" |
| `bosch.cam.uptime[{#HOST.NAME}]` | **`hrSystemUptime.0`** (`.1.3.6.1.2.1.25.1.1.0`) | 5 m | `change() < 0` → reboot (INFORMATION) | **G28 — do NOT use `sysUpTime.0`** |
| `bosch.cam.process.count[{#HOST.NAME}]` | `hrSystemProcesses.0` (`.1.3.6.1.2.1.25.1.6.0`) | 5 m | `< 80` → AVERAGE | sanity guard against half-booted state |
| `bosch.cam.cpu.avg.pct[{#HOST.NAME}]` | calc: `avg(last_foreach(/host/bosch.cam.cpu.core.pct[*]))` | 5 m | baseline TBD | **replaces UCD `laTable` which is gone post-FW 06010980** |
| `bosch.cam.mem.avail.kb[{#HOST.NAME}]` | `hrStorage` row where `hrStorageDescr = 'Available memory'`, fallback to derived `total − used + cached + buffers` | 5 m | `< 50000` for 15 m → AVERAGE | new firmware exposes `MemAvailable` directly at hrStorage idx 11; older firmware needs the calc |

### 3b — Per-core CPU (LLD on `hrProcessorTable`)

LLD walk `hrDeviceTable` filtered on `hrDeviceType = hrDeviceProcessor` (OID `.1.3.6.1.2.1.25.3.1.3`). 4 cores discovered on both pilot boxes; indices `196608..196611` stable across firmware versions.

| Item prototype | Source OID | Cadence | Trigger |
|---|---|---|---|
| `bosch.cam.cpu.core.pct[{#CORE.IDX}]` | `hrProcessorLoad.{#CORE.IDX}` | 5 m | `> 90` for 15 m → AVERAGE |

### 3c — Filesystem LLD (`hrStorageTable`)

**Critical: never hardcode the hrStorage index.** Bosch firmware updates renumber filesystem indices (G32 — `/data` moved from idx 49 to 47 across the 5100i FW upgrade). Discover by name.

Filter: `hrStorageDescr =~ ^/` (excludes RAM / Buffers / Cached / Shared / Swap pseudo-rows). G29 strip on `hrStorageDescr` before filter.

| Item prototype | Source | Cadence | Trigger |
|---|---|---|---|
| `bosch.cam.fs.total.bytes[{#FS.PATH}]` | `hrStorageSize × hrStorageAllocationUnits` | 1 d | — |
| `bosch.cam.fs.used.pct[{#FS.PATH}]` | `hrStorageUsed / hrStorageSize` | 5 m | `> 90` → AVERAGE (`/data` and `/var/log` only; the tmpfs `/run/*` mounts auto-cap and shouldn't alert) |

Mounts seen on the 5100i: `/dev/shm`, `/run`, `/tmp`, `/var/log`, `/var/tmp`, `/data`, `/run/credentials`, `/run/systemd/incoming`, `/run/systemd/journal`. Only `/data` is persistent (eMMC); `/var/log` is tmpfs (logs lost on reboot — measure rate of growth, not absolute fill). No SD slot on the indoor 5100i IR — no `mmcblk1*` / `sda*` entries.

### 3d — Network LLD (`ifTable`)

**Critical: never hardcode `ifIndex`.** Bosch firmware updates renumber (G32 — `eth0` moved from ifIndex 2 to 3 across the 5100i FW upgrade because a `sit0` IPv6-in-IPv4 tunnel pseudo-interface appeared at idx 2). Discover by `ifDescr` matching `^eth\d+$`. G29 strip before filter.

| Item prototype | Source | Cadence | Trigger |
|---|---|---|---|
| `bosch.cam.if.oper.status[{#IFIDX}]` | `ifOperStatus.{#IFIDX}` | 1 m | `= 2` (down) → WARNING |
| `bosch.cam.if.speed[{#IFIDX}]` | `ifSpeed.{#IFIDX}` | 1 d | — | drives bandwidth-percent calc — 5100i is fast-eth, 7000i is gigabit |
| `bosch.cam.if.in.bps[{#IFIDX}]` | `ifInOctets.{#IFIDX}` × 8, Change per second | 5 m | — | **G30 — use 32-bit ifInOctets, NOT HC counters** (HC OIDs return zero or are absent on Bosch firmware) |
| `bosch.cam.if.out.bps[{#IFIDX}]` | `ifOutOctets.{#IFIDX}` × 8, Change per second | 5 m | — | same |
| `bosch.cam.if.in.discards.rate[{#IFIDX}]` | `ifInDiscards.{#IFIDX}`, Change per second | 5 m | `> 1/min` for 15 m → INFORMATION | the pilot 5100i was showing ~150 discards / hour at idle on FW `10020900` — establish per-camera baseline |

### 3e — Per-imager LLD (root `.3967.1.1.1.3.1.1`)

| Item prototype | Source OID | Cadence | Trigger | Notes |
|---|---|---|---|---|
| `bosch.imager.name[{#IMAGER.IDX}]` | `.3967.1.1.1.3.1.1.{#IMAGER.IDX}` | 1 d | — | operator-set in Bosch UI |
| **`bosch.imager.motion.active[{#IMAGER.IDX}]`** | `.3967.1.3.2.1.1.{#IMAGER.IDX}` | 1 m | `= 1` → INFORMATION (don't alarm by default — Milestone-side calc consumes it) | **primary VCA-motion signal**; decoded May 2026 |
| `bosch.imager.motion.detail[{#IMAGER.IDX}]` | `.3967.1.3.3.1.1.{#IMAGER.IDX}` | 1 m | — | 5-byte bitmap: byte 0 = flags, byte 3 = alarm-type code, byte 4 = rule index. Idle = `00 00 00 00 00`, active = `80 00 00 01 01` on the pilot. |
| `bosch.imager.motion.indicator[{#IMAGER.IDX}]` | `.3967.1.1.4.1.1.2.{#IMAGER.IDX}` | 1 m | — | secondary motion flag; pair with `motion.active` for cross-check (semantic still TBD — boolean vs counter — deferred). |
| `bosch.imager.encoder.bitrate.kbps[{#IMAGER.IDX}]` | `.3967.1.2.2.1.1.{(idx-1)*8+1}` bytes 4–7 (decode in preprocessing) | 5 m | `< 100` for 15 m while `motion.active = 0` → INFORMATION ("encoder may be stalled or scene fully static") | live bitrate of the primary encoder stream; range observed 750 kbps (static scene) → 6 Mbps (active scene) on the pilot 5100i-5MP |

Encoder slot 1 bytes 4–7 parsed as big-endian uint32 = kbps. The 8-slot table (`.3967.1.2.2.1.1.{1..8}` per imager — rows beyond the configured stream count contain `FF FF FF FF` sentinels in bytes 28–31) is the per-imager encoder profile array; we only need the primary stream (slot 1 per imager) for v1.

---

## 4 · Gotchas — promoted to base per-camera template

All four are validated against multiple Bosch devices and multiple firmware versions. They are net-snmpd-on-Bosch traits, not pilot-specific quirks.

| G | What | Impact | Resolution |
|---|---|---|---|
| **G28** | `sysUpTime.0` reflects **snmpd restart**, not camera boot. On the pilot 5100i with 196.7 days kernel uptime, `sysUpTime.0` read 56 seconds. | Templates that key reboot detection on `sysUpTime.0` will fire false reboot alarms every time an operator hits *Save* on the SNMP settings page. | Use **`hrSystemUptime.0`** (`.1.3.6.1.2.1.25.1.1.0`) for reboot detection. Applies to any vendor shipping net-snmpd. |
| **G29** | Bosch net-snmpd build returns OCTET STRINGs with **trailing binary garbage** (`eth0à‡HÖe`, `8.64à‡HÖe`, `/var/logl`, `Camera 1l`, etc.). Looks like a fixed-buffer build that doesn't NUL-terminate cleanly. | Unsanitised LLD keys break UTF-8 in the Zabbix frontend and produce broken item keys. | Preprocessing step `^([\x20-\x7E]+).*  →  \1` on every Bosch SNMP string item BEFORE the LLD filter sees it. The motion-probe parser does this for the operator-facing tool. |
| **G30** | `ifXTable` HC counters (`ifHCInOctets`/`ifHCOutOctets`) **return zero or are absent** on Bosch firmware. 32-bit counters work fine. | Bandwidth items pointing at `.ifHC*.X` silently produce zero values. | Use 32-bit `ifInOctets`/`ifOutOctets` with Zabbix `Change per second` preprocessing. Counter32 handles wraparound natively. |
| **G32** | Bosch firmware updates **renumber `ifIndex` and `hrStorageIndex`**. Same physical 5100i: `eth0` moved from idx 2 to idx 3 across one FW upgrade; `/data` moved from idx 49 to idx 47. | Per-camera SNMP items that hardcode a table index silently produce wrong values after operator updates camera firmware. | Every table-row item **LLD-discovers by name** (`ifDescr`/`hrStorageDescr` with G29 strip). Discovered index becomes an LLD macro (`{#IFIDX}`, `{#FSIDX}`). Likely applies to every vendor running net-snmpd. |

**G31** (multi-imager join) is sub-template-specific: the per-camera Zabbix host's imager LLD enumerates imagers via `.3967.1.1.1.3.1.1`; Milestone-side child camera GUIDs join to Bosch-side `{#IMAGER.IDX}` via **Milestone's `camera.channel`** field, NOT via displayName (independently operator-set, no enforced parity).

---

## 5 · OIDs deliberately NOT in the v1 sub-template

Don't ship items pointing at these — they're either noise, ambiguous, or chassis-level signals we haven't validated. Re-evaluate post-v1.

| OID | Why excluded |
|---|---|
| `.3967.1.1.12.0` | **Per-second tick counter, not a config digest.** Changes every poll (validated: `BC55 → BC56 → … → BC60` over 12 s). Useful as alive-heartbeat in principle but adds no signal Zabbix doesn't already get from `hrSystemUptime.0`. |
| `.3967.1.1.7.1.1.1` | Slow-drift sensor scalar (~10-unit steps every few seconds). Candidate colour-temp or sensor-temp; without the MIB the value's unit is unknown. No trigger possible. |
| `.3967.1.1.9.1.1.1` + `.3967.1.1.9.1.4.1` | **Auto-exposure feedback loop** (byte 0 of the 4-tuple = the integer scalar exactly). Useful as a "sensor is alive" diagnostic but moves at ~1 Hz on a static scene — not triggerable. |
| `.3967.1.3.1.1.1.{idx}` | Reserved — did NOT toggle under VCA motion. Candidate input-contact / tamper but not validated. |
| `.3967.1.3.4.1.1.{1..16}` | Device-wide 16-slot alarm matrix. Chassis-level signals (tamper, network loss, memory fault) — none toggled during pilot motion testing. Validating each slot needs cover-lens / cable-pull / config-corruption tests that we're not running for M0. |
| `.3967.1.4.{1,2}.1.1.1.1.1` | Two zeroed device-wide analytics counters. Possibly rising-edge lifetime counters; insufficient pilot data to confirm. |
| `.3967.1.1.5.{12,13,14,15}` | Slow-moving state blobs. `.5.13` encodes the camera IP (redundant); `.5.12` is a persistent counter (operating-hours candidate); `.5.14` and `.5.15` are opaque. None are alert-worthy. |
| `.3967.1.2.2.1.1.{2..8}` (per imager) | Secondary encoder slots — usually `FFFFFFFF` sentinels indicating "no stream configured". Only slot 1 carries useful state for v1; iterate to multi-slot in a future enhancement if operators care about secondary-stream bitrate. |

---

## 6 · Macro vocabulary the sub-template introduces

All new macros land in `M0_Macros.md` (per Plan v1.2 §C5 / G24 — single canonical list for the TCS handoff). Defaults shown.

| Macro | Default | Notes |
|---|---|---|
| `{$MS.CAM.BOSCH.SNMP.COMMUNITY}` | (empty, type=SECRET_TEXT) | v1/v2c community; pair with `{$MS.CAM.BOSCH.SNMP.VERSION}` |
| `{$MS.CAM.BOSCH.SNMP.VERSION}` | `2c` | one of `1`/`2c`/`3` |
| `{$MS.CAM.BOSCH.SNMP.V3.USER}` | (empty) | SNMPv3 user |
| `{$MS.CAM.BOSCH.SNMP.V3.AUTH.PROTO}` | `SHA` | Bosch defaults to SHA + AES |
| `{$MS.CAM.BOSCH.SNMP.V3.AUTH.PASS}` | (empty, type=SECRET_TEXT) | |
| `{$MS.CAM.BOSCH.SNMP.V3.PRIV.PROTO}` | `AES` | |
| `{$MS.CAM.BOSCH.SNMP.V3.PRIV.PASS}` | (empty, type=SECRET_TEXT) | |
| `{$MS.CAM.BOSCH.POLL.INTERVAL}` | `5m` | tune per scale per G12 |
| `{$MS.CAM.BOSCH.MOTION.POLL.INTERVAL}` | `1m` | alarm OIDs polled faster than baseline |

The sub-template documents in its description that operators must **enable SNMPv1/v2c or configure a dedicated v3 user in the Bosch UI under `Configuration → Service → SNMP`** — recent Bosch firmware ships with v1/v2c disabled by default, and the default `service` account does NOT have SNMP access.

---

## 7 · Test / validation matrix for M1 acceptance

The Bosch sub-template ships as ready when:

| Test | How | Acceptance |
|---|---|---|
| Identity gating fires correctly | Apply sub-template to a host_prototype with model regex `^FLEXIDOME`; confirm the sub-template links for a 5100i and a 7000i, doesn't link for a non-Bosch | Both pilot boxes show the Bosch items; non-Bosch test box has no Bosch items |
| Imager LLD enumerates correctly | Run discovery; confirm 1 imager on 5100i, 4 on 7000i | LLD row count matches |
| Standard-MIB items populate (CPU / mem / FS / network) | Poll once, confirm non-empty values for `bosch.cam.cpu.core.pct[*]`, `bosch.cam.mem.avail.kb`, `bosch.cam.fs.used.pct[/data]`, `bosch.cam.if.in.bps[eth0]` | All four populate within 2 poll cycles |
| G29 garbage strip works | `bosch.cam.fs.used.pct` for `/var/log` (which Bosch returns as `/var/logl`) | Discovery key resolves to `/var/log`, not `/var/logl`, and matches across firmware versions |
| G32 robustness | Discovery on 5100i pre-FW (`eth0` at idx 2) and post-FW (`eth0` at idx 3) | Same item keys produce values on both; no stale items orphaned |
| Reboot detection | `hrSystemUptime.0` resets correctly across a real camera reboot | Trigger fires; `sysUpTime.0`-based logic would have missed it |
| Motion alarm reaches Zabbix | Trigger a VCA motion event in the Bosch UI; `bosch.imager.motion.active[1]` should flip `0 → 1 → 0` | Item history shows the transition pair |
| Encoder-bitrate signal works | With a moving scene vs a covered lens, `bosch.imager.encoder.bitrate.kbps[1]` differs by at least 3× | Pilot 5100i-5MP showed 750 kbps idle vs 6 Mbps active — confirms range |

---

## 8 · What's deferred to post-v1

- Disambiguating `.3967.1.1.4.1.1.2.{idx}` (boolean vs lifetime counter).
- Decoding the chassis-level 16-slot alarm matrix `.3967.1.3.4.1.1.{1..16}` (tamper, network loss, memory fault).
- Decoding `.3967.1.3.1.1.1.{idx}` (probable input-contact / tamper).
- Decoding the two device-wide analytics counters `.3967.1.4.{1,2}.1.1.1.1.1`.
- Pulling the Bosch MIB from the firmware `.zip` and committing under `templates/mibs/` — would resolve every unknown symbolic label and give exact field units. Currently the M1 sub-template ships with the empirical labels in this document.
- SNMPv3 default-credential probe — there's no analogue to G18 (Dell `root/calvin` style default) for Bosch SNMP because v3 users are operator-created from scratch; no default to probe.
- Multi-stream encoder telemetry (secondary stream bitrates via slots 2–8) — only the primary stream is monitored in v1.
- ONVIF `GetSystemDateAndTime` for NTP drift detection — held in the desk audit's "next pilot session" list but not blocked on for v1, since the device's NTP server endpoint (`.3967.1.5.2.1.1.1`) is already monitored for the drift-worthy case (camera reconfigured against the wrong NTP).
