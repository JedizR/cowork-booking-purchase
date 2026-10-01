/* Purchase: progressive enhancement only. Every form posts the same fields with or without script;
   the server stays the authority for availability, price and deadlines. */
(function () {
  "use strict";
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function thb(satang) { return "THB " + Math.floor(satang / 100).toLocaleString("en-US") + "." + pad(satang % 100); }
  function dur(n) { var h = Math.floor(n / 2); return [h ? h + " h" : "", n % 2 ? "30 min" : ""].filter(Boolean).join(" "); }

  var form = $("#book");
  if (form) keepDetails(form);
  $$("[data-stepper]").forEach(stepper);
  if (form) picker(form);
  countdown($("[data-seconds-left]"));
  // The header's Operator menu closes on an outside click or Escape, like any menu.
  document.addEventListener("click", function (e) {
    $$("details.topnav-menu[open]").forEach(function (d) { if (!d.contains(e.target)) d.open = false; });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") $$("details.topnav-menu[open]").forEach(function (d) { d.open = false; d.querySelector("summary").focus(); });
  });
  var chip = $(".date-strip [aria-current]");
  if (chip) chip.parentNode.scrollLeft = chip.offsetLeft - chip.parentNode.offsetWidth / 2 + chip.offsetWidth / 2;

  /* Party size and note survive a date change in this tab (a per-viewer convenience, PUR-R14 still on the server). */
  function keepDetails(form) {
    var key = "cowork-book:" + location.pathname, party = form.elements.party_size, note = form.elements.note;
    try {
      var saved = JSON.parse(sessionStorage.getItem(key) || "null");
      if (saved) { if (+saved.p >= 1 && +saved.p <= +party.max) party.value = saved.p; note.value = saved.n || ""; }
    } catch (e) { /* storage blocked: start empty */ }
    form.addEventListener("input", function () {
      try { sessionStorage.setItem(key, JSON.stringify({ p: party.value, n: note.value })); } catch (e) { /* ignore */ }
    });
    form.addEventListener("submit", function () { try { sessionStorage.removeItem(key); } catch (e) { /* ignore */ } });
  }

  /* Stepper: − and + move the number between min and max; the input stays typeable. */
  function stepper(box) {
    var input = $("input", box), minus = $('[data-step="-1"]', box), plus = $('[data-step="1"]', box);
    function sync() { var v = +input.value || 0; minus.disabled = v <= +input.min; plus.disabled = v >= +input.max; }
    box.addEventListener("click", function (e) {
      var b = e.target.closest("[data-step]");
      if (!b) return;
      input.value = Math.min(+input.max, Math.max(+input.min, (+input.value || 0) + +b.dataset.step));
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    input.addEventListener("input", sync);
    sync();
  }

  /* Range picker (DESIGN 8.1): select a start, then an end, on the day. The server rendered each 30-min
     block as free or blocked with its reason (PUR-R13); this only joins contiguous free blocks, 1 to 8. */
  function picker(form) {
    var root = $("#timeline"), btn = $("#bar-btn"), title = $("#bar-title"), meta = $("#bar-meta"), hint = $("#range-hint");
    var startIn = form.elements.start, blocksIn = form.elements.blocks, anon = form.dataset.anon === "1";
    var rate = +form.dataset.rate, coverage = form.dataset.coverage, day = form.dataset.day;
    var idle = [title.textContent, meta.textContent], MAX = 8;
    startIn.disabled = false;
    if (!root) { btn.disabled = !anon; return; }
    var slots = $$(".slot", root), dflt = Math.min(MAX, Math.max(1, +root.dataset.defaultBlocks || 1));
    var start = null, end = null, picking = false, hover = null, note = "", drag = null, suppress = false;

    function free(i) { return i >= 0 && i < slots.length && !slots[i].disabled; }
    function from(i) { return slots[i].dataset.time; }
    function until(i) { return slots[i].dataset.end; }
    // From slot a toward slot b: the longest run of free blocks, at most 8, and why it stopped short.
    function span(a, b) {
      var step = b >= a ? 1 : -1, k = a, why = "";
      while (k !== b) {
        if (Math.abs(k + step - a) + 1 > MAX) { why = "4 hours maximum"; break; }
        if (!free(k + step)) {
          why = slots[k + step].classList.contains("is-booked") ? "That range crosses a booked slot" : "Earlier times are too soon";
          break;
        }
        k += step;
      }
      return { s: Math.min(a, k), e: Math.max(a, k), why: why };
    }

    function choose(i) {
      if (!free(i)) return;
      if (picking && i >= start) { var r = span(start, i); end = r.e; picking = false; note = r.why; }
      else { start = i; end = span(i, i + dflt - 1).e; picking = true; note = ""; }
      hover = null;
      paint();
    }

    function paint() {
      var pv = null, why = note;
      if (picking && hover !== null && hover > end) {
        var r = span(start, hover);
        if (r.e > end) pv = r.e;
        if (r.why) why = r.why;
      }
      slots.forEach(function (el, i) {
        if (el.disabled) return;
        var sel = start !== null && i >= start && i <= end, body = el.lastElementChild;
        el.classList.toggle("is-selected", sel);
        el.classList.toggle("is-range-start", sel && i === start);
        el.classList.toggle("is-range-end", sel && i === end);
        el.classList.toggle("is-in-range", sel && i > start && i < end);
        el.classList.toggle("is-preview", pv !== null && i > end && i <= pv);
        el.classList.toggle("is-preview-end", pv !== null && i === pv);
        el.setAttribute("aria-pressed", sel ? "true" : "false");
        el.setAttribute("aria-label", from(i) + " to " + until(i) + ", " + (sel ? "selected" : "free"));
        el.tabIndex = i === (start === null ? firstFree : start) ? 0 : -1;
        if (picking && i > start) { var s = span(start, i); el.dataset.hint = s.e === i ? "End " + until(i) : s.why; }
        else el.dataset.hint = "Start " + from(i);
        var html = sel && i === start
          ? '<span class="slot-label">' + from(start) + "–" + until(end) + '</span><span class="slot-sub">' + dur(end - start + 1) + "</span>"
          : pv !== null && i === pv ? '<span class="slot-label">Until ' + until(pv) + " · " + dur(pv - start + 1) + "</span>" : "";
        if (el._html !== html) { body.innerHTML = el._html = html; }  // never swap nodes under a pressed pointer
      });
      if (start === null) {
        title.textContent = idle[0]; meta.textContent = idle[1]; startIn.value = ""; btn.disabled = !anon;
      } else {
        var n = end - start + 1, price = Math.floor((rate * n + 1) / 2);  // round_half_up(rate x blocks / 2), PUR-R17
        title.textContent = day + " · " + from(start) + "–" + until(end);
        meta.textContent = dur(n) + " · " + thb(price) + (coverage === "plan" ? " · covered by your plan" : coverage === "free" ? " · free, no payment" : "");
        startIn.value = from(start); blocksIn.value = n; btn.disabled = false;
      }
      hint.textContent = why || (!picking ? "" : dflt > 1 ? "Select an end time to change the length" : "Select an end time, or book 30 min");
      hint.hidden = !hint.textContent;
      hint.classList.toggle("is-warning", !!why);
    }

    var firstFree = slots.findIndex(function (el) { return !el.disabled; });
    root.addEventListener("click", function (e) {
      var el = e.target.closest(".slot");
      if (suppress) { suppress = false; return; }
      if (el) choose(+el.dataset.i);
    });
    // Drag to select with a mouse or pen; touch keeps scrolling the page and uses taps.
    root.addEventListener("pointerdown", function (e) {
      var el = e.target.closest(".slot");
      if (e.pointerType === "touch" || e.button !== 0 || !el || el.disabled) return;
      drag = { a: +el.dataset.i, moved: false };
    });
    document.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var el = document.elementFromPoint(e.clientX, e.clientY);
      el = el && el.closest(".slot");
      if (!el || !root.contains(el)) return;
      var j = +el.dataset.i;
      if (j === drag.a && !drag.moved) return;
      drag.moved = true;
      var r = span(drag.a, j);
      start = r.s; end = r.e; picking = false; note = r.why; hover = null;
      paint();
    });
    document.addEventListener("pointerup", function () {
      if (drag && drag.moved) suppress = true;
      drag = null;
      setTimeout(function () { suppress = false; }, 0);
    });
    root.addEventListener("pointerover", function (e) {
      if (e.pointerType === "touch" || drag) return;
      var el = e.target.closest(".slot");
      hover = el && !el.disabled ? +el.dataset.i : hover;
      if (picking) paint();
    });
    root.addEventListener("pointerleave", function () { hover = null; if (picking) paint(); });
    root.addEventListener("focusin", function (e) {
      var el = e.target.closest(".slot");
      if (el && picking) { hover = +el.dataset.i; paint(); }
    });
    // Keyboard: slots are buttons (Enter and Space select); arrows move between free slots; Escape clears.
    root.addEventListener("keydown", function (e) {
      var el = e.target.closest(".slot");
      if (!el) return;
      if (e.key === "Escape" && start !== null) { start = end = null; picking = false; note = ""; paint(); return; }
      var step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      for (var k = +el.dataset.i + step; k >= 0 && k < slots.length; k += step) if (free(k)) { slots[k].focus(); return; }
    });
    form.addEventListener("submit", function (e) {
      if (!anon && !startIn.value) { e.preventDefault(); note = "Select a start time first"; paint(); }
    });
    paint();
  }

  /* Countdown to the payment deadline (PUR-R40): the server renders the text and data-seconds-left;
     at zero "Continue to payment" goes away and "Time to pay has run out" shows. The server still decides. */
  function countdown(el) {
    if (!el) return;
    var total = +el.dataset.secondsLeft, t0 = Date.now(), left = $(".countdown-left", el), bar = $("#pay-progress");
    var timer = setInterval(tick, 1000);
    function tick() {
      var s = total - Math.floor((Date.now() - t0) / 1000);
      if (s <= 0) {
        clearInterval(timer);
        $$("[data-until-deadline]").forEach(function (x) { x.hidden = true; });
        $$("[data-after-deadline]").forEach(function (x) { x.hidden = false; });
        return;
      }
      left.textContent = "(" + (s < 60 ? "less than 1 min left" : Math.ceil(s / 60) + " min left") + ")";
      el.classList.toggle("is-urgent", s <= 120);
      if (bar) bar.value = s;
    }
    tick();
  }
})();
