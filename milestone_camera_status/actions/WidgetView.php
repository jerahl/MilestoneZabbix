<?php
/**
 * Milestone camera status widget — view action.
 *
 * Backend that the widget fetches via AJAX. Pulls the latest values for
 * milestone.cam.status[*] items in the configured hosts and, for every
 * host that has at least one fault row, also pulls the sibling
 * milestone.cam.mac[*] / milestone.cam.address[*] items so each fault
 * row can carry MAC and IP. Result is shipped to the JS class as JSON
 * via CControllerResponseData.
 *
 * Click-to-lookup: each fault row is rendered with a data-row-index
 * attribute on the JS side; a delegated tbody click handler dispatches
 * a 'mcs:cameraSelected' DOM event so a companion widget (e.g.
 * milestone_camera_packetfence) can react.
 */

declare(strict_types=1);

namespace Modules\MilestoneCameraStatus\Actions;

use API;
use CControllerDashboardWidgetView;
use CControllerResponseData;

class WidgetView extends CControllerDashboardWidgetView
{
    /**
     * Status code values produced by the calculated milestone.cam.status item.
     * See the template's item-prototype description for the full mapping.
     */
    private const STATUS_DISABLED  = -1;
    private const STATUS_OK        = 0;
    private const STATUS_ESS_ONLY  = 1;  // ESS comm fault, ping OK
    private const STATUS_PING_ONLY = 2;  // ping down, ESS still says OK
    private const STATUS_BOTH      = 3;  // ping down + ESS comm fault

    /**
     * Item-key prefixes we look up. The Milestone template produces keys of
     * shape 'milestone.cam.<facet>[<guid>]' for each camera, where <facet>
     * is one of 'status' (calculated combined state), 'mac' (HW MAC), and
     * 'address' (current IPv4).
     *
     * Note: 'startSearch' anchors at the start of key_, so these don't
     * accidentally match 'milestone.cam.status_extra' or similar — and the
     * Milestone template doesn't define such keys anyway.
     */
    private const KEY_STATUS_PREFIX  = 'milestone.cam.status';
    private const KEY_MAC_PREFIX     = 'milestone.cam.mac';
    private const KEY_ADDRESS_PREFIX = 'milestone.cam.address';

