# M0 — Vendor probe survey: Bosch FLEXIDOME indoor 5100i IR

Scope: what we can pull from a Bosch FLEXIDOME indoor 5100i IR (NDV-5704-AL family, CPP7.3 platform, FW 7.8x+) without touching Milestone — i.e. **direct camera HTTP/S / SNMP / ONVIF / ICMP**. Feeds the future `Milestone Camera vendor — Bosch` sub-template (per Plan v1.2 §B G11, ships once we encounter Bosch in production; the v1.1 plan ships Axis first).

There are four independent channels into a Bosch IP camera. Each has a different auth model, payload shape, and rate budget. Pick per-metric in M1 so we don't poll the same value twice via two paths.

| Channel | Transport | Auth | What it's good for | What to know |
|---|---|---|---|---|
| **RCP+ over CGI** | HTTPS `GET /rcp.xml?command=…` | HTTP **Digest** (camera supports Basic + Digest; default `service`/`<set-at-install>`) | The proprietary bus that the Bosch web UI itself uses. Everything you see in *Configuration → Service → System Overview* is reachable here. | Hex command codes, payload encoded as hex string. Available since FW 3.0. RCP+ v3 is the wire protocol; the CGI wrapper is the doc below. |
| **ONVIF** | HTTPS SOAP `/onvif/device_service` + `/onvif/media_service` etc. | WS-UsernameToken (digest password hash) | Cross-vendor parity — same shape for Axis/Hanwha/Hikvision/Bosch. Profiles **S, T, G, M** all conformant on 5100i. | SOAP envelope overhead vs RCP+. The auth helper is non-trivial — use a library. |
| **SNMP** | UDP/161 (polling) + UDP/162 (traps) | v1 community **or** v3 user (auth + priv) | Lowest-overhead poll for plain scalars (uptime, ifInOctets, etc.). | v1 + MIB-II + Bosch private MIB-II extension (≤ FW 6.40). v3 basic since later FW. **Bosch ships the camera-specific `.mib` inside the firmware `.zip`** on the product page — pull it once per FW major and check it into the repo. (Pre-FW 7.50.xxx: the MIB is also reachable at `http://{cam}/snmp_cmds.htm`.) |
| **ICMP + TCP probe** | ICMP echo + `net.tcp.service` | none | What the existing Site template already does (`icmpping[{#CAM.ADDRESS}]`). Cheapest possible up/down signal. | Already covered by the per-camera template's three ICMP prototypes per §A.2a of the inventory. |

The 5100i datasheet also lists NTP/SNTP, 802.1x, DNS/DNSv6, DDNS, SMTP, iSCSI, UPnP/SSDP, DiffServ, LLDP, SOAP, CHAP, digest auth — all of those are *services the camera consumes*, not monitoring surfaces. Useful context: SMTP + syslog + SNMP traps are the three push paths the camera itself can emit; **the FLEXIDOME 5100i has no on-board syslog server**, only the syslog *client* (it can send), which means anything we want to know about it has to be pulled.

---

## 1 · RCP+ over CGI — primary monitoring channel

Reference: *Bosch Video IP — RCP+ over CGI* (`media.boschsecurity.com/fs/media/.../rcpplus-over-cgi.pdf`).

### URL shape

```
https://{cam}/rcp.xml?command=0xNNNN&type=P_XXXX&direction=READ&num=1&payload=...
```

| Parameter | Meaning |
|---|---|
| `command=0xNNNN` | RCP+ opcode (table below) |
| `type=P_OCTET\|P_STRING\|P_DWORD\|P_BYTE\|...` | Payload type. `P_OCTET` is the common "give me the bytes" reply. |
| `direction=READ\|WRITE` | Polling = always `READ`. |
| `num=N` | Instance index (e.g. encoder 1 / 2 / 3 for the multi-stream encoder). |
| `payload=hex…` | For writes; optional / empty for reads of scalar-valued commands. |

