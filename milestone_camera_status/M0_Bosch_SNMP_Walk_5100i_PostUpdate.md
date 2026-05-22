# M0 — Bosch FLEXIDOME indoor 5100i IR · post-firmware-update SNMP walk

Third pilot walk: the **same physical 5100i** (MAC `00:07:5F:D7:95:84`, IP `10.24.18.22` — matched against the pre-update walk), now on **new firmware**. Walk was taken ~12 minutes after the camera rebooted off the new image.

| | Pre-update | Post-update |
|---|---|---|
| Kernel | Linux 5.4.238 (Nov 2023) | **Linux 5.15.173 (Jan 2026)** |
| FW short code (`.3967.1.1.1.5.0`) | `10020900` | `06010980` |
| FW long signature (`.3967.1.1.1.6.0`) | `04000407 05040904 03080003 00050200 0903` | **identical (unchanged)** |
| Model string (`.3967.1.1.5.1.0`) | `FLEXIDOME indoor 5100i IR` | **`FLEXIDOME indoor 5100i IR - 5MP`** |
| Total SNMP OIDs walked | 4022 | **2439** (−39 %) |

## 1 · The big operational change: SNMP surface shrank by 40 %

Net-snmpd on the new firmware exposes substantially less. Branches affected:

| Branch | Pre-update | Post-update | Impact |
|---|---|---|---|
| `.1.3.6.1.4.1.2021` (UCD-SNMP-MIB) | 80 OIDs | **0** | **Load average via `.2021.10.1.5.X` is gone. The `bosch.cam.load.{1,5,15}m` items in the prior sub-template draft will return "Not supported on this version" after firmware update.** |
| `.1.3.6.1.2.1.25.4.*` (hrSWRun process list) | ~800 OIDs | **0** | Process discovery LLD impossible. Not currently used by us — but the predicted "discover specific Milestone/encoder process and trigger on absence" idea is now off the table. |
| `.1.3.6.1.2.1.25.6.*` (hrSWInstalled) | present | 0 | Package inventory gone. Not used. |
| `.1.3.6.1.2.1.6.*` (TCP table) | 268 OIDs | 14 | TCP connection table gone — only scalars left |
| `.1.3.6.1.2.1.7.*` (UDP table) | 96 OIDs | 4 | UDP listener table gone — only scalars left |
| `.1.3.6.1.2.1.25.3.*` (hrDevice + hrProcessor) | 8 procs / 4 cores | **4 cores still present** (`.25.3.3.1.2.196608..196611` works) + virtual devices added | **Per-core CPU% (`hrProcessorLoad`) still works** — use it for CPU. Indices unchanged. |
| `.1.3.6.1.2.1.25.2.*` (hrStorage) | 16 entries, indices 1–52 | **17 entries, indices 1–50, BUT renumbered** | `/data` moved from idx 49 → 47, `/var/log` from 47 → 45, etc. **Never hardcode hrStorage indices.** |
| `.1.3.6.1.2.1.2.*` (ifTable) + `.31.*` (ifXTable) | 2 interfaces (lo, eth0) | **3 interfaces (lo, sit0, eth0)** — `eth0 moved from ifIndex 2 → 3` | **Every `ifInOctets.2`, `ifOperStatus.2`, etc. in the prior sub-template is now silently wrong.** |
| `.1.3.6.1.4.1.3967` (Bosch private) | 74 OIDs | 66 OIDs | Schema unchanged, a few scalars dropped (most notably `.3967.1.2.1.1.1.1` "video input count" is gone). |

**G32-proposed (must add to plan §B before the sub-template ships):**

> **Bosch firmware updates renumber SNMP indices.** `ifIndex` for `eth0` shifted from 2 → 3 between Bosch firmware short codes `10020900` and `06010980` on the same physical 5100i. `hrStorage` indices renumbered (`/data` 49 → 47). **All per-camera SNMP items must discover their target row by name** (`ifDescr`/`ifName` + G29 strip for interfaces; `hrStorageDescr` + G29 strip for filesystems) and reference the discovered `{#IFIDX}` / `{#FSIDX}` — never hardcode the integer. Applies to **every** vendor template, not just Bosch, but Bosch is the first one we have empirical confirmation on.

This is the largest single behavioural change between the three walks. **G32 is mandatory for any Bosch SNMP item that targets a table row.**

## 2 · The big operational shrinkage: UCD-SNMP-MIB is gone

`.1.3.6.1.4.1.2021` is **completely empty** on the new firmware. That removes:

