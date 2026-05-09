<?php
/**
 * Unified PacketFence widget — view template.
 *
 * Single renderer for both source modes. The header shape varies (switch
 * vs device) but the device cards are identical. Each card carries:
 *   - MAC + status pill
 *   - IP (with PF/CAM/DHCP source badge), hostname, vendor, OS, etc.
 *   - Optional locationlog block (switch / port / VLAN / role / 802.1X)
 *   - Optional security-events block
 *   - Action buttons (Reevaluate access / Restart switchport / Cycle PoE)
 *
 * @var CView $this
 * @var array $data
 */

function pfdHtml(string $s): string {
    return htmlspecialchars($s, ENT_QUOTES);
}

function pfdDetailRow(string $label, string $value, string $extra_cls = ''): string {
    return '<div class="pf-detail">'
        . '<span class="pf-detail__label">' . pfdHtml($label) . '</span>'
        . '<span class="pf-detail__value ' . $extra_cls . '">' . $value . '</span>'
        . '</div>';
}

function pfdRenderHeader(?array $h): string {
    if ($h === null) return '';
    $out = '<div class="pf-header">';
    if (!empty($h['title']))    $out .= '<span class="pf-header__title">' . pfdHtml((string)$h['title']) . '</span>';
    if (!empty($h['subtitle'])) $out .= '<span class="pf-header__port">'  . pfdHtml((string)$h['subtitle']) . '</span>';
    if (!empty($h['mac_count'])) {
        $n = (int)$h['mac_count'];
        $out .= '<span class="pf-header__count">' . $n . ' MAC' . ($n === 1 ? '' : 's') . '</span>';
    }
    if (!empty($h['age'])) {
        $out .= '<span class="pf-header__age" title="MAC list last updated">' . pfdHtml((string)$h['age']) . ' old</span>';
    }
    $out .= '</div>';
    return $out;
}

