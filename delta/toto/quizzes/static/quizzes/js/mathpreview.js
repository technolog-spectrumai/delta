/* Live KaTeX preview for any <textarea class="js-mathsource">.
 *
 * Inserts a rendered preview under the textarea and updates it (debounced) as
 * the teacher types. Reuses window.renderMathInElement + window.quizKatexOptions
 * from quizzes/_katex.html. Hidden while the field is empty.
 */
(function () {
  function renderMath(el) {
    if (window.renderMathInElement && window.quizKatexOptions) {
      try { window.renderMathInElement(el, window.quizKatexOptions); } catch (e) {}
    }
  }

  function attach(textarea) {
    if (textarea._mathPreview) return;
    textarea._mathPreview = true;

    var preview = document.createElement("div");
    preview.className = "js-math";
    preview.setAttribute("aria-hidden", "true");
    preview.style.cssText =
      "margin-top:.4rem;padding:.5rem .75rem;border:1px dashed rgba(128,128,128,.4);" +
      "border-radius:.5rem;font-size:.95rem;opacity:.9;";
    textarea.insertAdjacentElement("afterend", preview);

    var timer = null;
    function refresh() {
      var value = textarea.value || "";
      preview.style.display = value.trim() ? "" : "none";
      preview.textContent = value;
      renderMath(preview);
    }
    textarea.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(refresh, 150);
    });
    refresh();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("textarea.js-mathsource").forEach(attach);
  });
})();
