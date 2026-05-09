# MilestoneZabbix

Zabbix 7.4 monitoring stack for Milestone XProtect 2022 R1+ video management
systems: a fleet-scale template, six external scripts, and a set of custom
dashboard widgets that together replace the native single-host XProtect view
with a three-tier host topology and a Surveillance NOC / Camera / Server
dashboard set.

This repository tracks **Project Plan v1.1** — see
[`milestone_camera_status/Milestone_Dashboard_Project_Plan_v1_1.html`](milestone_camera_status/Milestone_Dashboard_Project_Plan_v1_1.html)
for the canonical, milestone-by-milestone document. The summary below is the
operator's-eye view of what's here and where it's going.

---

## What's in the repo today

| Path | Purpose |
|---|---|
| [`templates/`](templates/) | `Milestone by HTTP API.yaml` — single-host XProtect template (sites, recording servers, cameras, license, ESS WebSocket state) plus six external scripts (`milestone_cameras_*`, `milestone_ess_*`). See [`templates/README.md`](templates/README.md) for full item / LLD / macro reference. |
| [`milestone_camera_status/`](milestone_camera_status/) | Custom Zabbix 7.4 widget — site-level fault summary tiles + click-to-PacketFence row table. See [`milestone_camera_status/README.md`](milestone_camera_status/README.md). |
| [`pf_device/`](pf_device/) | Unified PacketFence device widget — companion to the Milestone camera status widget and to the switch-port widgets from the AP project. See [`pf_device/README.md`](pf_device/README.md). |

The existing template polls the XProtect API Gateway as one Zabbix host and
exposes per-camera and per-RS data as dependent items on that single host. It
works, and ships with a serviceable native dashboard (honeycomb + item
navigator + item card).

## Where the project is going (Plan v1.1)

The next step is to split the one-host model into a **three-tier host
topology** so cameras and DVRs become first-class Zabbix hosts that can be
polled directly, and to replace the native dashboard with three custom widgets
modelled on the AP project's `apdetail` visual language.

### Target topology

```
Zabbix Server / Proxy
  ├── [Milestone Site host]            ← API Gateway poll target (unchanged)
  │     ├── milestone.sites.get / rs.getall / license.get
  │     ├── milestone_cameras_read.sh / milestone_ess_read.sh
  │     ├── net.tcp.service[443]
  │     ├── LLD: Camera discovery      → host_prototype per parent hardware
  │     └── LLD: RS discovery          → host_prototype per recording server
  │
  ├── [Camera hosts ×N]                ← auto-created, polled directly
  │     ├── icmpping / icmppingsec / icmppingloss
  │     ├── http probe (vendor admin URL, optional)
  │     └── linked (conditional): vendor SNMP sub-template (Axis first)
  │
  └── [Recording Server hosts ×M]      ← auto-created, polled by agent
        ├── Zabbix Agent 2 on Windows: CPU, memory, disk, NICs, services, eventlog
        ├── service.info[Milestone XProtect Recording / Event / Log Server,state]
        ├── perf_counter[\Process(VideoOS.Recorder.Service)\…]
        ├── LLD: storage.volume.discovery (per archive root)
        └── linked (conditional): "Dell iDRAC by SNMP"
              ├── inlet/exhaust temp, PSU state + watts, fan RPM
              ├── chassis service tag, BMC firmware
              └── LLD: physicalDisk + virtualDisk (RAID state, rebuild %)
```

The Milestone API Gateway remains the source of truth — it's the only path
that knows the camera ↔ hardware ↔ recording-server graph and the ESS state.
Per-camera and per-RS hosts *add* direct telemetry; they do not replace the
API-derived items.

### Three custom widgets

All three follow the Zabbix 7.4 widget framework rules carried forward from
AP Plan v3.2 §B (G16–G29) and use deployment-specific namespace prefixes
(`TcsMsSite`, `TcsMsCamera`, `TcsMsServer`).

| Module | Mockup | Tabs | Highlights |
|---|---|---|---|
| `mssite` | Surveillance NOC | 8 — Overview · Sites · Cameras · Recording Servers · Alarms · Storage · Evidence Lock · Reports | Top KPI strip (4 cells), three-card middle row (XProtect summary + Recording Storage + Live Ingress 24h), Sites table + Recording Servers mini-card grid, full-width Active Alarm Feed, full-width Camera Wall (opt-in, per-site, throttled — see G21). |
| `mscamera` | Camera Detail | 6 — Overview · Live · Recordings · Events · Health · Configuration | 320px sidecar (live preview pane with REC indicator + Smart Client / Restart Stream + Location / Hardware / Templates / Linked Switch Port blocks); right column: Active Issue, Stream Health 4-ring grid, Live Telemetry sparkline strips, Stream / Recording / Network KV, Recent Events. |
| `msserver` | Server Detail | 6 — Overview · Channels · Storage · Network · Events · Configuration | 6-cell KPI strip, Resource Utilization 24h dual-line + Server Health 4-ring (CPU/Mem/Disk/Inlet Temp), full-width Recording Channels grid, RAID 24-disk grid + System Information KV (service tag + BMC firmware), Network Interfaces table including iDRAC management port, Cameras-on-this-server table. |

