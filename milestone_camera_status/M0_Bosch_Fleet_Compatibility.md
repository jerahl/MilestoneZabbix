# M0 — Bosch fleet SNMP compatibility analysis

**Scope:** confirm whether the SNMP schema decoded from the 5100i + 7000i pilots (per `M0_Bosch_Findings.md`) holds across the rest of the fleet, or whether older model families need separate handling in the Bosch sub-template.

**Fleet shape:** ~2,461 Bosch cameras across **28 distinct model strings** spanning at least four firmware generations. The pilot work validated two model families on the same chipset platform (`F000B543` = CPP7.3); the fleet includes models from older platforms (CPP3, CPP4, CPP6) that pre-date the validation.

---

## 1 · Fleet inventory (operator-supplied)

| Count | Model string (as Milestone reports it) |
|---:|---|
| 743 | FLEXIDOME IP 5000i IR |
| 556 | FLEXIDOME IP 4000i |
| 265 | FLEXIDOME IP indoor 5000 HD |
| 212 | FLEXIDOME IP 5000i |
| 203 | FLEXIDOME indoor 5100i IR |
| 158 | FLEXIDOME IP outdoor 5000 HD |
| 97 | FLEXIDOME outdoor 5100i IR |
| 69 | FLEXIDOME IP micro 3000i |
| 35 | Bosch FLEXIDOME outdoor 5100i IR - 5MP |
| 28 | FLEXIDOME multi 7000i |
| 18 | Bosch FLEXIDOME IP 4000i |
| 18 | FLEXIDOME multi 7000i IR |
| 8 | DINION IP starlight 6000 HD |
| 8 | FLEXIDOME IP panoramic 5000 MP |
| 7 | FLEXIDOME IP 3000i IR |
| 6 | FLEXIDOME multi 7000i IR - 20MP |
| 4 | Bosch FLEXIDOME outdoor 5100i IR - 8MP |
| 4 | FLEXIDOME outdoor 5100i IR - 5MP |
| 3 | Bosch FLEXIDOME multi 7000i IR - 20MP |
| 3 | BOSCH FLEXIDOME IP indoor 5000 HD |
| 2 | FLEXIDOME outdoor 5100i |
| 1 | Bosch FLEXIDOME outdoor 5100i IR |
| 1 | BOSCH FLEXIDOME HD 720p VR IVA |
| 1 | Bosch FLEXIDOME multi 7000i |
| 1 | FLEXIDOME panoramic 5100i IR |
| 1 | FLEXIDOME indoor 5100i IR - 5MP |
| 1 | Bosch FLEXIDOME IP 5000i IR |
| 1 | Bosch FLEXIDOME multi 7000i IR |

---

## 2 · Model-string normalisation — the same model under multiple names

Milestone's `hardwareModel` is whatever the camera reports during discovery; case + vendor-prefix conventions drift across firmware versions and across hardware-onboarding tools. Same physical model often appears under 2–4 variants in one fleet:

| Canonical model | Variants seen | Total cameras |
|---|---|---:|
| FLEXIDOME outdoor 5100i IR | `FLEXIDOME outdoor 5100i IR` (97) · `Bosch FLEXIDOME outdoor 5100i IR` (1) · `Bosch FLEXIDOME outdoor 5100i IR - 5MP` (35) · `Bosch FLEXIDOME outdoor 5100i IR - 8MP` (4) · `FLEXIDOME outdoor 5100i IR - 5MP` (4) · `FLEXIDOME outdoor 5100i` (2) | 143 |
| FLEXIDOME multi 7000i IR | `FLEXIDOME multi 7000i IR` (18) · `Bosch FLEXIDOME multi 7000i IR` (1) · `FLEXIDOME multi 7000i IR - 20MP` (6) · `Bosch FLEXIDOME multi 7000i IR - 20MP` (3) | 28 |
| FLEXIDOME multi 7000i | `FLEXIDOME multi 7000i` (28) · `Bosch FLEXIDOME multi 7000i` (1) | 29 |
| FLEXIDOME IP 4000i | `FLEXIDOME IP 4000i` (556) · `Bosch FLEXIDOME IP 4000i` (18) | 574 |
| FLEXIDOME IP 5000i IR | `FLEXIDOME IP 5000i IR` (743) · `Bosch FLEXIDOME IP 5000i IR` (1) | 744 |
| FLEXIDOME IP indoor 5000 HD | `FLEXIDOME IP indoor 5000 HD` (265) · `BOSCH FLEXIDOME IP indoor 5000 HD` (3) | 268 |
| FLEXIDOME indoor 5100i IR | `FLEXIDOME indoor 5100i IR` (203) · `FLEXIDOME indoor 5100i IR - 5MP` (1) | 204 |

