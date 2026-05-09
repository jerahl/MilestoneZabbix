<?php
/**
 * @var CView $this
 * @var array $data
 */

declare(strict_types=0);

(new CWidgetFormView($data))
    ->addField(
        new CWidgetFieldRadioButtonListView($data['fields']['source_mode'])
    )
    ->addField(
        new CWidgetFieldMultiSelectOverrideHostView($data['fields']['override_hostid'])
    )
    ->addField(
        new CWidgetFieldTextBoxView($data['fields']['pf_url'])
    )
    ->addField(
        new CWidgetFieldTextBoxView($data['fields']['pf_admin_url'])
    )
    ->addField(
        new CWidgetFieldTextBoxView($data['fields']['pf_username'])
    )
    ->addField(
        new CWidgetFieldTextBoxView($data['fields']['pf_password'])
    )
    ->addField(
        new CWidgetFieldTextBoxView($data['fields']['dhcp_host'])
    )
    ->addField(
        new CWidgetFieldTextBoxView($data['fields']['dhcp_item_key'])
    )
    ->addField(
        new CWidgetFieldTextBoxView($data['fields']['mac_item_prefix'])
    )
    ->addField(
        new CWidgetFieldCheckBoxView($data['fields']['verify_ssl'])
    )
    ->addField(
        new CWidgetFieldCheckBoxView($data['fields']['show_debug'])
    )
    ->show();
