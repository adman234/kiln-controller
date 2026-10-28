// Public read-only kiln status. Polls status.json; there is nothing here
// that can change the kiln.
(function () {
    "use strict";
    var POLL_MS = 10000;
    var STATES = { IDLE: "Idle", RUNNING: "Firing", PAUSED: "Holding", SCHEDULED: "Delayed start", TUNING: "Autotuning" };
    var last = null, lastGraph = null;

    function $(id) { return document.getElementById(id); }
    function text(id, v) { $(id).textContent = v; }

    function fmtDuration(s) {
        s = Math.max(0, Math.round(s));
        var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
        return h ? h + "h " + (m < 10 ? "0" : "") + m + "m" : m + "m";
    }

    function ago(t) {
        if (!t) return "never";
        var s = Math.max(0, Date.now() / 1000 - t);
        return s < 60 ? Math.round(s) + " s ago" : fmtDuration(s) + " ago";
    }

    function render(d) {
        var online = d.online !== false;
        $("offline").hidden = online;
        if (!online) {
            text("updated", "Last reading " + ago(d.last_seen));
            if (!last) return;
            d = last;   // keep showing the last thing we knew
        } else {
            last = d;
        }
        text("title", d.title || "Kiln");
        document.title = (d.temperature != null ? d.temperature + "°" + d.unit + " · " : "") + (d.title || "Kiln");
        var st = $("state");
        st.textContent = STATES[d.state] || d.state || "?";
        st.className = "badge " + ({ RUNNING: "firing", PAUSED: "paused", SCHEDULED: "scheduled", TUNING: "firing" }[d.state] || "");
        var u = "°" + d.unit;
        text("temp", d.sensor_ok && d.temperature != null ? d.temperature : "ERR");
        text("unit1", d.sensor_ok ? u : "");
        text("target", d.target != null ? d.target : "–");
        text("unit2", d.target != null ? u : "");
        text("heat", d.state === "IDLE" || d.state === "SCHEDULED" ? "off" : (d.heating_percent || 0) + "%");

        var msgs = [];
        if (d.error) msgs.push({ title: "Problem", message: d.error });
        (d.issues || []).forEach(function (i) { msgs.push(i); });
        var att = $("attention");
        att.textContent = "";
        if (msgs.length) {
            msgs.forEach(function (m) {
                var b = document.createElement("b");
                b.textContent = m.title || "Alert";
                att.appendChild(b);
                if (m.message) att.appendChild(document.createTextNode(m.message));
            });
        } else if (d.attention) {
            att.textContent = "The kiln needs attention.";
        }
        att.hidden = !(msgs.length || d.attention);

        var firing = d.state === "RUNNING" || d.state === "PAUSED";
        $("run").hidden = !(firing || d.state === "SCHEDULED");
        if (firing) {
            text("profile", d.profile || "Firing");
            text("remaining", d.remaining_s != null ? fmtDuration(d.remaining_s) + " left" : "");
            $("bar").style.width = (d.progress_percent || 0) + "%";
        } else if (d.state === "SCHEDULED") {
            text("profile", (d.profile || "Firing") + " starts " +
                 (d.scheduled_start ? new Date(d.scheduled_start * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "later"));
            text("remaining", "");
            $("bar").style.width = "0";
        }
        lastGraph = d.graph;
        drawGraph(d.graph);
        if (online) text("updated", "Updated " + ago(d.time));
    }

    var NS = "http://www.w3.org/2000/svg";
    function el(name, attrs, parent) {
        var e = document.createElementNS(NS, name);
        for (var k in attrs) e.setAttribute(k, attrs[k]);
        parent.appendChild(e);
        return e;
    }

    function drawGraph(g) {
        var box = $("graphbox"), svg = $("graph");
        if (!g || !g.actual || g.actual.length < 2) { box.hidden = true; return; }
        box.hidden = false;
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        // draw at the real pixel width so text is never stretched
        var W = Math.max(200, Math.round(svg.getBoundingClientRect().width) || 600), H = 220, L = 40, B = 20, T = 8, R = 8;
        svg.setAttribute("viewBox", "0 0 " + W + " " + H);
        var all = g.actual.concat(g.profile || []);
        var maxX = Math.max.apply(null, all.map(function (p) { return p[0]; })) || 1;
        var maxY = Math.max.apply(null, all.map(function (p) { return p[1]; })) || 100;
        maxY = Math.ceil(maxY / 200) * 200;
        var x = function (v) { return L + (W - L - R) * v / maxX; };
        var y = function (v) { return T + (H - T - B) * (1 - v / maxY); };
        for (var t = 0; t <= maxY; t += maxY / 4) {
            el("line", { x1: L, x2: W - R, y1: y(t), y2: y(t), "class": "grid" }, svg);
            el("text", { x: L - 4, y: y(t) + 4, "text-anchor": "end", "class": "axis" }, svg).textContent = Math.round(t);
        }
        var hours = maxX / 3600, step = hours > 12 ? 4 : hours > 6 ? 2 : 1;
        for (var h = 0; h <= hours; h += step) {
            el("text", { x: x(h * 3600), y: H - 4, "text-anchor": "middle", "class": "axis" }, svg).textContent = h + "h";
        }
        var path = function (pts) {
            return pts.map(function (p, i) { return (i ? "L" : "M") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1); }).join(" ");
        };
        if (g.profile && g.profile.length > 1) el("path", { d: path(g.profile), "class": "profile" }, svg);
        el("path", { d: path(g.actual), "class": "actual" }, svg);
    }

    function poll() {
        var req = new XMLHttpRequest();
        req.open("GET", "status.json", true);
        req.timeout = 8000;
        req.onload = function () {
            try { render(JSON.parse(req.responseText)); } catch (e) { render({ online: false }); }
        };
        req.onerror = req.ontimeout = function () { render({ online: false, last_seen: last && last.time }); };
        req.send();
    }

    poll();
    setInterval(poll, POLL_MS);
    window.addEventListener("resize", function () { drawGraph(lastGraph); });
    // keep "updated N s ago" honest between polls
    setInterval(function () { if (last && $("offline").hidden) text("updated", "Updated " + ago(last.time)); }, 1000);
})();
