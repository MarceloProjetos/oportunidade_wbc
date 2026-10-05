/* Central Integração SAP — the theme button of the shared top bar (casa/__init__.py).
 *
 * The choice goes to the `casa_tema` cookie, which the server reads to write `data-theme`
 * on <html> before the first paint. A cookie and not localStorage: storage is per port, and
 * the three screens live on three ports of the same host — cookies ignore the port.
 * Delegated listener: works for a bar rendered after this script, and needs no id.
 */
(function () {
  "use strict";
  var raiz = document.documentElement;

  document.addEventListener("click", function (evento) {
    var botao = evento.target && evento.target.closest ? evento.target.closest("[data-casa-tema]") : null;
    if (!botao) { return; }
    var claro = raiz.getAttribute("data-theme") !== "light";
    raiz.setAttribute("data-theme", claro ? "light" : "dark");
    document.cookie = "casa_tema=" + (claro ? "claro" : "escuro") + "; Path=/; Max-Age=31536000; SameSite=Lax";
    // Pages that draw (charts, canvas) listen to this and repaint.
    document.dispatchEvent(new CustomEvent("themechange", { detail: { theme: claro ? "light" : "dark" } }));
  });
})();
