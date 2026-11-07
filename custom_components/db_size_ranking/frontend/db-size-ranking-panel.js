// Panel de DB Size Ranking: ranking de entidades con borrado y exclusión.
// Componente web sin dependencias (no necesita compilación).

const DOMAIN = "db_size_ranking";
const REFRESH_AFTER_PURGE_MS = 65000;

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

const mib = (bytes) => (bytes / 1048576).toFixed(bytes >= 10485760 ? 1 : 2);

class DbSizeRankingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._period = "total";
    this._topN = 50;
    this._data = null;
    this._selected = new Set();
    this._busy = false;
    this._message = null; // {text, kind: "info" | "ok" | "error"}
    this._yaml = "";
    this._loaded = false;
    this._timer = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._render();
      this._load();
    }
  }

  set narrow(value) {
    this._narrow = value;
    if (this._loaded) this._render();
  }

  disconnectedCallback() {
    clearTimeout(this._timer);
  }

  // --- llamadas a Home Assistant ------------------------------------------

  async _call(domain, service, data = {}, returnResponse = false) {
    const msg = { type: "call_service", domain, service, service_data: data };
    if (returnResponse) msg.return_response = true;
    const result = await this._hass.connection.sendMessagePromise(msg);
    return result?.response;
  }

  async _load() {
    this._busy = true;
    this._render();
    try {
      this._data = await this._call(
        DOMAIN, "get_ranking", { period: this._period, top_n: this._topN }, true
      );
      const ids = new Set(this._data.ranking.map((e) => e.entity_id));
      this._selected = new Set([...this._selected].filter((id) => ids.has(id)));
    } catch (err) {
      this._message = { kind: "error", text: `No se pudo leer el ranking: ${err.message || err}` };
    }
    this._busy = false;
    this._render();
  }

  async _run(label, fn) {
    this._busy = true;
    this._message = { kind: "info", text: label };
    this._render();
    try {
      await fn();
    } catch (err) {
      this._message = { kind: "error", text: `Error: ${err.message || err}` };
    }
    this._busy = false;
    this._render();
  }

  _scheduleReload() {
    clearTimeout(this._timer);
    this._pendingReload = true;
    this._timer = setTimeout(() => {
      this._pendingReload = false;
      this._load();
    }, REFRESH_AFTER_PURGE_MS);
  }

  _purgeSelected() {
    const items = this._data.ranking.filter((e) => this._selected.has(e.entity_id));
    if (!items.length) return;
    const freed = items.reduce((s, e) => s + e.bytes_states + e.bytes_attributes, 0);
    if (!confirm(
      `¿Borrar el historial de ${items.length} entidades (~${mib(freed)} MiB)?\n\n` +
      "No se puede deshacer. Las estadísticas a largo plazo se conservan."
    )) return;
    this._run("Borrando historial…", async () => {
      await this._call("recorder", "purge_entities", {
        entity_id: items.map((e) => e.entity_id), keep_days: 0,
      });
      this._data.ranking = this._data.ranking.filter((e) => !this._selected.has(e.entity_id));
      this._selected.clear();
      this._message = {
        kind: "ok",
        text: `Historial de ${items.length} entidades borrado (~${mib(freed)} MiB). ` +
          "El ranking se recalculará en un minuto. Pulsa «Compactar» para que el archivo encoja.",
      };
      setTimeout(() => this._call(DOMAIN, "refresh").catch(() => {}), 60000);
      this._scheduleReload();
    });
  }

  _purgeTop() {
    const n = this._topN;
    if (!confirm(
      `¿Borrar el historial de las ${n} entidades que más ocupan (ranking ${this._periodLabel()})?\n\n` +
      "No se puede deshacer. Las estadísticas a largo plazo se conservan."
    )) return;
    this._run(`Borrando historial del top ${n}…`, async () => {
      const res = await this._call(
        DOMAIN, "purge_top", { top_n: n, period: this._period }, true
      );
      this._selected.clear();
      const purged = new Set(res.purged);
      this._data.ranking = this._data.ranking.filter((e) => !purged.has(e.entity_id));
      this._message = {
        kind: "ok",
        text: `Historial de ${res.purged.length} entidades borrado (~${mib(res.bytes)} MiB). ` +
          "El ranking se recalculará en un minuto. Pulsa «Compactar» para que el archivo encoja.",
      };
      this._scheduleReload();
    });
  }

  _repack() {
    if (!confirm(
      "¿Compactar la base de datos?\n\nPuede tardar varios minutos en bases grandes; " +
      "mientras tanto Home Assistant sigue funcionando pero no guarda historial."
    )) return;
    this._run("Compactando la base de datos…", async () => {
      await this._call("recorder", "purge", { repack: true });
      this._message = {
        kind: "ok",
        text: "Compactación lanzada. El tamaño de la base de datos se actualizará en el próximo cálculo.",
      };
      this._scheduleReload();
    });
  }

  _refresh() {
    this._run("Recalculando el ranking…", async () => {
      await this._call(DOMAIN, "refresh", { period: this._period });
      this._message = null;
      await this._load();
    });
  }

  _exclusionYaml() {
    const ids = [...this._selected].filter((id) => !id.includes(":")).sort();
    if (!ids.length) return;
    this._yaml =
      "recorder:\n  exclude:\n    entities:\n" + ids.map((id) => `      - ${id}`).join("\n");
    navigator.clipboard?.writeText(this._yaml).then(
      () => { this._message = { kind: "ok", text: "YAML copiado. Pégalo en configuration.yaml y reinicia." }; this._render(); },
      () => {}
    );
    this._message = { kind: "info", text: "Copia este YAML en configuration.yaml y reinicia Home Assistant." };
    this._render();
  }

  _periodLabel() {
    return this._period === "total" ? "total" : "últimas 24 h";
  }

  // --- interfaz -------------------------------------------------------------

  _render() {
    const d = this._data;
    const ranking = d?.ranking ?? [];
    const maxBytes = ranking.length ? ranking[0].bytes : 1;
    const dbSize = d?.calibration?.db_size;
    const selectable = ranking.filter((e) => !e.external_statistic);
    const allChecked = selectable.length && selectable.every((e) => this._selected.has(e.entity_id));
    const nSel = this._selected.size;
    const updated = d?.updated ? new Date(d.updated).toLocaleString() : "—";

    const rows = ranking.map((e) => {
      const checked = this._selected.has(e.entity_id) ? "checked" : "";
      const disabled = e.external_statistic ? "disabled" : "";
      const badge = !e.exists
        ? '<span class="badge warn">ya no existe</span>'
        : e.external_statistic ? '<span class="badge">estadística</span>' : "";
      const width = Math.max(1, Math.round((e.bytes / maxBytes) * 100));
      return `
        <tr class="${checked ? "sel" : ""}">
          <td class="chk"><input type="checkbox" data-id="${esc(e.entity_id)}" ${checked} ${disabled}></td>
          <td class="num">${e.rank}</td>
          <td class="ent">
            <a href="#" data-more="${esc(e.entity_id)}" class="${e.exists && !e.external_statistic ? "" : "nolink"}">${esc(e.name || e.entity_id)}</a>
            ${badge}
            <div class="eid">${esc(e.entity_id)}</div>
          </td>
          <td class="size">
            <div class="mb">${e.mb.toFixed(e.mb >= 10 ? 1 : 2)}</div>
            <div class="bar"><span style="width:${width}%"></span></div>
          </td>
          <td class="num hide-narrow">${e.pct}%</td>
          <td class="num hide-narrow">${e.states.toLocaleString()}</td>
        </tr>`;
    }).join("");

    const msg = this._message
      ? `<div class="msg ${this._message.kind}">${esc(this._message.text)}</div>` : "";

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <div class="toolbar">
        ${this._narrow ? '<button class="icon" id="menu" title="Menú">☰</button>' : ""}
        <div class="title">DB Size Ranking</div>
        ${this._busy ? '<div class="spinner"></div>' : ""}
      </div>
      <div class="content">
        <div class="stats">
          <div class="stat"><div class="label">Base de datos</div><div class="value">${dbSize ? mib(dbSize) + " MiB" : "—"}</div></div>
          <div class="stat"><div class="label">${this._period === "total" ? "Ocupado por entidades" : "Escrito en 24 h"}</div><div class="value">${d ? mib(d.total_bytes) + " MiB" : "—"}</div></div>
          <div class="stat"><div class="label">Entidades</div><div class="value">${d ? d.entity_count : "—"}</div></div>
          <div class="stat"><div class="label">Huérfanas</div><div class="value">${d ? d.orphan_count : "—"}</div></div>
        </div>

        <div class="card">
          <div class="controls">
            <div class="seg">
              <button data-period="total" class="${this._period === "total" ? "on" : ""}">Total</button>
              <button data-period="daily" class="${this._period === "daily" ? "on" : ""}">Últimas 24 h</button>
            </div>
            <label>Mostrar
              <select id="topn">
                ${[25, 50, 100, 200].map((n) => `<option ${n === this._topN ? "selected" : ""}>${n}</option>`).join("")}
              </select>
            </label>
            <span class="muted">Actualizado: ${esc(updated)}</span>
          </div>

          <div class="actions">
            <button class="danger" id="purge-sel" ${nSel && !this._busy ? "" : "disabled"}>Borrar seleccionadas${nSel ? ` (${nSel})` : ""}</button>
            <button class="danger outline" id="purge-top" ${d && !this._busy ? "" : "disabled"}>Borrar top ${this._topN}</button>
            <button id="yaml" ${nSel ? "" : "disabled"}>No registrar seleccionadas</button>
            <button id="repack" ${this._busy ? "disabled" : ""}>Compactar</button>
            <button id="refresh" ${this._busy ? "disabled" : ""}>Recalcular</button>
          </div>
          ${msg}
          ${this._yaml ? `<textarea readonly rows="${Math.min(14, this._yaml.split("\n").length + 1)}">${esc(this._yaml)}</textarea>` : ""}

          <div class="table-wrap">
            <table>
              <thead><tr>
                <th class="chk"><input type="checkbox" id="all" ${allChecked ? "checked" : ""}></th>
                <th class="num">#</th>
                <th>Entidad</th>
                <th class="num">MiB</th>
                <th class="num hide-narrow">%</th>
                <th class="num hide-narrow">Estados</th>
              </tr></thead>
              <tbody>${rows || `<tr><td colspan="6" class="empty">${this._busy ? "Cargando…" : this._pendingReload ? "Se recalculará en un minuto…" : "Sin datos todavía. Pulsa «Recalcular»."}</td></tr>`}</tbody>
            </table>
          </div>
        </div>

        <p class="muted help">
          <b>Borrar</b> elimina el historial guardado de esas entidades; las estadísticas a largo plazo
          (Energía, medias) se conservan. Si no quieres que vuelvan a llenarse, selecciónalas y pulsa
          <b>No registrar seleccionadas</b> para obtener el YAML de exclusión. En SQLite el archivo
          sólo encoge tras <b>Compactar</b>.
        </p>
      </div>`;
    this._bind();
  }

  _bind() {
    const $ = (sel) => this.shadowRoot.querySelector(sel);
    $("#menu")?.addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
    this.shadowRoot.querySelectorAll("[data-period]").forEach((b) =>
      b.addEventListener("click", () => {
        if (this._period === b.dataset.period) return;
        this._period = b.dataset.period;
        this._selected.clear();
        this._yaml = "";
        this._load();
      }));
    $("#topn").addEventListener("change", (ev) => {
      this._topN = Number(ev.target.value);
      this._load();
    });
    this.shadowRoot.querySelectorAll("input[data-id]").forEach((cb) =>
      cb.addEventListener("change", () => {
        if (cb.checked) this._selected.add(cb.dataset.id);
        else this._selected.delete(cb.dataset.id);
        this._render();
      }));
    $("#all")?.addEventListener("change", (ev) => {
      const ids = (this._data?.ranking ?? []).filter((e) => !e.external_statistic).map((e) => e.entity_id);
      this._selected = ev.target.checked ? new Set(ids) : new Set();
      this._render();
    });
    this.shadowRoot.querySelectorAll("a[data-more]").forEach((a) =>
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        if (a.classList.contains("nolink")) return;
        this.dispatchEvent(new CustomEvent("hass-more-info", {
          bubbles: true, composed: true, detail: { entityId: a.dataset.more },
        }));
      }));
    $("#purge-sel").addEventListener("click", () => this._purgeSelected());
    $("#purge-top").addEventListener("click", () => this._purgeTop());
    $("#yaml").addEventListener("click", () => this._exclusionYaml());
    $("#repack").addEventListener("click", () => this._repack());
    $("#refresh").addEventListener("click", () => this._refresh());
  }
}

const STYLE = `
  :host { display: block; min-height: 100vh; background: var(--primary-background-color);
    color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar { display: flex; align-items: center; gap: 12px; height: 56px; padding: 0 16px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, #fff); font-size: 20px; }
  .toolbar .title { flex: 1; }
  .icon { background: none; border: 0; color: inherit; font-size: 22px; cursor: pointer; padding: 4px 8px; }
  .spinner { width: 18px; height: 18px; border: 2px solid currentColor; border-right-color: transparent;
    border-radius: 50%; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .content { max-width: 1000px; margin: 0 auto; padding: 16px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .stat, .card { background: var(--card-background-color, #fff); border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); }
  .stat { padding: 12px 16px; }
  .stat .label { font-size: 13px; color: var(--secondary-text-color); }
  .stat .value { font-size: 22px; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .card { padding: 16px; }
  .controls, .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-bottom: 12px; }
  .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .seg button { border: 0; border-radius: 0; margin: 0; }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  button { font: inherit; font-size: 14px; padding: 8px 14px; border-radius: 8px; cursor: pointer;
    border: 1px solid var(--divider-color); background: var(--secondary-background-color, transparent);
    color: var(--primary-text-color); }
  button:disabled { opacity: .45; cursor: default; }
  button.danger { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); color: #fff; }
  button.danger.outline { background: transparent; color: var(--error-color, #db4437); }
  select { font: inherit; padding: 4px 6px; margin-left: 4px; background: var(--card-background-color);
    color: var(--primary-text-color); border: 1px solid var(--divider-color); border-radius: 6px; }
  .muted { color: var(--secondary-text-color); font-size: 13px; }
  .help { line-height: 1.5; margin: 16px 4px; }
  .msg { padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; font-size: 14px;
    background: var(--secondary-background-color); }
  .msg.ok { background: rgba(67, 160, 71, .15); }
  .msg.error { background: rgba(219, 68, 55, .15); }
  textarea { width: 100%; box-sizing: border-box; font-family: monospace; font-size: 13px; margin-bottom: 12px;
    background: var(--secondary-background-color); color: var(--primary-text-color);
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; font-weight: 500; color: var(--secondary-text-color); font-size: 13px;
    border-bottom: 1px solid var(--divider-color); padding: 8px 6px; }
  td { padding: 8px 6px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
  tr.sel td { background: rgba(3, 169, 244, .08); }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .chk { width: 28px; }
  .ent a { color: var(--primary-text-color); text-decoration: none; font-weight: 500; }
  .ent a:not(.nolink):hover { color: var(--primary-color); }
  .eid { font-size: 12px; color: var(--secondary-text-color); word-break: break-all; }
  .badge { font-size: 11px; padding: 1px 6px; border-radius: 10px; margin-left: 4px;
    background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .badge.warn { background: rgba(255, 152, 0, .2); color: var(--warning-color, #ff9800); }
  .size { width: 110px; text-align: right; }
  .mb { font-variant-numeric: tabular-nums; }
  .bar { height: 4px; background: var(--divider-color); border-radius: 2px; margin-top: 4px; }
  .bar span { display: block; height: 100%; background: var(--primary-color); border-radius: 2px; margin-left: auto; }
  .empty { text-align: center; color: var(--secondary-text-color); padding: 24px; }
  @media (max-width: 600px) { .hide-narrow { display: none; } .content { padding: 8px; } .size { width: 80px; } }
`;

if (!customElements.get("db-size-ranking-panel")) {
  customElements.define("db-size-ranking-panel", DbSizeRankingPanel);
}
