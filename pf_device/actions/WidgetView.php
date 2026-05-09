<?php
/**
 * Unified PacketFence widget — view action.
 *
 * Branches on the configured `source_mode`:
 *   - 'event'      → single device card driven by a JS selection event
 *                    (pf:deviceSelected / mcs:cameraSelected / sw:portSelected).
 *                    The JS class injects sel_mac / sel_ip / sel_name /
 *                    sel_host / sel_source into the update request.
 *   - 'host_items' → multi-device cards driven by override-host +
 *                    sw_snmpIndex from a sibling switch-port widget.
 *                    The widget reads a configurable MAC-list item on
 *                    the host, parses the comma-separated MACs, and
 *                    looks each up in PacketFence (with optional
 *                    Windows-DHCP fallback for missing IPs).
 *
 * Both branches converge on the same payload shape consumed by
 * widget.view.php — { mode, waiting, error?, header, devices[] }.
 */

declare(strict_types=0);

namespace Modules\PfDevice\Actions;

use API;
use CControllerDashboardWidgetView;
use CControllerResponseData;
use Modules\PfDevice\Includes\PfClient;
use Modules\PfDevice\Includes\WidgetForm;

class WidgetView extends CControllerDashboardWidgetView
{
    protected function init(): void
    {
        parent::init();
        $this->addValidationRules([
            'name'         => 'string',
            // host_items mode (legacy switchport selection)
            'sw_snmpIndex' => 'int32',
            'sw_hostid'    => 'db hosts.hostid',
            // event mode
            'sel_mac'      => 'string',
            'sel_ip'       => 'string',
            'sel_name'     => 'string',
            'sel_host'     => 'string',
            'sel_source'   => 'string',
        ]);
    }

    protected function doAction(): void
    {
        $name       = $this->getInput('name', $this->widget->getDefaultName());
        $show_debug = (bool)($this->fields_values['show_debug'] ?? false);
        $mode       = (int)($this->fields_values['source_mode'] ?? WidgetForm::SOURCE_EVENT);

        if ($mode === WidgetForm::SOURCE_HOST_ITEMS) {
            $this->runHostItems($name, $show_debug);
        } else {
            $this->runEvent($name, $show_debug);
        }
    }

    // ── Event mode ───────────────────────────────────────────────────────────

