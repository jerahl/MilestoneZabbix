<?php
/**
 * Unified PacketFence widget — per-node action endpoint.
 *
 * Issues PUT /api/v1/node/{mac}/{action} for the action buttons on a card
 * (Reevaluate access / Restart switchport). Cycle PoE is dispatched by the
 * JS class to the portdetail widget's existing rConfig action — this
 * endpoint does not handle it.
 *
 * Response shape (returned via main_block JSON):
 *   { ok: bool, http_code: int|null, error: string|null, data: array|null,
 *     action: string, mac: string }
 */

declare(strict_types=0);

namespace Modules\PfDevice\Actions;

use CControllerDashboardWidgetView;
use CControllerResponseData;
use Modules\PfDevice\Includes\PfClient;

class WidgetAction extends CControllerDashboardWidgetView
{
    private const ALLOWED_ACTIONS = ['reevaluate_access', 'restart_switchport'];

    protected function init(): void
    {
        parent::init();
        $this->addValidationRules([
            'mac'       => 'string',
            'pf_action' => 'string',
        ]);
    }

    protected function doAction(): void
    {
        $mac    = strtolower(trim((string)$this->getInput('mac', '')));
        $action = (string)$this->getInput('pf_action', '');

        if (!preg_match('/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/', $mac)) {
            $this->respondJson(['ok' => false, 'error' => 'Invalid MAC address']);
            return;
        }
        if (!in_array($action, self::ALLOWED_ACTIONS, true)) {
            $this->respondJson(['ok' => false, 'error' => 'Unknown action']);
            return;
        }

        $pf_url     = rtrim((string)($this->fields_values['pf_url']      ?? ''), '/');
        $pf_user    = (string)($this->fields_values['pf_username'] ?? '');
        $pf_pass    = (string)($this->fields_values['pf_password'] ?? '');
        $verify_ssl = (bool)  ($this->fields_values['verify_ssl']  ?? false);

        if (!$pf_url || !$pf_user || !$pf_pass) {
            $this->respondJson(['ok' => false, 'error' => 'PacketFence URL/credentials not configured']);
            return;
        }

        $login = PfClient::login($pf_url, $pf_user, $pf_pass, $verify_ssl);
        if (!$login['ok']) {
            $this->respondJson([
                'ok'    => false,
                'error' => 'PacketFence login failed: ' . ($login['error'] ?? 'unknown'),
            ]);
            return;
        }

        $url = $pf_url . '/api/v1/node/' . rawurlencode($mac) . '/' . $action;
        $r   = PfClient::request($url, 'PUT', $login['token'], null, $verify_ssl, 30);

        $this->respondJson([
            'ok'        => $r['ok'],
            'http_code' => $r['http_code'],
            'error'     => $r['error'],
            'data'      => $r['data'],
            'action'    => $action,
            'mac'       => $mac,
        ]);
    }

    private function respondJson(array $payload): void
    {
        $this->setResponse(new CControllerResponseData(['main_block' => json_encode($payload)]));
    }
}
