// Collectd Graph Panel: interactive charts, live refresh, theme and filters
(function () {
  "use strict";

  var LIVE_SECONDS = 60;
  var SI = ["y", "z", "a", "f", "p", "n", "µ", "m", "", "k", "M", "G", "T", "P", "E", "Z", "Y"];
  var charts = [];

  function storage(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) { /* storage unavailable */ }
    return null;
  }

  // --- number formatting, following the plugin's rrdtool legend format ---

  function siScale(v, base) {
    if (!v) return { value: v, prefix: "" };
    var e = Math.floor(Math.log(Math.abs(v)) / Math.log(base) + 1e-9);
    e = Math.max(-8, Math.min(8, e));
    return { value: v / Math.pow(base, e), prefix: SI[e + 8] };
  }

  function formatValue(v, f) {
    if (v === null || v === undefined || !isFinite(v)) return "–";
    if (f.si) {
      var s = siScale(v, f.base);
      var unit = s.prefix + f.suffix;
      return f.prefix + s.value.toFixed(f.decimals) + (unit && !/^\s/.test(unit) ? " " : "") + unit;
    }
    return f.prefix + v.toFixed(f.decimals) + f.suffix;
  }

  function formatAxis(v, f) {
    if (v === null || v === undefined) return "";
    if (f.si) {
      var s = siScale(v, f.base);
      return +s.value.toPrecision(3) + (s.prefix ? " " + s.prefix : "");
    }
    return String(+v.toPrecision(4));
  }

  // --- colours ---

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function hexToRgb(hex) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex) || [0, "0", "0", "0"];
    return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
  }

  // A solid colour between fg and bg, like CGP's faded colours but on any background
  function mix(fg, bg, amount) {
    var a = hexToRgb(fg), b = hexToRgb(bg);
    return "rgb(" + a.map(function (c, i) { return Math.round(c * amount + b[i] * (1 - amount)); }).join(",") + ")";
  }

  function alpha(hex, a) {
    return "rgba(" + hexToRgb(hex).join(",") + "," + a + ")";
  }

  function isDark() {
    return cssVar("--scheme") === "dark";
  }

  // Near-black lines (e.g. CPU steal) disappear on a dark background
  function lineColor(hex) {
    if (!isDark()) return hex;
    var rgb = hexToRgb(hex);
    return rgb[0] + rgb[1] + rgb[2] < 120 ? "#b0b0b0" : hex;
  }

  // --- charts ---

  function Chart(el) {
    this.el = el;
    this.plot = el.querySelector(".plot");
    this.src = el.dataset.src;
    this.big = el.hasAttribute("data-big");
    this.start = el.dataset.start ? +el.dataset.start : null;
    this.end = el.dataset.end ? +el.dataset.end : null;
    this.data = null;
    this.uplot = null;
    this.hidden = {};
    this.loading = false;
    charts.push(this);
  }

  Chart.prototype.zoomed = function () {
    return this.start !== null;
  };

  Chart.prototype.load = function (reload) {
    var self = this;
    if (self.loading) return;
    self.loading = true;
    var width = Math.max(self.plot.clientWidth, 100);
    var url = self.src + "&w=" + Math.round(width);
    if (self.zoomed()) url += "&start=" + self.start + "&end=" + self.end;
    self.el.classList.add("busy");
    fetch(url, reload ? { cache: "no-cache" } : {})
      .then(function (r) {
        return r.json().then(function (body) { return { ok: r.ok, body: body }; });
      })
      .then(function (r) {
        if (r.body.none) {
          var card = self.el.closest(".card");
          if (card) card.hidden = true;
          return;
        }
        if (!r.ok) throw new Error(r.body.error || "Couldn't load the graph");
        self.data = r.body;
        self.render();
      })
      .catch(function (e) {
        self.plot.innerHTML = "";
        var p = document.createElement("p");
        p.className = "chart-error";
        p.textContent = e.message;
        self.plot.appendChild(p);
      })
      .finally(function () {
        self.loading = false;
        self.el.classList.remove("busy");
      });
  };

  Chart.prototype.render = function () {
    var self = this, d = self.data, f = d.format;
    if (self.uplot) { self.uplot.destroy(); self.uplot = null; }
    self.plot.innerHTML = "";
    if (self.big) {
      var caption = self.el.closest(".card").querySelector("figcaption .title");
      if (caption) caption.textContent = d.title;
    }

    var surface = cssVar("--surface") || "#ffffff";
    var axisColor = cssVar("--muted"), gridColor = cssVar("--grid");
    var opaque = d.kind === "stacked" || d.kind === "uptime";

    var series = [{}].concat(d.series.map(function (s, i) {
      var color = lineColor(s.color);
      var opts = {
        label: s.label,
        stroke: color,
        width: s.width,
        points: { show: false },
        show: !self.hidden[i],
      };
      if (s.dash) opts.dash = [6, 4];
      if (s.fill) opts.fill = opaque ? mix(s.color, surface.charAt(0) === "#" ? surface : "#ffffff", isDark() ? 0.45 : 0.3)
                                     : alpha(s.color, 0.22);
      return opts;
    }));
    var data = [d.timestamps].concat(d.series.map(function (s) { return s.values; }));

    var lim = d.ylim;
    var yScale = {
      distr: lim.log ? 3 : 1,
      range: function (u, min, max) {
        if (lim.log) return [min || 1, max || 10];
        if (lim.rigid && lim.min !== null && lim.max !== null) return [lim.min, lim.max];
        if (min === null || max === null) { min = lim.min || 0; max = lim.max || 1; }
        if (lim.min !== null) min = Math.min(min, lim.min);
        if (lim.max !== null) max = Math.max(max, lim.max);
        if (max === min) max = min + 1;
        var pad = (max - min) * 0.05;
        return [min < 0 ? min - pad : min, max + pad];
      },
    };

    var height = parseInt(self.plot.style.height, 10) || 200;
    var opts = {
      width: self.plot.clientWidth,
      height: height,
      series: series,
      legend: { show: false },
      scales: { x: { time: true }, y: yScale },
      axes: [
        { stroke: axisColor, grid: { stroke: gridColor, width: 1 }, ticks: { stroke: gridColor, width: 1 } },
        {
          stroke: axisColor, grid: { stroke: gridColor, width: 1 }, ticks: { stroke: gridColor, width: 1 },
          label: d.vertical || undefined, labelSize: 20, labelFont: "12px system-ui, sans-serif",
          size: 60, values: function (u, splits) { return splits.map(function (v) { return formatAxis(v, f); }); },
        },
      ],
      cursor: {
        sync: { key: "cgp" },
        drag: { x: true, y: false, setScale: false },
        bind: { dblclick: function () { return null; } },
      },
      hooks: {
        setCursor: [function (u) { self.showCursor(u.cursor.idx); }],
        setSelect: [function (u) {
          if (u.select.width > 5) {
            var min = Math.floor(u.posToVal(u.select.left, "x"));
            var max = Math.ceil(u.posToVal(u.select.left + u.select.width, "x"));
            self.zoomTo(min, max);
          }
          u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
        }],
      },
    };

    self.uplot = new uPlot(opts, data, self.plot);
    self.uplot.over.addEventListener("dblclick", function () { self.reset(); });
    self.renderLegend();
  };

  var STAT_COLUMNS = [["min", "Min"], ["avg", "Avg"], ["max", "Max"], ["last", "Last"], ["total", "Total"], ["pct", "Value"]];

  Chart.prototype.renderLegend = function () {
    var self = this, d = self.data;
    var old = self.el.querySelector("table.legend");
    if (old) old.remove();
    var rows = d.series.map(function (s, i) { return [s, i]; }).filter(function (r) { return r[0].legend; });
    if (!rows.length) return;
    var columns = STAT_COLUMNS.filter(function (c) {
      return rows.some(function (r) { return r[0].stats && c[0] in r[0].stats; });
    });

    var table = document.createElement("table");
    table.className = "legend";
    var head = table.createTHead().insertRow();
    head.appendChild(document.createElement("th"));
    self.cursorHead = document.createElement("th");
    self.cursorHead.className = "cursor";
    head.appendChild(self.cursorHead);
    columns.forEach(function (c) {
      var th = document.createElement("th");
      th.textContent = c[1];
      head.appendChild(th);
    });

    var body = table.createTBody();
    self.cursorCells = [];
    rows.forEach(function (r) {
      var s = r[0], i = r[1];
      var tr = body.insertRow();
      tr.tabIndex = 0;
      tr.title = "Show or hide this line";
      if (self.hidden[i]) tr.classList.add("off");
      var name = tr.insertCell();
      var swatch = document.createElement("span");
      swatch.className = "swatch" + (s.dash ? " dashed" : "");
      swatch.style.setProperty("--c", lineColor(s.color));
      name.appendChild(swatch);
      name.appendChild(document.createTextNode(s.label));
      name.className = "name";
      var cur = tr.insertCell();
      cur.className = "cursor";
      self.cursorCells.push([cur, s]);
      columns.forEach(function (c) {
        var td = tr.insertCell();
        td.textContent = s.stats && c[0] in s.stats ? formatValue(s.stats[c[0]], d.format) : "";
      });
      function toggle() {
        self.hidden[i] = !self.hidden[i];
        tr.classList.toggle("off", self.hidden[i]);
        self.uplot.setSeries(i + 1, { show: !self.hidden[i] });
      }
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    });
    self.el.appendChild(table);
    self.showCursor(null);
  };

  Chart.prototype.showCursor = function (idx) {
    var self = this;
    if (!self.cursorCells) return;
    var has = idx !== null && idx !== undefined;
    self.el.classList.toggle("hovering", has);
    self.cursorHead.textContent = has ? new Date(self.data.timestamps[idx] * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    }) : "";
    self.cursorCells.forEach(function (c) {
      var values = c[1].hover || c[1].values;
      c[0].textContent = has ? formatValue(values[idx], self.data.format) : "";
    });
  };

  Chart.prototype.zoomTo = function (start, end) {
    this.start = Math.round(start);
    this.end = Math.round(end);
    this.el.classList.add("zoomed");
    this.syncUrl();
    this.load();
  };

  Chart.prototype.reset = function () {
    if (!this.zoomed()) return;
    this.start = this.end = null;
    this.el.classList.remove("zoomed");
    this.syncUrl();
    this.load();
  };

  Chart.prototype.range = function () {
    if (this.zoomed()) return [this.start, this.end];
    return this.data ? [this.data.start, this.data.end] : null;
  };

  // The detail page keeps a zoomed range in its address, to share or reload
  Chart.prototype.syncUrl = function () {
    if (!this.big || !window.history.replaceState) return;
    var url = new URL(window.location.href);
    if (this.zoomed()) {
      url.searchParams.set("start", this.start);
      url.searchParams.set("end", this.end);
    } else {
      url.searchParams.delete("start");
      url.searchParams.delete("end");
    }
    window.history.replaceState(null, "", url);
  };

  Chart.prototype.resize = function () {
    if (this.uplot) this.uplot.setSize({ width: this.plot.clientWidth, height: this.uplot.height });
  };

  function setupCharts() {
    if (typeof uPlot === "undefined") return;
    var els = document.querySelectorAll(".chart[data-src]");
    if (!els.length) return;
    var observer = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          observer.unobserve(e.target);
          e.target._chart.load();
        }
      });
    }, { rootMargin: "300px 0px" }) : null;

    Array.prototype.forEach.call(els, function (el) {
      el._chart = new Chart(el);
      if (el._chart.zoomed()) el.classList.add("zoomed");
      if (observer) observer.observe(el);
      else el._chart.load();
    });

    var timer;
    window.addEventListener("resize", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { charts.forEach(function (c) { c.resize(); }); }, 150);
    });

    // detail page controls
    document.querySelectorAll("[data-move], [data-zoom], [data-reset]").forEach(function (button) {
      button.addEventListener("click", function () {
        var chart = charts[0];
        var r = chart && chart.range();
        if (!r) return;
        var span = r[1] - r[0];
        if (button.hasAttribute("data-reset")) chart.reset();
        else if (button.dataset.move) {
          var shift = span * +button.dataset.move;
          var end = Math.min(r[1] + shift, Math.floor(Date.now() / 1000));
          chart.zoomTo(end - span, end);
        } else {
          var mid = (r[0] + r[1]) / 2, half = span * +button.dataset.zoom / 2;
          var now = Math.floor(Date.now() / 1000);
          chart.zoomTo(mid - half, Math.min(mid + half, now));
        }
      });
    });
  }

  function redrawCharts() {
    charts.forEach(function (c) { if (c.data) c.render(); });
  }

  // --- live refresh ---

  function setupLive() {
    var box = document.getElementById("live");
    if (!box) return;
    var timer = null;
    function tick() {
      if (document.hidden) return;
      if (!charts.length) {
        window.location.reload();
        return;
      }
      charts.forEach(function (c) { if (c.data && !c.zoomed()) c.load(true); });
      document.querySelectorAll(".card img").forEach(function (img) {
        var url = new URL(img.src);
        url.searchParams.set("_", Date.now());
        img.src = url;
      });
    }
    function apply() {
      clearInterval(timer);
      timer = box.checked ? setInterval(tick, LIVE_SECONDS * 1000) : null;
      document.body.classList.toggle("live", box.checked);
    }
    box.checked = storage("cgp-live") === "1";
    box.addEventListener("change", function () {
      storage("cgp-live", box.checked ? "1" : null);
      apply();
    });
    apply();
  }

  // --- theme ---

  function setupTheme() {
    var button = document.getElementById("theme");
    var order = ["auto", "light", "dark"];
    function current() { return document.documentElement.dataset.theme || "auto"; }
    function label() { button.title = "Theme: " + current(); }
    if (button) {
      label();
      button.addEventListener("click", function () {
        var next = order[(order.indexOf(current()) + 1) % order.length];
        if (next === "auto") delete document.documentElement.dataset.theme;
        else document.documentElement.dataset.theme = next;
        storage("cgp-theme", next === "auto" ? null : next);
        label();
        redrawCharts();
      });
    }
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", redrawCharts);
    }
  }

  // --- graph filter ---

  function setupFilter() {
    var input = document.getElementById("filter");
    if (!input) return;
    input.addEventListener("input", function () {
      var terms = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      document.querySelectorAll(".graphs .card").forEach(function (card) {
        var title = card.dataset.title || "";
        card.classList.toggle("filtered", !terms.every(function (t) { return title.indexOf(t) >= 0; }));
      });
      document.querySelectorAll("section.plugin").forEach(function (section) {
        section.classList.toggle("filtered", !section.querySelector(".card:not(.filtered)"));
      });
      // charts that come into view need loading
      window.dispatchEvent(new Event("scroll"));
    });
  }

  // --- "updated 5 minutes ago" ---

  function setupTimes() {
    var times = document.querySelectorAll("time.ago[data-epoch]");
    if (!times.length || !window.Intl || !Intl.RelativeTimeFormat) return;
    var rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    var units = [["day", 86400], ["hour", 3600], ["minute", 60], ["second", 1]];
    function update() {
      var now = Date.now() / 1000;
      times.forEach(function (t) {
        var diff = Math.round(+t.dataset.epoch - now);
        for (var i = 0; i < units.length; i++) {
          if (Math.abs(diff) >= units[i][1] || units[i][0] === "second") {
            t.textContent = rtf.format(Math.round(diff / units[i][1]), units[i][0]);
            break;
          }
        }
        t.title = new Date(+t.dataset.epoch * 1000).toLocaleString();
      });
    }
    update();
    setInterval(update, 15000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupTheme();
    setupCharts();
    setupLive();
    setupFilter();
    setupTimes();
  });
})();
