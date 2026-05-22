# M0 Task 1 — §A Existing-asset inventory (validated)

**Source artifacts walked**

- `templates/Milestone by HTTP API.yaml` (template name in YAML: **`Milestone XProtect by HTTP`**; the plan body refers to the file as `Milestone_by_HTTP_API.yaml`).
- `templates/milestone_cameras_read.sh` · `milestone_cameras_refresh.sh` · `milestone_cameras_state.py`
- `templates/milestone_ess_read.sh` · `milestone_ess_refresh.sh` · `milestone_ess_state.py`
- Diagnostic one-shots also present (operator tools — not on the runtime data path, kept where they are): `milestone_ess_lookup.py`, `milestone_ess_resolve.py`.

**Handoff classification** per Plan v1.2 §C:

- **LIFT** = copied verbatim (or with the mechanical namespace rewrite for PHP) into `tcs_dashboard/` by TCS Plan M1.
- **WIDGET** = stays in this repo only.
- **BOTH** = consumed by this repo's widgets *and* the unified module.

Every YAML artifact in this template ends up **LIFT** (the whole YAML ships to `tcs_dashboard/templates/` per §C1); the table classifies finer-grained behaviour where the same artifact is also consumed by `mssite` / `mscamera` / `msserver` (**BOTH**) vs. lift-only.

---

## §A.1 — Site-host items (top-level)

| # | Key | Type | Cadence | Master / dep-of | M1 decision | Handoff |
|---|---|---|---|---|---|---|
| 1 | `milestone.sites.get` | SCRIPT (JS, OAuth2 + GET `/api/rest/v1/sites`) | 5m | — (master) | **Stays** on Site host. Master for site.* dependents. | **LIFT** (consumed by `mssite` + TCS `ActionSurveillanceData`) → **BOTH** |
| 2 | `milestone.site.name` | DEPENDENT (`$.array[0].displayName`) | — | `milestone.sites.get` | **Stays.** | **BOTH** |
| 3 | `milestone.site.version` | DEPENDENT (`$.array[0].version`) | — | `milestone.sites.get` | **Stays.** | **BOTH** |
| 4 | `milestone.site.physicalmemory` | DEPENDENT (`$.array[0].physicalMemory`) | — | `milestone.sites.get` | **Stays.** | **BOTH** |
| 5 | `milestone.site.lasthandshake` | DEPENDENT CHAR | — | `milestone.sites.get` | **Stays.** | **BOTH** |
| 6 | `milestone.site.handshake.age` | DEPENDENT, seconds (JSONPATH + JS) | — | `milestone.sites.get` | **Stays.** | **BOTH** |
| 7 | `milestone.license.get` | SCRIPT (OAuth2 + GET `/licenseOverviewAll`) | 1h | — | **Stays.** Per G8 site-level license only in v1.x. | **LIFT** (read by TCS `ActionSurveillanceData` for license-summary card) → **BOTH** |
| 8 | `milestone.rs.getall` | SCRIPT (OAuth2 + GET `/recordingServers?disabled`) | 2m | — (RS-LLD + dependents master) | **Stays.** Plan A7 — confirmed. | **BOTH** |
| 9 | `milestone_cameras_read.sh[3600]` | EXTERNAL (reads JSON snapshot) | 5m | — (Camera-LLD + dependents master) | **Stays.** Plan A2/A6 — confirmed. | **BOTH** |
| 10 | `milestone_ess_read.sh[]` | EXTERNAL | `{$MILESTONE.ESS.DELAY}` (default 1d) | — | **Stays** per G15 (ESS is one WebSocket, scope must remain site-level). | **BOTH** |
| 10a | trigger: `milestone_ess_read.sh[] regexp \"error\":\"(stale|no_snapshot)\"` → WARNING | — | — | — | **Stays.** | **LIFT** |
| 11 | `net.tcp.service[tcp,{$MILESTONE.HOST},443]` | SIMPLE | 30s | — | **Stays.** | **BOTH** (API-gateway reachability tile in `mssite` + TCS) |

---

## §A.2 — Camera LLD (`milestone.cameras.discovery`)