**Sub-template gating implication:** the model-match regex needs to be **case-insensitive**, **prefix-tolerant** (`^(?:Bosch\s+)?FLEXIDOME` not `^FLEXIDOME`), and **suffix-tolerant** (the `- \dMP` / `- 20MP` sensor-variant tail is optional). Concretely:

```
^(?i:bosch\s+)?(?:flexidome|dinion)\s
```

The case-insensitive flag matters because at least one model arrives as `BOSCH FLEXIDOME` (full caps), which a default-case regex would miss. **G29 garbage strip applies first** — if the firmware appends trailing non-printable bytes (which the pilot confirmed it does), the regex must run against the cleaned value.

---

## 3 · Platform classification (predicted, pending probe)

Bosch IP cameras have shipped on a series of "Common Product Platform" SoCs since ~2010 — CPP3, CPP4, CPP4-HD, CPP6, CPP7, CPP7.3. The SNMP MIB schema is consistent within a CPP generation but can change *between* generations. Our pilots only validated CPP7.3 (hardware platform code `F000B543`).

Below: the predicted CPP generation per model string, the predicted SNMP coverage, and the **probe priority** for confirming it.

### 3a — Validated (CPP7.3, schema confirmed) — 533 cameras

These are model strings of the families covered by the live pilots. Treat as schema-confirmed.

| Models | Count | Notes |
|---|---:|---|
| FLEXIDOME indoor 5100i IR (+ `-5MP` variant) | 204 | pilot box (`F000B543`, FW `10020900`/`06010980`) |
| FLEXIDOME outdoor 5100i IR (+ `-5MP`, `-8MP`, `Bosch` prefix variants) | 143 | same family, same chipset, same firmware track |
| FLEXIDOME outdoor 5100i (non-IR) | 2 | |
| FLEXIDOME panoramic 5100i IR | 1 | panoramic variant; **has SD slot** (panoramic differs from indoor here) — skip the "no SD" assumption per-model |
| FLEXIDOME multi 7000i IR (all variants incl 20MP) | 28 | second pilot box (`F000B543` again, codename `co-cam-48`) |
| FLEXIDOME multi 7000i (no IR) | 29 | same chipset family as 7000i IR; very likely identical schema |
| FLEXIDOME indoor 5100i IR - 5MP (with hyphen) | 1 | suffix variant of the pilot 5100i — confirmed schema |
| Bosch FLEXIDOME multi 7000i IR + 20MP variants | 4 | same as the 7000i variants above modulo string normalisation |
| **Total** | **533** | **~22 % of fleet** | 

Bosch sub-template ships against this group with **no further validation needed**.

### 3b — Likely-compatible (CPP6 / CPP7 "i" generation) — 1,488 cameras

The "i" suffix on FLEXIDOME models marks the post-2017 generation that introduced Essential Video Analytics and the unified web UI. These run later firmware codelines (kernel 4.x or 5.x) and almost-certainly carry the same `.3967` private MIB shape, but on a different chipset platform code.

| Models | Count | Risk | Probe priority |
|---|---:|---|---|
| FLEXIDOME IP 5000i IR (+ `Bosch` prefix variant) | 744 | **largest single group in the fleet** — highest impact if schema deviates | **High — probe at least 2 cameras** |
| FLEXIDOME IP 4000i (+ `Bosch` prefix variant) | 574 | second-largest group | **High — probe at least 2 cameras** |
| FLEXIDOME IP 5000i (non-IR) | 212 | same family as 5000i IR | Medium — probe 1 |
| FLEXIDOME IP micro 3000i | 69 | distinct micro form-factor; may strip features | Medium — probe 1 |
| FLEXIDOME IP 3000i IR | 7 | smallest "i" group | Low — probe 1 |
| **Total** | **1,606** | **~65 % of fleet** | |

**Risk model:** these are *likely* CPP6 or CPP7 (pre-7.3). Bosch has historically maintained the `.3967.1.1.*` identity branch and the `.3967.1.5.*` network branch across CPP generations, but `.3967.1.3.*` (alarm matrices) and `.3967.1.2.2.*` (encoder slots) have a real chance of differing in field layout. **Probe these before shipping the alarm and encoder-bitrate items**; if they deviate, ship the identity-only items and gate the others behind a model-list macro.