Every cell carries a **`SourceBadge`** — `zbx` (Zabbix-polled item — agent /
SNMP / ICMP / HTTP), `ext` (Milestone API), or `idrac` (Dell BMC OOB) — so an
operator viewing a number always knows which probe path produced it. This is
the difference between "Milestone says the camera is recording" and "the
camera responds to ICMP." Operators need both, and they need to know which is
which when one says yes and the other says no.

### iDRAC OOB integration on Dell DVRs

The Server Detail mockup surfaces inlet temperature, chassis service tag,
RAID rebuild state, hot-spare count, and a dedicated iDRAC management
interface row in the network table — none of which is reachable from the
in-OS Zabbix Agent. v1.1 ships SNMP only (Dell `iDRAC-MIB-SMIv2`); Redfish
support is deferred to v2. M0 includes an iDRAC capability probe; M1 adds a
`Dell iDRAC by SNMP` sub-template conditionally linked off the per-RS
`host_prototype` when chassis vendor is Dell.

iDRAC credentials are template-level secret macros, default empty — items
skip-poll until set. **No defaults that pre-fill `root`/`calvin`** (G18).

## Roadmap — six milestones, ~12 weeks

| | Milestone | Weeks | Scope |
|---|---|---|---|
| **M0** | Foundation & Architecture | 1–2 | Audit existing template, vendor SNMP capability survey (Axis / Hanwha / Bosch / Hikvision / Dahua), DVR agent feasibility on a Windows pilot, naming + dedup rules (multi-channel encoders, site prefix), camera + server information-surface matrices, iDRAC pilot probe (snmpwalk + Redfish curl), widget design walkthrough → frozen architecture spec. |
| **M1** | Three-template split + LLD host_prototypes | 3–5 | Fork → `Milestone Site by HTTP API` (fleet) + `Milestone Camera by Direct Polling` (per-camera) + `Milestone Recording Server by Agent` (per-RS) + `Dell iDRAC by SNMP` sub-template. Pilot validation against ≤20-camera / 1-RS site. **Highest-risk milestone — clone first, smoke-test green before swapping production.** |
| **M2** | `mssite` widget — Surveillance NOC | 6–7 | 8 tabs, KPI strip, three-card middle row, Sites + RS grid, Active Alarm Feed, Camera Wall (opt-in per-site, default 30s thumb refresh, pause on visibility change). |
| **M3** | `mscamera` widget — Camera Detail | 6–7 | 6 tabs, 320px sidecar with live preview, 4-ring Stream Health, dual-strip Live Telemetry sparklines. Stream stats sourced from Milestone, **not** from parallel RTSP probes (G20). |
| **M4** | `msserver` widget — Server Detail | 8–9 | 6 tabs, 6-cell KPI strip, dual-line resource chart, iDRAC inlet-temp ring, Channels grid, 24-disk RAID grid, iDRAC mgmt-interface row in network table. |
| **M5** | Polish, alerts, sign-off | 10–12 | iDRAC alert pass, switch-port discovery cross-link, fleet rollout playbooks (DVR agent + iDRAC), permissions, multi-site validation, ICMP scaling guidance (1m at ≤500 cams, 5m at 500–2500, 10m above), per-camera license drill-down (click-only), final sign-off. |

M2/M3/M4 run in parallel after M1 sign-off.

## Carry-forward gotchas — settled, do not rediscover

Pulled from AP Plan v3.2 §B; same Zabbix 7.4 deployment, same constraints:

- **G16–G24 (Zabbix 7.4 widget framework):** globally-unique module
  namespace; manifest `"type":"widget"` with `widget.js_class` + `size`;
  asset paths under `"assets":{"js":[…],"css":[…]}`; `Widget.php extends
  CWidget` not `CModule`; host fields use `CWidgetFieldMultiSelectHost`
  (plural `hostids`).