Master: `milestone_cameras_read.sh[3600]` (preprocessing `$.__array[*]`).
Filter pair: `{$MILESTONE.CAM.NAME.MATCHES}` (include) / `{$MILESTONE.CAM.NAME.NOT_MATCHES}` (exclude, NOT_MATCHES_REGEX).

**LLD macros (today, five — Plan A4):** `{#CAM.ID}` (`$.id`), `{#CAM.NAME}` (`$.displayName`), `{#CAM.ADDRESS}` (`$.address`, injected by Python helper per G1), `{#CAM.MAC}` (`$.mac`), `{#CAM.ENABLED}` (`$.enabled`). **M1 adds** `{#CAM.HW.ID}` (G2 dedup), `{#CAM.HW.NAME}` / `{#CAM.HW.IP}` (G3 host_prototype naming), `{#CAM.HW.VENDOR}` (G11 vendor sub-template gating), `{#CAM.RS.ID}` (tag schema §C7).

### A.2a — Item prototypes that **MOVE** to the per-camera host (M1)

| Prototype | Type | Notes | Plan ref | Handoff |
|---|---|---|---|---|
| `icmppingloss[{#CAM.ADDRESS}]` | SIMPLE | ICMP loss % at `{$MILESTONE.CAM.PING.INTERVAL}` | A5, G14 | **LIFT** (per-camera template lifted, polled by per-camera host) → **BOTH** |
| `icmppingsec[{#CAM.ADDRESS}]` | SIMPLE FLOAT | RTT seconds | A5 | **BOTH** |
| `icmpping[{#CAM.ADDRESS}]` | SIMPLE | reachability, valuemap *Service state* | A5 | **BOTH** |

### A.2b — Item prototypes that **STAY** on the Site host (per A6/G15)

All dependent off the two batch masters at Site scope; per-camera hosts reach them via cross-host item lookup keyed by `cam_id` tag (G4 / G23).

| Prototype | Type | Master | Plan/gotcha ref | Handoff |
|---|---|---|---|---|
| `milestone.cam.raw[{#CAM.ID}]` | DEPENDENT master (extract one camera record + heartbeat 1h) | `milestone_cameras_read.sh[3600]` | A2 | **BOTH** |
| └ trigger: `nodata(15m)` AVERAGE, *disabled by default* | — | — | — | **LIFT** |
| `milestone.cam.address[{#CAM.ID}]` | DEPENDENT CHAR | `milestone.cam.raw` | G1 | **BOTH** |
| `milestone.cam.channel[{#CAM.ID}]` | DEPENDENT CHAR | `milestone.cam.raw` | A6 | **BOTH** |
| `milestone.cam.enabled[{#CAM.ID}]` | DEPENDENT CHAR + valuemap *Bool string* | `milestone.cam.raw` | A6 | **BOTH** |
| └ trigger: `="false"` WARNING (enabled by default) | — | — | — | **LIFT** |
| `milestone.cam.hwmodel[{#CAM.ID}]` | DEPENDENT CHAR | `milestone.cam.raw` | A6 | **BOTH** |
| `milestone.cam.hwname[{#CAM.ID}]` | DEPENDENT CHAR | `milestone.cam.raw` | A6 | **BOTH** |
| `milestone.cam.mac[{#CAM.ID}]` | DEPENDENT CHAR (heartbeat 1h) | `milestone.cam.raw` | A6 | **BOTH** |
| `milestone.cam.lastmodified[{#CAM.ID}]` | DEPENDENT CHAR | `milestone.cam.raw` | A6 | **BOTH** |
| `milestone.cam.rs[{#CAM.ID}]` | DEPENDENT CHAR (parent **hardware** GUID — name retained for back-compat) | `milestone.cam.raw` | G2, schema rename pending | **BOTH** |
| `milestone.cam.ess.raw[{#CAM.ID}]` | DEPENDENT TEXT (full per-cam ESS record + heartbeat 7d) | `milestone_ess_read.sh[]` | A3, G15 | **BOTH** |
| `milestone.cam.ess.comm.time[{#CAM.ID}]` | DEPENDENT CHAR (by_group lookup, requires `{$MILESTONE.ESS.STATEGROUP.COMMUNICATION}`) | `milestone.cam.ess.raw` | A6 | **BOTH** |
| `milestone.cam.ess.comm.type[{#CAM.ID}]` | DEPENDENT CHAR (by_group type GUID) | `milestone.cam.ess.raw` | A6, A9 | **BOTH** |
| `milestone.cam.ess.rec.type[{#CAM.ID}]` | DEPENDENT CHAR (requires `{$MILESTONE.ESS.STATEGROUP.RECORDING}`) | `milestone.cam.ess.raw` | A6 | **BOTH** |
| `milestone.cam.status[{#CAM.ID}]` | **CALCULATED** combined `{-1,0,1,2,3}` | refs `enabled` + `icmpping` + `ess.comm.type` | **A9 — formula must be rewritten in M1**: today uses `//icmpping[{#CAM.ADDRESS}]` (same-host shortcut); once ICMP moves to per-camera host, must use `last_foreach(...?[tag="cam_id" and value="{#CAM.ID}"])` per G4/G23 | **BOTH** + add `src=zbx` tag per §C7/G27 |
| `milestone.cam.alarm[{#CAM.ID}]` | **CALCULATED** (same formula as status, but JS preprocessing drops 0/-1 → only fault values 1/2/3 are stored — drives Honeycomb) | refs `enabled`+`icmpping`+`ess.comm.type` | Same G4 rewrite as `status`; the dashboard honeycomb keys on `tag view=alarm`. | **BOTH** |

