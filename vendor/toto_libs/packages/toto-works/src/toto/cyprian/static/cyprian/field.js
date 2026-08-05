/* Embedded cyprian editors: one per [data-cy-field], no shared state.
 *
 * The full-page editor is an Alpine component that reads four json_script
 * islands by fixed id and mounts on #cy-editor — a page singleton by
 * construction. This file is the other half of the seam: plain DOM, one
 * instance per host element, talking to the same window.CyprianTipTap factory.
 *
 * The contract with Django is deliberately dull: the textarea is the form
 * field, and every change writes HTML into it. Nothing here posts anything
 * (except an image upload, which stores nothing server-side and answers with a
 * data URI) — saving is the surrounding form's job, so every form restriction
 * applies exactly as it did before the editor existed.
 */
(function (global) {
  "use strict";

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  /* KaTeX over every formula node. Unconditional, unlike the full-page
   * editor's version: the sanitiser strips rendered KaTeX markup and keeps only
   * data-latex, so content arriving from the server never has a .katex child to
   * short-circuit on, and skipping the render would show bare source. */
  function renderFormulas(root) {
    if (!root || !global.katex) return;
    var nodes = root.querySelectorAll(".cy-formula[data-latex]");
    Array.prototype.forEach.call(nodes, function (node) {
      try {
        global.katex.render(node.getAttribute("data-latex") || "", node, {
          displayMode: true, throwOnError: false, errorColor: "#ef4444"
        });
      } catch (e) { /* leave the source showing */ }
    });
  }

  var BUTTONS = [
    { cmd: "bold", label: "B", title: "Bold", style: "font-weight:700" },
    { cmd: "italic", label: "I", title: "Italic", style: "font-style:italic" },
    { cmd: "underline", label: "U", title: "Underline", style: "text-decoration:underline" },
    { cmd: "strike", label: "S", title: "Strikethrough", style: "text-decoration:line-through" },
    { sep: true },
    { cmd: "h2", label: "H2", title: "Heading" },
    { cmd: "h3", label: "H3", title: "Subheading" },
    { cmd: "paragraph", label: "¶", title: "Paragraph" },
    { sep: true },
    { cmd: "bulletList", label: "•", title: "Bulleted list" },
    { cmd: "orderedList", label: "1.", title: "Numbered list" },
    { cmd: "blockquote", label: "❝", title: "Quote" },
    { cmd: "codeBlock", label: "</>", title: "Code block" },
    { sep: true },
    { cmd: "link", label: "🔗", title: "Link" },
    { cmd: "image", label: "🖼", title: "Image" },
    { cmd: "formula", label: "∑", title: "Formula (LaTeX)" },
    { cmd: "table", label: "▦", title: "Table" },
    { sep: true },
    { cmd: "undo", label: "↶", title: "Undo" },
    { cmd: "redo", label: "↷", title: "Redo" }
  ];

  var ACTIVE_CHECK = {
    bold: "bold", italic: "italic", underline: "underline", strike: "strike",
    bulletList: "bulletList", orderedList: "orderedList",
    blockquote: "blockquote", codeBlock: "codeBlock"
  };

  function run(editor, cmd, field) {
    var chain = editor.chain().focus();
    switch (cmd) {
      case "bold": return chain.toggleBold().run();
      case "italic": return chain.toggleItalic().run();
      case "underline": return chain.toggleUnderline().run();
      case "strike": return chain.toggleStrike().run();
      case "h2": return chain.toggleHeading({ level: 2 }).run();
      case "h3": return chain.toggleHeading({ level: 3 }).run();
      case "paragraph": return chain.setParagraph().run();
      case "bulletList": return chain.toggleBulletList().run();
      case "orderedList": return chain.toggleOrderedList().run();
      case "blockquote": return chain.toggleBlockquote().run();
      case "codeBlock": return chain.toggleCodeBlock().run();
      case "table":
        return chain.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run();
      case "undo": return chain.undo().run();
      case "redo": return chain.redo().run();
      case "link": {
        var previous = editor.getAttributes("link").href || "";
        var href = global.prompt("Link address (empty removes the link):", previous);
        if (href === null) return;
        if (!href) return chain.unsetLink().run();
        return chain.extendMarkRange("link").setLink({ href: href }).run();
      }
      case "formula": {
        var latex = global.prompt("LaTeX (without the $ signs):", "");
        if (!latex) return;
        var rendered = "";
        if (global.katex) {
          try {
            rendered = global.katex.renderToString(latex, {
              displayMode: true, throwOnError: false, errorColor: "#ef4444"
            });
          } catch (e) { rendered = ""; }
        }
        return chain.setFormula({ latex: latex, rendered: rendered }).run();
      }
      case "image":
        return pickImage(editor, field);
      default:
        return;
    }
  }

  /* The one server round trip. cyprian:media_upload stores nothing: it resizes,
   * sanitises an SVG, and answers with a data URI — which is also the only image
   * form sanitize_content keeps, so what the editor inserts is what survives. */
  function pickImage(editor, field) {
    var input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) return;
      var body = new FormData();
      body.append("file", file);
      field.setStatus("Uploading…");
      fetch(field.uploadUrl, {
        method: "POST", headers: { "X-CSRFToken": csrf() }, body: body
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data || data.error) {
            field.setStatus((data && data.error) || "Upload failed.");
            return;
          }
          field.setStatus("");
          if (data.kind === "svg") {
            editor.chain().focus().insertContent(data.payload).run();
          } else {
            editor.chain().focus()
              .setImage({ src: data.payload, alt: data.alt || "" }).run();
          }
        })
        .catch(function () { field.setStatus("Upload failed."); });
    });
    input.click();
  }

  function buildToolbar(bar, editor, field) {
    var buttons = [];
    BUTTONS.forEach(function (item) {
      if (item.sep) {
        var sep = document.createElement("span");
        sep.className = "cy-field-sep";
        bar.appendChild(sep);
        return;
      }
      var b = document.createElement("button");
      b.type = "button";            // never submit the surrounding form
      b.className = "cy-field-btn";
      b.title = item.title;
      b.textContent = item.label;
      if (item.style) b.setAttribute("style", item.style);
      b.addEventListener("click", function () { run(editor, item.cmd, field); });
      bar.appendChild(b);
      if (ACTIVE_CHECK[item.cmd]) buttons.push({ el: b, name: ACTIVE_CHECK[item.cmd] });
    });
    return function syncActive() {
      buttons.forEach(function (entry) {
        var on = false;
        try { on = editor.isActive(entry.name); } catch (e) { on = false; }
        entry.el.classList.toggle("is-active", !!on);
      });
    };
  }

  function mount(root) {
    if (root.dataset.cyMounted === "1") return;
    var input = root.querySelector("[data-cy-input]");
    var host = root.querySelector("[data-cy-host]");
    var bar = root.querySelector("[data-cy-toolbar]");
    if (!input || !host || !global.CyprianTipTap) return;
    root.dataset.cyMounted = "1";

    // Progressive enhancement, in this order: the textarea was the field until
    // this moment, and only now that an editor is definitely mounting does it
    // become the transport.
    bar.hidden = false;
    host.hidden = false;
    input.hidden = true;

    var status = document.createElement("span");
    status.className = "cy-field-status";
    bar.appendChild(status);

    var field = {
      uploadUrl: root.dataset.uploadUrl || "",
      setStatus: function (text) { status.textContent = text || ""; }
    };

    var editor = global.CyprianTipTap.create(host, input.value, {
      placeholder: root.dataset.placeholder || "",
      /* Paste and drop take the same route as the toolbar's image button, so
       * the resize and SVG policies stay server-side and singular. The handler
       * is handed only the file; `editor` below is assigned before any of this
       * can run. */
      onImageFile: function (file) {
        var body = new FormData();
        body.append("file", file);
        field.setStatus("Uploading…");
        fetch(field.uploadUrl, {
          method: "POST", headers: { "X-CSRFToken": csrf() }, body: body
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (!data || data.error) {
              field.setStatus((data && data.error) || "Upload failed.");
              return;
            }
            field.setStatus("");
            if (data.kind === "svg") {
              editor.chain().focus().insertContent(data.payload).run();
            } else {
              editor.chain().focus()
                .setImage({ src: data.payload, alt: data.alt || "" }).run();
            }
          })
          .catch(function () { field.setStatus("Upload failed."); });
      }
    });

    var syncActive = buildToolbar(bar, editor, field);

    function flush() {
      input.value = editor.getHTML();
      // Let anything listening on the real field (dirty-form guards, previews)
      // see a normal change.
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    editor.on("update", function () { flush(); renderFormulas(host); syncActive(); });
    editor.on("selectionUpdate", syncActive);
    renderFormulas(host);
    syncActive();

    // A form that never fires an update (opened and submitted untouched) must
    // still post exactly what it was given — the textarea already holds it, so
    // this only guards against a browser that reformats on submit.
    var form = input.form;
    if (form) form.addEventListener("submit", flush);
  }

  function mountAll() {
    var roots = document.querySelectorAll("[data-cy-field]");
    Array.prototype.forEach.call(roots, mount);
  }

  /* The module that publishes CyprianTipTap is type="module", so it runs after
   * this classic script. Handle both orders — the module may already be there
   * (bfcache, a late-injected form), or may announce itself later. */
  if (global.CyprianTipTap) {
    document.addEventListener("DOMContentLoaded", mountAll);
    if (document.readyState !== "loading") mountAll();
  }
  global.addEventListener("cyprian-tiptap-ready", mountAll);
  document.addEventListener("DOMContentLoaded", function () {
    if (global.CyprianTipTap) mountAll();
  });

  global.CyprianField = { mountAll: mountAll };
})(window);
