<?php
/**
 * Milestone camera status widget — config form.
 *
 * Two fields:
 *   - hostids:  hosts to scope the search (multiselect; required)
 *   - max_rows: cap on table rows (numeric; default 100)
 *
 * This widget is intended for regular dashboards. Template-dashboard
 * use is not currently supported because we don't depend on per-host
 * dynamic binding (the host multiselect is always shown).
 */

declare(strict_types=1);

namespace Modules\MilestoneCameraStatus\Includes;

use Zabbix\Widgets\{
    CWidgetField,
    CWidgetForm
};
use Zabbix\Widgets\Fields\{
    CWidgetFieldMultiSelectHost,
    CWidgetFieldIntegerBox
};

class WidgetForm extends CWidgetForm
{
    public function addFields(): self
    {
        return $this
            ->addField(
                (new CWidgetFieldMultiSelectHost('hostids', _('Hosts')))
                    ->setFlags(CWidgetField::FLAG_NOT_EMPTY | CWidgetField::FLAG_LABEL_ASTERISK)
            )
            ->addField(
                (new CWidgetFieldIntegerBox('max_rows', _('Max table rows'), 1, 5000))
                    ->setDefault(100)
            );
    }
}