### A.2c — LLD-level trigger prototypes (Site host)

All three are **DISABLED by default** today (operators rely on the honeycomb instead). Keep status untouched; tag with the canonical schema per §C7/G23.

| Trigger | Severity | Notes | Handoff |
|---|---|---|---|
| `Cam: communication not OK (per last ESS snapshot)` | HIGH | depends on `{$MILESTONE.ESS.TYPE.COMMUNICATION_OK}` + `{$MILESTONE.ESS.STATEGROUP.COMMUNICATION}` | **LIFT** |
| `Cam: not recording (per last ESS snapshot)` | AVERAGE | depends on `{$MILESTONE.ESS.TYPE.RECORDING_STARTED}` + `{$MILESTONE.ESS.STATEGROUP.RECORDING}` | **LIFT** |
| `Cam: unreachable by ICMP` (`max(...,#3)=0 and enabled="true"`) | HIGH | per G14 this stays *information* in M1 — the Combined-status calc is the canonical offline alert | **LIFT** with severity bump to INFORMATION per G14 |

---

## §A.3 — RS LLD (`milestone.recordingservers.discovery`)

Master: `milestone.rs.getall` (re-keyed by id in JS preprocessing).
**LLD macros (today, three — Plan A7):** `{#RS.ID}`, `{#RS.NAME}` (`$.displayName`), `{#RS.DESCRIPTION}`. **M1 adds** `{#RS.HOSTNAME}` + `{#RS.IP}` (resolved by new `milestone_rs_state.py`, per G5).

All RS item prototypes **stay** on the Site host today; M1 introduces a per-RS host (agent-based) for OS metrics + services + storage, but the API-derived fields below remain Site-scoped dependents.

| Prototype | Type | Master | Triggers | Handoff |
|---|---|---|---|---|
| `milestone.rs.raw[{#RS.ID}]` | DEPENDENT TEXT (extract one RS record, heartbeat 1h) | `milestone.rs.getall` | `nodata(15m)` AVERAGE | **BOTH** |
| `milestone.rs.enabled[{#RS.ID}]` | DEPENDENT CHAR + *Bool string* | `milestone.rs.raw` | `="false"` WARNING | **BOTH** |
| `milestone.rs.handshake.age[{#RS.ID}]` | DEPENDENT, seconds | `milestone.rs.raw` | `>300` HIGH (per G6 still useful) | **BOTH** |
| `milestone.rs.hostname[{#RS.ID}]` | DEPENDENT CHAR | `milestone.rs.raw` | — | **BOTH** |
| `milestone.rs.lasthandshake[{#RS.ID}]` | DEPENDENT CHAR | `milestone.rs.raw` | — | **BOTH** |
| `milestone.rs.version[{#RS.ID}]` | DEPENDENT CHAR | `milestone.rs.raw` | — | **BOTH** |

