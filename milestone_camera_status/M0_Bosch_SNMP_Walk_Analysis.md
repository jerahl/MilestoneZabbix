# M0 — Bosch FLEXIDOME indoor 5100i IR · SNMP walk analysis

Source: `mibdata.csv` — full `snmpwalk` of the pilot FLEXIDOME indoor 5100i IR (4022 OIDs). This is the **validation run** for the §B desk audit; flags below as "Δ vs desk audit" or "G‑new" are findings that contradict or extend the prior document.

## TL;DR

- **The agent is net‑snmpd**, not a Bosch‑proprietary daemon. `sysObjectID` = `.1.3.6.1.4.1.8072.3.2.10` (NET‑SNMP Linux agent). This means we get the **full standard MIB suite for free**: MIB‑II, HOST‑RESOURCES‑MIB, UCD‑SNMP‑MIB, IF‑MIB (ifXTable), plus a small Bosch private branch under `.1.3.6.1.4.1.3967`.
- **Δ vs desk audit:** monitoring surface is **wildly richer** than predicted. The desk audit said "SNMP gets us sysUpTime + ifOctets + the private branch"; the real camera serves **load average, per‑core CPU%, RAM breakdown, filesystem table, process list, full TCP/UDP/IP counters** in addition to the Bosch‑specific fields.
- **G‑new (must capture before the sub‑template ships):**
  - **G28‑proposed — `sysUpTime.0` lies on Bosch cameras**: agent uptime, not system uptime. Use `hrSystemUptime.0` for reboot detection.
  - **G29‑proposed — Bosch snmpd build returns octet strings with trailing binary garbage**: every string item needs preprocessing to strip non‑printable bytes, or interface/filesystem/process discovery will produce broken LLD entries.
  - **G30‑proposed — ifXTable HC counters are zero on this firmware**: the 64‑bit `ifHCInOctets`/`ifHCOutOctets` columns under `.31.1.1.1.6`/`.10` return `0`. Fall back to the 32‑bit `ifInOctets`/`ifOutOctets` with Zabbix `Change per second` preprocessing.

---

## 1 · Device identity (validated)

