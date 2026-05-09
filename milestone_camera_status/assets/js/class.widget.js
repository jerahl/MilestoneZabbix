/**
 * Milestone camera status widget — frontend class.
 *
 * Subclasses CWidget to receive update responses, then renders the
 * summary tiles and fault-row table directly into the existing DOM.
 *
 * Data flow note:
 *   Zabbix's widget framework only ships {name, body, debug} back to
 *   the JS — any extra keys returned by WidgetView::doAction() get
 *   stripped. So we don't read response.summary / response.rows here;
 *   instead, widget.view.php embeds the data payload as a JSON blob
 *   inside <script type="application/json" class="mcs-data">, and we
 *   parse that blob from the rendered body.
 *
 * Interactions:
 *   - Click a column header to sort by that column. Click again to reverse.
 *   - Click a fault tile (ESS fault, Ping down, Offline) to filter the
 *     table to just that severity. Click a different fault tile to switch.
 *     A "Show all" pill appears next to the table when a filter is active.
 *
 * Sort and filter state are held in instance state, so they survive
 * auto-refreshes but reset when the dashboard reloads (intentional —
 * operators don't usually want a sticky non-default view across days).
 */

class WidgetMilestoneCameraStatus extends CWidget {

    static VERSION_MARKER = 'js-v9-2026-04-30-no-host-column';

    // Map from tile data-field to the status code(s) that bucket holds.
    // Only fault buckets are clickable — OK, Disabled, No data tiles
    // exist for at-a-glance counts but don't represent table rows.
    static FILTERABLE_TILES = {
        ess_only:  1,
        ping_only: 2,
        both:      3,
    };

    onInitialize() {
        super.onInitialize();

        // Sort state. Default: severity desc, then host name, then camera name.
        // Matches what the backend returns, so initial render is stable
        // even if the user never clicks a header.
        this._sort_key = 'status';
        this._sort_dir = 'desc';

        // Filter state. null = show all rows. Otherwise an integer status code.
        this._filter_status = null;

        // Hold the latest payload so a header or tile click can re-render
        // without an extra server round-trip.
        this._latest = null;
    }

    setContents(response) {
        super.setContents(response);

        // First-time setup: wire up handlers exactly once. setContents
        // fires on every refresh, so guard with _wired.
        if (!this._wired) {
            this._wireSortHandlers();
            this._wireFilterHandlers();
            this._wireRowClickHandler();
            this._wired = true;
        }

        const payload = this._readPayload();
        if (!payload) return;
        this._latest = payload;
        this._render(payload);
    }

    _readPayload() {
        if (!this._body) return null;
        const node = this._body.querySelector('script.mcs-data');
        if (!node) return null;
        try {
            return JSON.parse(node.textContent || '{}');
        } catch (e) {
            console.error('mcs: failed to parse data blob', e);
            return null;
        }
    }

    _wireSortHandlers() {
        const ths = this._body && this._body.querySelectorAll('th[data-sort]');
        if (!ths) return;
        ths.forEach((th) => {
            th.addEventListener('click', () => {
                const key = th.getAttribute('data-sort');
                if (key === this._sort_key) {
                    this._sort_dir = (this._sort_dir === 'asc') ? 'desc' : 'asc';
                } else {
                    this._sort_key = key;
                    // Numeric / time fields default to descending; string
                    // fields default to ascending. Most useful default for
                    // each column type.
                    this._sort_dir = (key === 'status' || key === 'lastclock') ? 'desc' : 'asc';
                }
                if (this._latest) this._render(this._latest);
            });
        });
    }