    protected function doAction(): void
    {
        $max_rows = (int)($this->fields_values['max_rows'] ?? 100);

        // Resolve which hosts to query.
        //   - On regular dashboards, the user picks hosts in the form;
        //     they arrive in fields_values['hostids'].
        //   - On template dashboards, fields_values has no 'hostids'
        //     (the form omits the field) — instead, the dashboard's
        //     bound host arrives via the dynamic-host data type, which
        //     the framework writes into fields_values['hostids'] when
        //     the widget viewer resolves it. We accept either.
        $hostids = [];
        if (!empty($this->fields_values['hostids'])) {
            $hostids = (array)$this->fields_values['hostids'];
        }

        $payload = [
            'name'    => $this->getInput('name', $this->widget->getDefaultName()),
            'summary' => [
                'total'      => 0,
                'ok'         => 0,
                'ess_only'   => 0,
                'ping_only'  => 0,
                'both'       => 0,
                'disabled'   => 0,
                'no_data'    => 0,
            ],
            'rows'      => [],
            'truncated' => false,
            'error'     => null,
            'debug_info' => [
                '_VERSION_MARKER'    => 'v6-2026-04-30-mac-ip-clickable',
                'hostids_queried'    => count($hostids),
                'items_found'        => 0,
                'phase'              => 'init',
                'fields_values_keys' => array_keys($this->fields_values ?? []),
                'raw_hostids_input'  => $this->fields_values['hostids'] ?? null,
            ],
            'user'      => ['debug_mode' => $this->getDebugMode()],
        ];

        if (!$hostids) {
            $payload['error'] = _('No host selected.');
            $this->setResponse(new CControllerResponseData($payload));
            return;
        }

        // ------------------------------------------------------------------
        // Single API call: every milestone.cam.status[*] item on the
        // selected hosts. Status items are numeric (calculated), so the
        // history-manager populates lastvalue from history_uint with one
        // batched lookup — no N+1.
        //
        // Filter notes:
        //   - 'search' with 'startSearch' anchors a substring match at
        //     the start of key_, matching e.g. 'milestone.cam.status[abc'.
        //   - We do NOT pass 'monitored=true'. That filter requires the
        //     whole host->template->item chain to be enabled, but
        //     LLD-discovered items can be enabled even when the LLD rule
        //     is briefly unsupported, and some sites disable specific
        //     hosts during maintenance — we want to see those cameras
        //     anyway.
        // ------------------------------------------------------------------
        try {
            $payload['debug_info']['phase'] = 'querying_items';
            $items = API::Item()->get([
                'output'       => ['itemid', 'hostid', 'name', 'key_', 'lastvalue', 'lastclock'],
                'selectHosts'  => ['hostid', 'host', 'name'],
                'hostids'      => $hostids,
                'search'       => ['key_' => self::KEY_STATUS_PREFIX],
                'startSearch'  => true,
                'preservekeys' => true,
            ]);
        } catch (\Throwable $e) {
            $payload['debug_info']['phase']     = 'item_query_failed';
            $payload['debug_info']['exception'] = $e->getMessage();
            $payload['error'] = _('Item API call failed: ').$e->getMessage();
            $this->setResponse(new CControllerResponseData($payload));
            return;
        }

        $payload['debug_info']['items_found'] = is_array($items) ? count($items) : 0;
        $payload['debug_info']['phase']       = 'items_returned';

        if (!$items) {
            $this->setResponse(new CControllerResponseData($payload));
            return;
        }

        // ------------------------------------------------------------------
        // Tally summary counts and build fault-row list in a single pass.
        // ------------------------------------------------------------------
        $rows            = [];
        $diag_lastvalues = [];

        foreach ($items as $itemid => $item) {
            $host = $item['hosts'][0] ?? null;
            if (!$host) {
                continue;
            }

            // Parse lastvalue. The API returns it as a string (or null
            // if the item has no history yet). Empty-string and null
            // both mean "no data". Numeric strings cast to int cleanly.
            $raw_lv    = $item['lastvalue'] ?? null;
            $parsed_lv = ($raw_lv === null || $raw_lv === '')
                ? null
                : (int)$raw_lv;

            if (count($diag_lastvalues) < 3) {
                $diag_lastvalues[] = [
                    'raw'    => $raw_lv,
                    'parsed' => $parsed_lv,
                ];
            }

            $payload['summary']['total']++;
            if ($parsed_lv === null) {
                $payload['summary']['no_data']++;
                continue;
            }
            switch ($parsed_lv) {
                case self::STATUS_DISABLED:
                    $payload['summary']['disabled']++;
                    break;
                case self::STATUS_OK:
                    $payload['summary']['ok']++;
                    break;
                case self::STATUS_ESS_ONLY:
                    $payload['summary']['ess_only']++;
                    break;
                case self::STATUS_PING_ONLY:
                    $payload['summary']['ping_only']++;
                    break;
                case self::STATUS_BOTH:
                    $payload['summary']['both']++;
                    break;
            }

            // Only fault states make it into the table.
            if (in_array((int)$parsed_lv, [self::STATUS_ESS_ONLY, self::STATUS_PING_ONLY, self::STATUS_BOTH], true)) {
                // Extract GUID from key 'milestone.cam.status[<guid>]' so we
                // can correlate with sibling mac/address items below.
                $guid = '';
                if (preg_match('/\[([^\]]+)\]\s*$/u', (string)$item['key_'], $km)) {
                    $guid = $km[1];
                }

                $rows[] = [
                    'host_name' => $host['name'],
                    'host_tech' => $host['host'],
                    'hostid'    => $host['hostid'],
                    'itemid'    => $itemid,
                    'cam_name'  => self::cleanItemName($item['name']),
                    'cam_guid'  => $guid,
                    'mac'       => null,
                    'ip'        => null,
                    'status'    => $parsed_lv,
                    'lastclock' => (int)$item['lastclock'],
                ];
            }
        }

        $payload['debug_info']['lastvalue_samples'] = $diag_lastvalues;

        // ------------------------------------------------------------------
        // Second pass: fetch MAC and IP items for the hosts that have at
        // least one fault row, and attach by GUID. We only do this for
        // fault rows since OK cameras aren't displayed — no need to
        // enrich them.
        //
        // Both prefixes share a single API call where possible. We pass
        // both keys to 'search.key_' as separate elements; Zabbix's API
        // doesn't support an OR of substrings in one filter, so we issue
        // two queries and merge.
        // ------------------------------------------------------------------
        $fault_hostids = array_values(array_unique(array_map(
            static fn(array $r): string => (string)$r['hostid'],
            $rows
        )));

        if ($fault_hostids) {
            $enrich_map = []; // [hostid][guid] => ['mac' => ..., 'ip' => ...]
            try {
                $payload['debug_info']['phase'] = 'querying_enrich_items';
                $enrich_items = API::Item()->get([
                    'output'      => ['itemid', 'hostid', 'key_', 'lastvalue'],
                    'hostids'     => $fault_hostids,
                    'search'      => ['key_' => [self::KEY_MAC_PREFIX, self::KEY_ADDRESS_PREFIX]],
                    'startSearch' => true,
                    'searchByAny' => true,
                ]);
                $payload['debug_info']['enrich_items_found'] = is_array($enrich_items)
                    ? count($enrich_items) : 0;
            } catch (\Throwable $e) {
                // Non-fatal — just leave mac/ip empty. The fault table is
                // still useful without these columns.
                $payload['debug_info']['enrich_exception'] = $e->getMessage();
                $enrich_items = [];
            }

            foreach ($enrich_items as $eitem) {
                $key  = (string)($eitem['key_'] ?? '');
                $hid  = (string)($eitem['hostid'] ?? '');
                $val  = $eitem['lastvalue'] ?? null;
                if ($val === null || $val === '') continue;
                if (!preg_match('/\[([^\]]+)\]\s*$/u', $key, $km)) continue;
                $guid = $km[1];

                if (!isset($enrich_map[$hid][$guid])) {
                    $enrich_map[$hid][$guid] = ['mac' => null, 'ip' => null];
                }
                if (strncmp($key, self::KEY_MAC_PREFIX, strlen(self::KEY_MAC_PREFIX)) === 0) {
                    $enrich_map[$hid][$guid]['mac'] = self::normalizeMac((string)$val);
                } elseif (strncmp($key, self::KEY_ADDRESS_PREFIX, strlen(self::KEY_ADDRESS_PREFIX)) === 0) {
                    $enrich_map[$hid][$guid]['ip'] = trim((string)$val);
                }
            }

            foreach ($rows as &$r) {
                $hid  = (string)$r['hostid'];
                $guid = (string)($r['cam_guid'] ?? '');
                if ($guid === '' || !isset($enrich_map[$hid][$guid])) continue;
                $r['mac'] = $enrich_map[$hid][$guid]['mac'];
                $r['ip']  = $enrich_map[$hid][$guid]['ip'];
            }
            unset($r);
        }

        // Stable sort: severity desc (3 before 2 before 1), then host name,
        // then camera name. Worst problems at the top.
        usort($rows, static function (array $a, array $b): int {
            return [$b['status'], $a['host_name'], $a['cam_name']]
                <=> [$a['status'], $b['host_name'], $b['cam_name']];
        });

        if (count($rows) > $max_rows) {
            $rows = array_slice($rows, 0, $max_rows);
            $payload['truncated']    = true;
            $payload['truncated_at'] = $max_rows;
        }
        $payload['rows']                = $rows;
        $payload['debug_info']['phase'] = 'done';

        $this->setResponse(new CControllerResponseData($payload));
    }

    /**
     * Trim "Cam [name]: Status (combined)" -> "name" for compact display.
     */
    private static function cleanItemName(string $item_name): string
    {
        if (preg_match('/^Cam \[(.+)\]: Status/u', $item_name, $m)) {
            return $m[1];
        }
        return $item_name;
    }

    /**
     * Normalize a MAC string to lowercase colon-separated form. Returns
     * null if the value doesn't parse cleanly. Accepts the common
     * Milestone-template variants: aa:bb:cc:dd:ee:ff, AA-BB-CC-DD-EE-FF,
     * aabbccddeeff. Whitespace and trailing separators are tolerated.
     */
    private static function normalizeMac(string $raw): ?string
    {
        $s = strtolower(trim($raw));
        // Strip everything that isn't a hex digit, then re-insert colons.
        $hex = preg_replace('/[^0-9a-f]/', '', $s);
        if ($hex === null || strlen($hex) !== 12) {
            return null;
        }
        return implode(':', str_split($hex, 2));
    }
}