Reply is XML wrapping a hex blob; the wrapper carries the same `command` echoed plus `result=` and length. Decoding rule per opcode (string → big-endian UTF-16 length-prefixed in many cases; counters → DWORD network-byte-order). The C library `hawell/rcpplus` is the cleanest reference implementation if we need an in-template JS port.

### Commands worth polling (confirmed names from `hawell/rcpplus` + `edgexfoundry/device-camera-go` + Bosch RCP+ CGI doc)

| Opcode | Const | What it returns | Cadence in our use | Notes |
|---|---|---|---|---|
| `0xFF10` | `CONF_CAPABILITY_LIST` | Device capability list (which other opcodes the camera answers) | once at discovery | Use this to feature-detect at LLD time — if the camera *isn't* a Bosch (or is a stripped-down Bosch), the sub-template self-disables. |
| `0x0026` (commonly cited as) | device version / firmware (P_STRING, UTF-16) | firmware version string e.g. `7.82.0028` | 1h heartbeat | The browser interface "Firmware version" field reads this. |
| `0x0011` / `0x002a` (vendor-specific in rcpdefs) | hardware version + hardware serial | hardware ID string | 1h heartbeat, `DISCARD_UNCHANGED_HEARTBEAT 24h` | `get_device()` in `rcpplus/src/device.c` reads this exact pair to identify the box. Match against `{#CAM.HW.MODEL}` from Milestone's hardware record (G9 — case-sensitive). |
| `CONF_MAC_ADDRESS` | network MAC (6 bytes) | already on Milestone side via the Python helper, but useful to **cross-check** that what Milestone thinks is on the IP is what's actually on the IP (catches IP reassignment / DHCP collisions). | 1d, `DISCARD_UNCHANGED_HEARTBEAT 7d` | Per `device.c`. |
| `CONF_IP_STR` / `CONF_GATEWAY_IP_STR` / `CONF_SUBNET` | IP / gateway / mask | once at discovery | confirms the LLD-time `{#CAM.ADDRESS}` matches reality |
| `0x0C38` | `CONF_ALARM_OVERVIEW` | active alarm state list — entry ID, flags, source/type, alarm name (UTF-16) | `{$MS.CAM.BOSCH.ALARM.INTERVAL}` default 1m | The richest non-Milestone alarm surface — motion, IVA rule trips, tamper, IR illuminator state, audio (when audio is enabled), input-contact closed. EdgeX uses this exact opcode. |
| `0x0B4A` | `CONF_IVA_COUNTER_VALUES` | IVA (Intelligent Video Analytics) counter ID + type + name + 32-bit value | 5m | If IVA is licensed and a rule has counters (line crossings, occupancy), this is how we trend them. **FLEXIDOME 5100i ships Essential Video Analytics (EVA) baked in** — counter readout works without an extra licence. |
| `CONF_VIDEO_INPUT_FORMAT` (`0x0504`) / `CONF_VIDEO_INPUT_FORMAT_EX` (`0x0B10`) | video-input format (resolution + framerate as seen by encoder) | 5m | Drops to 0 / unknown when the imager is asleep or the sensor has faulted. Useful "is the camera actually capturing?" — distinct from "is the network up?". |
| `CONF_MPEG4_CURRENT_PARAMS` (`0x0600`) | current encoder params (bitrate, GOP, profile) per encoder index `num=1..3` | 5m | Reveals stream config drift (somebody changed the bitrate in the browser UI). |
| `CONF_RCP_CONNECTIONS_ALIVE` (`0xFFC2`) | connections-alive ping | 1m | Cheapest "is RCP+ responding at all" probe. Failure here means *RCP+ down*, separately from ICMP-down. |
| `CONF_PASSWORD_SETTINGS` (`0x028B`) | password configuration status (does the `service` account still have the default password?) | 1d | **Security guardrail.** Per G18 ethos: if this reports a default password is still set, raise an INFORMATION trigger. Don't *write* the password from Zabbix (operator action), just surface it. |
| `CONF_RCP_CLIENT_REGISTRATION` (`0xFF00`) + `…UNREGISTER` (`0xFF01`) | session register/unregister | — | RCP+ has a session model; for stateless polling we don't register — fire each command as a one-shot with digest auth. Same pattern Milestone uses for OAuth2 (G25). |

