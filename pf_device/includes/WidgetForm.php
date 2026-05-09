<?php
/**
 * Unified PacketFence widget form.
 *
 * Source mode picks how the widget gets the device(s) to look up:
 *   - 'event'      — listen for pf:deviceSelected / mcs:cameraSelected /
 *                    sw:portSelected (single device card per selection).
 *                    Used by camera, AP, and other source widgets that
 *                    already know the MAC + IP.
 *   - 'host_items' — bind to override-host, read SNMP index from a
 *                    sibling switch-port widget, look up the configured
 *                    MAC-list item on the host, and render one card per
 *                    learned MAC. The original switchport behavior.
 *
 * Switchport-only fields (override host, MAC-item prefix, DHCP fallback)
 * are always shown for now — there's no conditional-visibility primitive
 * in CWidgetForm, and these fields are simply ignored in event mode.
 */

declare(strict_types=1);

namespace Modules\PfDevice\Includes;

use Zabbix\Widgets\CWidgetForm;
use Zabbix\Widgets\Fields\{
    CWidgetFieldCheckBox,
    CWidgetFieldMultiSelectOverrideHost,
    CWidgetFieldRadioButtonList,
    CWidgetFieldTextBox
};

class WidgetForm extends CWidgetForm
{
    // Integer keys — CWidgetFieldRadioButtonList casts submitted values to
    // int during validation, so string keys (e.g. 'event') always fail.
    public const SOURCE_EVENT      = 0;
    public const SOURCE_HOST_ITEMS = 1;

    public function addFields(): self
    {
        return $this
            ->addField(
                (new CWidgetFieldRadioButtonList('source_mode', _('Source mode'), [
                    self::SOURCE_EVENT      => _('Selection event'),
                    self::SOURCE_HOST_ITEMS => _('Switch port (host items)'),
                ]))->setDefault(self::SOURCE_EVENT)
            )
            // Override-host binds the widget to whichever host was
            // broadcast by a sibling switch-port widget. Only used in
            // host_items mode; ignored in event mode.
            ->addField(
                new CWidgetFieldMultiSelectOverrideHost()
            )
            ->addField(
                (new CWidgetFieldTextBox('pf_url', _('PacketFence API URL')))
                    ->setDefault('https://packetfence.example.com:9999')
                    ->setFlags(CWidgetFieldTextBox::FLAG_NOT_EMPTY)
            )
            ->addField(
                (new CWidgetFieldTextBox('pf_admin_url', _('PacketFence admin UI URL')))
                    ->setDefault('https://packetfence.example.com:1443')
            )
            ->addField(
                (new CWidgetFieldTextBox('pf_username', _('Username')))
                    ->setDefault('admin')
                    ->setFlags(CWidgetFieldTextBox::FLAG_NOT_EMPTY)
            )
            ->addField(
                (new CWidgetFieldTextBox('pf_password', _('Password')))
                    ->setDefault('')
            )
            // host_items mode — Windows DHCP fallback for missing IPs.
            ->addField(
                (new CWidgetFieldTextBox('dhcp_host', _('DHCP server Zabbix host name')))
                    ->setDefault('')
            )
            ->addField(
                (new CWidgetFieldTextBox('dhcp_item_key', _('DHCP lease item key')))
                    ->setDefault('dhcp.leases')
            )
            // host_items mode — prefix for the MAC-list item key. Full key
            // is built as <prefix><snmpIndex>] (e.g. "port.mac.list[1001]").
            ->addField(
                (new CWidgetFieldTextBox('mac_item_prefix', _('MAC-list item key prefix')))
                    ->setDefault('port.mac.list[')
            )
            ->addField(
                (new CWidgetFieldCheckBox('verify_ssl', _('Verify TLS certificate')))
                    ->setDefault(0)
            )
            ->addField(
                (new CWidgetFieldCheckBox('show_debug', _('Show debug info')))->setDefault(0)
            );
    }
}