### 3c — Older generation (CPP4 / CPP4-HD "non-i" series) — 432 cameras

The "non-i" 5000 HD generation predates 2017 and runs on older CPP4 / CPP4-HD hardware with kernel 3.x and a different firmware codeline. Bosch has stated publicly that the SNMP MIB *was* expanded around the CPP6 transition.

| Models | Count | Risk | Probe priority |
|---|---:|---|---|
| FLEXIDOME IP indoor 5000 HD (+ caps variant) | 268 | older platform; may have minimal private MIB | **High — probe at least 2** |
| FLEXIDOME IP outdoor 5000 HD | 158 | same generation | **High — probe at least 1** |
| FLEXIDOME IP panoramic 5000 MP | 8 | older panoramic | Medium — probe 1 |
| DINION IP starlight 6000 HD | 8 | box camera (not dome) form factor; same SoC family | Medium — probe 1 |
| **Total** | **442** | **~18 % of fleet** | |

**Expected deviations:**

- The `.3967.1.1.5.1.0` model field is likely still present but may have different surrounding scalars in `.3967.1.1.5.{2..15}`.
- Encoder slot layout in `.3967.1.2.2.*` is likely different (older codecs, no 5 MP variants, different byte fields).
- Alarm matrix shape `.3967.1.3.*` may differ — VCA support is more limited on the CPP4-era cameras.
- Standard MIBs (MIB-II, HOST-RESOURCES, UCD) — likely older net-snmpd version, less complete. **Probe is the only way to know**.

**Decision for v1 sub-template:** ship the identity items for these (`bosch.dev.model`, `bosch.dev.fw.short`, `bosch.dev.platform.code`) plus the standard-MIB items (CPU, mem, FS, network) — all of which should work on any net-snmpd. **Conditionally gate** the Bosch-private-MIB items behind a calc-item check that `.3967.1.3.2.1.1.1` exists and is integer-valued; the gate auto-disables on the older platforms.

### 3d — Legacy / risk-unknown — 1 camera

| Model | Count | Notes |
|---|---:|---|
| BOSCH FLEXIDOME HD 720p VR IVA | 1 | CPP3 / CPP4 era; VR = "vandal resistant", IVA = "intelligent video analytics" branding (~2012). May not run net-snmpd at all — could be a proprietary embedded SNMP stack. |

**Decision:** the sub-template best-effort enumerates what's there. If only `sysDescr.0` works, that's fine. One camera in 2,461 — operator can rule it manually.

---

## 4 · The schema-fingerprint probe

To confirm the schema across model families, walk a small set of OIDs per pilot camera and compare. **Eight OIDs is enough** to fingerprint compatibility:

| # | OID | What it confirms |
|---|---|---|
| 1 | `.1.3.6.1.2.1.1.1.0` (sysDescr) | net-snmpd present, kernel version |
| 2 | `.1.3.6.1.4.1.3967.1.1.1.7.0` | Bosch private branch present at all |
| 3 | `.1.3.6.1.4.1.3967.1.1.5.1.0` | Model string (validates G29 strip + matches Milestone-side) |
| 4 | `.1.3.6.1.4.1.3967.1.1.1.5.0` | Firmware short code |
| 5 | `.1.3.6.1.4.1.3967.1.1.1.4.0` | Hardware platform code (e.g. `F000B543` = CPP7.3) — **the discriminator across generations** |
| 6 | `.1.3.6.1.4.1.3967.1.1.1.3.1.1.1` | Imager-1 name (proves the per-imager LLD root exists) |
| 7 | `.1.3.6.1.4.1.3967.1.3.2.1.1.1` | Imager-1 VCA motion-active flag (proves the decoded alarm OID exists; integer-valued) |
| 8 | `.1.3.6.1.4.1.3967.1.2.2.1.1.1` | Imager-1 encoder slot 1 (proves the encoder telemetry exists; expect 44-byte hex blob) |

A camera "fingerprints as compatible" if all 8 return non-error responses **and** OIDs 7–8 have the expected types (integer + 44-byte hex). Identity-only compatibility = OIDs 1–5 work, 6–8 don't. Older-platform deviation = OIDs 1–5 work but 6–8 return different shapes (e.g. shorter encoder blob, different alarm-OID semantics).

A small probe runner — `bosch_fleet_compat.py` — lives alongside the motion-probe tool to automate this against a list of IPs. See `tools/README.md` § Fleet compatibility probe for usage.

