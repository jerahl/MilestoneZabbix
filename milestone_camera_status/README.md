# Milestone Camera Status — Zabbix custom widget

A custom dashboard widget for the Milestone XProtect Zabbix template that:

1. **Summarises** every camera by current state across one or more host groups (Total / OK / ESS fault / Ping down / Offline / Disabled / No data).
2. **Lists fault cameras only** in a sortable table with severity pill, camera name, MAC, IP, and last check time.
3. **Cross-references PacketFence on click**: clicking any fault row broadcasts a `mcs:cameraSelected` DOM event with the camera's hostid, MAC, IP, name, and host. Pair with the **Camera Device (PacketFence)** widget on the same dashboard to see the corresponding PacketFence node card.
4. Stays empty (just the summary tiles) when everything is healthy — no row spam on a clean site.

The widget reads from three calculated/agent items per camera produced by the Milestone XProtect template:

| Key | Purpose |
|---|---|
| `milestone.cam.status[<guid>]` | Combined status (-1, 0, 1, 2, 3) |
| `milestone.cam.mac[<guid>]` | Camera hardware MAC |
| `milestone.cam.address[<guid>]` | Current IPv4 address |

Only cameras with a status item show up in the summary tiles. MAC and IP are looked up as siblings by GUID; if either is missing, the column shows `—` and the row is still clickable (the companion PF widget falls back to the IP if the MAC isn't available).

## Installation

Tested on Zabbix 7.4. Should also work on 7.0+ (manifest version 2.0 widgets).

Copy the entire `milestone_camera_status/` directory into your Zabbix frontend's `modules/` directory:

```bash
sudo cp -r milestone_camera_status /usr/share/zabbix/modules/
sudo chown -R www-data:www-data /usr/share/zabbix/modules/milestone_camera_status
```

(Path may differ by distro: `/usr/share/zabbix`, `/var/www/html/zabbix`, or wherever your `ui/` directory lives. The widget goes inside `ui/modules/`.)

Then in the Zabbix UI:

1. **Users → Modules** → **Scan directory**
2. Locate **Milestone camera status** → click the **Disabled** link to enable
3. Edit any dashboard → **Add widget** → type **Milestone camera status**
4. Configure the host group(s) and (optionally) the max rows, save

## Configuration fields

| Field | Required | Default | Notes |
|---|---|---|---|
| Hosts | Yes (regular dashboards) | — | Multi-select. Pick one or more Milestone hosts. On a template dashboard this field is hidden — the widget binds to the template's host automatically. |
| Max table rows | No | 100 | Cap on rows displayed. Excess rows are hidden with a "showing first N" notice. Increase if you have very large outages. |

## Status code mapping

The widget interprets the values produced by the template's `milestone.cam.status[*]` calculated item:

| Value | Meaning | Tile / pill |
|---|---|---|
| -1 | Disabled in XProtect | Disabled (grey) |
| 0 | OK (ping up + ESS comm OK) | OK (green) |
| 1 | ESS comm fault, ping still up | ESS fault (amber) |
| 2 | Ping down, ESS still says OK | Ping down (orange) |
| 3 | Ping down + ESS comm fault | Offline (red) |
| (no value) | Item never polled or unsupported | No data (grey) |

Only values 1, 2, 3 produce table rows. Severity sort puts 3 first, then 2, then 1.

## File layout

```
milestone_camera_status/
├── manifest.json                      Module metadata + widget registration
├── README.md                          (this file)
├── actions/
│   └── WidgetView.php                 Backend: queries Item API, builds payload
├── includes/
│   └── WidgetForm.php                 Config field definitions
├── views/
│   ├── widget.view.php                Initial markup shell
│   └── widget.edit.php                Config dialog
└── assets/
    ├── css/widget.css                 Theme-aware styles
    └── js/class.widget.js             Renders summary + table on each refresh
```

## How it works

The backend `WidgetView` controller does two API calls per refresh:

1. `Item.get` with `search.key_=milestone.cam.status` across the selected hosts — returns one row per camera with the current status code.
2. A second `Item.get` for the sibling `milestone.cam.mac[*]` and `milestone.cam.address[*]` items, scoped to only the hostids that have at least one fault row, so we have MACs and IPs for the fault rows without a wasteful query for healthy cameras.

Tally counts, filter to faults, sort severity-desc + host-asc + camera-asc, cap to `max_rows`, ship as JSON. The frontend `class.widget.js` then renders the tiles and the table, attaching click handlers so column headers can re-sort, summary tiles can filter, and rows can broadcast a `mcs:cameraSelected` event to companion widgets — all without another server round-trip.

The default refresh rate is 60s; change it via the standard widget refresh-rate selector.

### Click-to-lookup integration

When an operator clicks a fault row, the widget:

1. Stores `{hostid, mac, ip, name, host}` under the `mcs_camera_selection` key in `sessionStorage`, so a dashboard reload restores the selection in the companion widget.
2. Dispatches `mcs:cameraSelected` as a DOM `CustomEvent` on `document`, with the same payload in `event.detail`.

The **Camera Device (PacketFence)** widget (sibling module `milestone_camera_packetfence`) listens for that event and renders a PF node card for the selected camera. Other widgets can listen for the same event for their own integrations.

## Troubleshooting

- **Widget not in the Add Widget list**: check **Users → Modules** that the module is **Enabled**, not just registered. A JSON syntax error in `manifest.json` causes silent rejection — `tail -f /var/log/zabbix/web/zabbix_*.log` while you Scan to catch errors.
- **"No `milestone.cam.status[*]` items found"**: the selected host groups don't contain any hosts with the Milestone template, or the template's camera LLD hasn't run yet. Run the cameras-refresh cron and wait for one LLD discovery cycle.
- **MAC / IP columns show `—`**: the `milestone.cam.mac[<guid>]` or `milestone.cam.address[<guid>]` items don't exist on the host, or have no last value yet. Rows are still clickable; the companion PacketFence widget will look up the camera by whichever value is present (MAC preferred, IP fallback).
- **Click does nothing on the companion widget**: confirm the *Camera Device (PacketFence)* widget is on the same dashboard and that it's the version that listens for `mcs:cameraSelected`. Check `document.addEventListener('mcs:cameraSelected', e => console.log(e.detail))` in the browser console while clicking a row.
- **Permissions**: the Zabbix user viewing the dashboard needs read access to the camera items. Check user-group permissions on the host groups.