function pfdRenderDevice(array $dev, string $pf_admin_url): string {
    $mac       = pfdHtml((string)($dev['mac']      ?? '—'));
    $ip        = pfdHtml((string)($dev['ip']       ?? ''));
    $ip_src    = (string)($dev['ip_source'] ?? '');
    $pf_host   = pfdHtml((string)($dev['hostname'] ?? ''));
    $dhcp_host = pfdHtml((string)($dev['dhcp_hostname'] ?? ''));
    $vendor    = pfdHtml((string)($dev['vendor']   ?? ''));
    $os        = pfdHtml((string)($dev['os']       ?? ''));
    $pid       = pfdHtml((string)($dev['pid']      ?? ''));
    $status    = (string)($dev['status']   ?? '');
    $bvlan     = pfdHtml((string)($dev['bypass_vlan'] ?? ''));
    $ua        = pfdHtml((string)($dev['user_agent']  ?? ''));
    $dhcp_fp   = pfdHtml((string)($dev['dhcp_fingerprint'] ?? ''));
    $last_seen = pfdHtml((string)($dev['last_seen'] ?? ''));
    $last_arp  = pfdHtml((string)($dev['last_arp']  ?? ''));
    $last_dhcp = pfdHtml((string)($dev['last_dhcp'] ?? ''));
    $scope     = pfdHtml((string)($dev['dhcp_scope'] ?? ''));
    $not_in_pf = !empty($dev['not_in_pf']);
    $events    = $dev['security_events'] ?? [];
    $loc       = $dev['location'] ?? null;

    // Prefer PF hostname, fall back to DHCP-leased hostname.
    $hostname = $pf_host !== '' ? $pf_host : $dhcp_host;

    $status_css = match (strtolower($status)) {
        'reg', 'registered'     => 'pf-status--ok',
        'unreg', 'unregistered' => 'pf-status--warn',
        'pending'               => 'pf-status--pending',
        default                 => 'pf-status--unknown',
    };
    $status_label = $status !== '' ? ucfirst($status) : 'Unknown';

    $out  = '<div class="pf-card' . ($not_in_pf ? ' pf-card--unknown' : '') . '">';

    // Header: MAC + status pill
    $out .= '<div class="pf-card__header">';
    $out .= '<span class="pf-card__mac">' . $mac . '</span>';
    $out .= '<span class="pf-card__pills">';
    if ($not_in_pf) {
        $out .= '<span class="pf-status pf-status--unknown">Not in PacketFence</span>';
    } else {
        $out .= '<span class="pf-status ' . $status_css . '">' . pfdHtml($status_label) . '</span>';
    }
    $out .= '</span></div>';

    // Details grid
    $out .= '<div class="pf-card__details">';
    if ($ip) {
        $ip_html = $ip;
        if ($ip_src === 'dhcp') {
            $ip_html .= ' <span class="pf-ip-src pf-ip-src--dhcp" title="IP from Windows DHCP lease">DHCP</span>';
        } elseif ($ip_src === 'pf') {
            $ip_html .= ' <span class="pf-ip-src pf-ip-src--pf" title="IP from PacketFence ip4log">PF</span>';
        } elseif ($ip_src === 'camera') {
            $ip_html .= ' <span class="pf-ip-src pf-ip-src--dhcp" title="IP from camera item">CAM</span>';
        } elseif ($ip_src === 'caller') {
            $ip_html .= ' <span class="pf-ip-src pf-ip-src--dhcp" title="IP from source widget">SRC</span>';
        }
        $out .= pfdDetailRow('IP', $ip_html, 'pf-mono');
    }
    if ($hostname)  $out .= pfdDetailRow('Hostname',     $hostname);
    if ($vendor)    $out .= pfdDetailRow('Vendor',       $vendor);
    if ($os)        $out .= pfdDetailRow('OS',           $os);
    if ($pid && $pid !== 'default') $out .= pfdDetailRow('Owner', $pid);
    if ($bvlan)     $out .= pfdDetailRow('Bypass VLAN',  $bvlan);
    if ($scope && $ip_src === 'dhcp') $out .= pfdDetailRow('DHCP Scope', $scope, 'pf-mono');
    if ($dhcp_fp)   $out .= pfdDetailRow('DHCP FP',      $dhcp_fp, 'pf-mono');
    if ($last_seen) $out .= pfdDetailRow('Last seen',    $last_seen);
    if ($last_arp)  $out .= pfdDetailRow('Last ARP',     $last_arp);
    if ($last_dhcp) $out .= pfdDetailRow('Last DHCP',    $last_dhcp);
    if ($ua && strlen($ua) < 80) $out .= pfdDetailRow('User-Agent', $ua);
    $out .= '</div>';

    // Locationlog block
    if (is_array($loc)) {
        $sw     = pfdHtml((string)($loc['switch']         ?? ''));
        $sw_ip  = pfdHtml((string)($loc['switch_ip']      ?? ''));
        $port   = pfdHtml((string)($loc['port']           ?? ''));
        $ifdesc = pfdHtml((string)($loc['ifDesc']         ?? ''));
        $vlan   = pfdHtml((string)($loc['vlan']           ?? ''));
        $role   = pfdHtml((string)($loc['role']           ?? ''));
        $ssid   = pfdHtml((string)($loc['ssid']           ?? ''));
        $ctype  = pfdHtml((string)($loc['connection_type'] ?? ''));
        $dot1x  = pfdHtml((string)($loc['dot1x_username'] ?? ''));
        $start  = pfdHtml((string)($loc['start_time']     ?? ''));
        $has_any = $sw || $port || $vlan || $role || $ssid || $ctype || $dot1x;
        if ($has_any) {
            $out .= '<div class="pf-card__details pf-card__details--location">';
            if ($sw) {
                $sw_html = $sw . ($sw_ip ? ' <span class="pf-mono">(' . $sw_ip . ')</span>' : '');
                $out .= pfdDetailRow('Switch', $sw_html);
            }
            if ($port) {
                $port_html = $port . ($ifdesc && $ifdesc !== $port ? ' <span class="pf-mono">' . $ifdesc . '</span>' : '');
                $out .= pfdDetailRow('Port', $port_html);
            }
            if ($vlan)  $out .= pfdDetailRow('VLAN',        $vlan);
            if ($role)  $out .= pfdDetailRow('Role',        $role);
            if ($ssid)  $out .= pfdDetailRow('SSID',        $ssid);
            if ($ctype) $out .= pfdDetailRow('Connection',  $ctype);
            if ($dot1x) $out .= pfdDetailRow('802.1X User', $dot1x);
            if ($start) $out .= pfdDetailRow('Since',       $start);
            $out .= '</div>';
        }
    }

    // Security events
    if ($events) {
        $n = count($events);
        $out .= '<div class="pf-card__events">';
        $out .= '<span class="pf-events-label">⚠ ' . $n . ' open security event' . ($n === 1 ? '' : 's') . '</span>';
        $out .= '<ul class="pf-events-list">';
        foreach ($events as $ev) {
            $desc = pfdHtml((string)($ev['description'] ?? $ev['name'] ?? 'Unknown'));
            $out .= '<li>' . $desc . '</li>';
        }
        $out .= '</ul></div>';
    }

    // Actions
    $out .= '<div class="pf-card__actions">';
    if ($pf_admin_url !== '' && !$not_in_pf) {
        $node_url = $pf_admin_url . '/admin/#/node/' . rawurlencode((string)($dev['mac'] ?? ''));
        $out .= '<a class="pf-action" href="' . pfdHtml($node_url) . '" target="_blank" rel="noopener">View in PacketFence</a>';
    }
    if (!$not_in_pf) {
        $mac_attr = pfdHtml((string)($dev['mac'] ?? ''));
        $out .= '<button type="button" class="pf-action pf-action--reeval"'
            . ' data-pf-action="reevaluate_access" data-pf-mac="' . $mac_attr . '"'
            . ' title="Re-apply PacketFence access policy for this MAC">Reevaluate access</button>';
        if (!empty($loc['iface_name'])) {
            $out .= '<button type="button" class="pf-action pf-action--restart"'
                . ' data-pf-action="restart_switchport" data-pf-mac="' . $mac_attr . '"'
                . ' title="Bounce the switch port this MAC is connected to">Restart switchport</button>';
        }
        if (is_array($loc) && !empty($loc['switch_hostid']) && !empty($loc['iface_name'])) {
            $sw_hid    = (int)$loc['switch_hostid'];
            $snmp_idx  = (int)($loc['snmp_index'] ?? 0);
            $iface     = pfdHtml((string)$loc['iface_name']);
            $sw_label  = pfdHtml((string)($loc['switch'] ?? $loc['switch_ip'] ?? ''));
            $out .= '<button type="button" class="pf-action pf-action--cyclepoe"'
                . ' data-pf-action="cycle_poe"'
                . ' data-pf-hostid="' . $sw_hid . '"'
                . ' data-pf-snmp-index="' . $snmp_idx . '"'
                . ' data-pf-iface="' . $iface . '"'
                . ' title="Cycle PoE on ' . $sw_label . ' port ' . $iface . ' via rConfig">Cycle PoE</button>';
        }

        $out .= '<span class="pf-action-status" data-pf-status-for="' . $mac_attr . '"></span>';
    }
    $out .= '</div></div>';
    return $out;
}