    private function runEvent(string $name, bool $show_debug): void
    {
        $sel_mac    = $this->hasInput('sel_mac')    ? trim((string)$this->getInput('sel_mac'))    : '';
        $sel_ip     = $this->hasInput('sel_ip')     ? trim((string)$this->getInput('sel_ip'))     : '';
        $sel_name   = $this->hasInput('sel_name')   ? trim((string)$this->getInput('sel_name'))   : '';
        $sel_host   = $this->hasInput('sel_host')   ? trim((string)$this->getInput('sel_host'))   : '';
        $sel_source = $this->hasInput('sel_source') ? trim((string)$this->getInput('sel_source')) : '';

        $mac = self::normalizeMac($sel_mac);

        $debug = [
            'mode'        => 'event',
            'has_mac'     => $sel_mac !== '',
            'has_ip'      => $sel_ip !== '',
            'mac_valid'   => $mac !== '',
            'sel_source'  => $sel_source,
        ];

        // Wait for a selection
        if ($mac === '' && $sel_ip === '') {
            $this->respond([
                'mode'    => 'event',
                'waiting' => true,
                'header'  => null,
            ], $name, $show_debug, $debug);
            return;
        }

        $pf = $this->resolvePfConfig();
        if ($pf['error']) {
            $this->respondError($name, self::eventHeader($sel_name, $sel_host, $sel_ip, $mac), $pf['error'], $debug, $show_debug);
            return;
        }

        $login = PfClient::login($pf['url'], $pf['user'], $pf['pass'], $pf['verify_ssl']);
        $debug['login'] = ['ok' => $login['ok'], 'http' => $login['http_code'], 'error' => $login['error']];
        if (!$login['ok']) {
            $this->respondError($name, self::eventHeader($sel_name, $sel_host, $sel_ip, $mac),
                sprintf(_('PacketFence login failed: %s'), $login['error'] ?? 'unknown'),
                $debug, $show_debug);
            return;
        }
        $token = $login['token'];

        // Search PF for the device. Prefer MAC-exact match if we have a
        // MAC; fall back to ip4log.ip otherwise. If both are present we
        // OR them so PF still finds the node when one side has gone stale.
        $clauses = [];
        if ($mac !== '')     $clauses[] = ['op' => 'equals', 'field' => 'mac',       'value' => $mac];
        if ($sel_ip !== '')  $clauses[] = ['op' => 'equals', 'field' => 'ip4log.ip', 'value' => $sel_ip];

        $nodes_r = PfClient::request($pf['url'] . '/api/v1/nodes/search', 'POST', $token,
            PfClient::nodeSearchBody($clauses, 5), $pf['verify_ssl']);
        $debug['nodes'] = ['http' => $nodes_r['http_code'], 'count' => is_array($nodes_r['data']['items'] ?? null) ? count($nodes_r['data']['items']) : 0];

        $node = null;
        if ($nodes_r['ok'] && !empty($nodes_r['data']['items'])) {
            if ($mac !== '') {
                foreach ($nodes_r['data']['items'] as $n) {
                    if (strtolower((string)($n['mac'] ?? '')) === $mac) { $node = $n; break; }
                }
            }
            if ($node === null) $node = $nodes_r['data']['items'][0];
        }

        $lookup_mac = $node ? strtolower((string)($node['mac'] ?? '')) : $mac;
        $loc        = ($lookup_mac !== '') ? PfClient::locationLookup($pf['url'], $token, $lookup_mac, $pf['verify_ssl']) : null;
        $events     = ($lookup_mac !== '') ? PfClient::openSecurityEvents($pf['url'], $token, $lookup_mac, $pf['verify_ssl']) : [];

        [$snmp_index, $iface_name] = PfClient::derivePortSpec($loc);
        $switch_hostid             = $loc ? self::resolveSwitchHostId((string)($loc['switch_ip'] ?? '')) : null;
        $location_card             = PfClient::buildLocationCard($loc, $switch_hostid, $snmp_index, $iface_name);

        $card = PfClient::buildDeviceCard(
            $mac !== '' ? $mac : (string)($node['mac'] ?? '—'),
            $sel_ip,
            $node,
            $events,
            $location_card,
            $show_debug
        );
        // Tag the IP source with the friendlier label the camera widget
        // used to emit. The renderer just displays whatever's in here.
        if (($card['ip_source'] ?? null) === 'caller' && $sel_source === 'milestone_camera') {
            $card['ip_source'] = 'camera';
        }

        $this->respond([
            'mode'    => 'event',
            'waiting' => false,
            'header'  => self::eventHeader($sel_name, $sel_host, $sel_ip, $mac),
            'devices' => [$card],
        ], $name, $show_debug, $debug);
    }

    // ── Host-items mode (switchport) ─────────────────────────────────────────

