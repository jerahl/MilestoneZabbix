<?php
/**
 * Shared PacketFence client + card builders.
 *
 * Hosts the curl-based auth/request helpers, the nodes/locationlogs/
 * security_events queries, and the device-card / location-card shape
 * functions used by both WidgetView and WidgetAction. Previously these
 * lived duplicated across the packetfence and milestone_camera_packetfence
 * widgets; consolidating here means a single place to fix bugs.
 *
 * No Zabbix dependencies — pure HTTP + array shaping. The Zabbix-side
 * lookups (resolveSwitchHostId, fetchDhcpLeases) live in WidgetView since
 * they call the API:: facade.
 */

declare(strict_types=0);

namespace Modules\PfDevice\Includes;

class PfClient
{
    /**
     * POST /api/v1/login. Returns ['ok'=>bool, 'token'=>?string,
     * 'http_code'=>int, 'error'=>?string].
     */
    public static function login(string $base_url, string $user, string $pass, bool $verify_ssl): array
    {
        $ch = curl_init();
        curl_setopt_array($ch, [
            CURLOPT_URL            => $base_url . '/api/v1/login',
            CURLOPT_CUSTOMREQUEST  => 'POST',
            CURLOPT_POSTFIELDS     => json_encode(['username' => $user, 'password' => $pass]),
            CURLOPT_HTTPHEADER     => [
                'Content-Type: application/json',
                'Accept: application/json',
            ],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT        => 15,
            CURLOPT_SSL_VERIFYPEER => $verify_ssl,
            CURLOPT_SSL_VERIFYHOST => $verify_ssl ? 2 : 0,
        ]);
        $body      = curl_exec($ch);
        $http_code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err_curl  = curl_error($ch);
        curl_close($ch);

        if ($err_curl) {
            return ['ok' => false, 'token' => null, 'http_code' => 0, 'error' => $err_curl];
        }
        if ($http_code !== 200) {
            return ['ok' => false, 'token' => null, 'http_code' => $http_code,
                'error' => sprintf('HTTP %d', $http_code)];
        }
        $data = json_decode((string)$body, true);
        if (!is_array($data) || empty($data['token'])) {
            return ['ok' => false, 'token' => null, 'http_code' => $http_code,
                'error' => 'No token in response'];
        }
        return ['ok' => true, 'token' => (string)$data['token'], 'http_code' => $http_code, 'error' => null];
    }

    /**
     * Generic authenticated request. $body is JSON-encoded if non-null.
     * Returns ['ok'=>bool, 'data'=>?array, 'http_code'=>int, 'error'=>?string].
     */
    public static function request(string $url, string $method, string $token, $body, bool $verify_ssl, int $timeout = 15): array
    {
        $ch = curl_init();
        $headers = [
            'Authorization: ' . $token,
            'Accept: application/json',
        ];
        $opts = [
            CURLOPT_URL            => $url,
            CURLOPT_CUSTOMREQUEST  => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT        => $timeout,
            CURLOPT_SSL_VERIFYPEER => $verify_ssl,
            CURLOPT_SSL_VERIFYHOST => $verify_ssl ? 2 : 0,
        ];
        if ($body !== null) {
            $opts[CURLOPT_POSTFIELDS] = json_encode($body);
            $headers[] = 'Content-Type: application/json';
        }
        $opts[CURLOPT_HTTPHEADER] = $headers;
        curl_setopt_array($ch, $opts);

        $resp      = curl_exec($ch);
        $http_code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err_curl  = curl_error($ch);
        curl_close($ch);

        if ($err_curl) {
            return ['ok' => false, 'data' => null, 'http_code' => 0, 'error' => $err_curl];
        }
        if ($http_code < 200 || $http_code >= 300) {
            return ['ok' => false, 'data' => null, 'http_code' => $http_code,
                'error' => sprintf('HTTP %d: %s', $http_code, substr((string)$resp, 0, 200))];
        }
        $data = json_decode((string)$resp, true);
        return ['ok' => true, 'data' => is_array($data) ? $data : [], 'http_code' => $http_code, 'error' => null];
    }

