<?php
/**
 * Milestone camera status widget — config dialog view.
 *
 * Renders the host multiselect and max-rows numeric input.
 *
 * Note: CWidgetField*View classes live in the GLOBAL namespace (unlike
 * the field model classes in Zabbix\Widgets\Fields\). Don't import them.
 *
 * @var CView $this
 * @var array $data
 */

declare(strict_types=0);

(new CWidgetFormView($data))
    ->addField(
        new CWidgetFieldMultiSelectHostView($data['fields']['hostids'])
    )
    ->addField(
        new CWidgetFieldIntegerBoxView($data['fields']['max_rows'])
    )
    ->show();
