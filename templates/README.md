# ZabbixSwitchPortWidgets — Template Reference Documentation

**Zabbix 7.4** | [github.com/jerahl/ZabbixSwitchPortWidgets](https://github.com/jerahl/ZabbixSwitchPortWidgets)

---

## Overview

This document describes three custom Zabbix 7.4 templates included in the ZabbixSwitchPortWidgets repository. Each template is designed to integrate a specific product or platform into Zabbix monitoring with minimal external dependencies and efficient use of dependent items to reduce API/SNMP call volume.

| Template | Type | Target | Group |
|---|---|---|---|
| Milestone XProtect by HTTP | HTTP / Script + External | Milestone VMS API Gateway | Templates/Video |
| Extreme EXOS by SNMP | SNMP | Extreme Networks EXOS switches | Templates/Network devices |
| Extreme XIQ APs by API | HTTP / Script | ExtremeCloud IQ REST API | Templates/Network devices |

---

## Template: Milestone XProtect by HTTP

**File:** `Milestone by HTTP API.yaml` | **Zabbix version:** 7.4 | **Group:** Templates/Video

### Description

Monitors a Milestone XProtect VMS site via the MIP VMS REST API (`/api/rest/v1`). Uses Script items to combine OAuth2 token acquisition and REST calls in a single JavaScript invocation, avoiding the need to store tokens in user macros. Recording servers and cameras are discovered automatically via LLD.

Inspired by the `lestoilfante/zabbix-integrations` MilestoneSys template (Zabbix 6.0), ported and extended for Zabbix 7.4 with a template dashboard and camera-level LLD.

### Requirements

- XProtect 2022 R1 or later with API Gateway installed and reachable
- An XProtect Basic user whose role grants read access to Management Server, Recording Server, and device configuration
- Network reachability from the Zabbix server/proxy to the API Gateway (TCP 443 for HTTPS, TCP 80 for HTTP)
- External helper scripts deployed on the Zabbix server/proxy (see [External Scripts](#external-scripts))

### Host Macros

| Macro | Default | Description |
|---|---|---|
| `{$MILESTONE.HOST}` | *(required)* | FQDN or IP of the API Gateway |
| `{$MILESTONE.SCHEME}` | `https` | Protocol: `http` or `https` |
| `{$MILESTONE.USER}` | *(required)* | XProtect Basic user name |
| `{$MILESTONE.PASSWORD}` | *(required)* | XProtect Basic user password |
| `{$MILESTONE.CLIENT_ID}` | `GrantValidatorClient` | OAuth2 client ID |
| `{$MILESTONE.ESS.DELAY}` | `1d` | Poll interval for the ESS snapshot item |
| `{$MILESTONE.ESS.STATEGROUP.COMMUNICATION}` | *(required)* | GUID of the communication state group |
| `{$MILESTONE.ESS.STATEGROUP.RECORDING}` | *(required)* | GUID of the recording state group |
| `{$MILESTONE.ESS.TYPE.COMMUNICATION_OK}` | *(required)* | GUID of the "OK" communication state type |
| `{$MILESTONE.ESS.TYPE.RECORDING_STARTED}` | *(required)* | GUID of the "Recording Started" state type |
| `{$MILESTONE.CAM.NAME.MATCHES}` | `.*` | LLD filter: camera names to include (regex) |
| `{$MILESTONE.CAM.NAME.NOT_MATCHES}` | `CHANGE_IF_NEEDED` | LLD filter: camera names to exclude (regex) |
| `{$MILESTONE.CAM.PING.INTERVAL}` | `1m` | Polling interval for camera ICMP and status items |

### External Scripts

The following scripts must be deployed to `/usr/lib/zabbix/externalscripts/` on the Zabbix server or proxy. Two scripts handle cameras via the REST API; two handle the Event & States Service (ESS) over WebSocket.

| Script | Role |
|---|---|
| `milestone_cameras_state.py` | Python script that calls the Milestone REST API, builds a per-camera JSON snapshot, and writes it to a temp file. |
| `milestone_cameras_refresh.sh` | Shell wrapper called by cron every 15 minutes to invoke `milestone_cameras_state.py`. |
| `milestone_cameras_read.sh` | Shell script called directly by the Zabbix External item to read the cached snapshot file. |
| `milestone_ess_state.py` | Python script that opens a WebSocket to the ESS, calls `get_state`, and writes a per-camera state snapshot. |
| `milestone_ess_refresh.sh` | Shell wrapper called by cron (default: daily at 03:15) to invoke `milestone_ess_state.py`. |
| `milestone_ess_read.sh` | Shell script called directly by the Zabbix External item to read the cached ESS snapshot. |

#### Required Cron Entries (as the `zabbix` user)

```bash
# Camera + hardware metadata refresh — every 15 minutes
*/15 * * * * /usr/lib/zabbix/externalscripts/milestone_cameras_refresh.sh \
    {$MILESTONE.HOST} {$MILESTONE.USER} 'PASSWORD' --scheme https

# ESS state snapshot — daily at 03:15 (takes 1-2 min for 2500+ cameras)
15 3 * * * /usr/lib/zabbix/externalscripts/milestone_ess_refresh.sh \
    {$MILESTONE.HOST} {$MILESTONE.USER} 'PASSWORD' --scheme https
```

### Master Items (Site & Server Level)

| Item Key | Type | Interval | Description |
|---|---|---|---|
| `milestone.sites.get` | Script | 5m | Authenticates via OAuth2 (ROPC flow) and fetches `/api/rest/v1/sites`. Feeds all site-level dependent items. |
| `milestone.rs.getall` | Script | 2m | Authenticates and fetches `/api/rest/v1/recordingServers`. Re-keyed by RS ID in preprocessing for O(1) lookups. Feeds the RS LLD rule. |
| `milestone.license.get` | Script | 1h | Fetches `/api/rest/v1/licenseOverviewAll`. History disabled; used for dashboard display. |
| `milestone_cameras_read.sh[3600]` | External | 5m | Reads the JSON camera snapshot written by the cron job. Already shaped for O(1) lookup by camera GUID — no preprocessing needed. |
| `milestone_ess_read.sh[]` | External | `{$MILESTONE.ESS.DELAY}` | Reads the ESS state snapshot. Dependent items parse individual camera states by GUID. |
| `net.tcp.service[tcp,{$MILESTONE.HOST},443]` | Simple | 30s | TCP reachability check on port 443 for the API Gateway. |

### Site-Level Dependent Items

All items below depend on `milestone.sites.get` and extract fields from `$.array[0]`.

| Item Key | Field Extracted | Description |
|---|---|---|
| `milestone.site.name` | `$.array[0].displayName` | Display name of the XProtect site. |
| `milestone.site.version` | `$.array[0].version` | Management Server software version. |
| `milestone.site.physicalmemory` | `$.array[0].physicalMemory` | Physical RAM of the Management Server (bytes). |
| `milestone.site.lasthandshake` | `$.array[0].lastStatusHandshake` | Timestamp of the last Management Server heartbeat. |
| `milestone.site.handshake.age` | `$.array[0].lastStatusHandshake` | Seconds elapsed since the last handshake (JavaScript preprocessing). |

### LLD Rule: Camera Discovery

**Key:** `milestone.cameras.discovery` | **Type:** Dependent on `milestone_cameras_read.sh[3600]` | **Lifetime:** 1 day

Filters cameras by `{$MILESTONE.CAM.NAME.MATCHES}` and `{$MILESTONE.CAM.NAME.NOT_MATCHES}`.

#### LLD Macros

| Macro | JSON Path | Description |
|---|---|---|
| `{#CAM.ID}` | `$.id` | Camera GUID |
| `{#CAM.NAME}` | `$.displayName` | Camera display name |
| `{#CAM.ADDRESS}` | `$.address` | IP/hostname from parent hardware record |
| `{#CAM.MAC}` | `$.mac` | MAC address of parent hardware (normalised `AA:BB:CC:DD:EE:FF`) |
| `{#CAM.ENABLED}` | `$.enabled` | Enabled flag from Milestone |

#### Per-Camera Item Prototypes

| Item Prototype Key | Type | Description |
|---|---|---|
| `milestone.cam.raw[{#CAM.ID}]` | Dependent (master: cameras_read) | Extracts this camera's record from the batch master JSON by GUID. All other camera items depend on this. |
| `milestone.cam.enabled[{#CAM.ID}]` | Dependent | Camera enabled state (`true`/`false` string). Triggers if `false`. |
| `milestone.cam.hwname[{#CAM.ID}]` | Dependent | Hardware display name from Milestone. |
| `milestone.cam.hwmodel[{#CAM.ID}]` | Dependent | Hardware model string. |
| `milestone.cam.mac[{#CAM.ID}]` | Dependent | MAC address of parent hardware. |
| `milestone.cam.address[{#CAM.ID}]` | Dependent | IP address used for ICMP ping items. |
| `milestone.cam.channel[{#CAM.ID}]` | Dependent | Channel number (relevant for multi-channel encoders). |
| `milestone.cam.rs[{#CAM.ID}]` | Dependent | Parent hardware GUID (`relations.parent.id`). Key retained as `milestone.cam.rs` for backward compatibility. |
| `milestone.cam.lastmodified[{#CAM.ID}]` | Dependent | Last-modified timestamp from Milestone. |
| `milestone.cam.ess.raw[{#CAM.ID}]` | Dependent (master: ess_read) | Full ESS state record for the camera including `states[]` and `by_group{}` map. |
| `milestone.cam.ess.comm.type[{#CAM.ID}]` | Dependent | State type GUID for the communication state group. Compare against `{$MILESTONE.ESS.TYPE.COMMUNICATION_OK}`. |
| `milestone.cam.ess.comm.time[{#CAM.ID}]` | Dependent | Timestamp of the last communication state change. |
| `milestone.cam.ess.rec.type[{#CAM.ID}]` | Dependent | State type GUID for the recording state group. Compare against `{$MILESTONE.ESS.TYPE.RECORDING_STARTED}`. |
| `milestone.cam.status[{#CAM.ID}]` | Calculated | Combined health score: `-1`=disabled, `0`=OK, `1`=comm fault, `2`=ICMP down, `3`=both faults. Used by Honeycomb dashboard widget. |
| `milestone.cam.alarm[{#CAM.ID}]` | Calculated | Same formula as `status` but discards `0` and `-1`; only fault states are stored. Drives dashboard alerting cells. |
| `icmpping[{#CAM.ADDRESS}]` | Simple | ICMP reachability to the camera's network address. |
| `icmppingloss[{#CAM.ADDRESS}]` | Simple | ICMP packet loss percentage. |
| `icmppingsec[{#CAM.ADDRESS}]` | Simple | Average ICMP round-trip time. |

#### Camera Trigger Prototypes

> **Note:** Most trigger prototypes are **DISABLED by default**. Use the template dashboard Honeycomb widget to identify cameras in fault states rather than enabling individual triggers at scale.

| Trigger | Severity | Status | Condition |
|---|---|---|---|
| `Cam [{#CAM.NAME}]: disabled in XProtect` | Warning | **Enabled** | `milestone.cam.enabled = "false"` |
| `Cam [{#CAM.NAME}]: unreachable by ICMP` | High | Disabled | 3 consecutive ICMP failures on an enabled camera |
| `Cam [{#CAM.NAME}]: communication not OK (ESS)` | High | Disabled | ESS comm type GUID ≠ `{$MILESTONE.ESS.TYPE.COMMUNICATION_OK}` and camera is enabled |
| `Cam [{#CAM.NAME}]: not recording (ESS)` | Average | Disabled | ESS recording type GUID ≠ `{$MILESTONE.ESS.TYPE.RECORDING_STARTED}` and camera is enabled |
| `Cam [{#CAM.NAME}]: no data for 15m` | Average | Disabled | `nodata()` on `cam.raw` for 15 minutes |

### LLD Rule: Recording Server Discovery

**Key:** `milestone.recordingservers.discovery` | **Type:** Dependent on `milestone.rs.getall` | **Lifetime:** 2 days

#### Per-RS Item Prototypes

| Item Prototype Key | Description |
|---|---|
| `milestone.rs.raw[{#RS.ID}]` | Extracts this RS's record from the batch master by GUID. All other RS items depend on this. |
| `milestone.rs.enabled[{#RS.ID}]` | Enabled/disabled state. Triggers if `false`. |
| `milestone.rs.hostname[{#RS.ID}]` | Hostname of the recording server. |
| `milestone.rs.version[{#RS.ID}]` | Recording Server software version. |
| `milestone.rs.lasthandshake[{#RS.ID}]` | Last check-in timestamp with Management Server. |
| `milestone.rs.handshake.age[{#RS.ID}]` | Seconds since last handshake. Triggers at >300s (5 minutes). |

#### RS Trigger Prototypes

| Trigger | Severity | Condition |
|---|---|---|
| `RS [{#RS.NAME}]: disabled in XProtect` | Warning | `milestone.rs.enabled = "false"` |
| `RS [{#RS.NAME}]: handshake stale (>5m)` | High | `milestone.rs.handshake.age > 300` seconds |
| `RS [{#RS.NAME}]: no API data for 15m` | Average | `nodata()` on `rs.raw` for 15 minutes |

### Site-Level Triggers

| Trigger | Severity | Condition |
|---|---|---|
| `Milestone [{HOST.NAME}]: ESS snapshot stale or missing` | Warning | ESS item value matches regexp for `stale` or `no_snapshot` error |

### Value Maps

| Value Map | Mappings |
|---|---|
| Camera status | `-1`=Disabled, `0`=OK, `1`=Communication fault, `2`=ICMP unreachable, `3`=Both faults |
| Bool string | `true`=True, `false`=False |
| Service state | `0`=Down, `1`=Up |

---

## Template: Extreme EXOS by SNMP

**File:** `Extreme EXOS by SNMP w POE.yaml` | **Zabbix version:** 7.4 | **Group:** Templates/Network devices

Vendor attribution: Zabbix 7.0-0 (generated from official Zabbix template tool, extended with custom items)

### Description

SNMP-based template for Extreme Networks switches running EXOS. Covers system health (CPU, memory, temperature, fans), network interface statistics, FDB/MAC table polling, and PoE port monitoring via LLD. Designed to feed the SwitchPortWidgets custom dashboard widgets.

**MIBs used:** EXTREME-SYSTEM-MIB, EXTREME-SOFTWARE-MONITOR-MIB, IF-MIB, EtherLike-MIB, HOST-RESOURCES-MIB, SNMPv2-MIB, ENTITY-MIB

### Host Macros

| Macro | Default | Description |
|---|---|---|
| `{$SNMP.TIMEOUT}` | `5m` | Duration without SNMP data before triggering "No SNMP data collection" |
| `{$ICMP_LOSS_WARN}` | `20` | ICMP packet loss % warning threshold |
| `{$ICMP_RESPONSE_TIME_WARN}` | `0.15` | ICMP response time (seconds) warning threshold |
| `{$CPU.UTIL.CRIT}` | `90` | CPU utilization % critical threshold |
| `{$MEMORY.UTIL.MAX}` | `90` | Memory utilization % average-severity threshold |
| `{$TEMP_WARN}` | `55` | Temperature warning threshold (°C) |
| `{$TEMP_CRIT}` | `65` | Temperature critical threshold (°C) |
| `{$TEMP_CRIT_LOW}` | `5` | Low temperature critical threshold (°C) |
| `{$TEMP_CRIT_STATUS}` | `1` | `extremeOverTemperatureAlarm` value for critical status |
| `{$FAN_CRIT_STATUS}` | `2` | `extremeFanOperational` value indicating fan failure |
| `{$NET.IF.IFNAME.MATCHES}` | `^.*$` | Interface name LLD include filter |
| `{$NET.IF.IFNAME.NOT_MATCHES}` | `^Software Loopback.*` | Interface name LLD exclude filter |
| `{$NET.IF.IFTYPE.MATCHES}` | `^.*$` | Interface type include filter |
| `{$NET.IF.IFTYPE.NOT_MATCHES}` | `^softwareLoopback$` | Interface type exclude filter |
| `{$NET.IF.IFADMINSTATUS.MATCHES}` | `^.*$` | Admin status include filter |
| `{$NET.IF.IFADMINSTATUS.NOT_MATCHES}` | `^2$` | Exclude admin-down interfaces from LLD |
| `{$NET.IF.IFDESCR.MATCHES}` | `.*` | Interface description include filter |
| `{$NET.IF.IFDESCR.NOT_MATCHES}` | `CHANGE_IF_NEEDED` | Interface description exclude filter |
| `{$NET.IF.IFALIAS.MATCHES}` | `.*` | Interface alias include filter |
| `{$NET.IF.IFALIAS.NOT_MATCHES}` | `CHANGE_IF_NEEDED` | Interface alias exclude filter |
| `{$NET.IF.IFOPERSTATUS.MATCHES}` | `^.*$` | Operational status include filter |
| `{$NET.IF.IFOPERSTATUS.NOT_MATCHES}` | `^6$` | Exclude `notPresent` interfaces |
| `{$IFCONTROL}` | `1` | Set to `0` via context macro on an interface to suppress its link-down trigger |

### System-Level Items

| Item Key | OID / Source | Interval | Description |
|---|---|---|---|
| `icmpping` | Simple check | 1m | ICMP reachability. High trigger after 3 consecutive failures. |
| `icmppingloss` | Simple check | 1m | ICMP packet loss %. Warning trigger above `{$ICMP_LOSS_WARN}`. |
| `icmppingsec` | Simple check | 1m | ICMP round-trip time. Warning trigger above `{$ICMP_RESPONSE_TIME_WARN}`. |
| `system.name` | `1.3.6.1.2.1.1.5.0` | 15m | System name (sysName). Info trigger on change. Populated to inventory NAME. |
| `system.descr[sysDescr.0]` | `1.3.6.1.2.1.1.1.0` | 15m | Full system description text. |
| `system.objectid[sysObjectID.0]` | `1.3.6.1.2.1.1.2.0` | 15m | SNMP enterprise OID. |
| `system.contact[sysContact.0]` | `1.3.6.1.2.1.1.4.0` | 15m | Contact details. Populated to inventory CONTACT. |
| `system.location[sysLocation.0]` | `1.3.6.1.2.1.1.6.0` | 15m | Physical location. Populated to inventory LOCATION. |
| `system.hw.uptime[hrSystemUptime.0]` | `1.3.6.1.2.1.25.1.1.0` | 30s | Hardware uptime (HOST-RESOURCES-MIB). Multiplied by 0.01. |
| `system.net.uptime[sysUpTime.0]` | `1.3.6.1.2.1.1.3.0` | 30s | SNMP agent uptime in hundredths of a second, multiplied by 0.01. |
| `system.hw.model` | `1.3.6.1.2.1.47.1.1.1.1.2.1` | 1h | Hardware model name (ENTITY-MIB). Populated to inventory MODEL. |
| `system.hw.serialnumber` | `1.3.6.1.2.1.47.1.1.1.1.11.1` | 1h | Serial number. Info trigger on change. Populated to inventory SERIALNO_A. |
| `system.hw.firmware` | `1.3.6.1.2.1.47.1.1.1.1.9.1` | 1h | Firmware version. Info trigger on change. |
| `system.hw.version` | `1.3.6.1.2.1.47.1.1.1.1.9.1` | 1h | Hardware revision (ENTITY-MIB). |
| `system.hw.stacking` | `1.3.6.1.4.1.1916.1.33.1.0` | — | SNMP GET to check whether the unit is part of a stack. |
| `system.sw.os[extremePrimarySoftwareRev.0]` | `1.3.6.1.4.1.1916.1.1.1.13.0` | 1h | EXOS primary software version. Info trigger on change. Populated to inventory OS. |
| `system.cpu.util[extremeCpuMonitorTotalUtilization.0]` | `1.3.6.1.4.1.1916.1.32.1.2.0` | — | Total CPU utilization %. Warning trigger above `{$CPU.UTIL.CRIT}`. |
| `sensor.temp.value[extremeCurrentTemperature.0]` | `1.3.6.1.4.1.1916.1.1.1.8.0` | 3m | Device temperature (°C). Warning and critical triggers with hysteresis recovery. |
| `sensor.temp.status[extremeOverTemperatureAlarm.0]` | `1.3.6.1.4.1.1916.1.1.1.7.0` | 3m | Temperature alarm flag from EXTREME-SYSTEM-MIB. Used in critical trigger dependency. |
| `snmptrap.fallback` | SNMP trap | — | Catch-all for unmatched SNMP traps (log type). |
| `zabbix[host,snmp,available]` | Internal | — | SNMP interface availability. Warning trigger after `{$SNMP.TIMEOUT}` with no data. |
| `fdb.raw` | SNMP walk (OIDs `.17.4.3.1.2` + `.17.1.4.1.2`) | 1h | Raw FDB walk combining `dot1dTpFdbTable` port mappings and `dot1dBasePortIfIndex`. Feeds the `port.mac.list` dependent prototype. Required by SwitchPortWidgets. |

### LLD Rule: FAN Discovery

**Key:** `fan.discovery` | **OID:** `1.3.6.1.4.1.1916.1.1.1.9.1.1` | **Interval:** 1h

| Prototype Key | OID | Description |
|---|---|---|
| `sensor.fan.speed[extremeFanSpeed.{#SNMPINDEX}]` | `...1916.1.1.1.9.1.4.N` | Fan speed in RPM. |
| `sensor.fan.status[extremeFanOperational.{#SNMPINDEX}]` | `...1916.1.1.1.9.1.2.N` | Operational status. Average trigger when value = `{$FAN_CRIT_STATUS}`. |

### LLD Rule: Memory Discovery

**Key:** `memory.discovery` | **OID:** `1.3.6.1.4.1.1916.1.32.2.2.1.1` | **Interval:** 1h

| Prototype Key | OID | Description |
|---|---|---|
| `vm.memory.total[extremeMemoryMonitorSystemTotal.{#SNMPINDEX}]` | `...1916.1.32.2.2.1.2.N` | Total DRAM in bytes (raw KB × 1024). |
| `vm.memory.available[extremeMemoryMonitorSystemFree.{#SNMPINDEX}]` | `...1916.1.32.2.2.1.3.N` | Free memory in bytes (raw KB × 1024). |
| `vm.memory.util[{#SNMPVALUE}]` | Calculated | Memory utilization % = `(total - free) / total * 100`. Average trigger above `{$MEMORY.UTIL.MAX}`. |

### LLD Rule: Network Interface Discovery

**Key:** `net.if.discovery` | **Source:** IF-MIB discovery walk | **Interval:** 1h

Discovers `ifOperStatus`, `ifAdminStatus`, `ifAlias`, `ifName`, `ifDescr`, `ifType`. Filters are applied independently per macro using the host macros in the [Host Macros](#host-macros-1) table above.

| Prototype Key | OID | Interval | Description |
|---|---|---|---|
| `net.if.adminstatus[ifIndex.{#SNMPINDEX}]` | `1.3.6.1.2.1.2.2.1.7.N` | — | Administrative status (1=up, 2=down, 3=testing). |
| `net.if.alias[ifIndex.{#SNMPINDEX}]` | `1.3.6.1.2.1.31.1.1.1.18.N` | — | Interface alias string. History 1d. |
| `net.if.in[ifHCInOctets.{#SNMPINDEX}]` | `1.3.6.1.2.1.31.1.1.1.6.N` | 3m | Inbound bits per second (64-bit counter, change/sec × 8). |
| `net.if.out[ifHCOutOctets.{#SNMPINDEX}]` | `1.3.6.1.2.1.31.1.1.1.10.N` | 3m | Outbound bits per second. |
| `net.if.in.errors[ifInErrors.{#SNMPINDEX}]` | `1.3.6.1.2.1.2.2.1.14.N` | 3m | Inbound error packet rate (change/sec). |
| `net.if.in.discards[ifInDiscards.{#SNMPINDEX}]` | `1.3.6.1.2.1.2.2.1.13.N` | 3m | Inbound discarded packet rate (change/sec). |
| `net.if.out.errors[ifOutErrors.{#SNMPINDEX}]` | `1.3.6.1.2.1.2.2.1.20.N` | 3m | Outbound error packet rate (change/sec). |
| `net.if.out.discards[ifOutDiscards.{#SNMPINDEX}]` | `1.3.6.1.2.1.2.2.1.19.N` | 3m | Outbound discarded packet rate (change/sec). |
| `net.if.speed[ifHighSpeed.{#SNMPINDEX}]` | `1.3.6.1.2.1.31.1.1.1.15.N` | 5m | Interface speed in bps (value × 1,000,000). Triggers on speed decrease. |
| `net.if.status[ifOperStatus.{#SNMPINDEX}]` | `1.3.6.1.2.1.2.2.1.8.N` | — | Operational status. Link-down trigger controllable per-interface via `{$IFCONTROL:"{#IFNAME}"}`. |
| `net.if.type[ifType.{#SNMPINDEX}]` | `1.3.6.1.2.1.2.2.1.3.N` | 1h | Interface type (IANAifType). Used as condition in speed-decrease trigger. |
| `port.mac.list[{#SNMPINDEX}]` | Dependent on `fdb.raw` | — | Comma-separated list of MACs currently learned on this interface, derived via JavaScript preprocessing of the FDB walk. |

#### Interface Trigger Prototypes

| Trigger | Severity | Status | Condition |
|---|---|---|---|
| `Interface {#IFNAME}({#IFALIAS}): Link down` | Average | **Disabled** | `ifOperStatus=2` and `{$IFCONTROL}=1` and status changed. Manual close. Suppress per-interface by setting `{$IFCONTROL:"{#IFNAME}"}=0`. |
| `Interface {#IFNAME}({#IFALIAS}): Ethernet has changed to lower speed` | Info | **Disabled** | Speed decreased on an Ethernet-type interface (types 6,7,11,62,69,117) that is not down. Manual close. Depends on: Link down. |
| `Interface {#IFNAME}({#IFALIAS}): High bandwidth usage` | Warning | Enabled | 15m avg in or out > `{$IF.UTIL.MAX:"{#IFNAME}"}` % of link speed, and speed > 100 Mbps. Recovers at 3% below threshold. Manual close. Depends on: Link down. |
| `Interface {#IFNAME}({#IFALIAS}): High error rate` | Warning | Enabled | Min in or out errors over 5m > `{$IF.ERRORS.WARN:"{#IFNAME}"}`. Recovers at 80% of threshold. Manual close. Depends on: Link down. |

### LLD Rule: EtherLike-MIB Discovery

**Key:** `net.if.duplex.discovery` | **Source:** EtherLike-MIB + IF-MIB discovery walk | **Interval:** 1h

Discovers interfaces where `ifOperStatus=1` (up) and duplex value is `2` (halfDuplex) or `3` (fullDuplex). Used to monitor duplex negotiation.

| Prototype Key | OID | Description |
|---|---|---|
| `net.if.duplex[dot3StatsDuplexStatus.{#SNMPINDEX}]` | `1.3.6.1.2.1.10.7.2.1.19.N` | Current duplex mode per EtherLike-MIB. Value map: EtherLike-MIB::dot3StatsDuplexStatus. Disabled Warning trigger when value = 2 (halfDuplex). |

### LLD Rule: PSU Discovery

**Key:** `psu.discovery` | **OID:** `1.3.6.1.4.1.1916.1.1.1.27.1.1` | **Interval:** 1h

Discovers power supply units from EXTREME-SYSTEM-MIB `extremePowerSupplyTable`.

| Prototype Key | OID | Description |
|---|---|---|
| `sensor.psu.status[extremePowerSupplyStatus.{#SNMPINDEX}]` | `...1916.1.1.1.27.1.2.N` | Power supply operational status. Interval 3m, no trends. Average trigger when value = `{$PSU_CRIT_STATUS}` (default: 3 = presentNotOK). Value map: EXTREME-SYSTEM-MIB::extremePowerSupplyStatus. |

### LLD Rule: Interfaces POE Discovery

**Key:** `snmp.interfaces.poe.discovery` | **OID:** `1.3.6.1.2.1.105.1.1.1.3` | **Interval:** 1h | **Lifetime:** DELETE_IMMEDIATELY

Discovers PoE-capable ports via the standard `POWER-ETHERNET-MIB` (`pethPsePortTable`). Deleted immediately when no longer returned by the walk. Use `{$POE_FILTER_REGEX}` to exclude management ports, uplinks, and high-numbered stack ports.

| Prototype Key | OID | Interval | Description |
|---|---|---|---|
| `snmp.interfaces.poe.dstatus[{#SNMPINDEX}]` | `1.3.6.1.2.1.105.1.1.1.6.N` | — | PoE detection status per `pethPsePortDetectionStatus`. No trends. Value map: POE Status (emoji-labelled). |
| `snmp.interfaces.poe.mpower[{#SNMPINDEX}]` | `1.3.6.1.4.1.1916.1.27.2.1.1.6.N` | 5m | Measured power draw in milliwatts from EXTREME-SYSTEM-MIB `extremePethPortMeasuredPower`. |

### LLD Rule: Stack Discovery

**Key:** `stack.discovery` | **OID:** `1.3.6.1.4.1.1916.1.33.2.1.1` | **Interval:** 1h

Discovers stack members from EXTREME-SYSTEM-MIB `extremeStackingTable`.

| Prototype Key | OID | Description |
|---|---|---|
| `stacking.memeber[{#SNMPVALUE}]` | `...1916.1.33.2.1.3.N` | Stack member operational status. |

### Additional Host Macros

The following macros are present in the template in addition to those listed in the main [Host Macros](#host-macros-1) table:

| Macro | Default | Description |
|---|---|---|
| `{$IF.ERRORS.WARN}` | `2` | Warning threshold for interface error packet rate. Supports interface-name context. |
| `{$IF.UTIL.MAX}` | `90` | Interface utilization % threshold for the high bandwidth trigger. Supports interface-name context. |
| `{$PSU_CRIT_STATUS}` | `3` | `extremePowerSupplyStatus` value indicating a faulty PSU (`presentNotOK`). |
| `{$POE_FILTER_REGEX}` | *(long regex)* | Excludes management ports, uplinks, and stack ports above index 48 from PoE discovery. |
| `{$INTER_FILTER_REGEX}` | *(long regex)* | General interface exclusion regex (tunnels, loopbacks, veth, docker, etc.). |
| `{$RCONFIG.URL}` | `https://rconfig.tusc.k12.al.us` | Base URL of the rConfig instance for PoE-cycle automation. Must be HTTPS. |
| `{$RCONFIG.TOKEN}` | Secret Text | rConfig API token. Stored as Secret so it is masked in the UI. |
| `{$RCONFIG.POE_SNIPPET_ID}` | `4` | Numeric ID of the PoE-cycle snippet in rConfig. |

### Template-Level Triggers

| Trigger | Severity | Condition |
|---|---|---|
| `{HOST.NAME} has been restarted` | Warning | Hardware uptime < 10m or (hw uptime = 0 and net uptime < 10m). Manual close. Depends on: No SNMP data collection. |
| `Temperature is above critical threshold` | High | 5m avg temp > `{$TEMP_CRIT}` OR `extremeOverTemperatureAlarm` = `{$TEMP_CRIT_STATUS}`. Recovers when max drops 3° below critical. |

### Template Dashboard: Network Interfaces

The template includes a pre-built dashboard with three custom widgets from this repository:

| Widget Type | Position | Description |
|---|---|---|
| `switchports` | Row 0 | Switch Port Status widget — 72 wide × 5 tall, header hidden. |
| `portdetail` | Row 5 | Switch Port Detail widget — 62 wide × 4 tall. `show_debug=0`. |
| `packetfence` | Row 5, col 62 | PacketFence device lookup widget — 10 wide × 4 tall. Configured with `pf_url` and `pf_password` fields. `show_debug=0`. |

### Value Maps

| Value Map | Mappings |
|---|---|
| `EXTREME-SYSTEM-MIB::extremeFanOperational` | `1`=true - on, `2`=false - off |
| `EXTREME-SYSTEM-MIB::extremeOverTemperatureAlarm` | `1`=alarm, `2`=normal |
| `EXTREME-SYSTEM-MIB::extremePowerSupplyStatus` | `1`=notPresent, `2`=presentOK, `3`=presentNotOK, `4`=presentPowerOff |
| `EtherLike-MIB::dot3StatsDuplexStatus` | `1`=unknown, `2`=halfDuplex, `3`=fullDuplex |
| `IF-MIB::ifOperStatus` | `1`=up, `2`=down, `4`=unknown, `5`=dormant, `6`=notPresent, `7`=lowerLayerDown |
| `IF-MIB::ifType` | Full IANAifType table (values 1–290) |
| `POE Status` | `0`=🔴 ERROR, `1`=🟠 Disabled, `2`=🔎 Searching, `3`=⚡️ Delivering Power, `4`=🔴 Fault, `6`=🔴 Other fault, `7`=🟡 Test, `8`=🔴 Deny |
| `POE ADMIN` | `1`=🟢 Enabled, `2`=🟠 Disabled |
| `Service state` | `0`=Down, `1`=Up |
| `zabbix.host.available` | `0`=not available, `1`=available, `2`=unknown |

---

## Template: Extreme XIQ APs by API

**File:** `Extreme_XIQ_APs.yaml` | **Zabbix version:** 7.4 | **Group:** Templates/Network devices

### Description

Discovers and monitors ExtremeCloud IQ (XIQ) access points via the XIQ REST API. A single Zabbix host runs one master Script item that paginates `GET /devices` and returns a flat JSON array of APs. An LLD rule generates per-AP dependent items from that array, resulting in O(1) API calls regardless of fleet size.

> **Tip:** Verify field names for your XIQ API version with:
> ```bash
> curl -s "$XIQ_URL/devices?views=FULL&limit=2" \
>   -H "Authorization: Bearer $TOKEN" | jq '.data[0] | keys'
> ```

### Host Macros

| Macro | Type | Default | Description |
|---|---|---|---|
| `{$XIQ_TOKEN}` | Secret Text | *(required)* | Permanent API token from XIQ Global Settings → API Token Management. |
| `{$XIQ_URL}` | Text | `https://api.extremecloudiq.com` | XIQ API base URL. |
| `{$XIQ_PAGE_SIZE}` | Text | `100` | Number of devices per API page. |
| `{$XIQ_FUNCTION}` | Text | `AP` | `device_function` regex filter for LLD. Can be set to `SWITCH`, `ROUTER`, etc. |
| `{$XIQ_DISCONNECT_TIME}` | Text | `10m` | How long an AP must be continuously disconnected before the disconnect trigger fires. |

### Master Item: `xiq.devices.raw`

**Type:** Script | **Interval:** 5m | **Timeout:** 60s | **History:** 1d

The Script item authenticates with the XIQ API using a Bearer token, paginates `GET /devices?views=FULL` until all pages are exhausted (up to 100 pages), and returns a flat JSON array. Each element contains the following fields:

| Field | Source API Field | Description |
|---|---|---|
| `id` | `id` | XIQ device numeric ID. |
| `serial` | `serial_number` | Device serial number. Used as the LLD unique key. |
| `hostname` | `hostname` | Configured device hostname. |
| `mac` | `mac_address` | MAC address of the AP. |
| `ip` | `ip_address` | Current IP address. |
| `model` | `product_type` | Hardware model (e.g., `AP305C`). |
| `version` | `software_version` | Current firmware version. |
| `func` | `device_function` | Device function type (`AP`, `SWITCH`, `ROUTER`, etc.). |
| `connected` | `connected` | Connection state: `1`=connected, `0`=disconnected. |
| `last_connect` | `last_connect_time` | Unix timestamp (ms or s) of last XIQ check-in. |
| `clients` | `active_clients` | Number of currently associated clients. |
| `uptime` | `system_up_time` | AP uptime in seconds (ms timestamps normalized in item preprocessing). |
| `policy` | `network_policy_name` | Assigned network policy name. |
| `config_mismatch` | `config_mismatch` | Config mismatch flag: `1`=mismatch, `0`=in sync. |
| `admin_state` | `device_admin_state` | Administrative state string. |
| `location` | `locations[last].name` | Leaf location name from the AP's location hierarchy. |
| `location_id` | `location_id` | Numeric ID of the AP's assigned location. |

### Aggregate Dependent Items

| Item Key | Description |
|---|---|
| `xiq.devices.count` | Total number of APs in the master item array (`$.length()`). Heartbeat 1h. History 90d. |
| `xiq.clients.total` | Sum of `active_clients` across all APs via JavaScript preprocessing. History 90d. |

### LLD Rule: XIQ AP Discovery

**Key:** `xiq.ap.discovery` | **Type:** Dependent on `xiq.devices.raw`

**Filter:** `{#FUNCTION}` must match `{$XIQ_FUNCTION}` AND `{#SERIAL}` must match `^.+$` (non-empty serial required).

#### LLD Macros

| Macro | JSON Path | Description |
|---|---|---|
| `{#SERIAL}` | `$.serial` | Serial number — unique key for all item prototypes. |
| `{#HOSTNAME}` | `$.hostname` | AP hostname shown in item names. |
| `{#ID}` | `$.id` | XIQ numeric device ID. |
| `{#MAC}` | `$.mac` | MAC address. |
| `{#IP}` | `$.ip` | IP address. |
| `{#MODEL}` | `$.model` | Hardware model. |
| `{#FUNCTION}` | `$.func` | Device function (used for LLD filter). |
| `{#LOCATION}` | `$.location` | Leaf location name. |
| `{#POLICY}` | `$.policy` | Network policy name. |

#### Per-AP Item Prototypes

All prototypes are Dependent items on `xiq.devices.raw`. They use a JSONPath filter to extract fields by serial: `$[?(@.serial=='{#SERIAL}')].FIELD.first()`

| Item Prototype Key | JSON Field | Type | Description |
|---|---|---|---|
| `xiq.ap.connected[{#SERIAL}]` | `connected` | Numeric | Connection state (0/1). Trend 90d. Value map: XIQ connected state. Triggers disconnect after `{$XIQ_DISCONNECT_TIME}`. |
| `xiq.ap.clients[{#SERIAL}]` | `clients` | Numeric | Active client count. History 30d. Heartbeat 1h. |
| `xiq.ap.uptime[{#SERIAL}]` | `uptime` | Uptime | AP uptime in seconds. JavaScript normalizes ms timestamps (>9999999999) to seconds. Trend 90d. |
| `xiq.ap.lastconnect[{#SERIAL}]` | `last_connect` | Unix time | Last XIQ check-in timestamp. JavaScript normalizes ms to seconds. Heartbeat 1h. |
| `xiq.ap.ip[{#SERIAL}]` | `ip` | Char | IP address. Heartbeat 6h. |
| `xiq.ap.version[{#SERIAL}]` | `version` | Char | Firmware version string. Heartbeat 6h. |
| `xiq.ap.configmismatch[{#SERIAL}]` | `config_mismatch` | Numeric | Config mismatch flag (0/1). Value map: XIQ config mismatch. Info trigger on mismatch. |

#### Trigger Prototypes

| Trigger | Severity | Condition |
|---|---|---|
| `AP {#HOSTNAME} ({#SERIAL}) is disconnected` | Warning | `max(xiq.ap.connected, {$XIQ_DISCONNECT_TIME}) = 0` — AP has been disconnected for the full disconnect time window. |
| `AP {#HOSTNAME} ({#SERIAL}) has config mismatch` | Info | `last(xiq.ap.configmismatch) = 1` |

#### Item Tags

All AP item prototypes carry consistent tags for dashboard filtering and alerting:

| Tag | Value |
|---|---|
| `ap_serial` | `{#SERIAL}` |
| `ap_mac` | `{#MAC}` |
| `ap_id` | `{#ID}` |
| `ap_model` | `{#MODEL}` *(on connected item)* |
| `location` | `{#LOCATION}` *(on connected and clients items)* |
| `component` | `status` / `clients` / `network` / `firmware` / `config` / `heartbeat` / `health` |

### Value Maps

| Value Map | Mappings |
|---|---|
| XIQ connected state | `0`=Disconnected, `1`=Connected |
| XIQ config mismatch | `0`=In sync, `1`=Mismatch |

---

## Appendix: Quick Reference

### Template Comparison

| Feature | Milestone XProtect | Extreme EXOS SNMP | Extreme XIQ APs |
|---|---|---|---|
| Protocol | HTTP REST + WebSocket | SNMP | HTTP REST |
| Auth | OAuth2 (ROPC flow) | SNMPv2c/v3 community | Bearer token (permanent) |
| Discovery | Cameras + Recording Servers | Fans, Memory, Interfaces | Access Points |
| API call strategy | 1 call per master item; dependents free | 1 SNMP walk per master; dependents free | Paginated batch; 1 item for all APs |
| External scripts required | Yes (6 scripts + cron) | No | No |
| Zabbix version | 7.4 | 7.4 | 7.4 |
| Template group | Templates/Video | Templates/Network devices | Templates/Network devices |

### Import Instructions

1. In Zabbix: **Configuration → Templates → Import**
2. Upload the `.yaml` file
3. Accept the default import options (update existing, create new)
4. Assign the template to a host
5. Configure all required host macros (see each template's Host Macros section)
6. For Milestone: deploy external scripts and configure cron before enabling items

> **Note:** The EXOS template's `fdb.raw` item and the `port.mac.list` prototype are required for the Switch Port Status and Switch Port Detail dashboard widgets. Ensure the SNMP walk OIDs `1.3.6.1.2.1.17.4.3.1.2` and `1.3.6.1.2.1.17.1.4.1.2` are accessible on your switches.