    /**
     * Build a nodes/search payload that ORs an arbitrary list of
     * (field, value) clauses. Used in both modes — host_items passes
     * many MACs, event mode passes one MAC plus optionally one IP.
     *
     * $clauses: array of ['op'=>..., 'field'=>..., 'value'=>...]
     */
    public static function nodeSearchBody(array $clauses, int $limit = 25): array
    {
        return [
            'cursor' => 0,
            'limit'  => $limit,
            'sort'   => ['mac ASC'],
            'fields' => [
                'autoreg', 'bandwidth_balance', 'bypass_acls', 'bypass_role_id',
                'bypass_vlan', 'category_id', 'computername', 'detect_date',
                'device_class', 'device_manufacturer', 'device_score', 'device_type',
                'device_version', 'dhcp6_enterprise', 'dhcp6_fingerprint',
                'dhcp_fingerprint', 'dhcp_vendor', 'last_arp', 'last_dhcp',
                'last_seen', 'mac', 'machine_account', 'notes', 'pid', 'regdate',
                'sessionid', 'status', 'time_balance', 'unregdate', 'user_agent',
                'voip',
                'ip4log.ip', 'ip6log.ip',
            ],
            'query' => [
                'op'     => 'and',
                'values' => [
                    ['op' => 'or', 'values' => $clauses],
                ],
            ],
        ];
    }

    /**
     * Latest locationlog row for a MAC (switch / port / vlan / role / 802.1X).
     */
    public static function locationLookup(string $base_url, string $token, string $mac, bool $verify_ssl): ?array
    {
        $r = self::request(
            $base_url . '/api/v1/locationlogs/search',
            'POST', $token,
            [
                'cursor' => 0,
                'limit'  => 1,
                'sort'   => ['start_time DESC'],
                'fields' => [
                    'mac', 'switch', 'switch_ip', 'switch_mac', 'port',
                    'vlan', 'role', 'ssid', 'connection_type',
                    'connection_sub_type', 'dot1x_username', 'realm',
                    'session_id', 'ifDesc', 'start_time', 'end_time',
                ],
                'query'  => ['op' => 'equals', 'field' => 'mac', 'value' => $mac],
            ],
            $verify_ssl
        );
        if ($r['ok'] && !empty($r['data']['items'])) {
            return $r['data']['items'][0];
        }
        return null;
    }

    /**
     * Open security events for a MAC, capped at 5. Filters defensively
     * against API shape changes that might widen the response.
     */
    public static function openSecurityEvents(string $base_url, string $token, string $mac, bool $verify_ssl): array
    {
        $r = self::request(
            $base_url . '/api/v1/security_events/search',
            'POST', $token,
            [
                'cursor' => 0,
                'limit'  => 5,
                'fields' => ['security_event_id', 'mac', 'description',
                    'status', 'start_date', 'release_date', 'ip'],
                'query'  => [
                    'op'     => 'and',
                    'values' => [
                        ['op' => 'equals', 'field' => 'mac',    'value' => $mac],
                        ['op' => 'equals', 'field' => 'status', 'value' => 'open'],
                    ],
                ],
            ],
            $verify_ssl
        );
        if (!$r['ok'] || empty($r['data']['items']) || !is_array($r['data']['items'])) {
            return [];
        }
        $out = [];
        foreach ($r['data']['items'] as $ev) {
            if (strtolower((string)($ev['mac'] ?? '')) === $mac) {
                $out[] = $ev;
            }
        }
        return $out;
    }