### What RCP+ is **not** going to give us

- **Internal temperature.** The 5100i datasheet doesn't surface a board-temp sensor; the *outdoor* models do. Per the FLEXIDOME 5100i indoor user manual the **System Overview** page doesn't include a temperature reading. Don't ship an item we can't populate.
- **Storage / SD card health.** The 5100i is fixed-dome and **has no edge storage slot** by default (the panoramic 5100i panoramic IR does have an SD slot; the indoor 5100i IR does *not*). Skip the SD-health item for this model. Make it a vendor-template macro toggle (`{$MS.CAM.BOSCH.HAS_SDCARD}` default `0`).
- **IR illuminator state on demand.** The IR LED state is *reflected* in the alarm-overview list when it transitions, but there is no clean "give me current IR illuminator current draw" opcode. Treat IR-on/off as a state derived from the alarm bus, not a poll.

### Auth + scale

- Digest. 5100i blocks Basic by default in current FW (`Secure-by-default` flag) — Basic only works if explicitly re-enabled.
- One RCP+ poll cycle = one HTTPS round trip + digest challenge (so 2 RTT cold, 1 RTT with `Authorization` cached on a keepalive). Budget **6 RCP+ items / camera @ 5m + alarm @ 1m** = ~7 polls/min — well under any reasonable Zabbix proxy load even at 2500 cameras per G12.
- The camera will rate-limit unauthenticated probe storms. Keep the same `{$MS.CAM.BOSCH.HTTP.TIMEOUT}` macro pattern we'd use for Axis.

---

## 2 · ONVIF — fallback / cross-vendor parity channel

5100i is **ONVIF Profile S + T + G + M** conformant. For us the high-value calls are on `device_service` (no media stream involved):

| ONVIF call | Returns | Cadence | Notes |
|---|---|---|---|
| `GetDeviceInformation` | Manufacturer, Model, FirmwareVersion, SerialNumber, HardwareId | 1h heartbeat | Plain SOAP, single round-trip. **Best vendor-agnostic firmware/serial pull** — the same call works on Axis/Hanwha/Hikvision/Bosch and the parsing is identical. Per G11, ships in the *base* per-camera template, not the vendor sub-template, so unknown-vendor cameras still get firmware/serial. |
| `GetSystemDateAndTime` | NTP status + manual / DST flag + current time | 5m | Detects NTP drift. Camera-clock drift > 2s breaks recording timeline alignment in Milestone. Trigger candidate. |
| `GetCapabilities` | which services the camera exposes (Imaging, PTZ, Events, Analytics, …) | once at discovery | feature-detect alongside RCP+ `CONF_CAPABILITY_LIST`. |
| `GetServices` | versioned URLs of each service | once at discovery | needed if the camera puts services on non-default paths. |
| `GetSystemDiagnostics` | arbitrary device-supplied diagnostics blob | 15m | Optional in ONVIF; Bosch returns a structured XML of subsystem statuses on most models. Worth probing in M0 pilot to see if it returns anything useful on the 5100i. |
| `GetEventProperties` + Pull-point subscription | live event stream (motion / tamper / IVA rule) | event-driven | An alternative path to the RCP+ `CONF_ALARM_OVERVIEW` pull. Polling RCP+ is simpler in a Zabbix template; pull-point is better for *push* into a unified module. **Recommendation: poll RCP+ from the template, leave ONVIF events for the future `tcs_dashboard` action layer if it wants real-time.** |
| `GetVideoSourceConfigurations` + `GetCompatibleVideoEncoderConfigurations` | encoder profile inventory | 1d | drift detection same as RCP+ `MPEG4_CURRENT_PARAMS`, less detailed. |
| `GetMetadataConfiguration` | analytics stream config | 1d | confirms whether IVA metadata is being streamed at all. |

