# PacketFence Device Widget (unified)

A single PacketFence dashboard widget that subsumes the two earlier ones —
**Switch Port Device (PacketFence)** and **Camera Device (PacketFence)** —
into one module. Pick the source mode in the widget config and it adapts.

## Source modes

| Mode | What it does | Use with |
|---|---|---|
| **Selection event** | Listens for a JS selection event (`pf:deviceSelected` / `mcs:cameraSelected` / `sw:portSelected`) carrying MAC + IP, renders one PF card for the clicked device. | XIQ AP status, Milestone camera status, future per-device source widgets |
| **Switch port (host items)** | Bound to override-host + a sibling switch-port widget; reads a configurable MAC-list item from the host (e.g. `port.mac.list[<snmpIndex>]`), parses the comma-separated MACs, renders one PF card per learned MAC. Optional Windows-DHCP fallback fills in IPs that PacketFence doesn't know. | Switch Port Status widget (legacy flow) |

Switch-port-only fields (override host, MAC-item prefix, DHCP fallback) are
always shown in the form, but ignored when the source mode is *Selection
event*. The form has no conditional-visibility primitive — the cost is two
extra fields visible in event mode, cleaner than splitting back into two
widgets.

## Selection event compatibility

The widget listens to **all three** events so it works alongside any source
widget without further config:

```js
// Unified — preferred. Sources should emit this.
document.dispatchEvent(new CustomEvent('pf:deviceSelected', {
    detail: { source: 'xiq_ap', hostid, mac, ip, name, host }
}));

// Legacy — still honored:
//   mcs:cameraSelected (Milestone camera status)
//   sw:portSelected    (Switch Port Status, host_items mode)
```

`sessionStorage` is also consulted on activate (keys `pf_device_selection`,
`mcs_camera_selection`, `sw_port_selection`) so a dashboard reload restores
the last selection.

## Configuration fields

| Field | Default | Used in mode |
|---|---|---|
| **Source mode** | Selection event | both |
| **Override host** | — | host_items |
| **PacketFence API URL** | `https://packetfence.example.com:9999` | both |
| **PacketFence admin UI URL** | `https://packetfence.example.com:1443` | both |
| **Username** | `admin` | both |
| **Password** | — | both |
| **DHCP server Zabbix host name** | — (blank disables) | host_items |
| **DHCP lease item key** | `dhcp.leases` | host_items |
| **MAC-list item key prefix** | `port.mac.list[` | host_items |
| **Verify TLS certificate** | unchecked | both |
| **Show debug info** | unchecked | both |

## Action buttons

Per device, the card surfaces:

- **View in PacketFence** — link to `/admin/#/node/<mac>` in the PF admin UI.
- **Reevaluate access** — `PUT /api/v1/node/{mac}/reevaluate_access`.
- **Restart switchport** — `PUT /api/v1/node/{mac}/restart_switchport` (with
  a JS confirm; it bounces every device on the port).
- **Cycle PoE** — only rendered when the controller could resolve a Zabbix
  switch host (by `switch_ip`) AND derive a `member:port` iface name from
  the PF locationlog. Dispatched to the existing `widget.portdetail.cyclepoe`
  rConfig action — same flow as the camera widget used.

## PacketFence API calls

| Call | Purpose |
|---|---|
| `POST /api/v1/login` | Auth token |
| `POST /api/v1/nodes/search` | Look up node(s) by MAC (and IP in event mode) |
| `POST /api/v1/locationlogs/search` | Latest switch / port / VLAN / 802.1X for a MAC |
| `POST /api/v1/security_events/search` | Open security events for a MAC |
| `PUT  /api/v1/node/{mac}/reevaluate_access` | Action button |
| `PUT  /api/v1/node/{mac}/restart_switchport` | Action button |

## Migration from the old widgets

The two original widgets — `packetfence/` and `milestone_camera_packetfence/`
— are still in the repo and continue to work. To migrate a dashboard:

1. Install `pf_device/` alongside (Users → Modules → Scan directory → Enable).
2. Add a *PacketFence device* widget. Pick **Selection event** mode for
   camera/AP companions or **Switch port (host items)** mode and bind the
   override-host for switch-port companions.
3. Copy over your PF URL/credentials/etc.
4. Delete the old widget instance.
5. Once no dashboards reference the old modules, disable + remove
   `packetfence/` and `milestone_camera_packetfence/` directories.

The unified `pf:deviceSelected` event is the long-term path — source
widgets should switch to dispatching it and stop emitting the legacy
event names. The XIQ AP status widget already does this.

## Compatibility

- **Zabbix 7.0+** (manifest version 2.0).
- **PacketFence 15.x** — uses the v1 API (`nodes/search`, `locationlogs/search`).
  12–14 should work; untested.

## Security note

PacketFence credentials are stored in the Zabbix DB in plaintext, same as
the original widgets. Use a dedicated PF webservices user with read-only
scope plus the two action endpoints, not full `admin`.