    private function runHostItems(string $name, bool $show_debug): void
    {
        $debug = ['mode' => 'host_items'];

        // Resolve target hostid from override-host or sw_hostid input.
        $hostid = 0;
        $override = $this->fields_values['override_hostid'] ?? null;
        if (is_array($override) && $override) {
            $hostid = (int)reset($override);
        } elseif ($this->hasInput('sw_hostid')) {
            $hostid = (int)$this->getInput('sw_hostid');
        }
        $snmpIndex = $this->hasInput('sw_snmpIndex') ? (int)$this->getInput('sw_snmpIndex') : 0;
        $debug['hostid']    = $hostid;
        $debug['snmpIndex'] = $snmpIndex;

        if ($hostid === 0 || $snmpIndex === 0) {
            $this->respond([
                'mode'    => 'host_items',
                'waiting' => true,
                'header'  => null,
            ], $name, $show_debug, $debug);
            return;
        }

        // Friendly host name for the header.
        $switch_name = '';
        $hosts = API::Host()->get(['output' => ['host', 'name'], 'hostids' => [$hostid]]);
        if ($hosts) {
            $switch_name = $hosts[0]['name'] ?: $hosts[0]['host'];
        }

        // Look up the MAC-list item. Try the configured prefix first,
        // then a couple of common alternatives.
        $primary_key = (string)($this->fields_values['mac_item_prefix'] ?? 'port.mac.list[') . $snmpIndex . ']';
        $candidates = [
            $primary_key,
            'port.mac.list[ifIndex.' . $snmpIndex . ']',
            'net.if.mac[ifIndex.' . $snmpIndex . ']',
        ];
        $debug['candidate_keys'] = $candidates;

        $items = API::Item()->get([
            'output'  => ['itemid', 'key_', 'lastvalue', 'lastclock'],
            'hostids' => [$hostid],
            'filter'  => ['key_' => $candidates],
        ]);
        $mac_item = null;
        foreach ($items as $it) {
            if ($it['lastvalue'] !== '' && $it['lastvalue'] !== null) { $mac_item = $it; break; }
        }
        if (!$mac_item && $items) $mac_item = reset($items);

        if (!$mac_item) {
            $this->respondError($name,
                self::switchHeader($switch_name, $snmpIndex, 0, null),
                sprintf(_('No MAC-list item found on host. Expected key: %s'), $primary_key),
                $debug, $show_debug);
            return;
        }

        // Parse the comma/space-separated MAC list.
        $macs = [];
        foreach (preg_split('/[,\s]+/', (string)$mac_item['lastvalue'], -1, PREG_SPLIT_NO_EMPTY) as $m) {
            $m = strtolower(trim($m));
            if (preg_match('/^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/', $m)) {
                $macs[] = str_replace('-', ':', $m);
            }
        }
        $macs = array_values(array_unique($macs));
        $debug['macs'] = $macs;
        $mac_age = $mac_item['lastclock'] ? (time() - (int)$mac_item['lastclock']) : null;

        if (!$macs) {
            $this->respond([
                'mode'    => 'host_items',
                'waiting' => false,
                'header'  => self::switchHeader($switch_name, $snmpIndex, 0, $mac_age),
                'devices' => [],
            ], $name, $show_debug, $debug);
            return;
        }

        $pf = $this->resolvePfConfig();
        if ($pf['error']) {
            $this->respondError($name, self::switchHeader($switch_name, $snmpIndex, count($macs), $mac_age),
                $pf['error'], $debug, $show_debug);
            return;
        }

        $login = PfClient::login($pf['url'], $pf['user'], $pf['pass'], $pf['verify_ssl']);
        $debug['login'] = ['ok' => $login['ok'], 'http' => $login['http_code'], 'error' => $login['error']];
        if (!$login['ok']) {
            $this->respondError($name, self::switchHeader($switch_name, $snmpIndex, count($macs), $mac_age),
                sprintf(_('PacketFence login failed: %s'), $login['error'] ?? 'unknown'),
                $debug, $show_debug);
            return;
        }
        $token = $login['token'];

        // Single nodes/search for all MACs at once.
        $clauses = [];
        foreach ($macs as $m) $clauses[] = ['op' => 'equals', 'field' => 'mac', 'value' => $m];
        $nodes_r = PfClient::request($pf['url'] . '/api/v1/nodes/search', 'POST', $token,
            PfClient::nodeSearchBody($clauses, 25), $pf['verify_ssl']);
        $debug['nodes'] = ['http' => $nodes_r['http_code'], 'count' => is_array($nodes_r['data']['items'] ?? null) ? count($nodes_r['data']['items']) : 0];

        $node_by_mac = [];
        if ($nodes_r['ok'] && isset($nodes_r['data']['items'])) {
            foreach ($nodes_r['data']['items'] as $n) {
                $node_by_mac[strtolower((string)($n['mac'] ?? ''))] = $n;
            }
        }

        // Per-MAC: locationlog + security events + card.
        $devices = [];
        foreach ($macs as $mac) {
            $node     = $node_by_mac[$mac] ?? null;
            $loc      = PfClient::locationLookup($pf['url'], $token, $mac, $pf['verify_ssl']);
            $events   = $node ? PfClient::openSecurityEvents($pf['url'], $token, $mac, $pf['verify_ssl']) : [];
            [$snmp_idx, $iface_name] = PfClient::derivePortSpec($loc);
            $sw_hostid = $loc ? self::resolveSwitchHostId((string)($loc['switch_ip'] ?? '')) : null;
            $loc_card  = PfClient::buildLocationCard($loc, $sw_hostid, $snmp_idx, $iface_name);

            $devices[] = PfClient::buildDeviceCard($mac, '', $node, $events, $loc_card, $show_debug);
        }

        // DHCP fallback — only run if at least one card has no IP.
        $needs_dhcp = false;
        foreach ($devices as $d) {
            if (empty($d['ip'])) { $needs_dhcp = true; break; }
        }
        $dhcp_host     = trim((string)($this->fields_values['dhcp_host']     ?? ''));
        $dhcp_item_key = trim((string)($this->fields_values['dhcp_item_key'] ?? ''));
        if ($needs_dhcp && $dhcp_host !== '' && $dhcp_item_key !== '') {
            $dbg_dhcp  = ['host' => $dhcp_host, 'item_key' => $dhcp_item_key, 'lookup_attempts' => 0,
                'matches' => 0, 'lease_count' => 0, 'item_age' => null, 'error' => null];
            $lease_map = self::fetchDhcpLeases($dhcp_host, $dhcp_item_key, $dbg_dhcp);
            if ($lease_map !== null) {
                foreach ($devices as &$dev) {
                    if (!empty($dev['ip'])) continue;
                    $dbg_dhcp['lookup_attempts']++;
                    $mac_lc = strtolower((string)$dev['mac']);
                    if (isset($lease_map[$mac_lc])) {
                        $lease = $lease_map[$mac_lc];
                        $dev['ip']            = $lease['ip']       ?? null;
                        $dev['ip_source']     = 'dhcp';
                        $dev['dhcp_hostname'] = $lease['hostname'] ?? null;
                        $dev['dhcp_expires']  = $lease['expires']  ?? null;
                        $dev['dhcp_scope']    = $lease['scope']    ?? null;
                        $dbg_dhcp['matches']++;
                    }
                }
                unset($dev);
            }
            $debug['dhcp'] = $dbg_dhcp;
        }

        $this->respond([
            'mode'    => 'host_items',
            'waiting' => false,
            'header'  => self::switchHeader($switch_name, $snmpIndex, count($macs), $mac_age),
            'devices' => $devices,
        ], $name, $show_debug, $debug);
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private function resolvePfConfig(): array
    {
        $url  = rtrim((string)($this->fields_values['pf_url']      ?? ''), '/');
        $user = (string)($this->fields_values['pf_username'] ?? '');
        $pass = (string)($this->fields_values['pf_password'] ?? '');
        $ssl  = (bool)  ($this->fields_values['verify_ssl']  ?? false);
        if (!$url || !$user || !$pass) {
            return ['error' => _('PacketFence URL, username, and password must be configured')];
        }
        return ['url' => $url, 'user' => $user, 'pass' => $pass, 'verify_ssl' => $ssl, 'error' => null];
    }

    private function respond(array $core, string $name, bool $show_debug, array $debug): void
    {
        $payload = array_merge($core, [
            'name'         => $name,
            'pf_admin_url' => rtrim((string)($this->fields_values['pf_admin_url'] ?? ''), '/'),
            'show_debug'   => $show_debug,
            'debug_info'   => $debug,
            'user'         => ['debug_mode' => $this->getDebugMode()],
        ]);
        $payload['devices'] = $payload['devices'] ?? [];
        $this->setResponse(new CControllerResponseData($payload));
    }

    private function respondError(string $name, ?array $header, string $error, array $debug, bool $show_debug): void
    {
        $this->respond([
            'mode'    => $debug['mode'] ?? 'event',
            'waiting' => false,
            'error'   => $error,
            'header'  => $header,
            'devices' => [],
        ], $name, $show_debug, $debug);
    }

    private static function eventHeader(string $name, string $host, string $ip, string $mac): array
    {
        $title = $name !== '' ? $name : ($mac !== '' ? $mac : ($ip ?: '—'));
        return [
            'kind'     => 'event',
            'title'    => $title,
            'subtitle' => $host !== '' ? sprintf(_('on %s'), $host) : '',
        ];
    }

    private static function switchHeader(string $switch_name, int $snmpIndex, int $mac_count, ?int $age_seconds): array
    {
        return [
            'kind'      => 'switch',
            'title'     => $switch_name,
            'subtitle'  => $snmpIndex > 0 ? 'ifIndex ' . $snmpIndex : '',
            'mac_count' => $mac_count,
            'age'       => self::formatAge($age_seconds),
        ];
    }

    private static function formatAge(?int $sec): ?string
    {
        if ($sec === null) return null;
        if ($sec < 60)    return $sec . 's';
        if ($sec < 3600)  return (int)round($sec / 60) . 'm';
        return (int)round($sec / 3600) . 'h';
    }

    /**
     * Normalize a MAC address to the canonical lowercase colon-separated form
     * (aa:bb:cc:dd:ee:ff) that PacketFence's API expects.
     *
     * Accepts any input that boils down to exactly 12 hex digits — colons,
     * dashes, dots, bare hex, mixed case all welcome. This matters because
     * different source widgets hand us MACs in different formats: switchports'
     * FDB items emit colon-separated, the camera widget passes them through
     * unchanged, and XIQ's API returns bare 12-digit hex (e.g. 34858460A500).
     *
     * Returns '' if the input doesn't reduce to 12 hex digits.
     */
    private static function normalizeMac(string $raw): string
    {
        if ($raw === '') return '';
        $hex = strtolower((string) preg_replace('/[^0-9a-f]/i', '', trim($raw)));
        if (strlen($hex) !== 12) return '';
        return implode(':', str_split($hex, 2));
    }

    /**
     * Look up the Zabbix host owning the given switch IP. Returns null on
     * no-match or ambiguous match — better to omit the Cycle PoE button
     * than fire it at the wrong port.
     */
    private static function resolveSwitchHostId(string $switch_ip): ?int
    {
        if ($switch_ip === '' || $switch_ip === '0.0.0.0') return null;
        $ifaces = API::HostInterface()->get([
            'output' => ['hostid'],
            'filter' => ['ip' => $switch_ip],
        ]);
        $hostids = array_unique(array_map('intval', array_column($ifaces, 'hostid')));
        return count($hostids) === 1 ? (int)$hostids[0] : null;
    }

    /**
     * Pull the DHCP lease JSON blob from a Zabbix item on the configured
     * host. Lease exporter emits gzip+base64 by default; falls back to
     * raw JSON for older agents. Returns lowercased-MAC → lease row or
     * null on failure.
     */
    private static function fetchDhcpLeases(string $hostname, string $item_key, array &$debug): ?array
    {
        $hosts = API::Host()->get([
            'output' => ['hostid', 'name'],
            'filter' => ['host' => $hostname],
        ]);
        if (!$hosts) {
            $hosts = API::Host()->get([
                'output' => ['hostid', 'name'],
                'search' => ['name' => $hostname],
            ]);
        }
        if (!$hosts) {
            $debug['error'] = 'DHCP host not found: ' . $hostname;
            return null;
        }

        $items = API::Item()->get([
            'output'  => ['itemid', 'key_', 'lastvalue', 'lastclock'],
            'hostids' => [$hosts[0]['hostid']],
            'filter'  => ['key_' => $item_key],
        ]);
        if (!$items) {
            $debug['error'] = 'DHCP item not found: ' . $item_key;
            return null;
        }
        $item = $items[0];
        $debug['item_age'] = $item['lastclock'] ? (time() - (int)$item['lastclock']) : null;

        $raw = (string)$item['lastvalue'];
        if ($raw === '') {
            $debug['error'] = 'DHCP item has empty value';
            return null;
        }

        $json = null;
        $trimmed = trim($raw);
        $looks_base64 = ($trimmed !== ''
            && $trimmed[0] !== '['
            && $trimmed[0] !== '{'
            && preg_match('/^[A-Za-z0-9+\/=\s]+$/', $trimmed));
        if ($looks_base64) {
            $decoded = base64_decode($trimmed, true);
            if ($decoded !== false) {
                $inflated = @gzdecode($decoded);
                if (is_string($inflated) && $inflated !== '') {
                    $json = $inflated;
                    $debug['encoding']            = 'gzip+base64';
                    $debug['compressed_bytes']   = strlen($trimmed);
                    $debug['decompressed_bytes'] = strlen($inflated);
                }
            }
        }
        if ($json === null) {
            $json = $raw;
            $debug['encoding'] = 'raw';
        }

        $data = json_decode($json, true);
        if (!is_array($data)) {
            $debug['error'] = 'DHCP item value is not valid JSON after decoding';
            return null;
        }
        $debug['lease_count'] = count($data);

        $map = [];
        foreach ($data as $lease) {
            if (!is_array($lease) || !isset($lease['mac'])) continue;
            $mac = strtolower(str_replace('-', ':', (string)$lease['mac']));
            if (!preg_match('/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/', $mac)) continue;
            $map[$mac] = $lease;
        }
        return $map;
    }
}