| Field | OID | Value |
|---|---|---|
| Model | `.1.3.6.1.4.1.3967.1.1.5.1.0` | `FLEXIDOME indoor 5100i IR` |
| Vendor | `.1.3.6.1.4.1.3967.1.1.1.7.0` | `Bosch` |
| Hardware ID | `.1.3.6.1.4.1.3967.1.1.1.4.0` | `F000B543` |
| Firmware short code | `.1.3.6.1.4.1.3967.1.1.1.5.0` | `10020900` |
| Firmware build identifier (hex) | `.1.3.6.1.4.1.3967.1.1.1.6.0` | `040004070504090403080003000502000903` |
| Kernel | `.1.3.6.1.2.1.1.1.0` (`sysDescr`) | `Linux arc-cam 5.4.238 #1 SMP PREEMPT Thu Nov 23 15:41:38 Europe 2023 aarch64` |
| Camera hostname | `.1.3.6.1.2.1.1.5.0` (`sysName`) | `arc-cam` |
| MAC (eth0) | `.1.3.6.1.2.1.2.2.1.6.2` (and `.3967.1.5.1.1.0`) | `00:07:5F:D7:95:84` *(OUI `00:07:5F` = Bosch Security Systems — confirmed)* |
| IP / mask / gateway | `.3967.1.5.1.{2..4}.0` | `10.24.18.22` / `255.255.255.0` / `10.24.18.1` |
| DHCP enabled? | `.3967.1.5.1.5.0` | `1` (yes) |
| DNS servers | `.3967.1.5.1.10.1.1.{1,2}` (string) and `.3967.1.5.1.9.1.1.{1,2}` (Gauge32 = network‑order IP) | `192.168.240.177`, `192.168.240.59` |
| NTP server | `.3967.1.5.2.1.1.1` (with G29 garbage to strip) | `10.112.0.149` |
| Configured TZ offset (s) | `.3967.1.1.2.1.0` | `-21600` (= UTC−6, US Central) |
| Configured NTP target | `.3967.1.1.2.2.0` | `10.10.0.3` *(note: differs from the runtime NTP server above — likely a stale config field that wasn't migrated)* |

**Sub‑template gating regex (for the host_prototype per G11):** match `sysObjectID = 1.3.6.1.4.1.8072.3.2.10` (net‑snmpd on Linux) **and** non‑empty `.1.3.6.1.4.1.3967.1.1.1.7.0`. The Bosch model is reliably in `.3967.1.1.5.1.0` — that's the field to read into `{#CAM.HW.VENDOR_MODEL}` for tagging.

## 2 · System health metrics — the new surface that the desk audit missed

These are all under standard MIBs the camera already serves. Every one is a `snmpget` away — no extra firmware, no extra config, no Bosch private MIB needed.

### 2a — System uptime (G28: read the right OID)

| OID | Value at walk time | Reading |
|---|---|---|
| `.1.3.6.1.2.1.1.3.0` — `sysUpTime` | `5597` ticks = **56 seconds** | snmpd restart time, NOT camera uptime |
| `.1.3.6.1.2.1.25.1.1.0` — `hrSystemUptime` | `1699125464` ticks ≈ **196.7 days** | actual camera uptime |

**Decision:** the per‑camera Zabbix template uses **`hrSystemUptime.0`** for the `bosch.cam.uptime[{#CAM.ID}]` item. The reboot trigger fires when `change() < 0` (delta goes negative across the wraparound boundary or actual reboot). Document G28 in §B before the sub‑template ships — anyone reading the existing v1.2 desk audit will reach for `sysUpTime.0` and miss real reboots.

### 2b — CPU load (UCD‑SNMP‑MIB · `.1.3.6.1.4.1.2021.10.1`)

| Metric | OID | Value | Note |
|---|---|---|---|
| Load‑1 (string) | `.10.1.3.1` | `8.64` | needs G29 garbage strip |
| Load‑5 | `.10.1.3.2` | `8.34` | |
| Load‑15 | `.10.1.3.3` | `7.96` | |
| Load‑1 ×100 (Integer) | `.10.1.5.1` | `863` | preferred — no string parsing, divide by 100 in preprocessing |
| Load‑5 ×100 | `.10.1.5.2` | `833` | |
| Load‑15 ×100 | `.10.1.5.3` | `795` | |

**Important:** load average ~8 across 4 cores ≈ **2.0 per core sustained**. This is *normal* for an encoder under load, not an alarm condition. Alert threshold for "Cam: CPU saturated" is `Load‑5 > 14` (≈ 3.5/core) for ≥ 3 samples — must baseline against the pilot before locking the macro.

### 2c — Per‑core CPU% (HOST‑RESOURCES‑MIB · `.1.3.6.1.2.1.25.3.3.1.2`)

Four cores discovered (`hrDeviceTable` indices `196608..196611`):

| Core idx | CPU% (`hrProcessorLoad`) |
|---|---|
| 196608 | 42 |
| 196609 | 67 |
| 196610 | 62 |
| 196611 | 67 |

LLD candidate: discover the four entries via `.25.3.3.1.2`, expose `bosch.cam.cpu.load[{#CORE.IDX}]`.

### 2d — Memory (HOST‑RESOURCES‑MIB · hrStorageTable, alloc unit 1024 B)

| Pool | hrStorage idx | Total (KB) | Used (KB) | Note |
|---|---|---|---|---|
| Physical memory | 1 | 2,041,848 (≈ 2 GB) | 1,922,860 | Linux usage — most is page cache; not a real shortage |
| Swap space | 10 | 0 | 0 | **no swap configured — good**; nothing to monitor here |
| Buffers | 6 | 2,041,848 | 111,468 | |
| Cached | 7 | 286,548 | 286,548 | |
| Shared | 8 | 145,480 | 145,480 | |

**Useful derived item:** "real free memory" ≈ `total − used + cached + buffers` ≈ 517 MB free. Build as a Zabbix calculated item.

### 2e — Filesystems (`hrStorageTable` indices ≥ 39, alloc unit 4096 B)

| idx | Mount (after G29 strip) | Total (× 4 KB) | Used (× 4 KB) | Used % |
|---|---|---|---|---|
| 39 | `/dev/shm` | 255,231 | 0 | 0 % |
| 43 | `/run` | 102,093 | 3,914 | 3.8 % |
| 44 | `/tmp` | 255,231 | 0 | 0 % |
| 46 | `/var/lib/pulse` | 255,231 | 0 | 0 % |
| 47 | `/var/log` | 255,231 | 32,340 | 12.7 % |
| 48 | `/var/tmp` | 255,231 | 0 | 0 % |
| **49** | **`/data`** | **313,397** | **817** | **0.3 %** *(persistent config — the partition to watch)* |
| 50–52 | `/run/credentials`, `/run/systemd/incoming`, `/run/systemd/journal` | 102,093 each | 3,914 each | runtime |

**LLD candidate:** discover storage entries where `hrStorageType = hrStorageFixedDisk` (OID `.25.2.1.4`) and `hrStorageDescr` matches `^/`. Build a per‑mount free‑pct item, alert at >90 %. `/var/log` filling is the realistic risk (log floods); `/data` filling is the catastrophic one (camera can't persist config). **No SD card / external storage on the indoor 5100i IR — confirmed by absence of any `mmcblk1*` or `sda*` mount in the walk.**

### 2f — Boot args / rootfs (cosmetic, but informative)

`.1.3.6.1.2.1.25.1.4.0` decodes (hex‑to‑ASCII) to:
```
console=ttyS0 noinitrd root=/dev/mmcblk0p7 rw rootfstype=squashfs init=/linuxrc rootwait
```
The camera runs squashfs on eMMC partition 7. Squashfs is read‑only — that's why `/data` is a separate, writable partition. Confirms the indoor 5100i platform stores firmware in immutable form, config on a smaller writable mount.

### 2g — System process count (`hrSystemProcesses`)

| OID | Value | Use |
|---|---|---|
| `.1.3.6.1.2.1.25.1.6.0` | `106` | base process count — if it drops below ~80 the camera is partly booted / services missing |

### 2h — Network counters

The HC (64‑bit) counters in `ifXTable` are **zero on this firmware** (G30). Use the 32‑bit MIB‑II versions:

| Metric | OID (eth0 = ifIndex 2) | Value |
|---|---|---|
| ifInOctets | `.1.3.6.1.2.1.2.2.1.10.2` | `2,158,175,427` |
| ifOutOctets | `.1.3.6.1.2.1.2.2.1.16.2` | `1,480,560,254` |
| ifInUcastPkts | `.1.3.6.1.2.1.2.2.1.11.2` | `78,447,282` |
| ifOutUcastPkts | `.1.3.6.1.2.1.2.2.1.17.2` | `240,464,649` |
| ifInDiscards | `.1.3.6.1.2.1.2.2.1.13.2` | `948,170` |
| ifSpeed | `.1.3.6.1.2.1.2.2.1.5.2` | `100,000,000` (100 Mbit) |
| ifOperStatus | `.1.3.6.1.2.1.2.2.1.8.2` | `1` (up) |
| ifPhysAddress | `.1.3.6.1.2.1.2.2.1.6.2` | `00:07:5F:D7:95:84` |

**Counter32 wraparound:** 100 Mbit · 7 months ≫ 2³² bytes; the values above show a single wrap has already happened (ifInOctets is past the 2 GB midpoint, ifOutOctets has clearly wrapped at least once — but Zabbix `Change per second` preprocessing with `Discard unchanged with heartbeat` handles this natively). Keep the items as `Counter32` type — Zabbix manages the rollover.

`ifInDiscards = 948170` is high. At 7 months uptime that's ~150 discards / hour. Worth a low‑severity trigger: `change()/uptime_delta > 1/min` → INFORMATION.

## 3 · Bosch private MIB (`.1.3.6.1.4.1.3967`) — 74 entries decoded

The branch lays out roughly:

| Sub‑tree | Meaning | Items worth polling |
|---|---|---|
| `.3967.1.1.1` | Device identity (vendor, HW ID, FW codes, name) | already tabled above |
| `.3967.1.1.2` | Time configuration (TZ offset, NTP target) | `.3967.1.1.2.2.0` cross‑check vs the runtime NTP server at `.3967.1.5.2.1.1.1` — they disagree on this camera (`10.10.0.3` vs `10.112.0.149`), good drift signal |
| `.3967.1.1.4` | Two integer counters (`.1.1.4.1.1.1.1 = 659211`, `.1.1.4.1.1.2.1 = 0`) | unknown semantic — **needs vendor MIB to label**. Counter 1 increments fast (659 211 / 196 days ≈ 0.04 Hz). Plausible candidates: motion events lifetime count, frame‑drop count, IVA‑rule trip count. **Action: trigger known events (motion in front of camera) and re‑poll to identify.** |
| `.3967.1.1.5` | Hardware/sensor‑specific values | `.5.1` = model string · `.5.2 = 80` (device kind code) · `.5.10 = 680`, `.5.11 = 220`, `.5.12 = 940510` (sensor capability triplet — possibly max horizontal/vertical resolution × scale, or framerate × 100. Not safe to use until vendor MIB confirms.) · `.5.13`, `.5.14`, `.5.15.1.1.1` = hex‑encoded state blobs (the `.5.15` blob ends in `…556E6B6E6F776E00` = ASCII `Unknown\0` — looks like a "current state" enum with a label table) |
| `.3967.1.1.7..10..12` | Various scalars: `.7.1.1.1 = 620`, `.10 = 130`, `.12` = a 16‑byte hex blob ending in `319441D5` (32‑bit timestamp = 2026‑05‑11 18:22:13 UTC — almost certainly **last config‑change timestamp** or boot‑complete timestamp) | `.12` is high‑value: parse the trailing 4 bytes as Unix epoch and surface as `bosch.cam.config.last_change` — alert on unexpected change |
| `.3967.1.2.1` | `1` — video input count | informational |
| `.3967.1.2.2.1.1.{1..8}` | **8 encoder slots, hex‑encoded blob per slot** | Each blob is 40 bytes. Slot 1 has `0000012C` (= 300 ms = GOP?) then `000031E4 0000 6667 0000 6667` (likely target bitrate × frame‑rate × resolution code) — distinct from the all‑zero slots 2–8. **The structure has to be confirmed against the firmware MIB**, but the *change‑detection* angle is already useful: trigger on `change()` of any slot ≠ slot 1 = "operator added a stream", trigger on `change()` of slot 1 = "encoder config drifted". |
| `.3967.1.3.1..4` | Alarm/state matrices — all zero in this walk (no active alarm) | **The interesting branch to revisit during a motion test.** `.1.3.4.1.1.{1..16}` looks like a 16‑slot alarm matrix (binary alarms 1–16, e.g. motion / tamper / VCA‑rule‑1..N / aux input). Trigger known events to identify slots. |
| `.3967.1.4.1/.2` | Two more zeroed counters | likely IVA / analytics‑specific counters; identify against EVA rules |
| `.3967.1.5.1.{1..10}` | Network config (MAC, IP/mask/GW, DHCP, DNS) | already in standard MIB‑II; duplicate for parity |
| `.3967.1.5.2.1.1.{1,2}` | NTP/management server endpoints | `10.112.0.149` (primary), `0.0.0.0` (unused secondary) |

### What the private branch is *missing*

- **No temperature OID.** Walked twice — there is no thermistor read on the indoor 5100i IR. Confirms the desk audit prediction.
- **No IR illuminator state OID.** The IR LED on/off transitions go through the *alarm matrix* (`.3967.1.3.4.1.1.X`) and the RCP+ `0x0C38` event bus; there is no dedicated scalar.
- **No SD card health / SMART.** No SD slot, no entry.
- **No encoder load / FPS / bitrate live‑value scalars** — encoder *config* lives in `.3967.1.2.2.1.1.X` but the *current realised* bitrate/FPS isn't there. That metric has to come from RCP+ or ONVIF.

## 4 · The three G‑class gotchas (propose for §B)

### G28 — sysUpTime.0 on Bosch reflects snmpd restart, not camera boot

The net‑snmpd build on Bosch CPP7.3 restarts the SNMP agent on certain config changes (apply network settings, change SNMP user, etc.). `sysUpTime.0` therefore tracks *the agent*, not *the camera*. The walk shows the gap: `sysUpTime.0 = 56 s` vs `hrSystemUptime.0 = 196.7 d`. Templates that key reboot detection on `sysUpTime.0` will fire spurious reboot alarms every time the operator hits *Save* on the SNMP settings page. **Use `hrSystemUptime.0`.** Cross‑vendor note: this is a net‑snmpd general behaviour, not Bosch‑specific — same gotcha applies to any vendor that ships net‑snmpd (some Hanwha, some Pelco). Worth promoting to a base‑template note, not a vendor‑sub one.

### G29 — net‑snmpd on Bosch returns octet strings with trailing binary garbage

Every short OCTET STRING in the walk has trailing non‑printable bytes when read off the wire — `eth0à‡HÖe`, `/tmpà‡HÖe`, `/var/logl`, `swapà‡`, `Camera 1l`, `kthreaddl`, `8.64à‡HÖe`. Looks like a build that allocates a fixed buffer and doesn't NUL‑terminate the response. **Fix in Zabbix preprocessing on every Bosch SNMP string item**:

```
Step 1 — Regular expression:  ^([\x20-\x7E]+).*  →  \1
```

Strips everything from the first non‑printable byte. Apply on `sysName`, `ifDescr`, `ifName`, every `hrStorageDescr`, every `hrSWRunName`, the Bosch private model field, etc. **Critical for LLD**: filesystem/process discovery uses these strings as item keys, and unsanitised garbage breaks UTF‑8 in Zabbix's frontend.

### G30 — ifXTable HC counters return zero

`ifHCInOctets`/`ifHCOutOctets` (`.1.3.6.1.2.1.31.1.1.1.6/.10`) are `0` despite the camera having transferred GB of traffic. The 32‑bit MIB‑II `ifInOctets`/`ifOutOctets` work correctly. **Use the 32‑bit columns** with Zabbix `Change per second` preprocessing — `Counter32` type handles wraparound natively.

## 5 · Concrete recommendations for the Bosch sub‑template (v1)

Updates to the §5 starter set from the desk audit, now that we've validated against a real walk:

| Key | Source | Cadence | Decision (updated) |
|---|---|---|---|
| `bosch.cam.uptime[{#CAM.ID}]` | **`hrSystemUptime.0`** (NOT `sysUpTime.0`) | 5m | G28 applied |
| `bosch.cam.firmware.version[{#CAM.ID}]` | **`.3967.1.1.1.5.0`** + `.3967.1.1.1.6.0` (concat) | 1h | shorter codes than ONVIF/RCP+ strings; pure SNMP saves a separate call |
| `bosch.cam.model[{#CAM.ID}]` | `.3967.1.1.5.1.0` | once | drives the host_prototype's vendor‑sub‑template gating |
| `bosch.cam.hw.id[{#CAM.ID}]` | `.3967.1.1.1.4.0` | once | |
| `bosch.cam.load.1m[{#CAM.ID}]` | `.1.3.6.1.4.1.2021.10.1.5.1` (÷ 100) | 5m | trigger threshold TBD by baseline |
| `bosch.cam.load.5m[{#CAM.ID}]` | `.1.3.6.1.4.1.2021.10.1.5.2` (÷ 100) | 5m | same |
| `bosch.cam.cpu.cores[{#CORE.IDX}]` *(LLD)* | `.1.3.6.1.2.1.25.3.3.1.2` walk | 5m | 4 cores discovered |
| `bosch.cam.mem.free.pct[{#CAM.ID}]` | derived from `hrStorageTable` idx 1 | 5m | calc item |
| `bosch.cam.fs.used.pct[{#FS.PATH}]` *(LLD)* | `hrStorageTable` filtered to `hrStorageFixedDisk` | 5m | watch `/data` and `/var/log` |
| `bosch.cam.net.in.bps[{#CAM.ID}]` | `ifInOctets.2` × 8, Change per second | 5m | **not** HC — G30 |
| `bosch.cam.net.out.bps[{#CAM.ID}]` | `ifOutOctets.2` × 8, Change per second | 5m | |
| `bosch.cam.net.discards.in[{#CAM.ID}]` | `ifInDiscards.2` | 5m | trigger on rate increase |
| `bosch.cam.link.status[{#CAM.ID}]` | `ifOperStatus.2` | 1m | as before |
| `bosch.cam.process.count[{#CAM.ID}]` | `hrSystemProcesses.0` | 5m | sanity guard |
| `bosch.cam.config.last_change[{#CAM.ID}]` | last 4 bytes of `.3967.1.1.12.0` parsed as epoch | 5m | drift detector |
| `bosch.cam.alarm.slot[{#ALARM.SLOT}]` *(LLD)* | walk `.3967.1.3.4.1.1` (16 slots) | 1m | semantics need a motion‑test validation |
| `bosch.cam.encoder.config.slot1[{#CAM.ID}]` | `.3967.1.2.2.1.1.1` hex blob | 5m | change‑detection only until we have the MIB |

All items get the canonical tag schema per §C7 (`cam_id`, `cam_hw_id`, `rs_id`, `vendor=bosch`, `site`, `src=zbx`).

## 6 · Open items for the next pilot session

1. **Trigger a motion event** while the snmpwalk is repeated. The deltas in `.3967.1.3.{1..4}` and `.3967.1.1.4` will identify the alarm and counter semantics. Currently every alarm slot is zero — non‑decodable without a state change.
2. **Pull the firmware `.zip`** for build code `10020900` from the Bosch download portal, extract the `.mib` (typically `BOSCH-VIDEOJET-MIB.txt`), commit under `templates/mibs/`, and **walk again with `-m +BOSCH-VIDEOJET-MIB`** so every OID resolves to a symbolic name. The hex blobs in `.3967.1.1.5.13/.14/.15` and `.3967.1.2.2.1.1.X` decode cleanly once the structure is known.
3. **Apply a known config change** (e.g. rename the camera in the browser UI) and re‑poll `.3967.1.1.12.0` to confirm the "last config change" hypothesis for the trailing 4 bytes.
4. **Disable SNMPv1 on the camera and enable SNMPv3** (community thread says supported on this FW). Re‑walk to confirm coverage parity — the per‑camera template ships SNMPv3 by default per G18.

## 7 · Documents to update

- §B in `Milestone_Dashboard_Project_Plan_v1_2.html` — add G28 / G29 / G30 (or fold into a single "G28 — Bosch net‑snmpd quirks" if the plan prefers coarse‑grained gotchas).
- `M0_Vendor_Audit_Bosch.md` — promote §3 SNMP section: **the Bosch private MIB is real and present, but the bigger monitoring win is the standard host‑resources + UCD MIBs that ship alongside it**. Update the starter‑set table.
- `templates/mibs/` — does not yet exist; create alongside the FW zip extraction in pilot session 2.
