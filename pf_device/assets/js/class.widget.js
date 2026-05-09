/**
 * Unified PacketFence widget — frontend class.
 *
 * Listens for selection from any of three event types so a single widget
 * works alongside switch-port, camera, and XIQ AP source widgets:
 *   - pf:deviceSelected      → unified event (preferred)
 *   - mcs:cameraSelected     → legacy camera event
 *   - sw:portSelected        → legacy switchport event
 *
 * In event mode the controller wants sel_mac / sel_ip / sel_name /
 * sel_host / sel_source. In host_items mode it wants sw_snmpIndex /
 * sw_hostid. We send whichever set has been received. If both have been
 * received in this session, we send both — the controller picks based on
 * its configured source_mode.
 *
 * Action buttons:
 *   - reevaluate_access / restart_switchport → POST to this widget's
 *     own widget.pf_device.action endpoint.
 *   - cycle_poe → POST to widget.portdetail.cyclepoe (the existing
 *     rConfig snippet runner). The button is only rendered when the
 *     server-side resolved a switch host + iface from PF locationlog.
 */

class WidgetPfDevice extends CWidget {

    // event-mode state
    #sel_mac    = null;
    #sel_ip     = null;
    #sel_name   = null;
    #sel_host   = null;
    #sel_source = null;

    // host_items-mode state (from sw:portSelected)
    #sw_hostid    = null;
    #sw_snmpIndex = null;

    #unifiedListener   = null;
    #cameraListener    = null;
    #portListener      = null;
    #actionListener    = null;

