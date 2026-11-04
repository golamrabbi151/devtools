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

  /* ---------- find in pane (input textarea / output code), like an editor's Ctrl+F ----------
     Matches are highlighted with the CSS Custom Highlight API (Range objects, no DOM rewriting).
     The textarea is searched through .ed-backdrop: a transparent copy of its text drawn underneath it.
     Everything happens in the browser; nothing is sent to the server. */
  const FIND_MAX = 5000;
  const canHighlight = typeof CSS !== "undefined" && CSS.highlights && typeof Highlight === "function";
  const finders = [];

  function refreshHighlights() {
    if (!canHighlight) return;
    const all = [], cur = [];
    finders.forEach((f) => {
      if (!f.open) return;
      f.ranges.forEach((r, i) => (i === f.index ? cur : all).push(r));
    });
    CSS.highlights.set("find", new Highlight(...all));
    CSS.highlights.set("find-current", new Highlight(...cur));
  }

  // Map [start, end) offsets in root's text to Range objects (matches may span several <span>s).
  function rangesFor(root, spans) {
    const out = [];
    if (!spans.length) return out;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node, pos = 0, i = 0, range = null;
    while ((node = walker.nextNode()) && i < spans.length) {
      const len = node.nodeValue.length, end = pos + len;
      while (i < spans.length) {
        const [s, e] = spans[i];
        if (!range) {
          if (s >= end) break;
          range = document.createRange();
          range.setStart(node, s - pos);
        }
        if (e <= end) { range.setEnd(node, e - pos); out.push(range); range = null; i++; }
        else break;                                    // match continues in the next text node
      }
      pos = end;
    }
    return out;
  }

  function makeFinder(pane) {
    const bar = $("[data-find-bar]", pane), input = $("[data-find-input]", pane), count = $("[data-find-count]", pane);
    const isInput = pane.dataset.findSource === "input";
    const f = { pane, open: false, ranges: [], spans: [], index: -1, caseSensitive: false, regex: false };

    const textarea = () => $("textarea", pane);
    const backdrop = () => $(".ed-backdrop", pane);
    const root = () => (isInput ? backdrop() : $("#out", pane));
    const scroller = () => (isInput ? textarea() : $(".editor.out", pane));

    function syncBackdrop() {
      const ta = textarea(), bd = backdrop();
      if (!ta || !bd) return;
      bd.scrollTop = ta.scrollTop;
      bd.scrollLeft = ta.scrollLeft;
    }
    function matches(text, q) {
      const spans = [];
      if (!q) return spans;
      if (f.regex) {
        let re;
        try { re = new RegExp(q, f.caseSensitive ? "g" : "gi"); } catch { return null; }
        let m;
        while ((m = re.exec(text)) && spans.length < FIND_MAX) {
          if (m[0].length === 0) { re.lastIndex++; continue; }
          spans.push([m.index, m.index + m[0].length]);
        }
      } else {
        const hay = f.caseSensitive ? text : text.toLowerCase(), needle = f.caseSensitive ? q : q.toLowerCase();
        let at = hay.indexOf(needle);
        while (at !== -1 && spans.length < FIND_MAX) { spans.push([at, at + needle.length]); at = hay.indexOf(needle, at + needle.length); }
      }
      return spans;
    }
    function run(keepPosition) {
      const el = root();
      if (isInput && el) { el.textContent = textarea().value; syncBackdrop(); }
      const q = input.value;
      const spans = el ? matches(el.textContent, q) : [];
      input.classList.toggle("no-match", !!q && (!spans || spans.length === 0));
      if (spans === null) { f.ranges = []; f.index = -1; count.textContent = "Bad pattern"; refreshHighlights(); return; }
      const prevStart = keepPosition && f.spans[f.index] ? f.spans[f.index][0] : 0;
      f.spans = spans;
      f.ranges = el ? rangesFor(el, spans) : [];
      f.index = spans.length ? Math.max(0, spans.findIndex(([s]) => s >= prevStart)) : -1;
      if (f.index === -1 && spans.length) f.index = 0;
      show(!keepPosition);
    }
    function show(scroll) {
      const n = f.spans.length;
      count.textContent = !input.value ? "" : n === 0 ? "No results"
        : `${f.index + 1} of ${n >= FIND_MAX ? FIND_MAX + "+" : n}`;
      refreshHighlights();
      if (scroll !== false) scrollToCurrent();
    }
    function scrollToCurrent() {
      const r = f.ranges[f.index], box = scroller();
      if (!r || !box) return;
      const rect = r.getBoundingClientRect(), view = box.getBoundingClientRect();
      const gutter = isInput ? 0 : (($(".gutter", box) || {}).offsetWidth || 0);
      const top = rect.top - view.top, left = rect.left - view.left;
      if (top < 0 || top + rect.height > box.clientHeight) box.scrollTop += top - box.clientHeight / 2;
      if (left < gutter || left + rect.width > box.clientWidth) box.scrollLeft += left - gutter - (box.clientWidth - gutter) / 3;
      if (isInput) syncBackdrop();
    }
    function step(dir) {
      const n = f.spans.length;
      if (!n) return;
      f.index = (f.index + dir + n) % n;
      show(true);
    }
    function openBar() {
      f.open = true;
      bar.hidden = false;
      const ta = textarea();
      // pre-fill with the selected text (textarea selection, or a selection inside the output)
      let sel = "";
      if (isInput && ta && ta.selectionEnd > ta.selectionStart) sel = ta.value.slice(ta.selectionStart, ta.selectionEnd);
      if (!isInput) { const s = window.getSelection(); if (s && root() && root().contains(s.anchorNode)) sel = String(s); }
      if (sel && !sel.includes("\n") && sel.length <= 200) input.value = sel;
      input.focus();
      input.select();
      run(false);
    }
    function closeBar() {
      f.open = false;
      bar.hidden = true;
      f.ranges = []; f.spans = []; f.index = -1;
      const bd = backdrop();
      if (isInput && bd) bd.textContent = "";                      // drop the copy until the next search
      refreshHighlights();
      const back = isInput ? textarea() : scroller();
      if (back) back.focus({ preventScroll: true });
    }

    let timer = 0;
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => run(false), 120); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
      else if (e.key === "Escape") { e.preventDefault(); closeBar(); }
      else if (e.key === "F3") { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
    });
    pane.addEventListener("click", (e) => {
      const t = e.target instanceof Element ? e.target : null;
      if (!t) return;
      if (t.closest("[data-find-open]")) { f.open && document.activeElement === input ? closeBar() : openBar(); }
      else if (t.closest("[data-find-next]")) step(1);
      else if (t.closest("[data-find-prev]")) step(-1);
      else if (t.closest("[data-find-close]")) closeBar();
      else if (t.closest("[data-find-case], [data-find-regex]")) {
        const b = t.closest(".find-opt");
        const on = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", String(on));
        if (b.hasAttribute("data-find-case")) f.caseSensitive = on; else f.regex = on;
        run(false);
        input.focus();
      }
    });
    if (isInput) {
      const ta = textarea();
      if (ta) {
        ta.addEventListener("scroll", syncBackdrop, { passive: true });
        ta.addEventListener("input", () => { if (f.open) { clearTimeout(timer); timer = setTimeout(() => run(true), 150); } });
      }
    }
    f.openBar = openBar;
    f.rerun = () => { if (f.open) run(false); };
    return f;
  }

  $$("[data-find-pane]").forEach((pane) => finders.push(makeFinder(pane)));
  if (finders.length) {
    // Ctrl/Cmd+F inside a pane opens that pane's search instead of the browser's page search
    document.addEventListener("keydown", (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "f" || e.shiftKey || e.altKey) return;
      const pane = e.target instanceof Element && e.target.closest("[data-find-pane]");
      const f = pane && finders.find((x) => x.pane === pane);
      if (f) { e.preventDefault(); f.openBar(); }
    });
    // new output arrived: search it again
    document.body.addEventListener("htmx:afterSwap", (e) => {
      if (e.target && e.target.id === "result") finders.forEach((f) => f.pane.contains(e.target) && f.rerun());
    });
    // input status bar: cursor position, lines, characters
    const ta = $("[data-find-source='input'] textarea"), status = $("[data-input-status]");
    if (ta && status) {
      const update = () => {
        const v = ta.value, pos = ta.selectionStart || 0;
        if (v.length > 2_000_000) { status.textContent = v.length.toLocaleString() + " chars"; return; }
        const before = v.slice(0, pos), line = before.split("\n").length;
        const col = pos - before.lastIndexOf("\n");
        const lines = v ? v.split("\n").length : 1;
        status.textContent = `Ln ${line}, Col ${col} · ${lines.toLocaleString()} line${lines === 1 ? "" : "s"} · ${v.length.toLocaleString()} chars`;
      };
      ["input", "keyup", "click", "select"].forEach((ev) => ta.addEventListener(ev, update));
      update();
    }
  }

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

  /* ---------- sign-in page: show/hide password, Caps Lock hint, no double submit ---------- */
  const authForm = $("[data-auth-form]");
  if (authForm) {
    const eye = $("[data-toggle-password]", authForm), pw = eye && $(eye.dataset.togglePassword);
    if (eye && pw) {
      eye.addEventListener("click", () => {
        const show = pw.type === "password";
        pw.type = show ? "text" : "password";
        eye.setAttribute("aria-pressed", String(show));
        eye.setAttribute("aria-label", show ? "Hide password" : "Show password");
        pw.focus();
      });
    }
    const caps = $("[data-caps-warning]", authForm);
    const checkCaps = (e) => { if (caps && e.getModifierState) caps.hidden = !e.getModifierState("CapsLock"); };
    if (pw) { pw.addEventListener("keydown", checkCaps); pw.addEventListener("keyup", checkCaps); pw.addEventListener("blur", () => { if (caps) caps.hidden = true; }); }
    const submit = $("[data-auth-submit]", authForm), label = $("[data-auth-label]", authForm);
    authForm.addEventListener("submit", () => {
      if (pw) pw.type = "password";                           // never leave the password visible on the way out
      if (submit) { submit.disabled = true; submit.classList.add("is-busy"); }
      if (label) label.textContent = "Signing in…";
    });
    // coming back with the browser's Back button: re-enable the form
    window.addEventListener("pageshow", () => {
      if (submit) { submit.disabled = false; submit.classList.remove("is-busy"); }
      if (label) label.textContent = "Sign in";
    });
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