function pfdRenderDebug(array $info): string {
    $out  = '<details class="pf-dbg"><summary>Debug Info</summary><div class="pf-dbg-body"><table class="pf-dbg-tbl">';
    foreach ($info as $k => $v) {
        if (is_array($v) || is_object($v)) {
            $v = json_encode($v, JSON_UNESCAPED_SLASHES);
        }
        $out .= '<tr><th>' . pfdHtml((string)$k) . '</th><td>' . pfdHtml((string)$v) . '</td></tr>';
    }
    $out .= '</table></div></details>';
    return $out;
}

// ── Render ──────────────────────────────────────────────────────────────────

$mode    = (string)($data['mode'] ?? 'event');
$header  = $data['header']  ?? null;
$devices = $data['devices'] ?? [];
$body    = '<div class="pf-widget">';

if (!empty($data['waiting'])) {
    $body .= '<div class="pf-empty">';
    if ($mode === 'host_items') {
        $body .= '<div class="pf-empty__icon">🔌</div>'
            . '<div class="pf-empty__msg">Awaiting port selection</div>'
            . '<div class="pf-empty__hint">Click a port in the Switch Port Status widget</div>';
    } else {
        $body .= '<div class="pf-empty__icon">📡</div>'
            . '<div class="pf-empty__msg">Awaiting device selection</div>'
            . '<div class="pf-empty__hint">Click a device in the source widget (camera, AP, etc.)</div>';
    }
    $body .= '</div>';
} elseif (!empty($data['error'])) {
    $body .= pfdRenderHeader($header);
    $body .= '<div class="pf-error">' . pfdHtml((string)$data['error']) . '</div>';
} else {
    $body .= pfdRenderHeader($header);

    if ($devices) {
        $body .= '<div class="pf-cards">';
        foreach ($devices as $dev) {
            $body .= pfdRenderDevice($dev, (string)($data['pf_admin_url'] ?? ''));
        }
        $body .= '</div>';
    } else {
        $body .= '<div class="pf-empty">';
        $body .= '<div class="pf-empty__icon">∅</div>';
        if ($mode === 'host_items') {
            $body .= '<div class="pf-empty__msg">No devices on this port</div>'
                . '<div class="pf-empty__hint">No MACs learned on this port in the switch FDB</div>';
        } else {
            $body .= '<div class="pf-empty__msg">No PacketFence record</div>'
                . '<div class="pf-empty__hint">PF returned no node for this MAC/IP</div>';
        }
        $body .= '</div>';
    }
}

if (!empty($data['show_debug']) && !empty($data['debug_info'])) {
    $body .= pfdRenderDebug($data['debug_info']);
}

$body .= '</div>';

(new CWidgetView($data))->addItem($body)->show();