Auth: **WS-UsernameToken** (digest of `nonce + created + password`). The camera supports the same user account as RCP+ (the `service` account or a dedicated monitoring user — we should create one with `Live` role only).

### Don't poll over ONVIF

- `GetSnapshotUri` + GET — fine for the *Camera Wall* opt-in thumbnail (per G21 + `{$MS.WALL.THUMB.INTERVAL}` default 30s), **not** for a fleet poll.
- Anything in `media_service` that triggers an RTSP setup (e.g. `GetStreamUri` followed by an RTSP PLAY). Per G20: we don't probe RTSP from Zabbix.

---

## 3 · SNMP — cheapest poll for the plain scalars

SNMP v1 + MIB-II + a private extension; v3 basic on later FW (the 5100i ships well past the SNMPv3 cutoff). The 5100i datasheet explicitly lists **"SNMP V1, V3, MIB-II"**.

| MIB-II OID | What | Cadence | Why bother (RCP+ has equivalents) |
|---|---|---|---|
| `1.3.6.1.2.1.1.3.0` (`sysUpTime`) | seconds since cold start × 100 | 5m | Zero-cost reboot detector. **Easier to pull SNMP than RCP+ for this one value.** |
| `1.3.6.1.2.1.1.1.0` (`sysDescr`) | vendor/model/FW string | once / discovery | Per G11, this is the **sysObjectID match** the host_prototype keys off to conditionally link the Bosch sub-template. |
| `1.3.6.1.2.1.1.2.0` (`sysObjectID`) | vendor private OID prefix | once | The vendor branch for Bosch lives under `.1.3.6.1.4.1.3967` (Bosch's IANA enterprise number). |
| `1.3.6.1.2.1.2.2.1.10.X` / `.16.X` (`ifInOctets`/`ifOutOctets`) | network counters | 5m | Bandwidth utilisation — direct read, no encoder-side cost. |
| `1.3.6.1.2.1.2.2.1.7.X` / `.8.X` (`ifAdminStatus`/`ifOperStatus`) | link up/down | 1m | Faster reboot detection than sysUptime (link drops first). |
| `1.3.6.1.2.1.4.20.1.X` | IP address table | once | confirms the LLD-time IP matches the running IP. |

### Bosch private MIB extension

Per Bosch knowledge base + community thread: **the camera-specific `.mib` is shipped inside the firmware `.zip`** on the camera's product download page. We ship the latest *as committed to this repo* under `templates/mibs/BOSCH-VIDEOJET-MIB.txt` (or whatever the firmware names it) and the per-camera Bosch sub-template references symbolic OIDs (Zabbix needs `snmpwalk -m +BOSCH-…` style support — confirm in M0 pilot whether our proxy's snmpd has `mibs all` set).

**Action item before the Bosch sub-template ships:** pull FW 7.82.0028 (the current FLEXIDOME 5100i IR firmware per the public release letter) `.zip`, extract the MIB, walk it on the pilot 5100i, and tabulate. The MIB-table dump is what feeds the v1.1 of this audit.

### SNMP traps

Bosch can emit traps on: motion, tamper, IR illuminator state change, video signal loss, alarm input, system reboot, configuration change. **Useful for push monitoring, not for our pull-based fleet template** — we'd need a snmptrapd → Zabbix-trapper item per trap type, which is a separate deployment concern (proxy needs trap collector running). **Recommendation: defer to a post-v1.1 enhancement.** Polling alarm-overview every 1m via RCP+ covers the same surface with one less infrastructure dep.

---

## 4 · ICMP — already covered

The three existing per-LLD prototypes (`icmpping`, `icmppingsec`, `icmppingloss` — §A.2a of the inventory) move to the per-camera host in M1 and continue to do the same job. No Bosch-specific behaviour.

