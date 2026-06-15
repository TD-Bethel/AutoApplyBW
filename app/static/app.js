// Confirm dialogs for destructive/outgoing actions, CSP-friendly
// (inline onclick handlers are blocked by the Content-Security-Policy).
document.addEventListener("submit", function (event) {
  var form = event.target.closest("form[data-confirm]");
  if (form && !window.confirm(form.getAttribute("data-confirm"))) {
    event.preventDefault();
  }
});

// Assistant chat: keep the newest message in view.
var chatBox = document.getElementById("chat-box");
if (chatBox) chatBox.scrollTop = chatBox.scrollHeight;

// Find-jobs: "Select" copies a found job into the Add-a-job form below.
document.addEventListener("click", function (event) {
  var btn = event.target.closest(".select-job");
  if (!btn) return;
  var fill = function (id, value) {
    var el = document.getElementById(id);
    if (el) el.value = value || "";
  };
  fill("company_name", btn.getAttribute("data-company"));
  fill("company_email", btn.getAttribute("data-email"));
  fill("job_title", btn.getAttribute("data-title"));
  fill("job_description", btn.getAttribute("data-desc"));
  var card = document.getElementById("add-job-card");
  if (card) card.scrollIntoView({ behavior: "smooth" });
  var email = document.getElementById("company_email");
  if (email && !email.value) email.focus();
});

// ---------------------------------------------------------------- support chat
(function () {
  var fab = document.getElementById("chat-fab");
  if (!fab) return; // not logged in / admin
  var panel = document.getElementById("chat-panel");
  var thread = document.getElementById("chat-thread");
  var quick = document.getElementById("chat-quick");
  var form = document.getElementById("chat-send-form");
  var input = document.getElementById("chat-text");
  var csrf = fab.getAttribute("data-csrf");
  var loaded = false;

  function render(data) {
    thread.innerHTML = "";
    (data.messages || []).forEach(function (m) {
      var row = document.createElement("div");
      row.className = "chat-msg " + (m.role === "user" ? "user" : "assistant");
      var bubble = document.createElement("div");
      bubble.className = "chat-bubble" + (m.role === "admin" ? " from-admin" : "");
      if (m.role === "admin") {
        var tag = document.createElement("span");
        tag.className = "admin-tag";
        tag.textContent = "Admin";
        bubble.appendChild(tag);
      }
      bubble.appendChild(document.createTextNode(m.content));
      row.appendChild(bubble);
      thread.appendChild(row);
    });
    quick.innerHTML = "";
    (data.options || []).forEach(function (opt) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "chat-option";
      b.textContent = opt.label;
      b.setAttribute("data-choice", opt.key);
      quick.appendChild(b);
    });
    thread.scrollTop = thread.scrollHeight;
  }

  function post(body) {
    return fetch(fab.getAttribute("data-send-url"), {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      body: body,
    }).then(function (r) { return r.json(); }).then(render);
  }

  fab.addEventListener("click", function () {
    var open = !panel.hidden;
    panel.hidden = open;
    if (!open && !loaded) {
      loaded = true;
      fetch(fab.getAttribute("data-messages-url"))
        .then(function (r) { return r.json(); }).then(render);
    }
  });
  document.getElementById("chat-close").addEventListener("click", function () {
    panel.hidden = true;
  });
  quick.addEventListener("click", function (e) {
    var btn = e.target.closest(".chat-option");
    if (!btn) return;
    var body = new FormData();
    body.append("choice", btn.getAttribute("data-choice"));
    post(body);
  });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    var body = new FormData();
    body.append("message", text);
    post(body);
  });
})();

// ---------------------------------------------------------- hero news slides
(function () {
  var box = document.getElementById("hero-slides");
  if (!box) return;
  var slides = box.querySelectorAll(".hero-slide");
  var dots = box.querySelectorAll(".slide-dot");
  var current = 0, timer = null;

  function show(i) {
    current = (i + slides.length) % slides.length;
    slides.forEach ? null : 0;
    for (var k = 0; k < slides.length; k++) {
      slides[k].classList.toggle("active", k === current);
      dots[k].classList.toggle("active", k === current);
    }
  }
  function next() { show(current + 1); }
  function start() { stop(); timer = setInterval(next, 5500); }
  function stop() { if (timer) clearInterval(timer); timer = null; }

  for (var k = 0; k < dots.length; k++) {
    dots[k].addEventListener("click", function () {
      show(parseInt(this.getAttribute("data-slide"), 10));
      start(); // restart the clock after a manual jump
    });
  }
  box.addEventListener("mouseenter", stop);
  box.addEventListener("mouseleave", start);
  if (!window.matchMedia || !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    start();
  }
})();
