async function api(path, opts = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...opts };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map(e => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("\n");
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return data;
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ESC[c]);
}

function toast(text, isErr = false) {
  let el = document.querySelector(".toast");
  if (!el) { el = document.createElement("div"); document.body.appendChild(el); }
  el.textContent = text;
  el.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), isErr ? 5000 : 2500);
}

function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/**
 * Autocomplete for an <input> placed inside a `.ac` wrapper.
 * source(q) -> Promise<items>, render(item) -> HTML, onPick(item).
 */
function autocomplete(input, { source, render, onPick }) {
  const list = document.createElement("div");
  list.className = "ac-list";
  input.closest(".ac").appendChild(list);
  let items = [], active = -1, seq = 0, timer = null;

  // cancel the pending debounce and any in-flight search, so the list can't reopen after closing
  const close = () => { clearTimeout(timer); seq++; list.classList.remove("open"); active = -1; };
  const paint = () => {
    list.innerHTML = items.length
      ? items.map((it, i) => `<div class="ac-item${i === active ? " active" : ""}" data-i="${i}">${render(it)}</div>`).join("")
      : `<div class="ac-empty">Ничего не найдено</div>`;
    list.classList.add("open");
  };
  const run = async () => {
    if (document.activeElement !== input) return;
    const my = ++seq;
    try {
      const res = await source(input.value.trim());
      if (my !== seq || document.activeElement !== input) return;
      items = res; active = items.length ? 0 : -1; paint();
    } catch (e) { toast(e.message, true); }
  };
  const load = () => { clearTimeout(timer); timer = setTimeout(run, 200); };

  input.addEventListener("input", load);
  input.addEventListener("focus", load);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && active < 0) e.preventDefault(); // never submit the form from a search box
    if (!list.classList.contains("open")) return;
    if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); paint(); e.preventDefault(); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); onPick(items[active]); close(); }
    else if (e.key === "Escape") close();
  });
  list.addEventListener("mousedown", e => {
    const el = e.target.closest(".ac-item");
    if (!el) return;
    e.preventDefault();
    onPick(items[+el.dataset.i]);
    close();
  });
  input.addEventListener("blur", () => setTimeout(close, 150));
}

function searchOffers(q) {
  return api(`/api/offers?limit=30&q=${encodeURIComponent(q)}`);
}

function renderOffer(o) {
  return `<span class="id">[${o.id}]</span>${esc(o.name)}`;
}
