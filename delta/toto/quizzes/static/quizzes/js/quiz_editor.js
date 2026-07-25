/* Alpine components for the back-office question editor.
 *
 * answerEditor() — owns the answer-format control, the live math preview and
 * the dynamic inline formset (add/remove rows). Rows inherit this scope, so
 * their `x-show`, onCorrectToggle() and removeRow() resolve here.
 *
 * traitPanel() — a self-contained advanced panel per answer row that edits the
 * hidden traits_data JSON field.
 */

function answerEditor(config) {
  config = config || {};
  return {
    format: config.format || "single",
    _timer: null,

    init() {
      this.$watch("format", () => this.refreshPreview());
      this.refreshPreview();
    },

    get isOpen() { return this.format === "open"; },
    get isSingle() { return this.format === "single"; },

    addRow() {
      const total = document.getElementById("id_answers-TOTAL_FORMS");
      if (!total) return;
      const idx = parseInt(total.value, 10);
      const tpl = document.getElementById("empty-answer-template");
      const html = tpl.innerHTML.replace(/__prefix__/g, idx);
      const holder = document.createElement("div");
      holder.innerHTML = html.trim();
      const row = holder.firstElementChild;
      this.$refs.rows.appendChild(row);
      total.value = idx + 1;
      if (window.Alpine && window.Alpine.initTree) window.Alpine.initTree(row);
      this.refreshPreview();
    },

    removeRow(el) {
      const row = el.closest("[data-answer-row]");
      if (!row) return;
      const del = row.querySelector("[data-delete] input, input[data-delete]");
      const cb = row.querySelector('input[type="checkbox"][name$="-DELETE"]');
      if (cb) cb.checked = true;
      row.style.display = "none";
      this.refreshPreview();
    },

    onCorrectToggle(el) {
      if (this.format === "single" && el.checked) {
        this.$root.querySelectorAll("input[data-correct]").forEach((other) => {
          if (other !== el) other.checked = false;
        });
      }
    },

    refreshPreview() {
      clearTimeout(this._timer);
      this._timer = setTimeout(() => this._renderPreview(), 150);
    },

    _renderPreview() {
      const preview = this.$root.querySelector("[data-preview]");
      if (!preview) return;
      const stemInput = this.$root.querySelector("#id_text");
      const stem = (stemInput && stemInput.value ? stemInput.value : "").trim();
      const stemEl = preview.querySelector("[data-preview-stem]");
      if (stemEl) stemEl.textContent = stem || "…";

      const list = preview.querySelector("[data-preview-answers]");
      if (list) {
        list.innerHTML = "";
        this.$root.querySelectorAll("[data-answer-row]").forEach((row) => {
          if (row.style.display === "none") return;
          const input = row.querySelector("[data-answer-text]");
          const txt = (input && input.value ? input.value : "").trim();
          if (!txt) return;
          const li = document.createElement("li");
          li.className = "rounded border px-3 py-1.5";
          li.textContent = txt;
          list.appendChild(li);
        });
      }
      if (window.renderMathInElement && window.quizKatexOptions) {
        try { window.renderMathInElement(preview, window.quizKatexOptions); } catch (e) {}
      }
    },
  };
}

function traitPanel() {
  return {
    rows: [],

    init() {
      const hidden = this._hidden();
      if (hidden && hidden.value) {
        try {
          const parsed = JSON.parse(hidden.value);
          if (Array.isArray(parsed)) {
            this.rows = parsed.map((r) => ({ trait: String(r.trait), weight: r.weight || 1 }));
          }
        } catch (e) { /* ignore malformed */ }
      }
    },

    _hidden() {
      const host = this.$root.closest("[data-answer-row]") || document;
      return host.querySelector("[data-traits-data]");
    },

    addRow() { this.rows.push({ trait: "", weight: 1 }); this.sync(); },
    removeRow(i) { this.rows.splice(i, 1); this.sync(); },

    sync() {
      const hidden = this._hidden();
      if (!hidden) return;
      const clean = this.rows
        .filter((r) => r.trait)
        .map((r) => ({ trait: parseInt(r.trait, 10), weight: parseInt(r.weight, 10) || 1 }));
      hidden.value = clean.length ? JSON.stringify(clean) : "";
    },
  };
}
