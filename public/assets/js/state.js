// PID state page. Works offline: only uses the jQuery and flot files
// served by the controller. Data is kept in this page only.

var all = [];
var MAX_POINTS = 20000;   // ~11 hours at one point per 2 seconds

function rnd(n) { return (n === null || n === undefined || isNaN(n)) ? "–" : Number(n).toFixed(2); }

function pad(n) { return (n < 10 ? "0" : "") + n; }

function unix_to_yymmdd_hhmmss(t) {
  var d = new Date(t * 1000);
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
         pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}

function average(field, minutes, data) {
  if (!data.length) return null;
  var oldest = data[data.length - 1].time - 60 * minutes;
  var sum = 0, n = 0;
  for (var i = data.length - 1; i >= 0 && data[i].time >= oldest; i--) { sum += data[i][field]; n++; }
  return n ? sum / n : null;
}

function percent_catching_up(data) {
  if (!data.length) return 0;
  var n = 0;
  for (var i = 0; i < data.length; i++) if (data[i].catching_up) n++;
  return n / data.length * 100;
}

var opts = {
  xaxis: { mode: null, tickFormatter: function (v) { var d = new Date(v * 1000); return pad(d.getHours()) + ":" + pad(d.getMinutes()); } },
  grid: { borderWidth: 1, color: "#888", backgroundColor: "#fff" },
  legend: { position: "nw" },
  series: { shadowSize: 0, lines: { lineWidth: 1.5 } }
};

function series(field, label, color) {
  return { label: label, color: color, data: all.map(function (r) { return [r.time, r[field]]; }) };
}

function drawall() {
  var catchup = { label: "catching up", color: "#e67e22", points: { show: true, radius: 1 }, lines: { show: false },
                  data: all.filter(function (r) { return r.catching_up; }).map(function (r) { return [r.time, r.ispoint]; }) };
  $.plot("#chart-temps", [series("setpoint", "target", "#2e7d32"), series("ispoint", "temp", "#1565c0"), catchup], opts);
  $.plot("#chart-error", [series("err", "error", "#c62828")], opts);
  $.plot("#chart-heat", [series("out", "heat %", "#ef6c00")], $.extend(true, {}, opts, { yaxis: { min: 0, max: 100 } }));
  $.plot("#chart-p", [series("p", "P", "#6a1b9a")], opts);
  $.plot("#chart-i", [series("i", "I", "#00838f")], opts);
  $.plot("#chart-d", [series("d", "D", "#4e342e")], opts);
}

var columns = [["datetime", "DateTime"], ["setpoint", "Target"], ["ispoint", "Temp"], ["err", "Error"],
               ["p", "P"], ["i", "I"], ["d", "D"], ["out", "Heat"], ["catching_up", "Catching Up"], ["timeDelta", "Time Delta"]];

function draw_table() {
  var html = "<tr>" + columns.map(function (c) { return "<th>" + c[1] + "</th>"; }).join("") + "</tr>";
  all.slice(-20).reverse().forEach(function (r) {
    html += "<tr>" + columns.map(function (c) {
      var v = r[c[0]];
      return "<td>" + (typeof v === "number" ? rnd(v) : String(v === undefined ? "" : v)) + "</td>";
    }).join("") + "</tr>";
  });
  document.getElementById("state-table").innerHTML = html;
}

function csv_download() {
  var lines = [columns.map(function (c) { return c[0]; }).join(",")];
  all.forEach(function (r) { lines.push(columns.map(function (c) { return r[c[0]]; }).join(",")); });
  var blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv" });
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "kiln-pid-" + unix_to_yymmdd_hhmmss(Date.now() / 1000).replace(/[ :]/g, "-") + ".csv";
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

var pending = false;
function handle(x) {
  if (x.type == "backlog") return;
  document.getElementById("state").textContent = JSON.stringify(x, null, 2);
  if (!x.pidstats || x.pidstats.time === undefined) return;
  var p = $.extend({}, x.pidstats);
  p.datetime = unix_to_yymmdd_hhmmss(p.time);
  p.err = p.err * -1;
  p.out = p.out * 100;
  p.catching_up = !!x.catching_up;
  if (all.length && all[all.length - 1].time === p.time) return;
  all.push(p);
  if (all.length > MAX_POINTS) all.splice(0, all.length - MAX_POINTS);

  $("#temp").text(rnd(p.ispoint));
  $("#target").text(rnd(p.setpoint));
  $("#error-current").text(rnd(p.err));
  $("#error-1min").text(rnd(average("err", 1, all)));
  $("#error-5min").text(rnd(average("err", 5, all)));
  $("#error-15min").text(rnd(average("err", 15, all)));
  $("#heat-pct").text(rnd(p.out));
  $("#catching-up").text(rnd(percent_catching_up(all)));

  // redraw at most once per animation frame
  if (!pending) {
    pending = true;
    window.requestAnimationFrame(function () { pending = false; drawall(); draw_table(); });
  }
}

$(function () {
  $("#csv").on("click", csv_download);
  var es = new EventSource("/api/events");
  es.onopen = function () { $("#conn").text("live").removeClass("bad"); };
  es.onerror = function () { $("#conn").text("reconnecting…").addClass("bad"); };
  es.onmessage = function (e) { handle(JSON.parse(e.data)); };
});
