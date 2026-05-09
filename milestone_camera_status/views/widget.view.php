<?php
/**
 * Milestone camera status widget — view template.
 *
 * Server-side renders the summary tiles, table shell, AND a JSON blob
 * carrying the data payload from WidgetView::doAction(). The JS reads
 * the JSON blob on every update to repopulate tiles and rows.
 *
 * Why JSON-in-HTML instead of returning data alongside the body:
 *   Zabbix's widget framework wraps the rendered view in {name, body,
 *   debug} for the AJAX response and ignores any additional data fields
 *   the controller's CControllerResponseData puts in $data. So putting
 *   custom data on the response is a dead end — the only reliable way
 *   to ferry data from PHP to the widget's JS is to render it into the
 *   HTML body and have the JS extract it client-side.
 *
 *   We use <script type="application/json"> rather than data attributes
 *   because the rows array can get large and base64/attribute encoding
 *   would be slower than a single JSON.parse.
 *
 * @var CView $this
 * @var array $data
 */

declare(strict_types=0);

$body = (new CDiv())->addClass('mcs-body');

// ----------------------------------------------------------------------
// JSON data blob. The JS finds this by class on every setContents call,
// parses it, and uses it to fill summary tiles and the fault table.
//
// Keys we hand off (must match what JS expects):
//   - summary: object of count buckets
//   - rows: array of fault rows
//   - error: string or null
//   - truncated, truncated_at: pagination flags
//   - debug_info: diagnostic block (kept until widget is fully trusted)
// ----------------------------------------------------------------------
$payload = [
    'summary'     => $data['summary']     ?? [],
    'rows'        => $data['rows']        ?? [],
    'error'       => $data['error']       ?? null,
    'truncated'   => $data['truncated']   ?? false,
    'truncated_at'=> $data['truncated_at']?? null,
    'debug_info'  => $data['debug_info']  ?? null,
];

// JSON_HEX_TAG protects against </script> appearing in any string value
// (e.g. a hostile camera name); JSON_UNESCAPED_UNICODE keeps the wire
// payload smaller and avoids surprises with non-ASCII host names.
$body->addItem(
    (new CTag('script', true, json_encode(
        $payload,
        JSON_HEX_TAG | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
    )))
        ->setAttribute('type', 'application/json')
        ->addClass('mcs-data')
);

// ----------------------------------------------------------------------
// Summary tile row. Numbers are filled by JS; we only render structure
// here so the very first paint doesn't show raw zeros prematurely — the
// JS clears these placeholders on first update.
// ----------------------------------------------------------------------
$summary = (new CDiv())->addClass('mcs-summary');
foreach ([
    'total'     => [_('Total'),       'mcs-tile--total'],
    'ok'        => [_('OK'),          'mcs-tile--ok'],
    'ess_only'  => [_('ESS fault'),   'mcs-tile--ess'],
    'ping_only' => [_('Ping down'),   'mcs-tile--ping'],
    'both'      => [_('Offline'),     'mcs-tile--both'],
    'disabled'  => [_('Disabled'),    'mcs-tile--disabled'],
    'no_data'   => [_('No data'),     'mcs-tile--nodata'],
] as $field => [$label, $cls]) {
    $summary->addItem(
        (new CDiv())
            ->addClass('mcs-tile')
            ->addClass($cls)
            ->setAttribute('data-field', $field)
            ->addItem((new CDiv('—'))->addClass('mcs-tile-value'))
            ->addItem((new CDiv($label))->addClass('mcs-tile-label'))
    );
}
$body->addItem($summary);

// ----------------------------------------------------------------------
// Fault table — header rendered server-side, body filled by JS.
// Schema: Severity, Camera, MAC, IP, Last check.
// ----------------------------------------------------------------------
$thead = (new CTag('thead', true))->addItem(
    (new CTag('tr', true))
        ->addItem((new CTag('th', true, _('Severity')))->setAttribute('data-sort', 'status'))
        ->addItem((new CTag('th', true, _('Camera')))->setAttribute('data-sort', 'cam_name'))
        ->addItem((new CTag('th', true, _('MAC')))->setAttribute('data-sort', 'mac'))
        ->addItem((new CTag('th', true, _('IP')))->setAttribute('data-sort', 'ip'))
        ->addItem((new CTag('th', true, _('Last check')))->setAttribute('data-sort', 'lastclock'))
);
$tbody = (new CTag('tbody', true))->addClass('mcs-rows');

$table = (new CTag('table', true))
    ->addClass('mcs-table')
    ->addItem($thead)
    ->addItem($tbody);

$body->addItem(
    (new CDiv())
        ->addClass('mcs-table-wrap')
        ->addItem($table)
);

// Empty/error state placeholder, hidden by default and shown by JS when
// payload.rows is empty or payload.error is set.
$body->addItem(
    (new CDiv())
        ->addClass('mcs-empty')
        ->setAttribute('hidden', 'hidden')
        ->addItem((new CDiv())->addClass('mcs-empty-msg'))
);

(new CWidgetView($data))
    ->addItem($body)
    ->show();