---

## 5 · Recommended starter set for the Bosch sub-template

Once we test against the FLEXIDOME 5100i IR pilot box, the v1 Bosch vendor sub-template ships these items, all tagged `vendor=bosch`, `cam_id={#CAM.ID}`, `src=zbx` (per §C7/G27):

| Key | Source | Cadence | Trigger |
|---|---|---|---|
| `bosch.cam.firmware.version[{#CAM.ID}]` | RCP+ `0x0026` *or* ONVIF `GetDeviceInformation.FirmwareVersion` | 1h, `DISCARD_UNCHANGED_HEARTBEAT 7d` | none (informational) |
| `bosch.cam.hardware.id[{#CAM.ID}]` | RCP+ `CONF_HARDWARE_VERSION` *or* ONVIF `GetDeviceInformation.HardwareId` | 1h, heartbeat 7d | none |
| `bosch.cam.serial[{#CAM.ID}]` | ONVIF `GetDeviceInformation.SerialNumber` | once | none |
| `bosch.cam.uptime[{#CAM.ID}]` | SNMP `sysUpTime.0` | 5m | `last() < lastclock()-300` → reboot (INFORMATION) |
| `bosch.cam.link.status[{#CAM.ID}]` | SNMP `ifOperStatus.1` | 1m | `=2` (down) → WARNING |
| `bosch.cam.net.in[{#CAM.ID}]` / `.out[{#CAM.ID}]` | SNMP `ifInOctets`/`ifOutOctets` | 5m, store as deltaps | rate < 1kbps for 15m on an enabled camera → INFORMATION |
| `bosch.cam.rcp.alive[{#CAM.ID}]` | RCP+ `0xFFC2` | 1m | `=0` for 3 samples → WARNING (camera is on the network but RCP+/web stack is hung — distinct signal from ICMP) |
| `bosch.cam.video.input.format[{#CAM.ID}]` | RCP+ `0x0504` | 5m | empty / "no signal" → HIGH (imager failure) |
| `bosch.cam.encoder.bitrate.kbps[{#CAM.ID}]` | RCP+ `0x0600` (per encoder `num=1..3`) | 5m | `< 50` while enabled → AVERAGE (encoder stalled) |
| `bosch.cam.alarm.overview[{#CAM.ID}]` | RCP+ `0x0C38` raw JSON | 1m, master | dependents: `motion`, `tamper`, `video_loss`, `iva_rule.<n>`, `input.<n>` |
| `bosch.cam.ntp.synced[{#CAM.ID}]` | ONVIF `GetSystemDateAndTime` | 5m | drift `> 2s` for 3 samples → AVERAGE |
| `bosch.cam.password.default[{#CAM.ID}]` | RCP+ `0x028B` | 1d | `=true` → INFORMATION (security guardrail per G18 ethos) |

**Not in v1** (defer): IVA counters (`0x0B4A` — adds complexity, only useful when EVA rules exist), SNMP traps, snapshot fetch (handled by Camera Wall opt-in per G21).

---

## 6 · Concrete next steps for the pilot box

When the pilot site (per M0 task 2) is online with a FLEXIDOME indoor 5100i IR reachable from the Zabbix proxy:

1. **Confirm digest auth and probe basic capability.** From the proxy:
   ```
   curl --digest -u svc_zbx:'…' \
     'https://{cam}/rcp.xml?command=0xFF10&type=P_OCTET&direction=READ&num=1&payload='
   ```
   If this returns the capability list, RCP+ is on. If `401`, check that the `service` or dedicated monitoring user is configured.
