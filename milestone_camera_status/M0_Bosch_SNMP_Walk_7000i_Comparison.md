# M0 — Bosch FLEXIDOME multi 7000i IR · SNMP walk + 5100i comparison

Second pilot walk: a **FLEXIDOME multi 7000i IR** (codename `co-cam-48`) — 4-imager housing, one Linux board, one network interface. 3823 OIDs.

Decisive value of having two walks side-by-side: the **multi-imager device sets N=4 in every per-camera-instance Bosch private table**, vs N=1 on the single-imager 5100i. That tells us which sub-branches are per-imager (LLD candidates on a multi unit) vs per-device (single-row scalars regardless of imager count).

## 1 · Device identity

| Field | OID | 5100i (single-imager) | 7000i (multi 4-imager) |
|---|---|---|---|
| Model | `.3967.1.1.5.1.0` | `FLEXIDOME indoor 5100i IR` | **`FLEXIDOME multi 7000i IR`** |
| Vendor | `.3967.1.1.1.7.0` | `Bosch` | `Bosch` |
| Hardware ID | `.3967.1.1.1.4.0` | `F000B543` | `F000B543` — **identical** |
| Firmware short code | `.3967.1.1.1.5.0` | `10020900` | `82000830` |
| Firmware long signature (hex) | `.3967.1.1.1.6.0` | `04000407 05040904 03080003 00050200 0903` | `04000407 03020502 07070105 00030905 0809` |
| Kernel | `sysDescr.0` | Linux 5.4.238 **#1 Nov 2023** aarch64 | Linux 5.4.73 **#1 Dec 2021** aarch64 |
| Codename | `sysName.0` | `arc-cam` | `co-cam-48` |
| MAC | `.3967.1.5.1.1.0` | `00:07:5F:D7:95:84` | `00:07:5F:C6:96:DD` (Bosch OUI both) |
| IP / mask / gw | `.3967.1.5.1.{2..4}.0` | `10.24.18.22 / .../.1` | `10.112.18.48 / .../.1` |
| TZ offset (s) | `.3967.1.1.2.1.0` | `-21600` (UTC−6, Central) | `-18000` (UTC−5, Eastern) |
| Management/VMS server | `.3967.1.5.2.1.1.1` | `10.112.0.149` | **`10.112.0.149` — identical** |

**Decisive findings:**

1. **`.3967.1.1.1.4.0` (HardwareID) is NOT a model discriminator.** Both cameras report `F000B543` despite being completely different products. This is the **CPP7.3 platform ID** — a chipset/board family code shared across the FLEXIDOME line. **Δ vs the prior 5100i analysis:** I previously listed this as a candidate for `bosch.cam.hw.id` — withdraw that. Use **`.3967.1.1.5.1.0` (model string)** as the sole vendor‑sub‑template gating field. Hardware ID is *informational* only.
2. **`.3967.1.5.2.1.1.1` is reliably the VMS management endpoint.** Two cameras in different sites (Central vs Eastern TZ, different /24 networks) both report `10.112.0.149` — confirms this is the camera‑side record of which Milestone instance it's registered with. **Useful cross‑check**: trigger if the Bosch‑side VMS IP differs from `{$MILESTONE.HOST}` resolved by the proxy — caught misregistrations / camera moved to wrong VMS.
3. **The 7000i kernel is *older* than the 5100i kernel** (Dec 2021 vs Nov 2023). The user described it as "more up to date firmware" — the *application* firmware code is in a different number space (`82000830` vs `10020900`) which makes direct comparison meaningless, but the underlying Linux kernel ships from a different codeline (`co-cam` vs `arc-cam`). **Conclusion:** different FLEXIDOME platforms have **independent firmware tracks**, and "FW version" is only comparable within a model family. The vendor sub-template's "stale firmware" trigger needs a per-model macro (`{$MS.CAM.BOSCH.<MODEL>.FW.LATEST}`), not a global one.

## 2 · Bosch private MIB schema (now decodable)