---

## §A.4 — Host macros (validated count: **13**, not "9 + filter pair")

Plan §A10 said *"9 host macros + camera include/exclude pair"*. Actual YAML has 13 — Plan §A10 omitted `STATEGROUP.RECORDING`, `TYPE.RECORDING_STARTED`, and `CAM.PING.INTERVAL` from its count. All 13 are LIFT and define the canonical vocabulary per §C5/G24 — **no renames at lift time**.

| Macro | Type | Default | Notes | Handoff |
|---|---|---|---|---|
| `{$MILESTONE.HOST}` | text | `127.0.0.1` | API gateway FQDN/IP | **LIFT** / **BOTH** |
| `{$MILESTONE.SCHEME}` | text | `https` | — | **LIFT** / **BOTH** |
| `{$MILESTONE.USER}` | text | — | XProtect Basic user | **LIFT** / **BOTH** |
| `{$MILESTONE.PASSWORD}` | **SECRET_TEXT** | — | per G24/G25 — never cached | **LIFT** / **BOTH** |
| `{$MILESTONE.CLIENT_ID}` | text | `GrantValidatorClient` | OAuth client id | **LIFT** / **BOTH** |
| `{$MILESTONE.CAM.NAME.MATCHES}` | text | `.*` | LLD include regex | **LIFT** |
| `{$MILESTONE.CAM.NAME.NOT_MATCHES}` | text | `^$` | LLD exclude regex | **LIFT** |
| `{$MILESTONE.CAM.PING.INTERVAL}` | text | `1m` | per G12 scale tuning | **LIFT** |
| `{$MILESTONE.ESS.DELAY}` | text | `1d` | ESS poll cadence | **LIFT** |
| `{$MILESTONE.ESS.STATEGROUP.COMMUNICATION}` | text | `53b40c77-…` | install-specific GUID | **LIFT** |
| `{$MILESTONE.ESS.STATEGROUP.RECORDING}` | text | — (empty) | install-specific GUID | **LIFT** |
| `{$MILESTONE.ESS.TYPE.COMMUNICATION_OK}` | text | `dd3e6464-…` | install-specific GUID | **LIFT** |
| `{$MILESTONE.ESS.TYPE.RECORDING_STARTED}` | text | — (empty) | install-specific GUID | **LIFT** |

**M1 adds (per Plan A10 + v1.1/v1.2 additions):** `{$MILESTONE.SITE.NAME}` (tag schema §C7), `{$MS.AGENT.*}`, `{$MS.CAM.SNMP.*}`, `{$MS.RS.*}`, `{$MS.WALL.THUMB.INTERVAL}` (G21), `{$DELL.IDRAC.SNMP.COMMUNITY|.SNMPV3.SECNAME|.AUTHPASS|.PRIVPASS}` (G18 — defaults empty, SECRET_TEXT). All go into the M0 `macros.md` per G24.

---

## §A.5 — Valuemaps (3, matches Plan §A10)

| Name | Mappings | Handoff |
|---|---|---|
| `Bool string` | `true→Enabled`, `false→Disabled` | **LIFT** |
| `Camera status` | `-1 Disabled in XProtect`, `0 OK`, `1 ESS comm fault (ping OK)`, `2 Ping down (ESS still OK)`, `3 Offline (ping + ESS)` | **LIFT** (drives §C6/§C8 status semantics — the SourceBadge map references these enums for tooltip text) |
| `Service state` | `0 Down`, `1 Up` | **LIFT** |

---

## §A.6 — Existing template dashboard (validated count: **8 widgets**, not 5)

Plan §A8 said *"five widgets — item ×4, honeycomb, itemnavigator, itemcard, problems"* but the YAML actually defines **8 widget tiles** (the four `item` tiles + the four others). The intent matches; the count was undercounted in the draft.

