/* motion-lite.js: one-pass motion for dashboards and pages.
 *
 * Rules from "Claude Motion Guide For Marketers" (NipPro AI):
 * - the final text is the real value already in the page; animation only plays toward it
 *   and always ends by restoring that exact text (no number is computed here);
 * - one pass when the element first scrolls into view, never a loop;
 * - prefers-reduced-motion or no JS = the static final state.
 *
 * Usage: <script src="motion-lite.js" defer
 *          data-values=".card-value,.kpi-value"          (number elements to count up)
 *          data-charts="svg.chart-svg,svg.goal-svg"      (SVG charts: lines draw, bars grow)
 *          data-hbars=".hbar-fill">                     (HTML bars: grow from the left)
 * Elements with data-motion-count are always included.
 */
(function () {
  "use strict";
  var script = document.currentScript || {};
  var ds = script.dataset || {};
  var VALUE_SEL = ["[data-motion-count]"].concat((ds.values || "").split(",")).filter(Boolean).join(",");
  var CHART_SEL = ds.charts || "";
  var HBAR_SEL = ds.hbars || "";
  var DURATION = 900;
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced || !("IntersectionObserver" in window) || !window.requestAnimationFrame) return;

  var played = new Set();   // signatures already animated (re-renders of the same data stay still)
  function ease(t) { return 1 - Math.pow(1 - t, 3); }
  // After a one-pass animation, hand the element back to the page's own CSS (hover
  // transitions, transforms) by clearing every inline style motion-lite set.
  function settle(el, props, ms) {
    setTimeout(function () {
      el.style.transition = "";
      props.forEach(function (name) { el.style[name] = ""; });
    }, ms);
  }

  // ---------- numbers ----------
  // First numeric token in the element; skips dates, times and ranges.
  var NUM = /(\d{1,3}(?:[,   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)/;
  function firstNumberNode(el) {
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = walker.nextNode())) {
      var t = n.nodeValue;
      if (!/\d/.test(t)) continue;
      if (/\d[\/:-]\d|\d{4}-\d{2}/.test(t)) return null; // date, time or range: leave it alone
      return n;
    }
    return null;
  }
  function parseToken(tok) {
    var group = "", dec = "";
    var m = tok.match(/[,.   ]/g) || [];
    if (m.length) {
      var last = tok.lastIndexOf(m[m.length - 1]);
      var tail = tok.length - last - 1;
      var lastSep = m[m.length - 1];
      if ((lastSep === "." || lastSep === ",") && (tail !== 3 || m.length > 1 && m[0] !== lastSep)) {
        dec = lastSep;
        if (m.length > 1) group = m[0];
      } else {
        group = lastSep;
      }
    }
    var digits = tok.split(group || "\u0000").join("");
    var decimals = 0;
    if (dec) { decimals = digits.length - digits.lastIndexOf(dec) - 1; digits = digits.replace(dec, "."); }
    return { value: parseFloat(digits), group: group, dec: dec, decimals: decimals };
  }
  function format(v, p) {
    var s = v.toFixed(p.decimals);
    var parts = s.split(".");
    if (p.group) parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, p.group);
    return parts.length > 1 ? parts[0] + (p.dec || ".") + parts[1] : parts[0];
  }
  function countUp(el) {
    var node = firstNumberNode(el);
    if (!node) return;
    var text = node.nodeValue, m = text.match(NUM);
    if (!m) return;
    var p = parseToken(m[1]);
    if (!isFinite(p.value) || p.value < 2) return;
    var sig = "v|" + (el.parentElement ? el.parentElement.textContent.slice(0, 80) : "") + "|" + el.textContent;
    if (played.has(sig)) return;
    played.add(sig);
    var before = text.slice(0, m.index), after = text.slice(m.index + m[1].length);
    el.style.fontVariantNumeric = "tabular-nums";
    var t0 = null;
    function tick(now) {
      if (t0 === null) t0 = now;
      var k = Math.min(1, (now - t0) / DURATION);
      node.nodeValue = k < 1 ? before + format(p.value * ease(k), p) + after : text; // exact original at the end
      if (k < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
    // rAF pauses in hidden tabs: the exact value always comes back, even mid-count
    setTimeout(function () { node.nodeValue = text; }, DURATION + 600);
  }

  // ---------- charts ----------
  function drawSvg(svg) {
    var sig = "c|" + (svg.getAttribute("aria-label") || "") + "|" + svg.innerHTML.length;
    if (played.has(sig)) return;
    played.add(sig);
    var lines = svg.querySelectorAll("polyline, path");
    Array.prototype.forEach.call(lines, function (l) {
      var cs = getComputedStyle(l);
      if (cs.fill !== "none" && l.getAttribute("fill") !== "none") return;
      if (cs.strokeDasharray && cs.strokeDasharray !== "none") return; // dashed target lines keep their dashes
      if (cs.animationName && cs.animationName !== "none") return;    // the page already animates it
      if (!l.getTotalLength) return;
      var len = l.getTotalLength();
      if (!len) return;
      l.style.strokeDasharray = len;
      l.style.strokeDashoffset = len;
      l.getBoundingClientRect();
      l.style.transition = "stroke-dashoffset " + DURATION + "ms cubic-bezier(.33,1,.68,1)";
      l.style.strokeDashoffset = "0";
      settle(l, ["strokeDasharray", "strokeDashoffset"], DURATION + 100);
    });
    var bars = svg.querySelectorAll("rect");
    var real = Array.prototype.filter.call(bars, function (r) {
      var fill = r.getAttribute("fill");
      return fill !== "transparent" && fill !== "none" && +r.getAttribute("height") > 0 && !r.classList.contains("chart-overlay");
    });
    real = real.filter(function (r) { var a = getComputedStyle(r).animationName; return !a || a === "none"; });
    real.forEach(function (r, i) {
      r.style.transformBox = "fill-box";
      r.style.transformOrigin = "50% 100%";
      r.style.transform = "scaleY(0)";
      r.getBoundingClientRect();
      var delay = Math.min(i * 40, 400);
      r.style.transition = "transform " + (DURATION * 0.7) + "ms cubic-bezier(.33,1,.68,1) " + delay + "ms";
      r.style.transform = "scaleY(1)";
      settle(r, ["transform", "transformBox", "transformOrigin"], DURATION * 0.7 + delay + 100);
    });
    var dots = svg.querySelectorAll("circle");
    Array.prototype.forEach.call(dots, function (c) {
      c.style.opacity = "0";
      c.getBoundingClientRect();
      c.style.transition = "opacity 300ms ease " + DURATION + "ms";
      c.style.opacity = "";
      settle(c, ["opacity"], DURATION + 400);
    });
  }
  function growBar(el, i) {
    var sig = "h|" + (el.parentElement ? el.parentElement.parentElement && el.parentElement.parentElement.textContent.slice(0, 120) : "") + "|" + el.style.width;
    if (played.has(sig)) return;
    played.add(sig);
    el.style.transformOrigin = "0 50%";
    el.style.transform = "scaleX(0)";
    el.getBoundingClientRect();
    var delay = Math.min(i * 50, 500);
    el.style.transition = "transform " + (DURATION * 0.8) + "ms cubic-bezier(.33,1,.68,1) " + delay + "ms";
    el.style.transform = "scaleX(1)";
    settle(el, ["transform", "transformOrigin"], DURATION * 0.8 + delay + 100);
  }

  // ---------- wiring ----------
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      io.unobserve(e.target);
      var el = e.target, kind = el.__motionKind;
      if (kind === "v") countUp(el);
      else if (kind === "c") drawSvg(el);
      else if (kind === "h") growBar(el, el.__motionIndex || 0);
    });
  }, { threshold: 0.3 });

  function scan(root) {
    if (!root.querySelectorAll) return;
    function add(sel, kind) {
      if (!sel) return;
      var list = root.matches && root.matches(sel) ? [root] : [];
      list = list.concat(Array.prototype.slice.call(root.querySelectorAll(sel)));
      list.forEach(function (el, i) {
        if (el.__motionSeen || el.closest(".no-motion")) return; // .no-motion: e.g. lower-is-better values
        el.__motionSeen = true; el.__motionKind = kind; el.__motionIndex = i;
        io.observe(el);
      });
    }
    add(VALUE_SEL, "v");
    add(CHART_SEL, "c");
    add(HBAR_SEL, "h");
  }
  var ALL_SEL = [VALUE_SEL, CHART_SEL, HBAR_SEL].filter(Boolean).join(",");
  // Only rescan when a mutation added something motion-lite cares about (tooltips that
  // rewrite themselves on every mousemove don't trigger a full-document scan).
  function relevant(mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (n.nodeType === 1 && (n.matches(ALL_SEL) || n.querySelector(ALL_SEL))) return true;
      }
    }
    return false;
  }
  var pending = false;
  var mo = new MutationObserver(function (mutations) {
    if (pending || !relevant(mutations)) return;
    pending = true;
    setTimeout(function () { pending = false; scan(document.body); }, 60); // not rAF: it pauses in hidden tabs
  });
  function start() { scan(document.body); mo.observe(document.body, { childList: true, subtree: true }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
