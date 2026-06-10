// Confirm dialogs for destructive/outgoing actions, CSP-friendly
// (inline onclick handlers are blocked by the Content-Security-Policy).
document.addEventListener("submit", function (event) {
  var form = event.target.closest("form[data-confirm]");
  if (form && !window.confirm(form.getAttribute("data-confirm"))) {
    event.preventDefault();
  }
});