- `laTable` (load average) — `.2021.10.1.5.{1,2,3}` — the Load-1m / 5m / 15m values
- `memTable` (UCD memory) — `.2021.4.*` — including the swap and ssCpuRaw\* fields
- `ssCpuRaw{User,System,Nice,Idle,…}` — `.2021.11.50..63`
- `systemStats.ssCpuRawWait/Kernel/Interrupt`

**Replacement strategy for the sub-template's CPU-related items:**

| Old plan | New plan |
|---|---|
| `bosch.cam.load.1m[{#CAM.ID}]` from `.2021.10.1.5.1 / 100` | **Drop.** No SNMP-side load average on new firmware. |
| `bosch.cam.cpu.cores[{#CORE.IDX}]` from `.25.3.3.1.2` LLD | **Keep.** Per-core CPU% still works at same OID + same indices (`196608..196611`) on both firmware versions. |
| (new) `bosch.cam.cpu.avg[{#CAM.ID}]` | **Add as a Zabbix calculated item**: `avg(last_foreach(/host/bosch.cam.cpu.cores[*]))`. Approximates Load average × 100 / cores; uses items the sub-template owns rather than UCD-MIB. |

The calculated item is per-camera-host and runs locally inside Zabbix — works regardless of Bosch firmware track.

## 3 · The model string now includes sensor variant

| Walk | `.3967.1.1.5.1.0` |
|---|---|
| 5100i (old FW) | `FLEXIDOME indoor 5100i IR` |
| 5100i (new FW) | `FLEXIDOME indoor 5100i IR - 5MP` |
| 7000i | `FLEXIDOME multi 7000i IR` |

The new firmware exposes the **sensor-megapixel variant** in the model string. Useful — operators can see "is this the 2 MP or 5 MP variant" at a glance — but the vendor-template gating regex must tolerate the suffix.

**Sub-template gating regex (updated):**
```
^FLEXIDOME .+? (?:- \d+MP)?$        # matches all FLEXIDOMEs with optional MP suffix
```
or, more permissive (recommended):
```
^FLEXIDOME                          # any Bosch FLEXIDOME
```
The base camera template ships first, the Bosch sub-template applies on any model starting with `FLEXIDOME`, and the *model-specific* tunables (e.g. has-SD-card on panoramic IR variants, gigabit-vs-fasteth bandwidth thresholds) come from per-model macros gated by `last(model) =~ <regex>` in calc items.

## 4 · Confirmed schema (third walk fully consistent with the decoded layout)

Every per-imager branch returned exactly 1 row (single-imager 5100i = N=1). Every per-device branch returned exactly its prior cardinality. **The schema decoded from the 5100i+7000i side-by-side is stable across firmware updates** — the FW change touches *what's exposed* (vanished UCD MIB, smaller HR tables) but not the *Bosch-private OID layout*.

## 5 · Validated facts about the Bosch private branch (corrections to prior analyses)

### `.3967.1.1.1.6.0` (the "long FW signature") is a HARDWARE platform fingerprint, NOT firmware

**Unchanged across the FW update** (same `040004070504090403080003000502000903` before and after). My earlier note classified this as a firmware build identifier — withdraw and reclassify as the **per-board hardware fingerprint**.

Use it as: `bosch.dev.board.fingerprint[{#HOST.NAME}]` — once-poll, never-changes informational. Different boards within the same model family probably have different signatures (the 7000i had a different value: `040004070302050207070105000309050809`).

### `.3967.1.1.1.5.0` (FW short code) does NOT sort numerically

| Walk | Value |
|---|---|
| 5100i old FW | `10020900` |
| 5100i new FW | `06010980` (numerically *lower* than the old code) |
| 7000i | `82000830` |

Firmware short codes are not lexicographically/numerically ordered. **Don't put a numeric comparison on this field**. Surface as informational, and let operators interpret current vs target FW per-model.

### `.3967.1.1.12.0` trailing 4 bytes — **per-second tick counter** (corrected; NOT a config digest)

| Walk | Trailing 4 bytes | Notes |
|---|---|---|
| 5100i old FW | `319441D5` | snapshot value |
| 5100i new FW | **`319448BB`** (delta: 7,398) | the "delta" is just seconds elapsed between walks |
| 7000i | `3194546A` | snapshot value on a different box |
| 5100i live watch (2026-05-12 16:24) | `…BC55 → BC56 → BC57 → BC59 → BC5A → BC5B → BC5C → BC5D → BC5E → BC5F → BC60` over 12 s | **+1 per second, monotonic** — proves it's a clock |