    _wireFilterHandlers() {
        if (!this._body) return;
        Object.keys(WidgetMilestoneCameraStatus.FILTERABLE_TILES).forEach((field) => {
            const tile = this._body.querySelector(`.mcs-tile[data-field="${field}"]`);
            if (!tile) return;
            // Mark tile as clickable for CSS hover effects and a11y.
            tile.classList.add('mcs-tile--clickable');
            tile.setAttribute('role', 'button');
            tile.setAttribute('tabindex', '0');

            const onActivate = () => {
                const code = WidgetMilestoneCameraStatus.FILTERABLE_TILES[field];
                // Clicking the active filter tile is a no-op (per the
                // chosen interaction: switch by clicking another tile,
                // clear via the dedicated affordance). This avoids
                // confusing toggle behavior when an operator is trying
                // to re-confirm what filter is on.
                if (this._filter_status === code) return;
                this._filter_status = code;
                if (this._latest) this._render(this._latest);
            };
            tile.addEventListener('click', onActivate);
            tile.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onActivate();
                }
            });
        });
    }

    _clearFilter() {
        this._filter_status = null;
        if (this._latest) this._render(this._latest);
    }

    _render(payload) {
        const summary = payload.summary || {};
        const rows = payload.rows || [];
        const error = payload.error || null;

        // -------- Summary tiles --------
        const tile_fields = ['total', 'ok', 'ess_only', 'ping_only', 'both', 'disabled', 'no_data'];
        tile_fields.forEach((f) => {
            const tile = this._body.querySelector(`.mcs-tile[data-field="${f}"]`);
            if (!tile) return;
            const v = tile.querySelector('.mcs-tile-value');
            const n = Number(summary[f] || 0);
            v.textContent = n.toLocaleString();
            // Highlight fault tiles with non-zero counts.
            if (['ess_only', 'ping_only', 'both'].includes(f)) {
                tile.classList.toggle('mcs-tile--active', n > 0);
            }
            // Mark the currently-selected filter tile so CSS can style it.
            const code = WidgetMilestoneCameraStatus.FILTERABLE_TILES[f];
            tile.classList.toggle(
                'mcs-tile--selected',
                code !== undefined && code === this._filter_status
            );
        });

        // -------- Empty / error state --------
        const empty = this._body.querySelector('.mcs-empty');
        const empty_msg = empty.querySelector('.mcs-empty-msg');
        const wrap = this._body.querySelector('.mcs-table-wrap');

        if (error) {
            empty_msg.textContent = error;
            empty.removeAttribute('hidden');
            wrap.style.display = 'none';
            this._renderFilterBar(wrap, false);
            return;
        }

        // Apply the filter to row data. We do this after the tiles are
        // updated so the bucket counts always reflect the full dataset,
        // not the filtered subset — operators need to see the totals
        // regardless of what they're currently drilled into.
        const visible_rows = (this._filter_status === null)
            ? rows
            : rows.filter((r) => r.status === this._filter_status);

        if (!visible_rows.length) {
            // Different message depending on whether the empty list is
            // because there are no faults at all, or because the filter
            // is hiding them.
            if (this._filter_status !== null && rows.length > 0) {
                empty_msg.textContent = this._t('No cameras match this filter.');
            } else {
                empty_msg.textContent = this._t('No camera faults.');
            }
            empty.removeAttribute('hidden');
            wrap.style.display = 'none';
            this._renderFilterBar(wrap, this._filter_status !== null);
            return;
        }
        empty.setAttribute('hidden', 'hidden');
        wrap.style.display = '';

        // -------- Filter bar (above the table) --------
        this._renderFilterBar(wrap, this._filter_status !== null);

        // -------- Fault table --------
        const sorted = this._sortRows(visible_rows);
        // Stash the sorted list so the delegated row-click handler can
        // resolve a clicked <tr>'s data-row-index back to the row object.
        this._visible_rows = sorted;
        const tbody = this._body.querySelector('.mcs-rows');
        tbody.innerHTML = sorted.map((r, i) => this._rowHtml(r, i)).join('');

        // -------- Sort indicator on header --------
        const ths = this._body.querySelectorAll('th[data-sort]');
        ths.forEach((th) => {
            th.classList.remove('mcs-sort-asc', 'mcs-sort-desc');
            if (th.getAttribute('data-sort') === this._sort_key) {
                th.classList.add('mcs-sort-' + this._sort_dir);
            }
        });

        // -------- Truncation notice --------
        // Note: truncation flags reflect the unfiltered row list (the
        // server caps before sending). If a filter is active and would
        // hide some of the truncated rows anyway, we still surface the
        // notice — better to over-warn than have the operator wonder
        // why a known fault isn't showing.
        let trunc_el = wrap.querySelector('.mcs-truncated');
        if (payload.truncated) {
            if (!trunc_el) {
                trunc_el = document.createElement('div');
                trunc_el.className = 'mcs-truncated';
                wrap.appendChild(trunc_el);
            }
            trunc_el.textContent = this._t('Showing first {0} rows. Increase Max table rows in widget config to see more.')
                .replace('{0}', payload.truncated_at);
        } else if (trunc_el) {
            trunc_el.remove();
        }
    }

    /**
     * Show or hide the "Show all" pill above the table. Created lazily on
     * first need; kept in the DOM but hidden when no filter is active so
     * we don't churn nodes on every refresh.
     */
    _renderFilterBar(wrap_el, filter_active) {
        let bar = wrap_el.querySelector('.mcs-filter-bar');
        if (!bar) {
            bar = document.createElement('div');
            bar.className = 'mcs-filter-bar';

            const label = document.createElement('span');
            label.className = 'mcs-filter-label';
            bar.appendChild(label);

            const clear_btn = document.createElement('button');
            clear_btn.type = 'button';
            clear_btn.className = 'mcs-filter-clear';
            clear_btn.textContent = this._t('Show all');
            clear_btn.addEventListener('click', () => this._clearFilter());
            bar.appendChild(clear_btn);

            // Insert at the top of the table-wrap so it sits above the
            // table rows but inside the same scroll context.
            wrap_el.insertBefore(bar, wrap_el.firstChild);
        }

        if (!filter_active) {
            bar.setAttribute('hidden', 'hidden');
            return;
        }
        bar.removeAttribute('hidden');

        // Update the label to name the active filter.
        const label_text = {
            1: this._t('Filtered: ESS fault'),
            2: this._t('Filtered: Ping down'),
            3: this._t('Filtered: Offline'),
        }[this._filter_status] || this._t('Filtered');
        bar.querySelector('.mcs-filter-label').textContent = label_text;
    }

    _sortRows(rows) {
        const key = this._sort_key;
        const dir = this._sort_dir;
        const mult = (dir === 'asc') ? 1 : -1;
        const copy = rows.slice();
        copy.sort((a, b) => {
            let av = a[key], bv = b[key];
            if (typeof av === 'string' && typeof bv === 'string') {
                return av.localeCompare(bv) * mult;
            }
            av = (av == null) ? -Infinity : av;
            bv = (bv == null) ? -Infinity : bv;
            if (av < bv) return -1 * mult;
            if (av > bv) return  1 * mult;
            return 0;
        });
        return copy;
    }

    /**
     * Delegated click handler on the tbody. We stop the listener from
     * firing on clicks inside the inner <a> elements so the existing host
     * and item links still navigate normally — only clicks on bare row
     * background or on the MAC/IP/severity cells trigger the broadcast.
     */
    _wireRowClickHandler() {
        if (!this._body) return;
        const tbody = this._body.querySelector('.mcs-rows');
        if (!tbody) return;
        tbody.addEventListener('click', (ev) => {
            // Let inner anchors do their normal navigation.
            if (ev.target.closest('a')) return;

            const tr = ev.target.closest('tr[data-row-index]');
            if (!tr) return;
            const idx = Number(tr.getAttribute('data-row-index'));
            const row = (this._visible_rows || [])[idx];
            if (!row) return;

            this._dispatchCameraSelected(row);
        });
    }

    /**
     * Persist the selection in sessionStorage so the companion widget can
     * restore it after a dashboard reload, then dispatch the DOM event
     * the companion listens for.
     */
    _dispatchCameraSelected(row) {
        const detail = {
            hostid: row.hostid,
            mac:    row.mac || null,
            ip:     row.ip  || null,
            name:   row.cam_name,
            host:   row.host_name,
        };
        try {
            sessionStorage.setItem('mcs_camera_selection', JSON.stringify(detail));
        } catch (e) {}
        document.dispatchEvent(new CustomEvent('mcs:cameraSelected', {detail}));
    }

    _rowHtml(r, idx) {
        const status_cls = {
            1: 'mcs-pill--ess',
            2: 'mcs-pill--ping',
            3: 'mcs-pill--both',
        }[r.status] || 'mcs-pill--unknown';
        const status_label = {
            1: this._t('ESS fault'),
            2: this._t('Ping down'),
            3: this._t('Offline'),
        }[r.status] || String(r.status);

        const last_check = r.lastclock
            ? new Date(r.lastclock * 1000).toLocaleString()
            : '—';

        // Item link on the camera name jumps to latest data so operators
        // can drill in without leaving the dashboard. (Host column was
        // removed — the host name is still broadcast in the click event
        // for companion widgets, just not displayed in the table.)
        const item_url = `history.php?action=showvalues&itemids[]=${encodeURIComponent(r.itemid)}`;

        const mac = r.mac ? this._esc(r.mac) : '<span class="mcs-dim">—</span>';
        const ip  = r.ip  ? this._esc(r.ip)  : '<span class="mcs-dim">—</span>';

        return [
            `<tr data-row-index="${idx}" class="mcs-row-clickable" title="${this._esc(this._t('Click to look up in PacketFence'))}">`,
                `<td><span class="mcs-pill ${status_cls}">${this._esc(status_label)}</span></td>`,
                `<td><a href="${this._esc(item_url)}" target="_blank" rel="noopener">${this._esc(r.cam_name)}</a></td>`,
                `<td class="mcs-mono">${mac}</td>`,
                `<td class="mcs-mono">${ip}</td>`,
                `<td class="mcs-mono mcs-dim">${this._esc(last_check)}</td>`,
            '</tr>'
        ].join('');
    }

    _esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    _t(s) {
        return (typeof t === 'function') ? t(s) : s;
    }
}
