/* Drag-and-drop reorder for back-office lists (SortableJS).
 *
 * POST mode  — a [data-reorder-url] container; each child carries
 *   [data-reorder-id]. On drop, POST order=<id>&order=<id>… to the URL (CSRF
 *   read from a page {% csrf_token %} input). No reload — the DOM already shows
 *   the new order; the server just renumbers.
 * Field mode — a [data-reorder-fields] container of [data-order-row] rows; on
 *   drop, write each row's 1-based index into its [data-order-input] (persisted
 *   when the surrounding form is saved).
 * A ".js-drag" element inside a row is the drag handle.
 */
(function () {
  function csrfToken() {
    var el = document.querySelector("input[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  function initPost(list) {
    // eslint-disable-next-line no-undef
    new Sortable(list, {
      handle: ".js-drag",
      draggable: "[data-reorder-id]",
      animation: 150,
      onEnd: function () {
        // Direct children only — nested [data-reorder-url] lists (e.g. badges
        // inside a group) carry their own [data-reorder-id]s.
        var ids = Array.prototype.map.call(
          list.querySelectorAll(":scope > [data-reorder-id]"),
          function (el) { return el.getAttribute("data-reorder-id"); });
        fetch(list.getAttribute("data-reorder-url"), {
          method: "POST",
          headers: {
            "X-CSRFToken": csrfToken(),
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: ids.map(function (id) { return "order=" + encodeURIComponent(id); }).join("&"),
        });
      },
    });
  }

  function initFields(list) {
    // eslint-disable-next-line no-undef
    new Sortable(list, {
      handle: ".js-drag",
      draggable: "[data-order-row]",
      animation: 150,
      onEnd: function () {
        Array.prototype.forEach.call(
          list.querySelectorAll("[data-order-row] [data-order-input]"),
          function (input, i) { input.value = i + 1; });
      },
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!window.Sortable) return;
    document.querySelectorAll("[data-reorder-url]").forEach(initPost);
    document.querySelectorAll("[data-reorder-fields]").forEach(initFields);
  });
})();
