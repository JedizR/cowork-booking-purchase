/* Purchase: progressive enhancement only. Every form posts the same fields with or without script;
   the server stays the authority for availability, price and deadlines. */
(function () {
  "use strict";
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function thb(satang) { return "THB " + Math.floor(satang / 100).toLocaleString("en-US") + "." + pad(satang % 100); }
  function dur(n) { var h = Math.floor(n / 2); return [h ? h + " h" : "", n % 2 ? "30 min" : ""].filter(Boolean).join(" "); }
  var DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // "Fri 2 Oct, 09:00" in Bangkok (fixed UTC+7, D2), whatever the browser's zone.
  function bkk(ms) {
    var d = new Date(ms + 7 * 3600e3);
    return DOW[d.getUTCDay()] + " " + d.getUTCDate() + " " + MON[d.getUTCMonth()] + ", " + pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
  }

  var form = $("#book");
  if (form) keepDetails(form);
  $$("[data-stepper]").forEach(stepper);
  if (form) picker(form);
  countdown($("[data-seconds-left]"));
  $$("[data-show-password]").forEach(passwordToggle);
  // Header menus (Operator, account) close on an outside click or Escape, like any menu.
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
    form.addEventListener("submit", function (e) {
      if (!e.defaultPrevented) try { sessionStorage.removeItem(key); } catch (x) { /* ignore */ }
    });
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

  function passwordToggle(b) {
    var input = document.getElementById(b.getAttribute("aria-controls"));
    b.hidden = false;
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      b.textContent = show ? "Hide" : "Show";
      b.setAttribute("aria-pressed", show ? "true" : "false");
    });
  }

  /* Range picker (DESIGN 8.1): tap a start, then an end, on the day; tap again to move the end; drag the
     handle (touch) or drag across rows (mouse). The server rendered each 30-min block as free or blocked
     with its reason (PUR-R13); this only joins contiguous free blocks, 1 to 8. */
  function picker(form) {
    var root = $("#timeline"), bar = $("#bar"), btn = $("#bar-btn"), title = $("#bar-title"), meta = $("#bar-meta");
    var refund = $("#bar-refund"), hint = $("#range-hint"), clear = $("#bar-clear"), details = $("#details");
    var startIn = form.elements.start, blocksIn = form.elements.blocks, anon = form.dataset.anon === "1";
    var rate = +form.dataset.rate, coverage = form.dataset.coverage, day = form.dataset.day, date = form.dataset.date;
    var now = Date.parse(form.dataset.now), hold = form.dataset.hold || "", holdMatch = form.dataset.holdMatch || "";
    var narrow = window.matchMedia("(max-width: 833px)"), label = btn.textContent, reviewed = false, MAX = 8;
    if (narrow.matches) title.textContent = "Tap a start time";
    if (hold && !holdMatch && !anon) title.textContent = "Finish " + hold + " first";
    var idle = [title.textContent, meta.textContent];
    startIn.disabled = false;
    if (!root) { btn.disabled = !anon || !!hold; return; }
    var slots = $$(".slot", root), dflt = Math.min(MAX, Math.max(1, +root.dataset.defaultBlocks || 1));
    var start = null, end = null, picking = false, hover = null, note = "", drag = null, suppress = false;
    var handle = document.createElement("span");
    handle.className = "range-handle";
    handle.setAttribute("aria-hidden", "true");
    handle.hidden = true;
    root.appendChild(handle);

    function free(i) { return i >= 0 && i < slots.length && !slots[i].disabled; }
    function from(i) { return slots[i].dataset.time; }
    function until(i) { return slots[i].dataset.end; }
    // From slot a toward slot b: the longest run of free blocks, at most 8, and why it stopped short.
    function span(a, b) {
      var step = b >= a ? 1 : -1, k = a, stop = "";
      while (k !== b) {
        if (Math.abs(k + step - a) + 1 > MAX) { stop = "max"; break; }
        if (!free(k + step)) { stop = slots[k + step].classList.contains("is-booked") ? "booked" : "soon"; break; }
        k += step;
      }
      return { s: Math.min(a, k), e: Math.max(a, k), stop: stop, back: step < 0 };
    }
    // A trimmed range is an outcome, not an error: say what the range became and why.
    function trimmed(r) {
      if (r.stop === "max") return "Shortened to 4 h, the longest booking";
      if (r.stop === "booked") return r.back ? "Starts at " + from(r.s) + ": the room is booked until then"
        : "Shortened to " + until(r.e) + ": the room is booked from " + until(r.e);
      if (r.stop === "soon") return "Starts at " + from(r.s) + ": earlier times are too soon";
      return "";
    }
    function fresh(i) { start = i; end = span(i, i + dflt - 1).e; picking = true; note = ""; }

    function choose(i) {
      if (!free(i)) return;
      if (start === null || i < start) fresh(i);
      else if (picking || i > end) {
        var r = span(start, i);
        if (r.stop === "booked" || r.stop === "soon") fresh(i);  // past a booked block: start over there
        else { end = r.e; picking = false; note = trimmed(r); }
      } else { end = i; picking = false; note = ""; }  // inside the range: the end moves back to this block
      hover = null;
      paint();
    }

    function reset() { start = end = null; picking = false; note = ""; hover = null; reviewed = false; paint(); }

    function paint() {
      var pv = null;
      if (start !== null && hover !== null && hover > end) {
        var r = span(start, hover);
        if (r.e > end && r.stop !== "booked" && r.stop !== "soon") pv = r.e;
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
        if (start !== null && i > end) {
          var s = span(start, i);
          el.dataset.hint = s.stop === "booked" || s.stop === "soon" ? "Start " + from(i) : s.e === i ? "End " + until(i) : "End " + until(s.e) + " (4 h max)";
        } else el.dataset.hint = "Start " + from(i);
        var html = sel && i === start
          ? '<span class="slot-label">' + from(start) + "–" + until(end) + '</span><span class="slot-sub">' + dur(end - start + 1) + "</span>"
          : pv !== null && i === pv ? '<span class="slot-label">Until ' + until(pv) + " · " + dur(pv - start + 1) + "</span>" : "";
        if (el._html !== html) { body.innerHTML = el._html = html; }  // never swap nodes under a pressed pointer
      });
      var last = pv !== null ? pv : end, n = start === null ? 0 : last - start + 1;
      bar.classList.toggle("is-idle", start === null);
      bar.classList.toggle("is-preview", pv !== null);
      clear.hidden = start === null;
      if (start === null) {
        title.textContent = idle[0]; meta.textContent = idle[1]; startIn.value = ""; refund.hidden = true;
      } else {
        var price = Math.floor((rate * n + 1) / 2);  // round_half_up(rate x blocks / 2), PUR-R17
        title.textContent = day + " · " + from(start) + "–" + until(last);
        meta.textContent = dur(n) + " · " + (coverage === "plan" ? "Covered by your plan · " + thb(0)
          : coverage === "free" ? "Free · " + thb(0) : thb(price));
        // The refund terms for this exact start (PUR-R30): the booking.com "Free cancellation until ..." line.
        var at = Date.parse(date + "T" + from(start) + ":00+07:00");
        refund.textContent = coverage !== "pay" ? "You can cancel until the start"
          : at - now >= 24 * 3600e3 ? "Full refund until " + bkk(at - 24 * 3600e3) : "No refund: starts in less than 24 h";
        refund.hidden = false;
        startIn.value = from(start); blocksIn.value = end - start + 1;
      }
      // PUR-R39: with a live hold only the held space, start and blocks can be sent (it resumes the hold).
      var match = holdMatch && start !== null && holdMatch === date + " " + from(start) + " " + (end - start + 1);
      if (hold && !match && !anon) { btn.disabled = true; btn.textContent = "Finish " + hold + " first"; }
      else {
        btn.disabled = !anon && start === null;
        btn.textContent = !anon && narrow.matches && !reviewed && start !== null && !match ? "Review details" : label;
      }
      hint.textContent = note || (anon && start !== null ? "After you log in, tap your start time again; we keep the date and length."
        : picking ? "Select an end time, or keep 30 min" : "");
      hint.hidden = !hint.textContent;
      hint.classList.toggle("is-info", !!note);
      // The end handle sits on the bottom edge of the range: drag it to resize (touch included).
      handle.hidden = start === null || picking || pv !== null;
      if (!handle.hidden) handle.style.top = (slots[end].offsetTop + slots[end].offsetHeight) + "px";
    }

    var firstFree = slots.findIndex(function (el) { return !el.disabled; });
    root.addEventListener("click", function (e) {
      var el = e.target.closest(".slot");
      if (suppress) { suppress = false; return; }
      if (el) choose(+el.dataset.i);
    });
    // Drag across rows with a mouse or pen; touch scrolls the page, except on the end handle.
    root.addEventListener("pointerdown", function (e) {
      if (e.target === handle) {
        e.preventDefault();
        handle.setPointerCapture(e.pointerId);
        drag = { a: start, moved: false };
        return;
      }
      var el = e.target.closest(".slot");
      if (e.pointerType === "touch" || e.button !== 0 || !el || el.disabled) return;
      drag = { a: +el.dataset.i, moved: false };
    });
    // The row under the pointer, by height only, so the handle under a finger never hides it.
    function slotAt(y) {
      for (var i = 0; i < slots.length; i++) {
        var r = slots[i].getBoundingClientRect();
        if (r.height && y >= r.top && y < r.bottom) return slots[i];
      }
      return null;
    }
    document.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var el = slotAt(e.clientY);
      if (!el || el.disabled) return;
      var j = +el.dataset.i;
      if (j === drag.a && !drag.moved) return;
      drag.moved = true;
      var r = span(drag.a, j);
      start = r.s; end = r.e; picking = false; note = trimmed(r); hover = null;
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
      if (start !== null) paint();
    });
    root.addEventListener("pointerleave", function () { hover = null; if (start !== null) paint(); });
    root.addEventListener("focusin", function (e) {
      var el = e.target.closest(".slot");
      if (el && start !== null) { hover = +el.dataset.i; paint(); }
    });
    // Keyboard: slots are buttons (Enter and Space select); arrows move between free slots; Escape clears.
    root.addEventListener("keydown", function (e) {
      var el = e.target.closest(".slot");
      if (!el) return;
      if (e.key === "Escape" && start !== null) { reset(); return; }
      var step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      for (var k = +el.dataset.i + step; k >= 0 && k < slots.length; k += step) if (free(k)) { slots[k].focus(); return; }
    });
    clear.addEventListener("click", function () { reset(); if (firstFree >= 0) slots[firstFree].focus({ preventScroll: true }); });
    window.addEventListener("resize", paint);
    form.addEventListener("submit", function (e) {
      if (!anon && !startIn.value) { e.preventDefault(); note = "Select a start time first"; paint(); return; }
      // Phones: party size, note and the refund terms sit below the timeline. Show them once before the
      // hold is made, because a hold keeps them (PUR-R21: to change them, cancel and book again).
      if (!anon && narrow.matches && !reviewed && btn.textContent === "Review details") {
        e.preventDefault();
        reviewed = true;
        details.scrollIntoView({ behavior: "smooth", block: "start" });
        form.elements.party_size.focus({ preventScroll: true });
        paint();
      }
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