- **G25–G29 (data flow + JS lifecycle):** view via
  `(new CWidgetView($data))->addItem($body)->show()`; pack operator-visible
  payload under `"data"` on AJAX; read fields via
  `$this->fields_values['name']`; every JS lifecycle override
  (`onInitialize` / `onActivate` / `onDeactivate` / `setContents`) calls
  `super.<method>()` first; DOM rendering happens in `setContents(response)`,
  not `onActivate`; `onReady` is not a real `CWidget` hook.
- **G21 (PHP 8.0, not 8.1+):** drop `readonly` properties, `array_is_list()`,
  enums, `never`, `new` in initializers, intersection types. Polyfills go in
  the calling namespace.
- **G16 (deployment-specific namespace prefix):** picked per-module from the
  start — `TcsMsSite`, `TcsMsCamera`, `TcsMsServer`.

Reference AP Plan v3.2 §B before writing the first widget line. Budget zero
iterations on these — they each cost one on the AP project.

## Milestone-specific gotchas (G1–G21, full list in the plan)

Highlights:

- **G1 — Camera IP source.** `/api/rest/v1/cameras` has no `address` field; the
  IP belongs to the parent *hardware*. The existing Python helper joins them
  before writing the snapshot. M1's host_prototype must use the pre-joined
  address.
- **G2 — Multi-channel encoders.** Key host_prototypes on `{#CAM.HW.ID}`
  (parent hardware GUID) — not `{#CAM.ID}` — or one physical encoder spawns
  N Zabbix hosts pinging the same IP every minute.
- **G3 — Display-name uniqueness.** Milestone allows duplicate camera names;
  Zabbix host names must be globally unique. Use `{#CAM.HW.NAME}` (else
  `{#CAM.NAME} ({#CAM.HW.IP})`), with optional `{$MILESTONE.SITE.PREFIX}` for
  multi-site instances.
- **G4 — Calc-item cross-host references.** Once ICMP moves to per-camera
  hosts, `milestone.cam.alarm` calc must use
  `last_foreach(/*/icmpping?[tag="cam_id" and value="{#CAM.ID}"])` — tag-based
  foreach is host-agnostic. Validate at scale before blanket conversion.
- **G10 — Honeycomb scale ceiling.** Native honeycomb caps at ~500 items.
  Either render custom SVG in `mssite`, or scope the native widget per-RS
  (≤500 cameras each).
- **G12 — ICMP scale.** 2500 cameras × 1m ICMP = 2500 active checks/min.
  Default proxy `StartPingers=1` will queue. Macro `{$MILESTONE.CAM.PING.INTERVAL}`
  exists; document the sizing table in M5.
- **G15 — ESS WebSocket scope.** One WebSocket emits all-camera state. Do
  not move it per-camera — that's N WebSockets for no benefit. Per-camera
  widgets read via cross-host `API::Item()->get()`.
- **G18 — iDRAC default credentials.** Never pre-fill `root`/`calvin`. Macros
  `{$DELL.IDRAC.SNMP.COMMUNITY}` etc. default empty, type `secret`. Items
  skip-poll until set.
- **G20 — ONVIF / RTSP probing.** v1.1 does **not** open RTSP from Zabbix.
  Stream FPS / bitrate / last-frame-age in `mscamera` source from
  *Milestone*'s view of the stream (RS perfcounters), not a parallel
  consumer. Running RTSP at scale would be a denial-of-service against the
  cameras.
- **G21 — Camera Wall thumbnail bandwidth.** Per-site only, opt-in,
  `{$MS.WALL.THUMB.INTERVAL}` defaults to 30s, pauses on `visibilitychange`.

The full G1–G21 list (with v1.1 additions G16–G21 added by the mockup
review) lives in §B of the plan.

## Companion projects

- **AP Plan v3.2** — the ExtremeCloud IQ AP monitoring project that this
  plan inherits widget-framework lessons from. Same Zabbix 7.4 deployment;
  the `apdetail` widget is the visual reference for `mscamera` / `msserver`.
- **PacketFence** — the [`pf_device/`](pf_device/) widget is shared between
  this project and the AP / switch-port projects; click-to-lookup events
  flow `mcs:cameraSelected` → `pf_device` (and the long-term `pf:deviceSelected`
  unified event).

## Installing what's here today

See the per-component READMEs for current install steps:

- Template + external scripts: [`templates/README.md`](templates/README.md)
- `milestone_camera_status` widget: [`milestone_camera_status/README.md`](milestone_camera_status/README.md)
- `pf_device` widget: [`pf_device/README.md`](pf_device/README.md)

The `mssite` / `mscamera` / `msserver` widgets and the
fleet/per-camera/per-RS template split land in M1–M4. This README will be
updated with install steps as each milestone ships.

## License

See [LICENSE](LICENSE).