| # | type | Name | Source item / ref | Disposition | Handoff |
|---|---|---|---|---|---|
| 1 | `item` | Site | `milestone.site.name` | Equivalent surface absorbed by `mssite` header | **WIDGET** (template dashboard stays — operator-fallback) |
| 2 | `item` | Mgmt Server version | `milestone.site.version` | absorbed by `mssite` | **WIDGET** |
| 3 | `item` | Physical memory | `milestone.site.physicalmemory` | absorbed by `mssite` | **WIDGET** |
| 4 | `item` | API Gateway | `net.tcp.service[tcp,{$MILESTONE.HOST},443]` | absorbed by `mssite` | **WIDGET** |
| 5 | `honeycomb` | Cameras with alarms (filterable) | items `Cam * Alarm`, tag `view=alarm`, thresholds `1=#FFD54F / 2=#FF9800 / 3=#FF465C` | **Gold — keep** per A8. M2's `mssite` ships a custom-SVG honeycomb (G10 — no 500-item cap); template dashboard's native one stays as fallback. | **WIDGET** (custom SVG is new in `mssite`); thresholds are **LIFT** (TCS reuses the colour map) |
| 6 | `itemnavigator` | Camera list (filterable) | `Cam * Status (combined)` | superseded by `mscamera` drill-down | **WIDGET** |
| 7 | `itemcard` | Selected camera details | reference `CAMNAV._itemid` (broadcast from `itemnavigator`) | **Replaced** by `mscamera` per A8 | **WIDGET** |
| 8 | `problems` | Other active problems (this host) | host-scoped problems | absorbed by NOC ribbon | **WIDGET** |

The dashboard YAML block is **LIFT** as part of the template, but the unified `tcs_dashboard` discards it per §C1/§C4 — it renders its own React equivalents.

---

## §A.7 — External scripts (validated)

The six scripts named by the plan plus two diagnostic helpers also present in `templates/`.

| Script | Role | Cron | Output | M1 decision | Handoff |
|---|---|---|---|---|---|
| `milestone_cameras_state.py` | Authenticate (OAuth2) → `GET /hardware?includeChildren=cameras` (+settings for MAC) → flatten to `{cameras:{<guid>:…}, __array:[…]}` snapshot. JSONPath-friendly. | invoked by refresh | stdout JSON | **Keep**, +M1 minor: emit `hardwareId`/`hardwareName`/`hardwareModel`/`vendor` keys consistently for the new LLD macros `{#CAM.HW.*}` (most already present). | **LIFT** (§C2) → **BOTH** |
| `milestone_cameras_refresh.sh` | flock + atomic-write wrapper, writes `/var/lib/zabbix/milestone_cameras_state.json` (15 min cron). | `*/15 * * * *` | file | **Keep.** | **LIFT** (§C2) → **BOTH** |
| `milestone_cameras_read.sh` | Stale-aware `cat` of the snapshot for Zabbix's EXTERNAL item. Emits `{"error":"no_snapshot"\|"stale",…}` JSON on miss. | — | stdout | **Keep.** | **LIFT** (§C2) → **BOTH** |
| `milestone_ess_state.py` | OAuth2 → WSS `/api/ws/events/v1` → `startSession` + `addSubscription(cameras,*)` + `getState` → JSON keyed by camera GUID with `states` + `by_group`. Has `--list-stategroups` diag mode. | invoked by refresh | stdout JSON | **Keep**, G15 confirms scope stays site-level (one WebSocket). | **LIFT** (§C2) → **BOTH** |
| `milestone_ess_refresh.sh` | flock + atomic-write wrapper, writes `/var/lib/zabbix/milestone_ess_state.json` (daily cron — 1–2 min at 2500 cameras per A3). | `15 3 * * *` | file | **Keep.** | **LIFT** (§C2) → **BOTH** |
| `milestone_ess_read.sh` | Stale-aware `cat` (MAX_AGE default 48h = 2× daily). Stale JSON drives the `ESS snapshot stale or missing` trigger. | — | stdout | **Keep.** | **LIFT** (§C2) → **BOTH** |
| `milestone_ess_lookup.py` *(diagnostic, present in `templates/`)* | One-shot: show all state-group/type GUIDs for a given camera. Operator tool to pick ESS macro values. | — | — | **Stays where it is.** Ship in `templates/` (and lifted to `tcs_dashboard/externalscripts/`) as an operator helper, but not invoked at runtime. | **LIFT** (bundled with the lift) → **BOTH** |
| `milestone_ess_resolve.py` *(diagnostic, present in `templates/`)* | Enrich `--list-stategroups` JSON with human names from Config-API `eventTypes`. | — | — | **Stays where it is.** | **LIFT** → **BOTH** |