    onActivate() {
        // Restore the most recent selection from sessionStorage. We try
        // unified first, then legacy keys, so a dashboard reload puts
        // the operator back where they were.
        this.#restoreFromSession('pf_device_selection');
        this.#restoreFromSession('mcs_camera_selection');
        this.#restorePortFromSession();

        this.#unifiedListener = ({detail}) => this.#applyDeviceSelection(detail);
        document.addEventListener('pf:deviceSelected', this.#unifiedListener);

        this.#cameraListener = ({detail}) => this.#applyDeviceSelection(detail);
        document.addEventListener('mcs:cameraSelected', this.#cameraListener);

        this.#portListener = ({detail}) => this.#applyPortSelection(detail);
        document.addEventListener('sw:portSelected', this.#portListener);

        this.#actionListener = (e) => {
            const btn = e.target.closest('button.pf-action[data-pf-action]');
            if (!btn) return;
            if (!this._target || !this._target.contains(btn)) return;
            e.preventDefault();
            this.#runAction(btn);
        };
        document.addEventListener('click', this.#actionListener);
    }

    onDeactivate() {
        if (this.#unifiedListener) document.removeEventListener('pf:deviceSelected', this.#unifiedListener);
        if (this.#cameraListener)  document.removeEventListener('mcs:cameraSelected', this.#cameraListener);
        if (this.#portListener)    document.removeEventListener('sw:portSelected',    this.#portListener);
        if (this.#actionListener)  document.removeEventListener('click',              this.#actionListener);
        this.#unifiedListener = this.#cameraListener = this.#portListener = this.#actionListener = null;
    }

    #restoreFromSession(key) {
        try {
            const raw = sessionStorage.getItem(key);
            if (!raw) return;
            const detail = JSON.parse(raw);
            this.#applyDeviceSelection(detail, /*refresh=*/false);
        } catch (e) {}
    }

    #restorePortFromSession() {
        try {
            const raw = sessionStorage.getItem('sw_port_selection');
            if (!raw) return;
            const {hostid, snmpIndex} = JSON.parse(raw);
            this.#sw_hostid    = hostid    ?? null;
            this.#sw_snmpIndex = snmpIndex ?? null;
        } catch (e) {}
    }

    #applyDeviceSelection(detail, refresh = true) {
        if (!detail) return;
        this.#sel_mac    = detail.mac    ?? null;
        this.#sel_ip     = detail.ip     ?? null;
        this.#sel_name   = detail.name   ?? null;
        this.#sel_host   = detail.host   ?? null;
        this.#sel_source = detail.source ?? null;
        if (refresh && this.getState() === WIDGET_STATE_ACTIVE) {
            this._startUpdating();
        }
    }

    #applyPortSelection(detail) {
        if (!detail) return;
        this.#sw_hostid    = detail.hostid    ?? null;
        this.#sw_snmpIndex = detail.snmpIndex ?? null;
        if (this.getState() === WIDGET_STATE_ACTIVE) {
            this._startUpdating();
        }
    }

    getUpdateRequestData() {
        const data = super.getUpdateRequestData();
        if (this.#sel_mac    !== null) data.sel_mac    = this.#sel_mac;
        if (this.#sel_ip     !== null) data.sel_ip     = this.#sel_ip;
        if (this.#sel_name   !== null) data.sel_name   = this.#sel_name;
        if (this.#sel_host   !== null) data.sel_host   = this.#sel_host;
        if (this.#sel_source !== null) data.sel_source = this.#sel_source;
        if (this.#sw_snmpIndex !== null) data.sw_snmpIndex = this.#sw_snmpIndex;
        if (this.#sw_hostid    !== null) data.sw_hostid    = this.#sw_hostid;
        return data;
    }

    async #runAction(btn) {
        const action = btn.dataset.pfAction || '';

        if (action === 'cycle_poe') {
            return this.#runCyclePoe(btn);
        }

        const mac = btn.dataset.pfMac || '';
        if (!mac || !action) return;

        const confirmMsg = action === 'restart_switchport'
            ? `Restart the switch port for ${mac}? This briefly drops every device on that port.`
            : `Reevaluate PacketFence access for ${mac}?`;
        if (!window.confirm(confirmMsg)) return;

        const status = this._target.querySelector(`.pf-action-status[data-pf-status-for="${mac}"]`);
        const siblings = this._target.querySelectorAll(`button.pf-action[data-pf-mac="${mac}"]`);
        siblings.forEach(b => { b.disabled = true; });
        if (status) {
            status.textContent = '…working';
            status.className = 'pf-action-status pf-action-status--pending';
        }

        try {
            const url = new Curl('zabbix.php');
            url.setArgument('action', 'widget.pf_device.action');

            const body = WidgetPfDevice.#serializeWidgetRequest(this.getUpdateRequestData());
            body.append('mac',       mac);
            body.append('pf_action', action);

            const resp = await fetch(url.getUrl(), {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body
            });
            const data = await WidgetPfDevice.#parseActionResponse(resp);

            if (data && data.ok) {
                if (status) {
                    status.textContent = '✓ ' + (action === 'restart_switchport' ? 'port restarted' : 'access reevaluated');
                    status.className = 'pf-action-status pf-action-status--ok';
                }
            } else {
                const msg = (data && data.error) ? data.error : `HTTP ${resp.status}`;
                if (status) {
                    status.textContent = '✗ ' + msg;
                    status.className = 'pf-action-status pf-action-status--err';
                }
            }
        } catch (err) {
            if (status) {
                status.textContent = '✗ ' + (err.message || 'request failed');
                status.className = 'pf-action-status pf-action-status--err';
            }
        } finally {
            siblings.forEach(b => { b.disabled = false; });
        }
    }

    /**
     * Cycle PoE on the switch port the device is on. The button only
     * appears when the controller resolved a switch host + iface from
     * PF locationlog. Dispatched to the portdetail widget's existing
     * rConfig snippet action — different endpoint, different params.
     */
    async #runCyclePoe(btn) {
        const hostid    = btn.dataset.pfHostid;
        const snmpIndex = btn.dataset.pfSnmpIndex;
        const iface     = btn.dataset.pfIface;
        const card      = btn.closest('.pf-card');
        const status    = card ? card.querySelector('.pf-action-status') : null;

        if (!hostid || !iface) {
            if (status) {
                status.textContent = '✗ Missing switch context';
                status.className = 'pf-action-status pf-action-status--err';
            }
            return;
        }

        const ok = window.confirm(
            `Cycle PoE on ${iface}?\n\n` +
            'The device (and any other on this port) will lose power briefly. ' +
            'This is dispatched to rConfig as a snippet deployment.'
        );
        if (!ok) return;

        btn.disabled = true;
        if (status) {
            status.textContent = '…sending to rConfig';
            status.className = 'pf-action-status pf-action-status--pending';
        }

        try {
            const url = new Curl('zabbix.php');
            url.setArgument('action', 'widget.portdetail.cyclepoe');

            const params = new URLSearchParams();
            params.set('hostid',     hostid);
            params.set('snmp_index', snmpIndex || '0');
            params.set('iface_name', iface);

            const resp = await fetch(url.getUrl(), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Accept': 'application/json'
                },
                body: params.toString()
            });
            const json = await WidgetPfDevice.#parseActionResponse(resp);

            if (json && json.ok) {
                if (status) {
                    status.textContent = '✓ ' + (json.message || 'PoE cycle queued');
                    status.className = 'pf-action-status pf-action-status--ok';
                }
            } else {
                const msg = (json && json.message) ? json.message : `HTTP ${resp.status}`;
                if (status) {
                    status.textContent = '✗ ' + msg;
                    status.className = 'pf-action-status pf-action-status--err';
                }
            }
        } catch (err) {
            if (status) {
                status.textContent = '✗ ' + (err.message || 'request failed');
                status.className = 'pf-action-status pf-action-status--err';
            }
        } finally {
            btn.disabled = false;
        }
    }

    /**
     * Serialize getUpdateRequestData() into the URLSearchParams shape
     * Zabbix's standard widget refresh uses, so $this->fields_values
     * gets populated server-side from the saved widget config.
     */
    static #serializeWidgetRequest(data) {
        const body = new URLSearchParams();
        for (const [k, v] of Object.entries(data || {})) {
            if (v === undefined || v === null) continue;
            if (k === 'fields' && typeof v === 'object') {
                for (const [fk, fv] of Object.entries(v)) {
                    if (fv === undefined || fv === null) continue;
                    if (Array.isArray(fv)) {
                        fv.forEach(item => body.append(`fields[${fk}][]`, String(item)));
                    } else {
                        body.append(`fields[${fk}]`, String(fv));
                    }
                }
            } else {
                body.append(k, String(v));
            }
        }
        return body;
    }

    /**
     * Parse our action response. Zabbix sometimes wraps action JSON in
     * the standard HTML page chrome; try strict JSON first, then fall
     * back to extracting the embedded {"ok":...} object.
     */
    static async #parseActionResponse(resp) {
        const text = await resp.text();
        try {
            const outer = JSON.parse(text);
            return (outer && outer.main_block !== undefined)
                ? JSON.parse(outer.main_block)
                : outer;
        } catch (_) {}

        const start = text.indexOf('{"ok"');
        if (start !== -1) {
            let depth = 0, inStr = false, esc = false;
            for (let i = start; i < text.length; i++) {
                const ch = text[i];
                if (inStr) {
                    if (esc)        { esc = false; }
                    else if (ch === '\\') { esc = true; }
                    else if (ch === '"')  { inStr = false; }
                } else {
                    if (ch === '"')  inStr = true;
                    else if (ch === '{') depth++;
                    else if (ch === '}') {
                        depth--;
                        if (depth === 0) {
                            try { return JSON.parse(text.slice(start, i + 1)); }
                            catch (_) { return null; }
                        }
                    }
                }
            }
        }
        return null;
    }
}