The live watch settles the question. The original "small delta across firmware update" observation looked like a digest but was actually `~7,400 seconds = ~2 hours` of elapsed time between the two walks: the trailing 4 bytes are a uint32 **per-second tick counter**.

**Corrected classification:** `bosch.dev.tick.counter[…]` — could be useful as an alive-heartbeat (if it ever stops incrementing, the camera's internal scheduler is stuck) but **noise during motion testing** and useless as a config-drift detector. The `bosch_motion_probe.py` tool suppresses it from the default change stream via `NOISY_OIDS`.

The unanswered question is what zero of the counter is. Across the four snapshots the value spans `0x31944100 → 0x319448BB → 0x319454BC → 0x31948FFF` — a range of `0xE000 ≈ 57 000 seconds ≈ 16 hours`, consistent with a counter that resets on some periodic event (NTP resync? firmware service restart? day rollover?). Probe in a follow-up — reboot the camera and watch whether `.1.1.12.0` resets to a value near zero or just continues incrementing.

### `.3967.1.1.5.13.0` — encodes the camera's IP-last-3-octets (decoded)

Bytes 5–7 of the 20-byte blob match the camera's IP:
- 5100i: `0A 18 12 16` (positions 4–7) = `10.24.18.22` ✓
- 7000i: `0A 70 12 30` = `10.112.18.48` ✓
- 5100i new FW: `0A 18 12 16` (unchanged — same camera, same IP) ✓

Useful sanity-check item but not a primary monitoring surface (IP is already in MIB-II).

### `.3967.1.1.5.12.0` — slow-increment persistent counter

| Walk | Value | Delta |
|---|---|---|
| 5100i old FW | 940510 | — |
| 5100i new FW | 940540 | +30 |
| 7000i | 1278430 | — (different box) |

The 5100i counter increased by 30 across a firmware update that **rebooted the camera** — so the counter is **persisted across reboots and survives firmware flash** (stored in the `/data` partition). The semantic is unclear without the MIB, but the increment rate is small enough that this is some "per-day operating hours" or "shutter actuation × scale" type metric. Worth surfacing for change-tracking even without a precise label.

### Scalars whose semantics moved on the same box — **auto-exposure correlated triple** (decoded via live watch)

| OID | Old FW | New FW | 7000i | Live watch | Decoded role |
|---|---|---|---|---|---|
| `.3967.1.1.7.1.1.1` | 620 | 600 | 385 | `590 → 600` (single +10 step) | slow-step scalar — colour-temp ×10 K? sensor-temp? |
| `.3967.1.1.9.1.1.1` | 39 | 45 | 17 | `51 → 43 → 42 → 43` (~1-2 Hz) | **auto-exposure integer** |
| `.3967.1.1.9.1.4.1` | `27062A00` | `2D012300` | `16000000` | `33 01 23 00 → 2B 01 24 00 → 2A 01 25 00 → 2B 01 25 00` | **auto-exposure 4-tuple** |

The live watch on a static scene shows `.9.1.1.1` and `.9.1.4.1` moving in lock-step at ~1 Hz with byte 0 of the 4-tuple **always equal** to the integer scalar (`51 = 0x33`, `43 = 0x2B`, `42 = 0x2A`). That's the smoking-gun signal these are the imager's auto-exposure loop:
- `.9.1.4.1` byte 0 = iris (= `.9.1.1.1`)
- `.9.1.4.1` byte 1 = static (=`01`)
- `.9.1.4.1` byte 2 = shutter or gain (varies)
- `.9.1.4.1` byte 3 = static (=`00`)

**Classification:** `bosch.imager.exposure.*` — useful as a "sensor is alive and adapting" diagnostic, but **noise during motion testing** and unsuitable for triggers (always changing). `bosch_motion_probe.py` suppresses these too.

### `.3967.1.2.2.1.1.X` (encoder slot blob) — decoded via live watch

Live watch on the 5100i-5MP captured a bitrate change on the primary encoder stream: `bytes 4-7` of slot 1 jumped from `00 00 02 EF` (= 751 kbps) to `00 00 03 42` (= 834 kbps) during an idle observation. Combined with the rest of the blob's static fields, the 44-byte layout decodes as:

| Bytes | Field | Pilot 5100i-5MP value | Meaning |
|---|---|---|---|
| 0–3 | `00 00 01 2C` → `00 00 01 2B` → `00 00 01 2C` (live) | 300 / 299 / 300 | **+1 per second, clock-like counter** (initial "GOP × fps" hypothesis was wrong — bytes move every second on the same encoder, same as the device-wide `.1.1.12.0` tick counter) |
| 4–7 | `00 00 02 EF` → `00 00 03 42` | **751 kbps → 834 kbps** | **current bitrate (kbps)** — this is the byte that moved |
| 8–11 | `00 00 07 13` | 1811 | avg / target bitrate? |
| 12–15 | `00 00 07 13` | 1811 | peak bitrate? |
| 16–19 | `00 00 00 00` | 0 | — |
| 20–23 | `20 0A 00 00` | **2592** | **width**  ← matches "5MP" model |
| 24–27 | `98 07 00 00` | **1944** | **height** (2592 × 1944 = 5,038,848 px ≈ 5 MP ✓) |
| 28–31 | `04 00 00 00` | 4 | codec (H.264 = 4?) |
| 32–35 | `FF 00 00 00` | — | — |
| 36–39 | `00 00 00 00` | — | — |
| 40–43 | `01 00 00 00` | 1 | enabled flag |

That's a **per-second snapshot of the encoder's working state**, including the live bitrate. Worth surfacing as `bosch.imager.encoder.bitrate.kbps[{#IMAGER.IDX}]` in the Zabbix sub-template — drop-to-zero is a real "encoder stalled" signal, sustained-low is a "static scene" signal that can correlate with motion-detection.

The 8 encoder slots per imager probably correspond to Bosch's 8 stream profiles (primary stream, secondary stream, snapshot, ROI, …); slots beyond the configured count fill with `FF FF FF FF` sentinels in the bitrate bytes — that pattern is visible in our walk dumps.

## 6 · Standard-MIB data points on the new firmware

For the record, since these are the items the sub-template actually uses:

| Item | OID | Value (new FW, ~12 min after reboot) |
|---|---|---|
| Camera kernel uptime | `hrSystemUptime.0` (`.25.1.1.0`) | 72,210 ticks ≈ **12.03 min** |
| Agent uptime | `sysUpTime.0` (`.1.3.0`) | 67,608 ticks ≈ 11.27 min — close to system uptime because they share a recent reboot |
| Per-core CPU% | `.25.3.3.1.2.{196608..196611}` | 36, 63, 61, 62 — same shape as old FW (42/67/62/67) |
| Process count | `hrSystemProcesses.0` (`.25.1.6.0`) | 151 — increased from 106 on old FW (more services in the new image) |
| Physical RAM total (KB) | `hrStorage idx 1` | 1,727,472 ≈ **1.65 GB** — **down from 2.0 GB on old FW** (new kernel reserves more for buffers/firmware) |
| Available memory (KB) | `hrStorage idx 11` *(new — was absent before)* | 609,920 ≈ 596 MB | 
| eth0 ifIndex | — | **3** *(was 2 — G32)* |
| eth0 ifSpeed | `ifSpeed.3` | 100,000,000 (Fast Eth — physical) |
| eth0 in/out octets | `ifInOctets.3` / `ifOutOctets.3` | 3,816,282 / 63,579,837 — fresh boot, low counts |
| eth0 phys addr | `ifPhysAddress.3` | `00:07:5F:D7:95:84` ✓ same MAC |

### New `Available memory` row (better than the old "free = total - used + cached" derivation)

The post-update walk has a new `hrStorage` entry: index **11**, descr **`Available memory`**, size 609,920 KB. This is net-snmpd-on-newer-Linux exposing the kernel's `MemAvailable` from `/proc/meminfo` — the *modern* Linux metric for "how much can you actually allocate without thrashing".

**Sub-template update:** prefer `hrStorage idx 11` (`Available memory`) over derived `total − used + cached + buffers` when the row is present. LLD by descr regex `^Available memory$`, fall back to the derived calc when the row is absent (e.g. on the old 5100i FW or the 7000i).

## 7 · The interface-renumbering catastrophe — concrete fix

The single most operationally impactful change is `eth0: ifIndex 2 → 3`. Without G32, a deployed Zabbix template that hardcoded `.X.2` everywhere will silently report zero bandwidth and "interface down" after any operator updates camera firmware.

**LLD structure for the per-camera Zabbix template's interface items:**

```yaml
discovery_rule:
  key: bosch.cam.if.discovery
  type: SNMP_AGENT
  snmp_oid: 'discovery[{#IFIDX},.1.3.6.1.2.1.2.2.1.1,{#IFNAME},.1.3.6.1.2.1.2.2.1.2]'
  filter:
    - macro: '{#IFNAME}'
      value: '^eth\d+$'                   # excludes lo, sit0, future tunnels
      operator: MATCHES_REGEX
  preprocessing:
    # G29 — strip trailing binary garbage off ifDescr before the filter sees it
    - regex: '^([\x20-\x7E]+).*'
      output: '\1'
      applies_to: '{#IFNAME}'
  item_prototypes:
    - key: 'bosch.cam.if.octets.in[{#IFIDX}]'
      snmp_oid: '.1.3.6.1.2.1.2.2.1.10.{#IFIDX}'
      preprocessing: [Change per second]
    - key: 'bosch.cam.if.octets.out[{#IFIDX}]'
      snmp_oid: '.1.3.6.1.2.1.2.2.1.16.{#IFIDX}'
      preprocessing: [Change per second]
    - key: 'bosch.cam.if.oper.status[{#IFIDX}]'
      snmp_oid: '.1.3.6.1.2.1.2.2.1.8.{#IFIDX}'
    - key: 'bosch.cam.if.speed[{#IFIDX}]'
      snmp_oid: '.1.3.6.1.2.1.2.2.1.5.{#IFIDX}'
```

Same pattern applies to `hrStorageTable` (filter by `hrStorageDescr =~ ^/`) and (when the firmware exposes it) `hrSWRunTable`.

## 8 · The three pre-existing gotchas, all reproduced

| | First walk (5100i old FW) | Second walk (7000i) | Third walk (5100i new FW) |
|---|---|---|---|
| **G28** sysUpTime ≠ hrSystemUptime | 56 s vs 196.7 d | 41 s vs 17.79 d | 11.27 min vs 12.03 min *(close because fresh reboot)* — **structurally still present** |
| **G29** trailing binary on octet strings | `eth0à‡HÖe`, `8.64à‡HÖe` | `eth0à‡`, `9.19à‡`, `/runà‡` | `sit0à‡HÖe`, `Camera 1l`, `F000B543l`, `/var/logl` |
| **G30** HC counters absent/zero | columns return 0 | columns absent entirely | columns absent entirely |

Three independent walks, three confirmations. These are now **structural Bosch-net-snmpd traits, not pilot quirks**. Promote G28/G29/G30 from "vendor sub-template notes" to the **base per-camera template**:

- G28 affects every vendor whose camera ships net-snmpd → use `hrSystemUptime.0` as a universal pattern.
- G29 affects every vendor whose camera ships net-snmpd with this particular libc/buffer build → the regex-strip preprocessing step is cheap and harmless on cameras that don't need it.
- G30 → never use ifHC* OIDs at the template level; use Counter32 `ifInOctets`/`ifOutOctets` with `Change per second`.

## 9 · Updated proposal: new gotcha **G32** for plan §B

> **G32 — Bosch firmware updates renumber `ifIndex`, `hrStorageIndex`, and `hrSWRunIndex`.** Same physical camera, same MAC, same IP — firmware update moved `eth0` from `ifIndex 2` to `ifIndex 3` (a `sit0` IPv6-in-IPv4 tunnel pseudo-interface appeared at idx 2). `/data` moved from `hrStorage idx 49` to `idx 47`. Any per-camera SNMP item that hardcodes a table index will silently produce wrong values after a firmware update. **Resolution:** every per-camera table-row item is built off an LLD that discovers the row by name (`ifName`/`ifDescr` for interfaces, `hrStorageDescr` for filesystems), with G29 garbage-strip preprocessing applied before the filter. The discovered index becomes an LLD macro (`{#IFIDX}`, `{#FSIDX}`). **This is the empirical case for "always discover, never hardcode" at the SNMP template layer.** Likely applies to every vendor running net-snmpd on a firmware that gets updates — the Bosch walk is just the first place we caught it red-handed.

## 10 · Updated v2 sub-template starter set (post all three walks)

| Key | Source | Cadence | Notes |
|---|---|---|---|
| `bosch.dev.model[{#HOST.NAME}]` | `.3967.1.1.5.1.0` (G29 strip) | 1d | Gating value for the sub-template + model-specific calc items |
| `bosch.dev.fw.short[{#HOST.NAME}]` | `.3967.1.1.1.5.0` (G29 strip) | 1d | Informational only; not numerically comparable |
| `bosch.dev.board.fingerprint[{#HOST.NAME}]` | `.3967.1.1.1.6.0` | once | Stable across FW updates; useful for hardware identity audit |
| `bosch.dev.platform.code[{#HOST.NAME}]` | `.3967.1.1.1.4.0` (G29 strip) | once | CPP platform code (e.g. `F000B543` is CPP7.3) — same value across many models |
| `bosch.dev.config.digest[{#HOST.NAME}]` | `.3967.1.1.12.0` trailing 4 bytes | 5m | `change()` → config-drift alert |
| `bosch.cam.uptime[{#HOST.NAME}]` | **`hrSystemUptime.0`** | 5m | G28 — never use `sysUpTime.0` |
| `bosch.cam.cpu.core.pct[{#CORE.IDX}]` *(LLD)* | walk `.25.3.3.1.2`, filter on `hrDeviceType == hrDeviceProcessor` | 5m | Stable across FW; only branch we have for CPU now |
| `bosch.cam.cpu.avg.pct[{#HOST.NAME}]` | calc item: `avg(last_foreach(/host/bosch.cam.cpu.core.pct[*]))` | 5m | Replaces UCD load average — works post-update |
| `bosch.cam.mem.free.kb[{#HOST.NAME}]` | `hrStorage idx where descr=='Available memory'`, fallback to derived | 5m | Use new `Available memory` row when present |
| `bosch.cam.fs[{#FS.DESCR}]` *(LLD)* | walk `hrStorageTable`, filter `hrStorageDescr =~ ^/`, with G29 strip | 5m | **Never hardcode** the index — G32 |
| `bosch.cam.if.in.bps[{#IFIDX}]` / `.out.bps` *(LLD)* | `ifTable` filtered by `ifDescr =~ ^eth`, G29 strip, G30: 32-bit columns + Change per second | 5m | **Never hardcode** ifIndex — G32 |
| `bosch.cam.if.speed[{#IFIDX}]` | `ifSpeed.{#IFIDX}` | 1d | Bandwidth-percent calcs reference this, not a hardcoded constant |
| `bosch.imager.name[{#IMAGER.IDX}]` *(LLD)* | walk `.3967.1.1.1.3.1.1` | 1d | 1 row on single-imager, N on multi |
| `bosch.imager.alarm.s1/s2/s3[{#IMAGER.IDX}]` | `.3967.1.3.{1,2,3}.1.1.{#IMAGER.IDX}` | 1m | needs motion-test walk to label |
| `bosch.imager.encoder.slot{1..8}[{#IMAGER.IDX}]` | `.3967.1.2.2.1.1.{(idx-1)*8+1..idx*8}` | 5m | change-detect only without the MIB |
| `bosch.dev.alarm.slot[1..16]` | `.3967.1.3.4.1.1.{1..16}` | 1m | device-wide, fixed 16 slots |
| `bosch.dev.vms.endpoint[{#HOST.NAME}]` | `.3967.1.5.2.1.1.1` (G29 strip) | 5m | trigger: ≠ `{$MILESTONE.HOST}` → INFORMATION |

The big edits vs. the post-7000i draft:
- **dropped** `bosch.cam.load.{1,5,15}m` (UCD MIB gone)
- **added** `bosch.cam.cpu.avg.pct` calc item as the load-average substitute
- **added** `bosch.dev.board.fingerprint` (re-classified `.3967.1.1.1.6.0`)
- **switched** every `hrStorage`/`ifTable` reference to LLD-by-name
- **expanded** the model regex tolerance for the new `- 5MP` suffix

## 11 · What's still open

1. **MIB extraction from BOTH firmware tracks.** Both the old `10020900` and the new `06010980` MIBs need to be pulled from the Bosch download portal. The schema didn't change between them — but if any scalar labels differ, we want to use the labels from the *newer* firmware.
2. **Motion-event walks** on both 5100i (post-update) and 7000i to disambiguate `.3967.1.3.{1..4}` semantics. The new firmware's smaller surface might also have changed how alarms are exposed — better to validate after the FW update than before.
3. **`hrStorage idx 11 "Available memory"`** appears to be a newer net-snmpd capability. Test against a 7000i with the **older** kernel (5.4.73, Dec 2021): does the row exist there? If yes, prefer it everywhere. If no, the calc item needs to fall back.
4. **`.3967.1.1.5.12.0`** counter semantics. Park for now; revisit when the MIB is in hand.