**New in M1 (per plan §C2):** `milestone_rs_state.py` + `milestone_rs_read.sh` (RS hostname/IP resolution + per-RS detail), and in M5 `milestone_camera_uplink.py` + reader. Both **LIFT/BOTH** when produced.

---

## §A.8 — Validation deltas vs. the plan's draft §A

Findings that should be folded into the v1.3 plan when the document is next revised (or captured in the M0 closeout):

1. **§A10 host-macro count is 9; YAML has 13.** Missing from the §A10 prose: `{$MILESTONE.ESS.STATEGROUP.RECORDING}`, `{$MILESTONE.ESS.TYPE.RECORDING_STARTED}`, `{$MILESTONE.CAM.PING.INTERVAL}`. All three are real and consumed by existing items/triggers — they need to make the macro inventory and `macros.md` per G24.
2. **§A8 dashboard widget count is 5; YAML has 8.** The "item ×4" line was already counted but the table summary read as 5 total. Description of which widgets are gold vs. replaced stays correct.
3. **Template name vs. filename.** Plan body says `Milestone_by_HTTP_API.yaml` (correct filename, with spaces: `Milestone by HTTP API.yaml`). The template's internal `template:` key is **`Milestone XProtect by HTTP`** — that's the name that appears in every calc-item formula (`/Milestone XProtect by HTTP/…`) and trigger expression. Important when M1 forks per-camera + per-RS templates: the calc/trigger expressions referencing `/Milestone XProtect by HTTP/…` need updating to the new template name(s) or rewritten as host-agnostic `last_foreach` per G4/G23.
4. **`milestone.cam.rs` is named after the wrong concept.** The item description in the YAML acknowledges the key was kept for back-compat but the value is the *parent hardware* GUID (`$.relations.parent.id`), not the recording server. M1 should add a parallel `milestone.cam.hw.id[{#CAM.ID}]` (correctly named, same source) and leave the old key alongside for backward history; the `{#CAM.HW.ID}` LLD macro should be the new canonical input to host_prototype dedup (G2). The actual RS GUID for the `rs_id` tag (§C7) requires either a separate lookup in the Python helper (camera → hardware → RS grandparent) or an extra LLD macro emitted by `milestone_cameras_state.py`.
5. **Two ESS diagnostic helpers** (`milestone_ess_lookup.py`, `milestone_ess_resolve.py`) are already present in `templates/` — the plan's "six external scripts" framing should explicitly call out that the diagnostics also ship (and lift), as one-shot operator tools.
6. **Combined-status / Alarm calc formulas** today use the `//key[…]` same-host shortcut. The same-host shortcut **must be replaced** in M1 (G4 / G23) with `last_foreach(//key?[tag="cam_id" and value="{#CAM.ID}"])` — flagged in A9 as a future-work item, but worth pulling into the M1 acceptance criteria so the per-camera-host ICMP move and the calc rewrite ship in the same commit (avoids a window where the calc references items on the wrong host).

---

## §A.9 — One-line classification roll-up (for M5b's reader)

```
LIFT-ONLY: external scripts bundle (8 files), template YAML as a file, valuemaps,
           macro vocabulary, LLD-level trigger prototypes.
BOTH:      every item + item-prototype + LLD + dependent in the YAML
           (consumed by mssite/mscamera/msserver AND by tcs_dashboard actions).
WIDGET:    the template's built-in dashboard (8 tiles) — operator fallback only;
           the unified module ships its own React UI per §C1/§C4.
```

M5b's handoff validator walks the YAML tag-by-tag (§C7 contract) and confirms every item in this inventory either (a) carries `src=ext|zbx|idrac` per G27, (b) carries `cam_id`/`rs_id`/`site` tags per G23, and (c) appears in `tcs_dashboard/templates/` byte-identical to this repo's copy.