Holding both walks side-by-side, every sub-branch can be classified as **per-device** (one row regardless of imager count) or **per-imager** (N rows = imager count). On the 7000i, N=4; on the 5100i, N=1.

| Sub-branch | Cardinality | Cardinality on 5100i | Cardinality on 7000i | Decoded meaning |
|---|---|---|---|---|
| `.3967.1.1.1.1.0`, `.3967.1.1.1.2.0` | per-device scalar | 1 (empty) | 1 (empty) | reserved / unused on these models |
| `.3967.1.1.1.3.1.1.X` | **per-imager** | 1 row | 4 rows | **per-imager logical name** (`co-cam-48-1..4`, `Camera 1` on 5100i). **This is the LLD walk root for imager-instance discovery on multi-cam units.** |
| `.3967.1.1.1.{4..7}.0` | per-device | 1 each | 1 each | hardware-ID, firmware-short, firmware-long, vendor — device-level identity |
| `.3967.1.1.2.{1,2}.0` | per-device | 1 each | 1 each | TZ offset, NTP target |
| `.3967.1.1.4.1.1.1.X` | **per-imager** | 1 row (=659211) | 4 rows (all 659210) | **monotonic counter, per-imager.** 5100i Δ uptime → counter delta confirms this isn't pure-uptime; near-identical value across two different hardware suggests **a firmware-internal build/version counter** that's evaluated per-imager runtime instance. **Treat as change-detect signal only** — don't try to extract a metric from it. |
| `.3967.1.1.4.1.1.2.X` | **per-imager** | 1 row (=0) | 4 rows (all 0) | second per-imager counter — possibly an event-trip lifetime counter, currently zero on both pilot boxes |
| `.3967.1.1.5.{1..15}.0` | per-device | 1 each | 1 each | model string + sensor/optics descriptors (`5.10`, `5.11`, `5.12` differ between models — sensor pixel-array or lens parameters), plus state blobs (`.5.13`, `.5.14`, `.5.15`) |
| `.3967.1.1.{7,9,10}.x` | per-device, sometimes single-row table | 1 row each | 1 row each | scalars whose semantics need the firmware MIB. **`.1.1.9.1.4.1`** is a 4-byte blob (5100i = `27062A00`, 7000i = `16000000`) — same structure, different values. Plausibly **IR illuminator state/intensity** since both pilot cameras are IR-equipped. Confirm by powering the illuminator on/off and re-walking. |
| `.3967.1.1.12.0` | per-device | 1 | 1 | 16-byte hex blob, trailing 4 bytes look like a hash. **Δ from prior analysis:** Both cameras' trailing-4 prefix is `0x3194…` — too coincidental to be a Unix epoch. **Retract the "config last-change timestamp" hypothesis.** More plausibly a config-blob digest. Still useful as a `change()` trigger ("config drifted since last poll"). |
| `.3967.1.2.1.1.1.X` | **per-imager** | 1 row (=1) | 4 rows (all =1) | **# video inputs per imager** — always 1 on the FLEXIDOMEs we've seen |
| `.3967.1.2.2.1.1.X` | **per-imager × per-stream** | 8 rows | 32 rows | **Encoder slot table: 8 stream slots per imager.** Row index = `(imager_idx − 1) × 8 + stream_idx`. On the 7000i, slots 1, 9, 17, 25 (= each imager's primary stream) carry `0000012C 0000XXXX 00006667 00006667 …` blobs; secondary slots carry an `FFFFFFFF` sentinel for "no stream configured". Use `change()` of any slot for encoder-config-drift detection. |
| `.3967.1.3.1.1.1.X` | **per-imager** | 1 row | 4 rows | per-imager alarm state #1 (currently 0 on both — needs a motion-test walk to identify) |
| `.3967.1.3.2.1.1.X` | **per-imager** | 1 row | 4 rows | per-imager alarm state #2 |
| `.3967.1.3.3.1.1.X` | **per-imager** | 1 row (5 hex bytes) | 4 rows (5 hex bytes each) | per-imager alarm state #3 — looks like a 5-byte bitmap |
| `.3967.1.3.4.1.1.X` | **per-DEVICE, fixed N=16** | 16 rows | 16 rows — **same count** | **Device-level alarm matrix (16 slots)** — does NOT scale with imager count. Likely covers chassis-wide signals (case-tamper, memory fault, network loss, system overheat, etc.). The fact that the 4-imager device still has exactly 16 slots is the strongest evidence this branch is per-housing not per-camera. |
| `.3967.1.4.{1,2}.1.1.1.1` | per-device | 1 each | 1 each | two zeroed counters — IVA/analytics device-wide |
| `.3967.1.5.1.*` | per-device | one set | one set | network config (one NIC per device, regardless of imager count) |
| `.3967.1.5.2.1.1.X` | per-device, table | 2 rows | 2 rows | management/VMS endpoints (primary + secondary) |

### Practical schema rule for the sub-template

```
Imager-instance LLD root        : walk .3967.1.1.1.3.1.1
Imager-instance alarm sources   : .3967.1.3.{1,2,3}.1.1.{idx}
Imager-instance encoder slots   : .3967.1.2.2.1.1.{(idx-1)*8+1 ... idx*8}
Device-wide alarm matrix        : .3967.1.3.4.1.1.{1..16}  (always 16, regardless of N)
Device-wide network/VMS/identity: .3967.1.5.*, .3967.1.1.{1,2,5}.*
```

For **single-imager models** (5100i): the LLD discovers one row, the sub-template behaves exactly like a per-camera template. For **multi-imager models** (7000i, plus the panoramic 5100i / 7000i / NIN-50122 family): the LLD discovers N rows and the host gets N copies of imager-level items keyed by `{#IMAGER.IDX}`.

This is **important for Milestone deployment**: per Plan v1.2 G2, the host_prototype dedups on `{#CAM.HW.ID}` so a 7000i multi-imager unit becomes **one Zabbix host**, not four — the four child Milestone cameras still appear as LLD-discovered Site-host items per §A.2b. Inside the single Zabbix host for the 7000i, our SNMP-side LLD discovers four imagers via `.3967.1.1.1.3.1.1.{1..4}` and tags each set of imager-level items with `imager_idx={#IMAGER.IDX}`. Cross-host lookup (G4 / G23) then joins each Milestone-side camera GUID to its corresponding imager_idx tag — most reliable join key is **displayName** (Milestone-side `displayName` matches `.3967.1.1.1.3.1.1.X` value modulo formatting).

## 3 · Standard MIBs: how the platforms differ

Both run net-snmpd on top of Linux 5.4; both serve the same MIB-II + HOST-RESOURCES + UCD trees. Numbers diverge as expected for "single imager, indoor, fast-eth" vs "four imagers, IR, gig-eth".

| Metric | OID | 5100i | 7000i |
|---|---|---|---|
| `hrSystemUptime` (camera) | `.25.1.1.0` | 1,699,125,464 ticks ≈ 196.7 d | 153,655,324 ticks ≈ **17.79 d** |
| `sysUpTime` (agent) | `.1.3.0` | 5,597 = 56 s | 4,127 = 41 s — **G28 confirmed twice** |
| `hrSystemDate` | `.25.1.2.0` | 2026-05-11 13:29:01 | 2026-05-11 13:48:21 — walks 19 min apart |
| `hrSystemProcesses` | `.25.1.6.0` | 106 | 99 — comparable |
| Cores (hrProcessorTable) | `.25.3.3.1.X` | 4 | 4 — same SoC family |
| Load-1m (×100) | `.2021.10.1.5.1` | 863 | 960 — slightly higher on 4-imager unit (encoding 4 streams) |
| Load-5m / Load-15m | `.2021.10.1.5.{2,3}` | 833 / 795 | 919 / 906 |
| Phys mem (KB) | `hrStorage idx 1, size×unit` | 2,041,848 ≈ **2 GB** | 3,572,328 ≈ **3.5 GB** — bigger box has more RAM |
| Phys mem used | same row, .6 column | 1,922,860 | 2,785,692 |
| Swap | `hrStorage idx 10` | 0 / 0 | 0 / 0 — no swap on either |
| Persistent `/data` mount total | `hrStorage idx 49 (5100i) / 46 (7000i)` | 313,397 × 4 KB ≈ 1.22 GB | 313,871 × 4 KB ≈ 1.23 GB — **same partition layout** (eMMC partition 7, squashfs root) |
| `/var/log` used % | `hrStorage` | 32,340/255,231 = 12.7 % | 56,946/446,541 = 12.7 % — coincidence or shared log-rotate policy |
| eth0 speed | `ifSpeed.2` | 100,000,000 (Fast Ethernet) | **1,000,000,000 (Gig Ethernet)** |
| eth0 oper | `ifOperStatus.2` | 1 (up) | 1 (up) |

**G28, G29, G30 are all reproduced on the 7000i.** Same `sysUpTime ≪ hrSystemUptime` gap (agent restarted ~41 s before walk despite 17 d device uptime). Same trailing-binary-garbage on string fields (`eth0à‡`, `/runà‡`, `/tmpà‡`, `swapà‡`, `9.19à‡`, `/var/logl`, etc.).

**G30 even stronger on the 7000i:** the HC counter columns (`.31.1.1.1.6` = `ifHCInOctets`, `.31.1.1.1.10` = `ifHCOutOctets`) are **completely absent from the walk** — not returned even as zero. The 32-bit counters work fine: `ifInOctets.2 = 2,043,172,025`, `ifOutOctets.2 = 3,226,510,889` (already past the 32-bit midpoint = at least one wraparound on this 17-day-old uptime). Use the 32-bit columns with Zabbix `Change per second` preprocessing — Counter32 handles rollover natively.

**These three gotchas are now confirmed as net-snmpd-on-Bosch behaviour, not 5100i-specific quirks.** Promote to base-template notes, not vendor-sub.

## 4 · Implications for the Bosch sub-template (v2 draft)

Updates to the §5 starter set from the prior analysis:

1. **Drop `bosch.cam.hw.id` as a per-camera distinct field.** `.3967.1.1.1.4.0` is platform code, identical across models. Replace with two items:
   - `bosch.dev.platform.code[{#HOST.NAME}]` — once, from `.3967.1.1.1.4.0` (informational, drives the platform-revision dashboard column).
   - `bosch.dev.model[{#HOST.NAME}]` — once, from `.3967.1.1.5.1.0` (the human-readable model — already in the prior draft).
2. **Add an imager-instance LLD** to the sub-template: walk `.3967.1.1.1.3.1.1` to discover `{#IMAGER.IDX}` and `{#IMAGER.NAME}`. Single-imager models discover one row; multi-imager models discover N.
3. **Move alarm / encoder-config items to the imager LLD**: keys become `bosch.imager.alarm.state1[{#IMAGER.IDX}]` (from `.3967.1.3.1.1.1.{IDX}`), `bosch.imager.alarm.state2[…]`, `bosch.imager.alarm.state3[…]`, `bosch.imager.encoder.slot{1..8}[{#IMAGER.IDX}]` (from rows `((IDX-1)*8+1)..(IDX*8)`).
4. **Keep the 16-slot device alarm matrix outside the LLD.** Add 16 items, `bosch.dev.alarm.slot[1..16]` from `.3967.1.3.4.1.1.{1..16}`. Same template item regardless of imager count.
5. **Add `bosch.dev.vms.endpoint`** from `.3967.1.5.2.1.1.1` (with G29 strip). Trigger: `≠ {$MILESTONE.HOST}` → INFORMATION ("camera VMS registration drift").
6. **Ethernet-speed-aware bandwidth thresholds.** Same `ifSpeed.2` item already in the starter set; the trigger expressions referencing `ifInOctets`/`ifOutOctets` rate need to compare to `ifSpeed.2 / 8` (bytes/s) rather than a hardcoded 100 Mbit assumption — the 7000i is gig, the 5100i is fast-eth.
7. **Per-model "stale firmware" trigger needs a per-model macro.** `.3967.1.1.1.5.0` is in a different number space per model family. Replace `{$MS.CAM.BOSCH.FW.LATEST}` with `{$MS.CAM.BOSCH.FW.LATEST.<MODEL_HASH>}` keyed off a hash of `.3967.1.1.5.1.0`, *or* (simpler) drop the trigger and surface firmware as informational-only, with the operator deciding what "current" means per-model. Recommend the latter for v1 — auto-discovering latest-firmware versions per Bosch model is a separate, never-completing project.

## 5 · The "multi" branch matters for the Milestone host model

A FLEXIDOME multi 7000i IR is **one Milestone hardware** with **four child cameras** (Milestone's data model: cameras are children of hardware, hardware is child of recording server). Per Plan v1.2 §B G2, the Camera LLD's host_prototype keys on **`{#CAM.HW.ID}`** (parent hardware GUID) to dedup multi-channel encoders — meaning the 7000i becomes **one Zabbix host**, and the four Milestone camera GUIDs are dependent items on the Site host (Plan §A.2b).

Cross-template join from the per-camera Zabbix host's imager LLD to the Milestone camera GUIDs uses **displayName equality**:

- Bosch SNMP imager-name (`.3967.1.1.1.3.1.1.X`) ≈ `co-cam-48-1`, `co-cam-48-2`, … on the pilot. Operator-set field, configurable in the Bosch browser UI.
- Milestone `displayName` (per-camera) is also operator-set, separately, in the XProtect Management Client.

**These two strings are not enforced to match** — operators name them independently and our pilot 7000i happens to have generic per-channel names that don't match what XProtect probably calls them. The join therefore has to go through the **parent hardware MAC** (already in the cross-host tag schema as `cam_hw_id={#CAM.HW.ID}` and recoverable from MIB-II `ifPhysAddress.2`).

**Cleaner join:** `cam_hw_id` → Bosch hardware MAC → Zabbix per-camera host (host_prototype keyed on MAC-derived `{#CAM.HW.ID}`), then for the four child camera GUIDs the `imager_idx` is recovered from Milestone-side `channel` field (Milestone's `camera.channel` matches the Bosch imager index for multi-imager hardware — confirm in M1 pilot before locking).

**Add to plan §B:**
- **G31-proposed** — Bosch multi-imager units expose imagers via `.3967.1.1.1.3.1.1.{1..N}` table; the per-camera Zabbix host gets an SNMP-side LLD to enumerate imager instances. Join between Milestone-side child camera GUIDs and Bosch-side imager_idx goes via `camera.channel` from the Milestone API, **NOT** via displayName (independently operator-set, no enforced parity).

## 6 · Open items (updated)

1. **HC-counter check on 7000i** — confirm G30 is the same on this firmware. (Will follow this message with a quick bash check.)
2. **Motion-event re-walk** — same as 5100i, but now with the deeper insight that we want imager-level alarm-state changes in `.3967.1.3.{1,2,3}.1.1.X` *and* the device-wide matrix `.3967.1.3.4.1.1.X` recorded simultaneously to disambiguate "this imager only saw motion" from "the whole housing tripped tamper".
3. **MIB extraction from both firmware tracks.** `co-cam` (7000i, FW `82000830`) and `arc-cam` (5100i, FW `10020900`) ship from different codelines — pull both MIB files. The OID schemas at `.3967.x` are clearly aligned (every leaf we tested at one ranks identically structured at the other), but the **value semantics** of unlabelled scalars (`.3967.1.1.7.1.1.1`, `.3967.1.1.10.0`, `.3967.1.1.5.{10,11,12}.0`) might differ across the two MIBs.
4. **Walk both with credentials disabled / minimum-rights user** to confirm the operator-facing "monitoring user" account works. Default `service` account on the 7000i may have wider privileges than we want for fleet monitoring.
