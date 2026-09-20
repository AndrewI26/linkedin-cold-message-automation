/*
 * lcm capture bookmarklet.
 *
 * Reads the LinkedIn people-search results currently on your screen and
 * accumulates them in localStorage across pages, so you click once per results
 * page and export once at the end.
 *
 * Deliberately matches on STRUCTURE, not CSS classes. LinkedIn ships obfuscated,
 * rotating class names, so any `.artdeco-entity-lane__title` selector is dead
 * within weeks. The one stable thing on the page is that a person's card
 * contains a link to /in/<slug>; everything else is found relative to that.
 */
(function () {
  "use strict";

  var STORE_KEY = "lcm_leads_v1";

  // Lines LinkedIn adds that are chrome, not data.
  var NOISE = /^(•\s*)?(1st|2nd|3rd\+?|Connect|Message|Follow|Pending|View [^\n]*profile|Status is [^\n]*|Premium|• \d+(st|nd|rd|th))$/i;
  var CURRENT_PREFIX = /^(Current|Past):\s*/i;

  function normalizeUrl(href) {
    // Strip query strings, tracking params and trailing slashes so the same
    // person captured from two pages collapses to one key.
    var m = /linkedin\.com\/in\/([^/?#]+)/i.exec(href);
    return m ? "https://www.linkedin.com/in/" + m[1] : null;
  }

  function visibleLines(el) {
    var text = el.innerText || "";
    var out = [];
    text.split("\n").forEach(function (raw) {
      var line = raw.replace(/\s+/g, " ").trim();
      if (!line) return;
      if (NOISE.test(line)) return;
      // LinkedIn duplicates the name into a hidden accessibility span, so the
      // same string often appears twice in a row.
      if (out.length && out[out.length - 1] === line) return;
      out.push(line);
    });
    return out;
  }

  function distinctProfileCount(el) {
    var seen = {};
    var n = 0;
    Array.prototype.forEach.call(
      el.querySelectorAll('a[href*="/in/"]'),
      function (a) {
        var u = normalizeUrl(a.href);
        if (u && !seen[u]) {
          seen[u] = 1;
          n++;
        }
      }
    );
    return n;
  }

  function findCard(anchor) {
    // The global nav and page header both link to /in/ (your own "Me" menu).
    // Those are not search results.
    if (anchor.closest("nav, header")) return null;

    // Climb until the container stops being about one person. A result card
    // holds exactly one profile link; the moment a candidate holds two, we
    // have climbed out of the card and into the results list.
    var el = anchor;
    var best = null;
    for (var i = 0; i < 8 && el.parentElement; i++) {
      el = el.parentElement;
      if (el === document.body || el.tagName === "MAIN") break;
      if (distinctProfileCount(el) > 1) break;
      best = el;
      if (el.tagName === "LI") break;
    }
    if (!best) return null;

    // A real card carries a name plus at least a headline and one more line.
    // Allow two lines inside an <li>, where the list structure already vouches
    // for it, so a sparse result is not silently dropped.
    var lineCount = visibleLines(best).length;
    if (lineCount >= 3) return best;
    if (best.tagName === "LI" && lineCount >= 2) return best;
    return null;
  }

  function companyFrom(headline) {
    if (!headline) return null;
    var m = /\s+(?:at|@)\s+(.+)$/i.exec(headline);
    if (!m) return null;
    // "Recruiter at Acme | Hiring engineers" -> "Acme"
    return m[1].split(/\s*[|•·—–]\s*/)[0].trim() || null;
  }

  function parseCard(anchor, card) {
    var lines = visibleLines(card);
    var anchorName = (anchor.innerText || "").split("\n").map(function (s) {
      return s.trim();
    }).filter(function (s) {
      return s && !NOISE.test(s);
    })[0] || null;

    var name = anchorName || lines[0] || null;
    if (!name) return null;

    var idx = lines.indexOf(name);
    var rest = idx >= 0 ? lines.slice(idx + 1) : lines.slice(1);

    var headline = null;
    var location = null;
    var company = null;

    rest.forEach(function (line) {
      if (CURRENT_PREFIX.test(line)) {
        // "Current: Technical Recruiter at Acme"
        var val = line.replace(CURRENT_PREFIX, "");
        if (!company) company = companyFrom(val) || val;
        return;
      }
      if (!headline) {
        headline = line;
        return;
      }
      // Locations are short and comma-shaped; headlines are long. Not perfect,
      // but raw lines are kept so ingest can re-parse later.
      if (!location && line.length < 60) location = line;
    });

    if (!company) company = companyFrom(headline);

    return {
      name: name,
      headline: headline,
      company: company,
      location: location,
      profile_url: normalizeUrl(anchor.href),
      raw_lines: lines,
      captured_from: location_href(),
    };
  }

  function location_href() {
    try {
      return window.location.href.split("?")[0];
    } catch (e) {
      return null;
    }
  }

  function scrape() {
    var byUrl = {};
    var anchors = document.querySelectorAll('a[href*="/in/"]');
    Array.prototype.forEach.call(anchors, function (anchor) {
      var url = normalizeUrl(anchor.href);
      if (!url) return;
      // The logged-in user's own "Me" link and nav entries live outside results;
      // they have no multi-line card around them, which findCard reveals.
      var card = findCard(anchor);
      if (!card || card === anchor) return;
      var lead = parseCard(anchor, card);
      if (!lead || !lead.profile_url) return;
      // Prefer the richest version when a person appears twice on a page.
      var existing = byUrl[url];
      if (!existing || (lead.raw_lines.length > existing.raw_lines.length)) {
        byUrl[url] = lead;
      }
    });
    return byUrl;
  }

  function load() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    } catch (e) {
      return {};
    }
  }

  function save(store) {
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
  }

  function download(store) {
    var rows = Object.keys(store).map(function (k) {
      return store[k];
    });
    var blob = new Blob([JSON.stringify(rows, null, 2)], {
      type: "application/json",
    });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "lcm-leads-" + new Date().toISOString().slice(0, 10) + ".json";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 1000);
  }

  function panel(added, total, store) {
    var old = document.getElementById("lcm-panel");
    if (old) old.remove();

    var box = document.createElement("div");
    box.id = "lcm-panel";
    box.style.cssText = [
      "position:fixed", "top:16px", "right:16px", "z-index:2147483647",
      "background:#111", "color:#fff", "padding:14px 16px",
      "border-radius:10px", "font:13px/1.5 -apple-system,system-ui,sans-serif",
      "box-shadow:0 8px 28px rgba(0,0,0,.35)", "min-width:210px",
    ].join(";");

    var msg = document.createElement("div");
    msg.textContent = added + " new on this page";
    var sub = document.createElement("div");
    sub.style.cssText = "opacity:.65;margin-bottom:10px";
    sub.textContent = total + " captured in total";
    box.appendChild(msg);
    box.appendChild(sub);

    function button(label, bg, fn) {
      var b = document.createElement("button");
      b.textContent = label;
      b.style.cssText = "margin-right:6px;padding:5px 10px;border:0;border-radius:6px;cursor:pointer;font:inherit;background:" + bg + ";color:#fff";
      b.onclick = fn;
      box.appendChild(b);
      return b;
    }

    button("Export", "#0a66c2", function () {
      download(store);
    });
    button("Clear", "#444", function () {
      if (confirm("Clear all " + total + " captured leads?")) {
        localStorage.removeItem(STORE_KEY);
        box.remove();
      }
    });
    button("×", "transparent", function () {
      box.remove();
    });

    document.body.appendChild(box);
    setTimeout(function () {
      var p = document.getElementById("lcm-panel");
      if (p) p.style.opacity = "0.96";
    }, 10);
  }

  var found = scrape();
  var store = load();
  var added = 0;
  Object.keys(found).forEach(function (url) {
    if (!store[url]) added++;
    store[url] = found[url];
  });
  save(store);
  panel(added, Object.keys(store).length, store);
})();