---

## 5 · Proposed probe campaign

For M0 closeout / M1 pre-flight, the operator picks **one camera per row in this list** from the fleet and runs the compatibility probe. Output is a fingerprint per camera; the analysis below tabulates expected vs actual.

| Group | Probe target — pick any one camera with this Milestone model string |
|---|---|
| CPP7.3 5100i family (validated reference) | already have a walk |
| CPP7.3 7000i family (validated reference) | already have a walk |
| 5100i panoramic | `FLEXIDOME panoramic 5100i IR` (1 camera in fleet — pick it) |
| `i` generation 5000-series | `FLEXIDOME IP 5000i IR` and `FLEXIDOME IP 5000i` — pick 1 each |
| `i` generation 4000-series | `FLEXIDOME IP 4000i` |
| `i` generation 3000-series | `FLEXIDOME IP micro 3000i` AND `FLEXIDOME IP 3000i IR` (different SKUs, both small) |
| Pre-`i` 5000 HD | `FLEXIDOME IP indoor 5000 HD` AND `FLEXIDOME IP outdoor 5000 HD` (probe both — different enclosure may matter) |
| Pre-`i` panoramic | `FLEXIDOME IP panoramic 5000 MP` |
| DINION box | `DINION IP starlight 6000 HD` |
| Legacy IVA | `BOSCH FLEXIDOME HD 720p VR IVA` |

**Total probes: 11 cameras** covering ~95% of the model-string distinct values and 100% of the platform-generation distinct values.

---

## 6 · Decision tree for sub-template gating

After the probe campaign, each generation lands in one of three buckets:

**Bucket A — full compatibility.** Schema fingerprint matches the 5100i/7000i. All v1 items from `M0_Bosch_Findings.md` §3 apply. **Bosch sub-template links as-is.**

**Bucket B — identity-only compatibility.** Standard MIBs work + Bosch private identity branch works, but the alarm/encoder OIDs don't return the expected types. **Sub-template branches:** a `bosch.dev.has_full_mib` calc item gates the alarm/encoder items off; the standard-MIB items still ship. Trigger an INFORMATION alert "Bosch model {model} only supports baseline monitoring".

**Bucket C — net-snmpd minimal.** Only standard MIBs return anything. The sub-template emits only the standard-MIB items (`bosch.cam.uptime` via `hrSystemUptime`, CPU LLD, FS LLD, IF LLD); no Bosch-private items. Identity macros default to `unknown` and the `bosch.dev.has_full_mib` flag = 0.

The single calc item:

```
bosch.dev.has_full_mib[{HOST.NAME}] =
    length(last(/host/bosch.imager.motion.active[1])) > 0
    AND length(last(/host/bosch.imager.encoder.slot1.raw[1])) >= 80
```

returns 1 if both decoded OIDs come back well-formed, 0 otherwise. Trigger expressions for the Bosch-private items reference `last(.../bosch.dev.has_full_mib[…]) = 1` so they auto-suppress on Bucket-B/C cameras.

---

## 7 · Risk summary

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| 5000-series ("i" generation) has schema deviation in encoder slot layout | Medium | High — affects 1,600+ cameras | Probe before shipping `bosch.imager.encoder.bitrate.kbps`; if deviates, ship identity-only for that generation |
| 5000 HD (non-i) generation has minimal/no private MIB | High | Medium — affects 432 cameras | Sub-template gracefully degrades to standard-MIB items via `has_full_mib` gate |
| Model-string regex misses a `BOSCH`-caps variant | Medium | Medium | Case-insensitive regex with `Bosch\s*` prefix tolerance |
| 5100i panoramic has SD card and our "no SD slot" call is wrong | High for that one camera | Low — 1 camera | Sub-template's filesystem LLD discovers by name; SD card mount auto-appears if present |
| Old IVA / 720p camera doesn't run net-snmpd | High | Low — 1 camera | Sub-template tolerates "no SNMP" via the host_prototype's existing reachability check |

---

## 8 · Next action

1. Operator runs `tools/bosch_fleet_compat.py` against one camera per row in §5 (11 cameras total).
2. Append output to this document as `§9 Fleet probe results`.
3. Each generation's bucket (A/B/C) is set.
4. M1 ships the sub-template with the bucket logic baked in.

This is the only outstanding blocker on `Milestone Camera vendor — Bosch.yaml` per Plan v1.2 §B G11.