    /**
     * Project a node row into the dashboard card shape. $fallback_ip is
     * used when PF has no ip4log row (e.g. the source widget already knew
     * the IP). $extra is merged into the result — used by host_items mode
     * to attach DHCP-lease enrichment.
     */
    public static function buildDeviceCard(string $mac, string $fallback_ip, ?array $node, array $events, ?array $location_card, bool $show_debug, array $extra = []): array
    {
        if ($node === null) {
            return array_merge([
                'mac'             => $mac,
                'ip'              => $fallback_ip ?: null,
                'ip_source'       => $fallback_ip !== '' ? 'caller' : null,
                'ip6'             => null,
                'last_arp'        => null,
                'last_dhcp'       => null,
                'hostname'        => null,
                'vendor'          => null,
                'os'              => null,
                'pid'             => null,
                'status'          => 'unknown',
                'not_in_pf'       => true,
                'security_events' => [],
                'location'        => $location_card,
            ], $extra);
        }

        $pf_ip = $node['ip4log.ip'] ?? null;
        $ip        = $pf_ip ?: ($fallback_ip ?: null);
        $ip_source = $pf_ip ? 'pf' : ($fallback_ip ? 'caller' : null);

        return array_merge([
            'mac'              => $node['mac'] ?? $mac,
            'ip'               => $ip,
            'ip_source'        => $ip_source,
            'ip6'              => $node['ip6log.ip']      ?? null,
            'last_arp'         => $node['last_arp']       ?? null,
            'last_dhcp'        => $node['last_dhcp']      ?? null,
            'hostname'         => $node['computername']   ?? null,
            'vendor'           => $node['device_manufacturer'] ?? null,
            'os'               => $node['device_type']    ?? ($node['device_class'] ?? null),
            'device_version'   => $node['device_version'] ?? null,
            'pid'              => $node['pid']            ?? null,
            'status'           => $node['status']         ?? null,
            'category'         => $node['category_id']    ?? null,
            'last_seen'        => $node['last_seen']      ?? null,
            'regdate'          => $node['regdate']        ?? null,
            'unregdate'        => $node['unregdate']      ?? null,
            'bypass_vlan'      => $node['bypass_vlan']    ?? null,
            'bypass_acls'      => $node['bypass_acls']    ?? null,
            'user_agent'       => $node['user_agent']     ?? null,
            'dhcp_fingerprint' => $node['dhcp_fingerprint'] ?? null,
            'machine_account'  => $node['machine_account'] ?? null,
            'notes'            => $node['notes']          ?? null,
            'voip'             => $node['voip']           ?? null,
            'security_events'  => $events,
            'location'         => $location_card,
            '_raw'             => $show_debug ? $node : null,
        ], $extra);
    }

    /**
     * Project a locationlog row into the location-card shape. Returns null
     * if no usable location data is present. The $switch_hostid + iface
     * fields are filled by the caller (WidgetView) since they require a
     * Zabbix Host API lookup.
     */
    public static function buildLocationCard(?array $loc, ?int $switch_hostid, ?int $snmp_index, ?string $iface_name): ?array
    {
        if ($loc === null) {
            return null;
        }
        return [
            'switch'              => $loc['switch']              ?? null,
            'switch_ip'           => $loc['switch_ip']           ?? null,
            'switch_mac'          => $loc['switch_mac']          ?? null,
            'port'                => $loc['port']                ?? null,
            'ifDesc'              => $loc['ifDesc']              ?? null,
            'vlan'                => $loc['vlan']                ?? null,
            'role'                => $loc['role']                ?? null,
            'ssid'                => $loc['ssid']                ?? null,
            'connection_type'     => $loc['connection_type']     ?? null,
            'connection_sub_type' => $loc['connection_sub_type'] ?? null,
            'dot1x_username'      => $loc['dot1x_username']      ?? null,
            'realm'               => $loc['realm']               ?? null,
            'session_id'          => $loc['session_id']          ?? null,
            'start_time'          => $loc['start_time']          ?? null,
            'end_time'            => $loc['end_time']            ?? null,
            'switch_hostid'       => $switch_hostid,
            'snmp_index'          => $snmp_index,
            'iface_name'          => $iface_name,
        ];
    }

    /**
     * Derive [snmp_index, iface_name] from a PacketFence locationlog. The
     * portdetail rConfig snippet expects iface_name in "<member>:<port>"
     * form (e.g. "1:7"). Most PF deployments populate `port` with the SNMP
     * ifIndex; stacked Cisco switches encode that as member*1000 + port.
     * Some PF configs populate `port` with the ifName instead — fall back
     * to regex parsing in that case.
     */
    public static function derivePortSpec(?array $loc): array
    {
        if ($loc === null) {
            return [null, null];
        }
        $port   = $loc['port']   ?? null;
        $ifDesc = $loc['ifDesc'] ?? null;

        if (is_numeric($port)) {
            $idx = (int)$port;
            if ($idx >= 1000) {
                return [$idx, ((int)($idx / 1000)) . ':' . ($idx % 100)];
            }
            return [$idx, '1:' . $idx];
        }

        foreach ([(string)$port, (string)$ifDesc] as $s) {
            if ($s !== '' && preg_match('#(\d+)/\d+/(\d+)\s*$#', $s, $m)) {
                return [null, $m[1] . ':' . $m[2]];
            }
        }
        return [null, null];
    }
}
