/* DevTools Web - UI behaviour. Served from 'self' (the CSP forbids inline scripts).
   Security notes: only textContent / .value are used to write data (never innerHTML);
   file contents are read locally in the browser and never sent anywhere until the user submits. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const root = document.documentElement;
  const MAX_FILE = 2 * 1024 * 1024; // matches server MAX_UPLOAD_BYTES

  /* ---------- toggleable left menu ---------- */
  const NAV_KEY = "devtools.nav";
  const narrow = window.matchMedia("(max-width: 900px)");
  const store = {
    get() { try { return localStorage.getItem(NAV_KEY); } catch { return null; } },
    set(v) { try { localStorage.setItem(NAV_KEY, v); } catch { /* storage unavailable: ignore */ } },
  };
  const navBtn = $("#nav-toggle");
  function setNav(state, persist) {
    root.dataset.nav = state;
    if (navBtn) navBtn.setAttribute("aria-expanded", String(state === "open"));
    if (persist && !narrow.matches) store.set(state);
  }
  setNav(narrow.matches ? "closed" : (store.get() === "closed" ? "closed" : "open"), false);
  narrow.addEventListener("change", (e) => setNav(e.matches ? "closed" : (store.get() === "closed" ? "closed" : "open"), false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && narrow.matches && root.dataset.nav === "open") { setNav("closed", false); navBtn && navBtn.focus(); }
  });

  /* ---------- line-number gutters for input editors ---------- */
  function updateGutter(ed) {
    const ta = $("textarea", ed), gutter = $(".gutter", ed);
    if (!ta || !gutter) return;
    const n = Math.min(ta.value.split("\n").length, 20000);
    gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
  }
  function initEditor(ed) {
    const ta = $("textarea", ed), gutter = $(".gutter", ed);
    if (!ta || !gutter) return;
    ta.addEventListener("input", () => updateGutter(ed));
    ta.addEventListener("scroll", () => { gutter.scrollTop = ta.scrollTop; });
    updateGutter(ed);
  }
  $$("[data-editor]").forEach(initEditor);

  const resultPlaceholder = (box) => {
    const p = document.createElement("p");
    p.className = "muted pad";
    p.textContent = "Output appears here.";
    box.replaceChildren(p);
  };
  function setField(sel, value) {
    const el = $(sel);
    if (!el) return;
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function download(text, name) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = name; a.rel = "noopener";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function flash(btn, text) {
    const old = btn.textContent;
    btn.textContent = text;
    setTimeout(() => { btn.textContent = old; }, 1200);
  }

  /* ---------- image lightbox (click a result preview to enlarge) ---------- */
  let lightbox = null;
  function openLightbox(img) {
    if (!lightbox) {
      lightbox = document.createElement("dialog");
      lightbox.className = "lightbox";
      lightbox.setAttribute("aria-label", "Image preview");
      const close = document.createElement("button");
      close.type = "button";
      close.className = "lightbox-close";
      close.setAttribute("aria-label", "Close");
      close.textContent = "×";
      lightbox.append(close, document.createElement("img"));
      // any click inside the dialog (backdrop, image, close button) dismisses it
      lightbox.addEventListener("click", () => lightbox.close());
      document.body.appendChild(lightbox);
    }
    const big = $("img", lightbox);
    big.src = img.src;
    big.alt = img.alt;
    lightbox.showModal();
  }
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if ((e.key === "Enter" || e.key === " ") && t instanceof HTMLImageElement && t.classList.contains("zoomable")) {
      e.preventDefault();
      openLightbox(t);
    }
  });

  /* ---------- delegated clicks ---------- */
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;

    const zoom = t.closest("img.zoomable");
    if (zoom) { openLightbox(zoom); return; }

    if (t.closest("#nav-toggle")) { setNav(root.dataset.nav === "open" ? "closed" : "open", true); return; }
    if (t.closest("#scrim")) { setNav("closed", false); return; }

    const copy = t.closest("[data-copy]");
    if (copy) {
      const el = $(copy.dataset.copy);
      if (el && navigator.clipboard) navigator.clipboard.writeText(el.textContent).then(() => flash(copy, "Copied ✔"));
      return;
    }
    const dl = t.closest("[data-download]");
    if (dl) {
      const el = $(dl.dataset.download);
      if (el) download(el.textContent, (dl.dataset.filename || "result.txt").replace(/[^\w.-]/g, "_"));
      return;
    }
    const sample = t.closest("[data-sample]");
    if (sample) {
      let data = {};
      try { data = JSON.parse(sample.dataset.sample); } catch { /* ignore malformed */ }
      Object.entries(data).forEach(([name, text]) => setField("#f-" + CSS.escape(name), String(text)));
      return;
    }
    const clear = t.closest("[data-clear-field]");
    if (clear) {
      clear.dataset.clearField.split(",").forEach((sel) => setField(sel.trim(), ""));
      const r = $("#result");
      if (r && !r.closest(".lay-cron")) resultPlaceholder(r);
      return;
    }
    const open = t.closest("[data-open]");
    if (open) { const inp = $(open.dataset.open); inp && inp.click(); return; }

    const fill = t.closest("[data-fill]");
    if (fill) {
      const target = $(fill.dataset.target);
      if (target) { target.value = fill.dataset.fill; target.form && target.form.requestSubmit(); }
    }
  });

  /* ---------- instant size feedback for upload inputs (the server enforces the real limit) ---------- */
  document.addEventListener("change", (e) => {
    const inp = e.target;
    if (!(inp instanceof HTMLInputElement) || inp.type !== "file" || !inp.dataset.maxBytes || !inp.files || !inp.files[0]) return;
    const max = Number(inp.dataset.maxBytes);
    if (max > 0 && inp.files[0].size > max) {
      window.alert("This file is larger than the " + (max / 1048576) + " MB limit for this tool.");
      inp.value = "";
    }
  });

  /* ---------- open a local file into an editor (read in-browser) ---------- */
  document.addEventListener("change", (e) => {
    const inp = e.target;
    if (!(inp instanceof HTMLInputElement) || !inp.dataset.load || !inp.files || !inp.files[0]) return;
    const file = inp.files[0];
    inp.value = "";
    if (file.size > MAX_FILE) { window.alert("File is larger than 2 MB."); return; }
    const reader = new FileReader();
    reader.onload = () => setField(inp.dataset.load, String(reader.result));
    reader.readAsText(file);
  });

  /* ---------- home: tool finder ---------- */
  const toolFilter = $("#tool-filter");
  if (toolFilter) {
    toolFilter.addEventListener("input", () => {
      const words = toolFilter.value.toLowerCase().split(/\s+/).filter(Boolean);
      let any = false;
      $$("[data-tool-group]").forEach((group) => {
        let shown = 0;
        $$(".tool-card", group).forEach((card) => {
          const ok = words.every((w) => card.dataset.toolText.includes(w));
          card.hidden = !ok;
          if (ok) shown++;
        });
        group.hidden = shown === 0;
        any = any || shown > 0;
      });
      const none = $("[data-tool-none]");
      if (none) none.hidden = any;
    });
    toolFilter.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { const first = $(".tool-card:not([hidden])"); if (first) first.click(); }
    });
  }

  /* ---------- home: birthday card slider ---------- */
  const bd = $("[data-bd]");
  const track = bd && $("[data-bd-track]", bd);
  if (bd && track) {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const cards = () => $$(".bd-card:not([hidden])", track);
    const step = () => { const c = cards()[0]; return c ? c.getBoundingClientRect().width + 18 : track.clientWidth; };
    const prev = $("[data-bd-prev]", bd), next = $("[data-bd-next]", bd);
    const updateNav = () => {
      const max = track.scrollWidth - track.clientWidth - 2;
      if (prev) prev.disabled = track.scrollLeft <= 2;
      if (next) next.disabled = track.scrollLeft >= max;
    };
    const go = (dir) => track.scrollBy({ left: dir * step(), behavior: reduced ? "auto" : "smooth" });
    function show(key) {
      $$("[data-bd-tab]", bd).forEach((t) => t.setAttribute("aria-selected", String(t.dataset.bdTab === key)));
      let n = 0;
      $$(".bd-card", track).forEach((c) => {
        const ok = c.dataset.windows.split(" ").includes(key);
        c.hidden = !ok;
        if (ok) n++;
      });
      const empty = $("[data-bd-empty]", track);
      if (empty) empty.hidden = n > 0;
      track.scrollLeft = 0;
      updateNav();
    }
    bd.addEventListener("click", (e) => {
      const t = e.target instanceof Element ? e.target : null;
      if (!t) return;
      const tab = t.closest("[data-bd-tab]");
      if (tab) { show(tab.dataset.bdTab); return; }
      if (t.closest("[data-bd-prev]")) go(-1);
      if (t.closest("[data-bd-next]")) go(1);
    });
    track.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
    });
    track.addEventListener("scroll", updateNav, { passive: true });
    window.addEventListener("resize", updateNav);
    // photo failed to load (missing / blocked): fall back to the initials underneath
    track.addEventListener("error", (e) => {
      if (e.target instanceof HTMLImageElement && e.target.hasAttribute("data-bd-img")) e.target.remove();
    }, true);
    $$("img[data-bd-img]", track).forEach((img) => { if (img.complete && !img.naturalWidth) img.remove(); });
    // gentle autoplay: pauses on hover / focus, off for reduced-motion users
    let paused = false;
    ["mouseenter", "focusin", "touchstart"].forEach((ev) => bd.addEventListener(ev, () => { paused = true; }, { passive: true }));
    ["mouseleave", "focusout"].forEach((ev) => bd.addEventListener(ev, () => { paused = false; }));
    if (!reduced) {
      setInterval(() => {
        if (paused || document.hidden || cards().length < 2) return;
        if (track.scrollLeft >= track.scrollWidth - track.clientWidth - 2) track.scrollTo({ left: 0, behavior: "smooth" });
        else go(1);
      }, 5000);
    }
    updateNav();
  }

  /* ---------- knowledge base: sign-in countdown, automatic sign-out at expiry ---------- */
  const kbSession = $("#kb-session");
  if (kbSession) {
    const expires = Number(kbSession.dataset.expires) * 1000;
    const left = document.createElement("span");
    kbSession.append(left);
    const tick = () => {
      const ms = expires - Date.now();
      if (ms <= 0) { location.reload(); return; }           // server has dropped the sign-in by now
      left.textContent = " · " + Math.ceil(ms / 60000) + " min left";
      setTimeout(tick, Math.min(ms + 500, 30000));
    };
    tick();
  }

  /* ---------- knowledge base: details / edit modal ---------- */
  const kbModal = $("#kb-modal");
  if (kbModal) {
    const openModal = () => { if (!kbModal.open) kbModal.showModal(); };
    if (kbModal.hasAttribute("data-autoopen")) openModal();
    document.body.addEventListener("htmx:afterSwap", (e) => {
      if (e.target && e.target.id === "kb-modal-body") {
        if (e.target.childElementCount) { openModal(); kbModal.scrollTop = 0; } else kbModal.close();
      }
    });
    document.body.addEventListener("kb-close", () => kbModal.close());
    kbModal.addEventListener("click", (e) => {
      // backdrop click (the dialog element itself) or any [data-kb-close] button closes it
      if (e.target === kbModal || (e.target instanceof Element && e.target.closest("[data-kb-close]"))) kbModal.close();
    });
    kbModal.addEventListener("close", () => {
      if (location.pathname !== "/kb" && /^\/kb\/\d+$/.test(location.pathname)) history.replaceState(null, "", "/kb");
    });
    // "/" focuses the search box (unless typing somewhere already)
    document.addEventListener("keydown", (e) => {
      const t = e.target;
      if (e.key === "/" && !kbModal.open && !(t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement)) {
        e.preventDefault();
        const q = $("#kb-q"); q && q.focus();
      }
    });
  }

  /* ---------- generic form reset (stacked layout) ---------- */
  document.addEventListener("reset", (e) => {
    const form = e.target;
    setTimeout(() => {
      $$("[data-editor]", form).forEach(updateGutter);
      const r = $("#result");
      if (r) resultPlaceholder(r);
    }, 0);
  }, true);
})();