2. **Pull `GetDeviceInformation` over ONVIF** with a one-liner (`curl` + a SOAP envelope template). Compare manufacturer/model/firmware/serial against what Milestone's `/hardware/{id}` reports — establishes whether we trust ONVIF or Milestone for those four fields.
3. **`snmpwalk -v2c -c public` against the camera** to confirm `sysObjectID` lives under `.1.3.6.1.4.1.3967` (Bosch IANA). The match regex in the host_prototype's vendor-template gating is built from this.
4. **Extract the MIB** from the FW 7.82.0028 `.zip` on the Bosch product page; commit to `templates/mibs/`. (`.mib` file ships inside; pull the `BOSCH-VIDEOJET-MIB` or whichever name applies to the 5100i platform.)
5. **`snmpwalk` against the private branch** with `-m +BOSCH-VIDEOJET-MIB` to dump every leaf — gives us the actual table of vendor-specific OIDs we can monitor. Append to this document as `§7 Private MIB walk — pilot 5100i FW 7.82.0028`.
6. **One sample `0x0C38` alarm-overview poll while triggering a motion event** in the camera browser UI. Confirms the alarm bus is live and we can parse the entry-ID / flag / name shape (per the EdgeX implementation reference). Sample payload goes into this doc as `§8 Alarm-overview sample`.

Items §7 and §8 turn this from a desk-audit into a validated audit — they unblock the Bosch sub-template task in M1.

---

## 7 · Open questions (to flag to vendor / community)

1. **Does the FLEXIDOME *indoor* 5100i IR expose any internal temperature OID at all?** Outdoor variants do; indoor variants traditionally don't ship a thermistor. Confirm against the pilot box's private-MIB walk.
2. **Encoder load / CPU %.** The CPP7.3 platform tends to expose encoder load in the `System Overview` page — locate the corresponding RCP+ opcode in the MIB. If absent, skip; if present, it's a high-value metric.
3. **Whether ONVIF `GetSystemDiagnostics` returns anything more structured than an empty blob on Bosch.** This is the only "free" cross-vendor diagnostics call — worth a probe.
4. **TLS cert validity window.** Camera-served HTTPS cert expiry is detectable from the proxy side (`net.tcp.tls.cert.notafter[…]` in Zabbix 7+). Stays at the base per-camera template, not vendor-specific.

---

## Sources

- [Bosch Video IP — RCP+ over CGI (official PDF)](https://media.boschsecurity.com/fs/media/pb/media/partners_1/integration_tools_1/developer/rcpplus-over-cgi.pdf)
- [`hawell/rcpplus` — C implementation of Bosch RCP+ v3 (GitHub)](https://github.com/hawell/rcpplus)
- [`edgexfoundry/device-camera-go` — Bosch RCP integration (`internal/pkg/bosch/rcp.go`)](https://github.com/edgexfoundry/device-camera-go/blob/main/internal/pkg/bosch/rcp.go)
- [Bosch community: "What should you know about SNMP and Bosch cameras"](https://community.boschsecurity.com/t5/Security-Video/What-should-you-know-about-SNMP-and-Bosch-cameras-SNMP-support/ta-p/27034)
- [Bosch community: "How to send SNMP v3 requests to Bosch cameras"](https://community.boschsecurity.com/t5/Security-Video/How-to-send-SNMP-v3-requests-to-Bosch-cameras/ta-p/27600)
- [FLEXIDOME indoor 5100i / 5100i IR user manual (PDF)](https://www.networkwebcams.co.uk/content/pdf/bosch/bosch-5100i-user-manual.pdf)
- [Bosch CPP7.3 FW 7.82.0028 release letter (FLEXIDOME 5000i family)](https://downloadstore.boschsecurity.com/FILES/Bosch_Releaseletter_CPP7.3_FW_7.82.0028_FD5000i.pdf)
- [Bosch IP Video and Data Security Guidebook](https://www.anixter.com/content/dam/Suppliers/Bosch/Literature/Data_Security_Guideb_Special_enUS_9007221590612491.pdf)
- [ONVIF Application Programmer's Guide v1.0](https://www.onvif.org/wp-content/uploads/2016/12/ONVIF_WG-APG-Application_Programmers_Guide-1.pdf)
