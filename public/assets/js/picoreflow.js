// Kiln controller web UI.
// Temperatures arrive from the server already in the display scale
// (cfg.temp_scale). Times are always seconds.

var state = "IDLE";
var state_last = "";
var graph = ['profile', 'live'];
var profiles = [];
var selected_profile = 0;
var selected_profile_name = null;
var cfg = { temp_scale: "c", time_scale_slope: "h", time_scale_profile: "m",
            currency_type: "$", kwh_rate: 0.15, kw_elements: 9 };
var settingsData = null;
var lastStatus = null;
var editor = { active: false, original_name: null };
var shownAutotune = null;
var diagTimer = null;
var events = null;
var connLost = false;
var drag = null;

graph.profile = {
    label: "Profile",
    data: [],
    points: { show: false },
    color: "#75890c",
    draggable: false
};

graph.live = {
    label: "Live",
    data: [],
    points: { show: false },
    color: "#d8d3c5",
    draggable: false
};

// ---------------------------------------------------------------------
// small helpers

function esc(s) {
    return String(s === undefined || s === null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function deg() { return "&deg;" + cfg.temp_scale.toUpperCase(); }

// small toast messages; msg is HTML (callers escape user content)
function notify(msg, type, delay) {
    var $t = $('<div class="alert alert-' + (type || 'info') + ' alert-dismissible" role="alert">' +
               '<button type="button" class="close" aria-label="Close"><span>&times;</span></button>' + msg + '</div>');
    $t.find('.close').on('click', function () { $t.remove(); });
    $('#toasts').append($t);
    if ($('#toasts .alert').length > 4) $('#toasts .alert').first().remove();
    if (delay !== 0) setTimeout(function () { $t.fadeOut(300, function () { $t.remove(); }); }, delay || 5000);
}

function apiErrorText(xhr) {
    try { return JSON.parse(xhr.responseText).error || xhr.statusText; }
    catch (e) { return xhr.statusText || "request failed"; }
}

function apiGet(url) {
    return $.ajax({ url: url, dataType: "json", cache: false });
}

function apiPost(url, body) {
    return $.ajax({ url: url, type: "POST", contentType: "application/json",
                    data: JSON.stringify(body || {}), dataType: "json" });
}

function fail(prefix) {
    return function (xhr) { notify("<b>" + esc(prefix) + "</b><br>" + esc(apiErrorText(xhr)), "danger"); };
}

function pad2(n) { return (n < 10 ? "0" : "") + n; }

// seconds -> H:MM:SS (works past 24h unlike Date().toISOString)
function fmtHMS(secs) {
    secs = Math.max(0, Math.round(secs));
    var h = Math.floor(secs / 3600), m = Math.floor(secs % 3600 / 60), s = secs % 60;
    return h + ":" + pad2(m) + ":" + pad2(s);
}

function fmtHM(secs) {
    secs = Math.max(0, Math.round(secs));
    var h = Math.floor(secs / 3600), m = Math.round(secs % 3600 / 60);
    if (m == 60) { h += 1; m = 0; }
    return h + "h " + pad2(m) + "m";
}

function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(iso);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmtMoney(v) { return esc(cfg.currency_type) + Number(v || 0).toFixed(2); }

function rateUnit() { return cfg.time_scale_slope == "m" ? "min" : "hr"; }

// degrees per second -> display rate (per hour or per minute)
function rateFromDps(dps) { return cfg.time_scale_slope == "m" ? dps * 60 : dps * 3600; }
function dpsFromRate(r) { return cfg.time_scale_slope == "m" ? r / 60 : r / 3600; }

// ---------------------------------------------------------------------
// profiles

function loadProfiles(selectName) {
    return apiGet("/api/profiles").done(function (resp) {
        profiles = resp.profiles;
        if (selectName) selected_profile_name = selectName;
        fillProfileSelect();
        if ($('#libraryModal').hasClass('in')) renderLibrary();
    }).fail(fail("Could not load schedules"));
}

function fillProfileSelect() {
    $('#profile_select').empty();
    var names = profiles.map(function (a) { return a.name; });
    if (names.length > 0 && $.inArray(selected_profile_name, names) === -1) {
        selected_profile = 0;
        selected_profile_name = names[0];
    }
    for (var i = 0; i < profiles.length; i++) {
        $('#profile_select').append('<option value="' + i + '">' + esc(profiles[i].name) + '</option>');
    }
    for (i = 0; i < profiles.length; i++) {
        if (profiles[i].name == selected_profile_name) {
            $('#profile_select').val(i);
            updateProfile(i);
        }
    }
}

function profileIndex(name) {
    for (var i = 0; i < profiles.length; i++) if (profiles[i].name == name) return i;
    return -1;
}

function updateProfile(id) {
    id = parseInt(id, 10);
    if (isNaN(id) || !profiles[id]) return;
    selected_profile = id;
    selected_profile_name = profiles[id].name;
    if (state != "RUNNING" && state != "PAUSED" && state != "TUNING") {
        graph.profile.data = profiles[id].data;
        replot();
    }
}

function estimateText(est) {
    if (!est) return ["", ""];
    var main = Number(est.kwh).toFixed(1) + " kWh (" + fmtMoney(est.cost) + ")";
    var how;
    if (est.method == "same_profile")
        how = "Average of your last " + est.runs_used + " firing(s) of this schedule.";
    else if (est.method == "history_model")
        how = "Learned from " + est.runs_used + " previous firing(s) of your kiln.";
    else
        how = "Rough guess (elements on half the time). Gets accurate after your first firing.";
    how += " Worst case " + Number(est.max_kwh).toFixed(1) + " kWh (" + fmtMoney(est.max_cost) + ").";
    return [main, how];
}

// ---------------------------------------------------------------------
// graph

function replot() {
    var series = [graph.profile, graph.live];
    graph.plot = $.plot("#graph_container", series, getOptions());
}

// Drag schedule points on the graph while editing. Pointer events work
// for mouse, pen and touch. Points cannot be dragged past their
// neighbours in time.
function bindGraphDrag() {
    var $g = $('#graph_container');
    function canvasPos(e) {
        var off = graph.plot.offset();
        return { x: e.pageX - off.left, y: e.pageY - off.top };
    }
    $g.on('pointerdown', function (ev) {
        if (!editor.active || !graph.plot) return;
        var e = ev.originalEvent, pos = canvasPos(e);
        var ax = graph.plot.getAxes(), d = graph.profile.data, best = -1, bestDist = 30;
        for (var i = 0; i < d.length; i++) {
            var dx = ax.xaxis.p2c(d[i][0]) - pos.x, dy = ax.yaxis.p2c(d[i][1]) - pos.y;
            var dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < bestDist) { bestDist = dist; best = i; }
        }
        if (best < 0) return;
        drag = { i: best, xmax: ax.xaxis.max, ymax: ax.yaxis.max };
        try { this.setPointerCapture(e.pointerId); } catch (err) {}
        ev.preventDefault();
    }).on('pointermove', function (ev) {
        if (!drag) return;
        var e = ev.originalEvent, pos = canvasPos(e), ax = graph.plot.getAxes(), d = graph.profile.data;
        var t = Math.round(ax.xaxis.c2p(pos.x) / 60) * 60;
        var temp = Math.round(ax.yaxis.c2p(pos.y));
        var lo = drag.i > 0 ? d[drag.i - 1][0] + 60 : 0;
        var hi = drag.i < d.length - 1 ? d[drag.i + 1][0] - 60 : Infinity;
        if (drag.i == 0) t = 0;
        d[drag.i] = [Math.max(lo, Math.min(hi, t)), Math.max(0, temp)];
        if (!drag.pending) {
            drag.pending = true;
            window.requestAnimationFrame(function () {
                if (drag) drag.pending = false;
                replot();
            });
        }
        ev.preventDefault();
    }).on('pointerup pointercancel', function () {
        if (!drag) return;
        drag = null;
        replot();
        updateProfileTable();
    });
}

function niceTick(span) {
    var steps = [300, 600, 900, 1800, 3600, 7200, 10800, 14400, 21600, 43200, 86400];
    for (var i = 0; i < steps.length; i++) if (span / steps[i] <= 12) return steps[i];
    return 86400;
}

function timeTickFormatter(val, axis) {
    if (axis.max > 3 * 3600) return Math.round(val / 3600 * 10) / 10 + "h";
    return Math.round(val / 60) + "m";
}

function getOptions() {
    var maxx = 0;
    $.each([graph.profile.data, graph.live.data], function (_, d) {
        if (d.length) maxx = Math.max(maxx, d[d.length - 1][0]);
    });
    var font = { size: 14, lineHeight: 14, weight: "normal", family: "Digi",
                 variant: "small-caps", color: "rgba(216, 211, 197, 0.85)" };
    return {
        series: { lines: { show: true }, points: { show: true, radius: 5, symbol: "circle" }, shadowSize: 3 },
        xaxis: { min: 0, tickColor: 'rgba(216, 211, 197, 0.2)', tickFormatter: timeTickFormatter,
                 tickSize: niceTick(maxx || 3600), font: font },
        yaxis: { min: 0, tickDecimals: 0, draggable: false, tickColor: 'rgba(216, 211, 197, 0.2)', font: font },
        grid: { color: 'rgba(216, 211, 197, 0.55)', borderWidth: 1, labelMargin: 10, mouseActiveRadius: 50 },
        legend: { show: false }
    };
}

function hazardTemp() {
    return cfg.temp_scale == "f" ? (1500 * 9 / 5) + 32 : 1500;
}

// ---------------------------------------------------------------------
// run control

function openStartModal() {
    var p = profiles[selected_profile];
    if (!p) return;
    var est = estimateText(p.estimate);
    $('#sel_prof').text(p.name);
    $('#sel_prof_eta').text(fmtHMS(p.duration));
    $('#sel_prof_peak').html(Math.round(p.peak) + deg());
    $('#sel_prof_cost').html(esc(est[0]));
    $('#sel_prof_cost_how').text(est[1]);
    $('input[name=start_when][value=now]').prop('checked', true);
    updateStartWhen();
    $('#jobSummaryModal').modal('show');
}

function startDelaySeconds() {
    var when = $('input[name=start_when]:checked').val();
    if (when == "delay") {
        var h = parseFloat($('#start_delay_h').val()) || 0;
        var m = parseFloat($('#start_delay_m').val()) || 0;
        return Math.max(0, Math.round(h * 3600 + m * 60));
    }
    if (when == "at") {
        var parts = ($('#start_at_time').val() || "").split(":");
        if (parts.length < 2) return null;
        var now = new Date();
        var t = new Date(now.getFullYear(), now.getMonth(), now.getDate(),
                         parseInt(parts[0], 10), parseInt(parts[1], 10), 0);
        if (t <= now) t.setDate(t.getDate() + 1);
        return Math.round((t - now) / 1000);
    }
    return 0;
}

function updateStartWhen() {
    var secs = startDelaySeconds();
    if (!secs) {
        $('#start_when_text').text("");
        $('#btn_start_confirm').text("Yes, start the run");
        return;
    }
    var start = new Date(Date.now() + secs * 1000);
    var p = profiles[selected_profile];
    var end = new Date(start.getTime() + (p ? p.duration * 1000 : 0));
    var opts = { weekday: 'short', hour: '2-digit', minute: '2-digit' };
    $('#start_when_text').text("Starts " + start.toLocaleString([], opts) + " (in " + fmtHM(secs) +
                               "), finishes around " + end.toLocaleString([], opts) + ".");
    $('#btn_start_confirm').text("Schedule the run");
}

function runTask() {
    var name = profiles[selected_profile].name;
    var secs = startDelaySeconds();
    if (secs === null) { notify("Pick a start time", "danger"); return; }
    $('#jobSummaryModal').modal('hide');
    if (secs > 0) {
        apiPost("/api", { cmd: "schedule", profile: name, delay_seconds: secs })
            .done(function () { notify("Firing scheduled", "success"); })
            .fail(fail("Could not schedule"));
    } else {
        graph.live.data = [];
        replot();
        apiPost("/api", { cmd: "run", profile: name }).fail(fail("Could not start"));
    }
}

function abortTask() {
    var what = state == "TUNING" ? "Stop autotune?" : state == "SCHEDULED" ? "Cancel the scheduled firing?" : "Stop the firing?";
    confirmAction(what, "The elements will be turned off.", "Stop", function () {
        apiPost("/api", { cmd: state == "SCHEDULED" ? "cancel_schedule" : "stop" }).fail(fail("Could not stop"));
    });
}

function pauseTask() { apiPost("/api", { cmd: "pause" }).fail(fail("Could not hold")); }
function resumeTask() { apiPost("/api", { cmd: "resume" }).fail(fail("Could not resume")); }

function confirmAction(title, body, okText, fn) {
    $('#confirm_title').text(title);
    $('#confirm_body').html(body);
    $('#confirm_ok').text(okText || "OK").off('click').on('click', function () {
        $('#confirmModal').modal('hide');
        fn();
    });
    $('#confirmModal').modal('show');
}

// ---------------------------------------------------------------------
// library

function openLibrary() {
    renderLibrary();
    $('#libraryModal').modal('show');
}

var librarySort = { key: "name", dir: 1 };

function renderLibrary() {
    var q = ($('#library_search').val() || "").toLowerCase();
    var rows = profiles.filter(function (p) {
        return !q || p.name.toLowerCase().indexOf(q) >= 0 || (p.notes || "").toLowerCase().indexOf(q) >= 0;
    });
    var keyf = {
        name: function (p) { return p.name.toLowerCase(); },
        created: function (p) { return p.created || ""; },
        modified: function (p) { return p.modified || ""; },
        duration: function (p) { return p.duration; },
        peak: function (p) { return p.peak; },
        fired: function (p) { return (p.history && p.history.last_fired) || ""; },
        cost: function (p) { return p.estimate ? p.estimate.cost : 0; }
    }[librarySort.key];
    rows.sort(function (a, b) {
        var x = keyf(a), y = keyf(b);
        return (x < y ? -1 : x > y ? 1 : 0) * librarySort.dir;
    });
    var cols = [["name", "Name"], ["created", "Created"], ["modified", "Modified"], ["duration", "Time"],
                ["peak", "Peak"], ["fired", "Last fired"], ["cost", "Est. cost"]];
    var html = "<thead><tr>";
    $.each(cols, function (_, c) {
        var arrow = librarySort.key == c[0] ? (librarySort.dir > 0 ? " &#9650;" : " &#9660;") : "";
        html += '<th class="sortable" data-key="' + c[0] + '">' + c[1] + arrow + '</th>';
    });
    html += "<th></th></tr></thead><tbody>";
    $.each(rows, function (_, p) {
        var i = profileIndex(p.name);
        var h = p.history;
        html += '<tr' + (p.name == selected_profile_name ? ' class="info"' : '') + '>';
        html += '<td><a href="#" class="lib-select" data-i="' + i + '"><b>' + esc(p.name) + '</b></a>' +
                (p.notes ? '<div class="small text-muted lib-notes">' + esc(p.notes) + '</div>' : '') + '</td>';
        html += '<td class="small">' + fmtDate(p.created) + '</td>';
        html += '<td class="small">' + fmtDate(p.modified) + '</td>';
        html += '<td>' + fmtHM(p.duration) + '</td>';
        html += '<td>' + Math.round(p.peak) + deg() + '</td>';
        html += '<td class="small">' + (h ? fmtDate(h.last_fired) + ' <span class="text-muted">(' + h.runs + 'x)</span>' : '<span class="text-muted">never</span>') + '</td>';
        html += '<td>' + (p.estimate ? fmtMoney(p.estimate.cost) : '') + '</td>';
        html += '<td class="text-nowrap">' +
            '<button class="btn btn-default btn-xs lib-edit" data-i="' + i + '" title="Edit"><span class="glyphicon glyphicon-edit"></span></button> ' +
            '<button class="btn btn-default btn-xs lib-copy" data-i="' + i + '" title="Duplicate"><span class="glyphicon glyphicon-file"></span></button> ' +
            '<a class="btn btn-default btn-xs" href="/api/profiles/export?name=' + encodeURIComponent(p.name) + '" download title="Download as a file"><span class="glyphicon glyphicon-export"></span></a> ' +
            '<button class="btn btn-danger btn-xs lib-delete" data-i="' + i + '" title="Delete"><span class="glyphicon glyphicon-trash"></span></button>' +
            '</td></tr>';
    });
    if (!rows.length) html += '<tr><td colspan="8" class="text-muted">No schedules found.</td></tr>';
    html += "</tbody>";
    $('#library_table').html(html);
}

function selectProfileByIndex(i) {
    $('#profile_select').val(i);
    updateProfile(i);
}

function copyProfile(name) {
    var suggested = name + " copy";
    var newName = window.prompt("Name for the copy:", suggested);
    if (newName === null) return;
    apiPost("/api/profiles/copy", { name: name, new_name: newName.trim() || null })
        .done(function (resp) {
            notify("Copied to <b>" + esc(resp.profile.name) + "</b>", "success");
            loadProfiles(resp.profile.name);
        }).fail(fail("Could not copy"));
}

function confirmDelete(name) {
    if (!name) { leaveEditMode(); return; }
    confirmAction("Delete schedule?", "Really delete <b>" + esc(name) + "</b>? This cannot be undone.", "Delete", function () {
        apiPost("/api/profiles/delete", { name: name }).done(function () {
            notify("Deleted " + esc(name), "success");
            if (editor.active) leaveEditMode(true);
            selected_profile_name = null;
            loadProfiles();
        }).fail(fail("Could not delete"));
    });
}

// ---------------------------------------------------------------------
// editor

function setEditUI(on) {
    editor.active = on;
    $('#profile_table').empty();
    if (on) {
        $('#status').slideUp();
        $('#edit').show();
        $('#profile_selector').hide();
        $('#btn_controls').hide();
        $('#profile_table').slideDown();
    } else {
        $('#edit').hide();
        $('#profile_selector').show();
        $('#btn_controls').show();
        $('#status').slideDown();
        $('#profile_table').slideUp();
    }
    graph.profile.points.show = on;
    $('#graph_container').toggleClass('editing', on);
}

function enterNewMode() {
    editor.original_name = null;
    $('#form_profile_name').val('');
    graph.profile.data = [[0, cfg.temp_scale == "f" ? 70 : 20]];
    graph.live.data = [];
    editor.notes = "";
    $('#edit_time_unit').val(cfg.time_scale_profile == "h" ? "h" : "m");
    $('#btn_delProfile').hide();
    setEditUI(true);
    replot();
    updateProfileTable();
}

function enterEditMode(i) {
    if (i === undefined) i = selected_profile;
    var p = profiles[i];
    if (!p) return;
    editor.original_name = p.name;
    editor.notes = p.notes || "";
    $('#form_profile_name').val(p.name);
    graph.profile.data = p.data.map(function (pt) { return [pt[0], pt[1]]; });
    graph.live.data = [];
    $('#edit_time_unit').val(cfg.time_scale_profile == "h" ? "h" : "m");
    $('#btn_delProfile').show();
    setEditUI(true);
    replot();
    updateProfileTable();
}

function leaveEditMode(skipReload) {
    setEditUI(false);
    if (selected_profile_name && profileIndex(selected_profile_name) >= 0)
        graph.profile.data = profiles[profileIndex(selected_profile_name)].data;
    replot();
    if (!skipReload) loadProfiles();
}

function timeUnit() { return $('#edit_time_unit').val() || "m"; }

function fmtTime(secs) {
    var u = timeUnit();
    if (u == "h") return String(Math.round(secs / 36) / 100);
    if (u == "hm") {
        var h = Math.floor(secs / 3600), m = Math.round(secs % 3600 / 60);
        if (m == 60) { h += 1; m = 0; }
        return h + ":" + pad2(m);
    }
    return String(Math.round(secs / 6) / 10);
}

function parseTime(text) {
    text = String(text).trim();
    var u = timeUnit();
    if (text.indexOf(":") >= 0) {
        var parts = text.split(":");
        return Math.round((parseFloat(parts[0]) || 0) * 3600 + (parseFloat(parts[1]) || 0) * 60);
    }
    var v = parseFloat(text);
    if (isNaN(v)) return null;
    if (u == "h" || u == "hm") return Math.round(v * 3600);
    return Math.round(v * 60);
}

function timeUnitLabel() {
    return { m: "min", h: "hours", hm: "h:mm" }[timeUnit()];
}

function updateProfileTable() {
    // keep notes the user typed when the table is redrawn (e.g. while dragging points)
    if ($('#form_profile_notes').length) editor.notes = $('#form_profile_notes').val();
    var d = graph.profile.data;
    var html = '<div class="table-responsive"><table class="table table-striped table-condensed profile-edit">';
    html += '<tr><th style="width:40px">#</th><th>Time (' + timeUnitLabel() + ')</th><th>Temp (' + deg() + ')</th>' +
            '<th>Rate (' + deg() + '/' + rateUnit() + ')</th><th>Segment</th><th></th></tr>';
    for (var i = 0; i < d.length; i++) {
        var rate = "", seg = "", arrow = "", color = "grey";
        if (i >= 1) {
            var dt = d[i][0] - d[i - 1][0];
            var dT = d[i][1] - d[i - 1][1];
            var r = dt > 0 ? rateFromDps(dT / dt) : 0;
            rate = dT == 0 ? "hold" : String(Math.round(Math.abs(r)));
            arrow = dT > 0 ? "up" : dT < 0 ? "down" : "right";
            color = dT > 0 ? "rgba(206, 5, 5, 1)" : dT < 0 ? "rgba(23, 108, 204, 1)" : "grey";
            seg = (dT == 0 ? "hold " : dT > 0 ? "heat " : "cool ") + fmtHM(dt);
        }
        html += '<tr' + (i >= 1 && d[i][0] <= d[i - 1][0] ? ' class="danger"' : '') + '>';
        html += '<td>' + (i + 1) + '</td>';
        html += '<td><input type="text" class="form-control input-sm pt-time" data-row="' + i + '" value="' + esc(fmtTime(d[i][0])) + '"' + (i == 0 ? ' readonly title="The first point is the start"' : '') + '></td>';
        html += '<td><input type="text" class="form-control input-sm pt-temp" data-row="' + i + '" value="' + Math.round(d[i][1]) + '"></td>';
        html += '<td>' + (i >= 1 ? '<div class="input-group input-group-sm"><span class="input-group-addon ds-trend" style="background:' + color + '"><span class="glyphicon glyphicon-circle-arrow-' + arrow + '"></span></span>' +
                '<input type="text" class="form-control pt-rate" data-row="' + i + '" value="' + esc(rate) + '"' + (rate == "hold" ? ' readonly' : '') + '></div>' : '') + '</td>';
        html += '<td class="small text-muted">' + seg + '</td>';
        html += '<td class="text-nowrap">' +
                '<button class="btn btn-default btn-xs pt-insert" data-row="' + i + '" title="Insert a hold after this point"><span class="glyphicon glyphicon-plus"></span></button> ' +
                (i > 0 ? '<button class="btn btn-default btn-xs pt-delete" data-row="' + i + '" title="Delete point"><span class="glyphicon glyphicon-minus"></span></button>' : '') +
                '</td></tr>';
    }
    html += '</table></div>';

    // quick segment builder, the way most kiln controllers are programmed
    html += '<div class="form-inline segment-builder"><b>Add segment:</b> ' +
        'ramp <input type="number" id="seg_rate" class="form-control input-sm" style="width:80px" value="' + (cfg.temp_scale == "f" ? 200 : 100) + '"> ' + deg() + '/' + rateUnit() +
        ' to <input type="number" id="seg_temp" class="form-control input-sm" style="width:80px"> ' + deg() +
        ' then hold <input type="number" id="seg_hold" class="form-control input-sm" style="width:70px" value="0"> min ' +
        '<button type="button" class="btn btn-primary btn-sm" onclick="addSegment()">Add</button></div>';
    html += '<div class="form-group" style="margin-top:10px"><label>Notes</label><textarea id="form_profile_notes" class="form-control" rows="2" maxlength="2000" placeholder="Clay body, cone, glaze, anything you want to remember">' + esc(editor.notes || "") + '</textarea></div>';
    $('#profile_table').html(html);
}

function pointsChanged() {
    replot();
    updateProfileTable();
}

function shiftAfter(row, delta) {
    var d = graph.profile.data;
    for (var j = row + 1; j < d.length; j++) d[j][0] += delta;
}

function addSegment() {
    var d = graph.profile.data;
    var rate = Math.abs(parseFloat($('#seg_rate').val()));
    var temp = parseFloat($('#seg_temp').val());
    var hold = parseFloat($('#seg_hold').val()) || 0;
    if (isNaN(temp)) { notify("Enter a target temperature", "danger"); return; }
    var last = d.length ? d[d.length - 1] : [0, cfg.temp_scale == "f" ? 70 : 20];
    if (!d.length) d.push([0, last[1]]);
    if (temp != last[1]) {
        if (!rate) { notify("Enter a ramp rate", "danger"); return; }
        var secs = Math.abs(temp - last[1]) / dpsFromRate(rate);
        d.push([Math.round(last[0] + secs), temp]);
    }
    if (hold > 0) {
        var l = d[d.length - 1];
        d.push([Math.round(l[0] + hold * 60), l[1]]);
    }
    pointsChanged();
    $('#seg_temp').focus();
}

$(document).on('change', '.pt-time', function () {
    var row = parseInt($(this).data('row'), 10);
    var d = graph.profile.data;
    var t = parseTime($(this).val());
    if (t === null || t < 0) { notify("Not a valid time", "danger"); updateProfileTable(); return; }
    var delta = t - d[row][0];
    d[row][0] = t;
    if ($('#edit_shift').is(':checked')) shiftAfter(row, delta);
    pointsChanged();
});

$(document).on('change', '.pt-temp', function () {
    var row = parseInt($(this).data('row'), 10);
    var v = parseFloat($(this).val());
    if (isNaN(v)) { notify("Not a valid temperature", "danger"); updateProfileTable(); return; }
    graph.profile.data[row][1] = v;
    pointsChanged();
});

$(document).on('change', '.pt-rate', function () {
    var row = parseInt($(this).data('row'), 10);
    var d = graph.profile.data;
    var r = Math.abs(parseFloat($(this).val()));
    if (!r) { notify("Rate must be a number above 0", "danger"); updateProfileTable(); return; }
    var dT = Math.abs(d[row][1] - d[row - 1][1]);
    var t = Math.round(d[row - 1][0] + dT / dpsFromRate(r));
    var delta = t - d[row][0];
    d[row][0] = t;
    shiftAfter(row, delta);  // keep the rest of the schedule's segments the same length
    pointsChanged();
});

$(document).on('click', '.pt-insert', function () {
    var row = parseInt($(this).data('row'), 10);
    var d = graph.profile.data;
    var holdSecs = 1800;
    d.splice(row + 1, 0, [d[row][0] + holdSecs, d[row][1]]);
    shiftAfter(row + 1, holdSecs);
    pointsChanged();
});

$(document).on('click', '.pt-delete', function () {
    var row = parseInt($(this).data('row'), 10);
    graph.profile.data.splice(row, 1);
    pointsChanged();
});

function toggleTable() {
    if ($('#profile_table').css('display') == 'none') $('#profile_table').slideDown();
    else $('#profile_table').slideUp();
}

function saveProfile(overwrite) {
    var name = $.trim($('#form_profile_name').val());
    if (!name) { notify("Please enter a name", "danger"); return false; }
    var raw = graph.profile.data;
    if (raw.length < 2) { notify("A schedule needs at least two points", "danger"); return false; }
    var data = [];
    var last = -1;
    for (var i = 0; i < raw.length; i++) {
        if (raw[i][0] <= last) {
            notify("<b>ERROR 88:</b><br/>An oven is not a time-machine. Point " + (i + 1) + " is not after point " + i + ".", "danger");
            return false;
        }
        data.push([raw[i][0], raw[i][1]]);
        last = raw[i][0];
    }
    var profile = { type: "profile", name: name, data: data, temp_units: cfg.temp_scale,
                    notes: $('#form_profile_notes').val() || "" };
    apiPost("/api/profiles", { profile: profile, original_name: editor.original_name, overwrite: !!overwrite })
        .done(function (resp) {
            notify("Saved <b>" + esc(resp.profile.name) + "</b>", "success", 2500);
            selected_profile_name = resp.profile.name;
            setEditUI(false);
            loadProfiles(resp.profile.name);
        })
        .fail(function (xhr) {
            var err = apiErrorText(xhr);
            if (err.indexOf("already exists") >= 0) {
                confirmAction("Overwrite schedule?", "A schedule named <b>" + esc(name) + "</b> already exists. Replace it?", "Overwrite",
                              function () { saveProfile(true); });
            } else {
                notify("<b>Could not save</b><br>" + esc(err), "danger");
            }
        });
    return true;
}

// ---------------------------------------------------------------------
// settings

function openSettings(tab) {
    apiGet("/api/settings").done(function (resp) {
        settingsData = resp;
        renderSettings();
        renderRecentAlerts(resp.notifications);
        renderAutotune(lastStatus ? lastStatus.autotune : null);
        loadHistory();
        loadUpdateInfo();
        $('#restart_banner').hide();
        $('#settings_msg').text("");
        $('#settingsModal').modal('show');
        if (tab) $('#settings_tabs a[href="#' + tab + '"]').tab('show');
    }).fail(fail("Could not load settings"));
}

function fieldUnit(item) {
    if (item.kind == "temp" || item.kind == "delta") return deg();
    return "";
}

function renderField(item, value) {
    var id = "set_" + item.key;
    var help = item.help ? '<span class="help-block small">' + esc(item.help) + (item.restart ? " (restart)" : "") + '</span>' :
               (item.restart ? '<span class="help-block small">needs restart</span>' : '');
    var html = '<div class="form-group setting-row" data-key="' + item.key + '"><label class="col-sm-5 control-label" for="' + id + '">' + esc(item.label) + '</label><div class="col-sm-7">';
    if (item.kind == "bool") {
        html += '<div class="checkbox"><label><input type="checkbox" id="' + id + '"' + (value ? " checked" : "") + '></label></div>';
    } else if (item.kind == "choice") {
        html += '<select id="' + id + '" class="form-control input-sm">';
        $.each(item.options, function (_, o) {
            html += '<option value="' + esc(o[0]) + '"' + (String(o[0]) == String(value) ? " selected" : "") + '>' + esc(o[1]) + '</option>';
        });
        html += '</select>';
    } else if (item.kind == "password") {
        html += '<input type="password" id="' + id + '" class="form-control input-sm" autocomplete="new-password" placeholder="' +
                (settingsData.values[item.key + "_set"] ? "(set - leave blank to keep, type a space to remove)" : "(none)") + '">';
    } else if (item.kind == "str") {
        html += '<input type="text" id="' + id + '" class="form-control input-sm" value="' + esc(value) + '">';
    } else {
        var step = item.kind == "int" ? "1" : "any";
        html += '<div class="input-group input-group-sm"><input type="number" step="' + step + '" id="' + id + '" class="form-control" value="' + esc(value) + '">' +
                (fieldUnit(item) ? '<span class="input-group-addon">' + fieldUnit(item) + '</span>' : '') + '</div>';
    }
    html += help + '</div></div>';
    return html;
}

function renderSettings() {
    var v = settingsData.values;
    $('[data-groups]').each(function () {
        var groups = $(this).data('groups').split(",");
        var html = '<div class="form-horizontal">';
        $.each(groups, function (_, g) {
            if (groups.length > 1) html += '<h4>' + esc(g) + '</h4>';
            $.each(settingsData.schema, function (_, item) {
                if (item.group == g) html += renderField(item, v[item.key]);
            });
        });
        $(this).html(html + '</div>');
    });
    $('#pid_tuned_at').text(v.pid_tuned_at ? "Last autotuned " + fmtDate(v.pid_tuned_at) : "PID values have not been autotuned yet.");
    $('#set_sensor_board, #set_spi_mode, #set_notify_service, #set_ct_sensor').on('change', updateSettingsVisibility);
    updateSettingsVisibility();

    $('.at-unit').html(deg());
    if (!$('#at_setpoint').val()) $('#at_setpoint').val(cfg.temp_scale == "f" ? 932 : 500);
    if (!$('#at_hyst').val()) $('#at_hyst').val(cfg.temp_scale == "f" ? 5 : 3);
}

function showRow(key, on) { $('.setting-row[data-key=' + key + ']').toggle(on); }

function updateSettingsVisibility() {
    var svc = $('#set_notify_service').val();
    showRow("notify_url", svc == "ntfy" || svc == "webhook");
    showRow("pushover_user", svc == "pushover");
    showRow("pushover_token", svc == "pushover");
    showRow("notify_on_complete", svc != "none");
    var board = $('#set_sensor_board').val();
    var spi = $('#set_spi_mode').val();
    var types = settingsData.board_tc_types[board] || [];
    var $tc = $('#set_thermocouple_type');
    $tc.find('option').each(function () {
        $(this).prop('disabled', types.indexOf($(this).val()) < 0);
    });
    if (types.length && types.indexOf($tc.val()) < 0) $tc.val(types.indexOf("K") >= 0 ? "K" : types[0]);
    showRow("thermocouple_type", types.length > 0);
    showRow("ac_freq_50hz", board == "max31856");
    showRow("mcp9600_address", board == "mcp9600");
    $.each(["rtd_nominal", "rtd_ref_resistor", "rtd_wires"], function (_, k) { showRow(k, board == "max31865"); });
    var isI2C = board == "mcp9600";
    showRow("spi_mode", !isI2C);
    showRow("spi_cs", !isI2C);
    $.each(["spi_sclk", "spi_miso", "spi_mosi"], function (_, k) { showRow(k, !isI2C && spi == "software"); });
    var ct = $('#set_ct_sensor').val() != "none";
    $.each(settingsData.schema, function (_, item) {
        if (item.group == "Current sensor" && item.key != "ct_sensor") showRow(item.key, ct);
    });
}

function collectSettings() {
    var out = {};
    var v = settingsData.values;
    $.each(settingsData.schema, function (_, item) {
        var $el = $('#set_' + item.key);
        if (!$el.length) return;
        var val;
        if (item.kind == "bool") val = $el.is(':checked');
        else if (item.kind == "password") {
            val = $el.val();
            if (val === "") return;          // unchanged
            if (val === " ") val = "";       // remove
        }
        else val = $el.val();
        if (item.kind != "password" && String(val) == String(v[item.key])) return;
        out[item.key] = val;
    });
    return out;
}

function saveSettings() {
    var values = collectSettings();
    if ($.isEmptyObject(values)) { $('#settings_msg').text("Nothing changed."); return; }
    var unitChanged = "temp_scale" in values;
    apiPost("/api/settings", { values: values }).done(function (resp) {
        if (!$.isEmptyObject(resp.errors)) {
            var msg = $.map(resp.errors, function (e) { return esc(e); }).join("<br>");
            notify("<b>Some settings were not saved</b><br>" + msg, "danger", 10000);
        }
        settingsData.values = resp.values;
        $('#settings_msg').html('<span class="text-success">Saved ' + resp.changed.length + ' setting(s).</span>');
        if (resp.restart_required.length) $('#restart_banner').show();
        if (unitChanged) { window.location.reload(); return; }
        loadConfig();
    }).fail(fail("Could not save settings"));
}

function resetSettings() {
    var keep = $('#reset_keep_hw').is(':checked');
    confirmAction("Reset settings?", keep ?
        "All firing and current sensor settings go back to their defaults. Your wiring, sensor, alerts, password, units, cost and PID values are kept." :
        "<b>Everything</b> goes back to the defaults, including pins, sensor board, alerts and password. Check your wiring settings afterwards.",
        "Reset", function () {
        apiPost("/api/settings/reset", { keep_hardware: keep }).done(function (resp) {
            settingsData.values = resp.values;
            renderSettings();
            $('#settings_msg').html('<span class="text-success">Reset ' + resp.reset.length + ' setting(s) to defaults.</span>');
            $('#restart_banner').show();
            loadConfig();
        }).fail(fail("Could not reset settings"));
    });
}

function setUnits(unit) {
    if (unit == cfg.temp_scale) return;
    apiPost("/api/settings", { values: { temp_scale: unit } })
        .done(function () { window.location.reload(); })
        .fail(fail("Could not change units"));
}

function restartController() {
    confirmAction("Restart controller?", "The kiln-controller service restarts and this page reconnects in about 30 seconds. (Only works when installed as a service.)", "Restart", function () {
        apiPost("/api", { cmd: "restart" }).done(function () {
            notify("Restarting&hellip;", "info");
            setTimeout(function () { window.location.reload(); }, 20000);
        }).fail(fail("Could not restart"));
    });
}

function loadHistory() {
    apiGet("/api/history").done(function (resp) {
        var html = '<tr><th>Started</th><th>Schedule</th><th>Time</th><th>kWh</th><th>Cost</th><th>Max</th><th>Result</th></tr>';
        $.each(resp.runs.slice(0, 30), function (_, r) {
            html += '<tr><td>' + fmtDate(r.started) + '</td><td>' + esc(r.profile) + (r.simulated ? ' <span class="label label-default">sim</span>' : '') + '</td><td>' + fmtHM(r.elapsed_s) +
                    '</td><td>' + Number(r.kwh).toFixed(1) + '</td><td>' + fmtMoney(r.cost) + '</td><td>' +
                    (r.max_temp_c !== null && r.max_temp_c !== undefined ? Math.round(cfg.temp_scale == "f" ? r.max_temp_c * 9 / 5 + 32 : r.max_temp_c) + deg() : '') +
                    '</td><td>' + (r.completed ? '<span class="text-success">completed</span>' : esc(r.reason)) + '</td></tr>';
        });
        if (!resp.runs.length) html += '<tr><td colspan="7" class="text-muted">No firings recorded yet.</td></tr>';
        $('#history_table').html(html);
    });
}

function clearHistory() {
    confirmAction("Clear firing history?", "Cost estimates go back to a rough guess until you fire again.", "Clear", function () {
        apiPost("/api/history/clear").done(function () { loadHistory(); loadProfiles(); });
    });
}

function showRemoteWarning(resp) {
    if (resp.remote && !resp.values.web_password_set) {
        $('#remote_bar').html('<span class="glyphicon glyphicon-warning-sign"></span> <b>This kiln is reachable from the internet without a password.</b> ' +
            'Anyone could start it. Set a password in Settings &rarr; Advanced, and use Tailscale or Raspberry Pi Connect instead of port forwarding (see docs/remote-access.md).').show();
    } else {
        $('#remote_bar').hide();
    }
}

function renderRecentAlerts(list) {
    if (!list || !list.length) { $('#recent_alerts').html('<span class="text-muted">None since the controller started.</span>'); return; }
    var html = '<table class="table table-condensed">';
    $.each(list.slice().reverse(), function (_, n) {
        html += '<tr' + (n.urgent ? ' class="danger"' : '') + '><td class="text-nowrap">' + new Date(n.time * 1000).toLocaleString() +
                '</td><td><b>' + esc(n.title) + '</b><br>' + esc(n.message) + '</td></tr>';
    });
    $('#recent_alerts').html(html + '</table>');
}

function testNotification() {
    var values = collectSettings();
    var send = function () {
        apiPost("/api", { cmd: "notify_test" })
            .done(function () { notify("Test alert sent. Check your phone.", "success"); })
            .fail(fail("Test alert failed"));
    };
    if ($.isEmptyObject(values)) { send(); return; }
    apiPost("/api/settings", { values: values }).done(function (resp) {
        settingsData.values = resp.values;
        send();
    }).fail(fail("Could not save settings"));
}

function readJsonFile(input, fn) {
    var file = input.files && input.files[0];
    input.value = "";
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
        var data;
        try { data = JSON.parse(reader.result); }
        catch (e) { notify("That file is not valid JSON.", "danger"); return; }
        fn(data, file.name);
    };
    reader.readAsText(file);
}

function importSchedules(data, overwrite) {
    var list = Array.isArray(data) ? data : (data.type == "kiln-controller-backup" ? data.profiles : [data]);
    apiPost("/api/profiles/import", { profiles: list, overwrite: !!overwrite }).done(function (resp) {
        if (resp.skipped.length && !overwrite) {
            confirmAction("Replace existing schedules?", "These already exist: <b>" + resp.skipped.map(esc).join(", ") +
                          "</b>. Replace them with the imported versions?", "Replace", function () {
                importSchedules(list.filter(function (p) { return resp.skipped.indexOf(p.name) >= 0; }), true);
            });
        }
        if (resp.imported.length) notify("Imported " + resp.imported.length + " schedule(s).", "success");
        loadProfiles();
    }).fail(fail("Import failed"));
}

function restoreBackup(data) {
    if (data.type != "kiln-controller-backup") { notify("That is not a kiln-controller backup file.", "danger"); return; }
    confirmAction("Restore backup?", "Settings, schedules and firing history from <b>" + esc(data.hostname || "?") + "</b> (" +
                  esc(data.created || "") + ") will replace what is here. Passwords are not changed.", "Restore", function () {
        apiPost("/api/restore", data).done(function (resp) {
            notify(esc(resp.message), "success", 8000);
            $('#restart_banner').show();
            loadConfig(); loadProfiles(); loadHistory();
        }).fail(fail("Restore failed"));
    });
}

// ---------------------------------------------------------------------
// autotune

function startAutotune() {
    var body = { cmd: "autotune_start", setpoint: parseFloat($('#at_setpoint').val()),
                 output_percent: parseFloat($('#at_output').val()), hysteresis: parseFloat($('#at_hyst').val()),
                 cycles: parseInt($('#at_cycles').val(), 10) };
    if (isNaN(body.setpoint)) { notify("Enter a target temperature", "danger"); return; }
    confirmAction("Start autotune?", "The kiln will heat to <b>" + esc(body.setpoint) + deg() +
                  "</b> and cycle around it. Make sure it is empty and safe to fire. You can stop at any time.", "Start", function () {
        apiPost("/api", body).done(function () {
            $('#settingsModal').modal('hide');
            graph.live.data = [];
            notify("Autotune started", "success");
        }).fail(fail("Could not start autotune"));
    });
}

function autotuneResultHtml(a) {
    if (!a) return "";
    if (a.phase == "failed") return '<div class="alert alert-danger">Autotune failed: ' + esc(a.error) + '</div>';
    if (a.phase != "done" || !a.result) return "";
    var r = a.result;
    var html = '<div class="autotune-result"><p>Measured: oscillation &plusmn;' + Number(a.amplitude).toFixed(1) + deg() +
               ', period ' + fmtHM(r.pu) + '. Ku=' + Number(r.ku).toFixed(2) + '</p>';
    if (r.warning) html += '<div class="alert alert-warning small">' + esc(r.warning) + '</div>';
    html += '<table class="table table-condensed"><tr><th>Rule</th><th>Kp</th><th>Ki</th><th>Kd</th><th></th></tr>';
    $.each(r.rules, function (key, g) {
        html += '<tr' + (key == r.recommended ? ' class="success"' : '') + '><td>' + esc(g.label) + (key == r.recommended ? ' <b>(recommended)</b>' : '') + '</td><td>' +
                g.kp.toFixed(3) + '</td><td>' + g.ki.toFixed(1) + '</td><td>' + g.kd.toFixed(1) + '</td><td>' +
                '<button class="btn btn-primary btn-xs" onclick="applyAutotune(\'' + key + '\')">Use</button></td></tr>';
    });
    html += '</table><p class="small text-muted">Gains are per &deg;C. The conservative rule is the safest for kilns; pick a more aggressive one if the kiln lags behind the schedule.</p></div>';
    return html;
}

function renderAutotune(a) {
    $('#autotune_result').html(autotuneResultHtml(a));
    $('#btn_autotune').prop('disabled', state != "IDLE");
}

function applyAutotune(rule) {
    apiPost("/api", { cmd: "autotune_apply", rule: rule, widen_window: $('#at_widen').length ? $('#at_widen').is(':checked') : true }).done(function (resp) {
        notify("PID values saved. They are used from the next firing.", "success");
        $('#autotuneModal').modal('hide');
        apiPost("/api", { cmd: "autotune_dismiss" });
        if ($('#settingsModal').hasClass('in')) openSettings("tab_pid");
    }).fail(fail("Could not apply"));
}

// ---------------------------------------------------------------------
// diagnostics

function row(k, v) { return '<tr><td>' + k + '</td><td>' + v + '</td></tr>'; }

function refreshDiagnostics() {
    apiGet("/api/diagnostics").done(function (d) {
        var s = d.sensor, y = d.system;
        var t = function (v) { return v === null || v === undefined ? "&ndash;" : Number(v).toFixed(1) + deg(); };
        var html = row("Board", esc(s.board) + (d.settings.simulate ? "" : " / type " + esc(d.settings.thermocouple_type))) +
                   row("Temperature (median)", t(s.temperature)) +
                   row("Last raw reading", t(s.last_raw)) +
                   row("Cold junction (board)", t(s.cold_junction)) +
                   row("Error rate", Number(s.error_percent).toFixed(0) + "%" + (s.over_error_limit ? ' <span class="label label-danger">over limit</span>' : '')) +
                   row("Reads / failures", s.reads + " / " + s.failures) +
                   row("SPI / relay pin", esc(d.settings.spi_mode) + " / BCM " + esc(d.settings.gpio_heat));
        $('#diag_sensor').html(html);
        html = row("Host", esc(y.hostname) + " (" + esc(y.board) + ")") +
               row("CPU temp", y.cpu_temp_c !== undefined ? y.cpu_temp_c.toFixed(1) + "&deg;C" : "&ndash;") +
               row("Memory free", y.mem_available_mb !== undefined ? y.mem_available_mb + " / " + y.mem_total_mb + " MB" : "&ndash;") +
               row("Disk free", y.disk_free_mb !== undefined ? y.disk_free_mb + " MB" : "&ndash;") +
               row("Load", y.load ? y.load.map(function (l) { return l.toFixed(2); }).join(" ") : "&ndash;") +
               row("Uptime", y.uptime_s ? fmtHM(y.uptime_s) : "&ndash;") +
               row("Power", y.throttled === undefined ? "&ndash;" :
                   (y.undervoltage_now ? '<span class="label label-danger">UNDER-VOLTAGE NOW</span>' :
                    y.undervoltage_since_boot ? '<span class="label label-warning">under-voltage since boot</span>' : '<span class="text-success">OK</span>')) +
               row("Python", esc(y.python)) + row("Time", esc(y.time));
        $('#diag_system').html(html);
        $('#diag_current_box').toggle(!!d.current);
        if (d.current) $('#diag_current').html(currentRows(d.current));
        if (s.recent_errors.length) {
            html = '<table class="table table-condensed"><tr><th>Time</th><th>Error</th><th>Raw</th><th>Ignored</th></tr>';
            $.each(s.recent_errors.slice().reverse(), function (_, e) {
                html += '<tr><td>' + new Date(e.time * 1000).toLocaleTimeString() + '</td><td>' + esc(e.error) + '</td><td>' + esc(e.raw) + '</td><td>' + (e.ignored ? "yes" : "no") + '</td></tr>';
            });
            $('#diag_errors').html(html + '</table>');
        } else {
            $('#diag_errors').html('<span class="text-success">No errors.</span>');
        }
    });
}

function currentRows(c) {
    var a = function (v) { return v === null || v === undefined ? "&ndash;" : Number(v).toFixed(1) + " A"; };
    var ago = function (t) { return t ? " (" + fmtHM(Date.now() / 1000 - t) + " ago)" : ""; };
    var html = row("Status", c.enabled ? '<span class="text-success">reading</span>' : '<span class="label label-danger">not working</span>') +
               (c.error ? row("Error", esc(c.error)) : "");
    if (c.last) html += row("Last reading", a(c.last.amps) + " with the elements " + (c.last.heater_on ? "ON" : "off") + ago(c.last.time));
    html += row("Last reading with elements on", a(c.on_amps)) +
            row("Counts as on above", a(c.threshold));
    return html;
}

function relayTest() {
    var secs = parseFloat($('#relay_secs').val());
    confirmAction("Test relay?", "This turns the kiln elements <b>ON for " + secs + " seconds</b>.", "Turn on", function () {
        apiPost("/api", { cmd: "relay_test", seconds: secs })
            .done(function () { notify("Relay on for " + secs + "s", "warning", 3000); })
            .fail(fail("Relay test failed"));
    });
}

// ---------------------------------------------------------------------
// software update

var updateTimer = null;

function loadUpdateInfo() {
    return apiGet("/api/update").done(renderUpdate);
}

function renderUpdate(u) {
    var v = u.version || {};
    $('#update_version').html(v.error ? '<span class="text-danger">' + esc(v.error) + '</span>' :
        'Running <b>' + esc(v.short) + '</b> (' + esc(v.date) + ') on branch <b>' + esc(v.branch) + '</b> from ' + esc(v.remote || "?") +
        '<br><span class="text-muted">' + esc(v.subject) + '</span>' +
        (v.local_changes && v.local_changes.length ? '<br><span class="text-warning">Changed on this kiln: ' + esc(v.local_changes.join(", ")) + '</span>' : ''));
    $('#update_force_label').toggle(!!(v.local_changes && v.local_changes.length));
    $('#btn_update_rollback').toggle(!!u.previous).attr('title', u.previous ? "Go back to " + u.previous.commit.substr(0, 7) : "");
    $('#btn_update_check, #btn_update_install, #btn_update_rollback').prop('disabled', u.busy);
    var cls = u.status == "error" ? "text-danger" : u.status == "done" ? "text-success" : "";
    var html = u.busy ? '<span class="glyphicon glyphicon-refresh"></span> ' + (u.status == "checking" ? "Checking&hellip;" : "Updating&hellip; this can take a while on a Pi Zero.") : '';
    if (u.message) html += '<span class="' + cls + '">' + esc(u.message) + '</span>';
    if (u.check && !u.check.up_to_date && u.status == "checked") {
        html += '<ul>' + $.map(u.check.commits, function (c) { return '<li>' + esc(c.date) + ' ' + esc(c.subject) + '</li>'; }).join("") + '</ul>';
    }
    $('#update_status').html(html);
    $('#update_log').text((u.log || []).join("\n")).toggle(!!(u.log && u.log.length));
    var $log = $('#update_log')[0];
    if ($log) $log.scrollTop = $log.scrollHeight;
    clearTimeout(updateTimer);
    if (u.busy) updateTimer = setTimeout(loadUpdateInfo, 2000);
    if (u.status == "done") waitForRestart();
}

function waitForRestart() {
    if (window.kcRestarting) return;
    window.kcRestarting = true;
    notify("Update installed. The controller is restarting&hellip;", "info", 0);
    var tries = 0;
    var poll = function () {
        tries++;
        $.ajax({ url: "/api/config", timeout: 3000 }).done(function () {
            if (tries > 3) window.location.reload();
            else setTimeout(poll, 4000);
        }).fail(function () { setTimeout(poll, 4000); });
    };
    setTimeout(poll, 6000);
}

function updateAction(action) {
    var body = { action: action, repo_url: $('#set_update_repo_url').val(), branch: $('#set_update_branch').val(),
                 force: $('#update_force').is(':checked') };
    var go = function () {
        apiPost("/api/update", body).done(function () {
            $('#update_status').html('<span class="glyphicon glyphicon-refresh"></span> Working&hellip;');
            setTimeout(loadUpdateInfo, 500);
        }).fail(fail(action == "check" ? "Could not check for updates" : "Could not update"));
    };
    if (action == "check") { go(); return; }
    var title = action == "install" ? "Install update?" : "Roll back?";
    var text = action == "install" ?
        "Install <b>" + esc(body.branch) + "</b> from " + esc(body.repo_url) + " and restart the controller. The kiln must be idle." :
        "Go back to the version that was running before the last update, and restart the controller.";
    confirmAction(title, text, action == "install" ? "Install" : "Roll back", go);
}

// ---------------------------------------------------------------------
// status from the server

function loadConfig() {
    return apiGet("/api/settings").done(function (resp) {
        settingsData = resp;
        var v = resp.values;
        cfg.temp_scale = v.temp_scale;
        cfg.time_scale_slope = v.time_scale_slope;
        cfg.time_scale_profile = v.time_scale_profile;
        cfg.kwh_rate = v.kwh_rate;
        cfg.kw_elements = v.kw_elements;
        cfg.currency_type = v.currency_type;
        $('.deg-unit').html(deg());
        showRemoteWarning(resp);
        $('#heat_rate_title').text("Heat Rate /" + rateUnit());
        $('#unit_toggle button').removeClass('active btn-primary').addClass('btn-default');
        $('#unit_toggle button[data-unit=' + cfg.temp_scale + ']').addClass('active btn-primary').removeClass('btn-default');
        $('#sim_badge').toggle(!!v.simulate);
    });
}

function updateProgress(percentage) {
    if (state == "RUNNING" || state == "PAUSED") {
        if (percentage > 100) percentage = 100;
        $('#progressBar').css('width', percentage + '%');
        $('#progressBar').html(percentage > 5 ? parseInt(percentage, 10) + '%' : '');
    } else {
        $('#progressBar').css('width', 0 + '%');
        $('#progressBar').html('');
    }
}

function handleBacklog(x) {
    if (x.profile) {
        selected_profile_name = x.profile.name;
        var i = profileIndex(x.profile.name);
        if (i >= 0) $('#profile_select').val(i);
        graph.profile.data = x.profile.data;
    }
    graph.live.data = $.map(x.log, function (v) { return [[v.runtime, v.temperature]]; });
    replot();
}

function setButtons() {
    var running = state == "RUNNING", paused = state == "PAUSED";
    var busy = running || paused || state == "TUNING" || state == "SCHEDULED";
    $('#nav_start').toggle(!busy);
    $('#nav_stop').toggle(busy);
    $('#nav_pause').toggle(running);
    $('#nav_resume').toggle(paused);
    $('#btn_edit, #btn_new').prop('disabled', busy);
    $('#btn_autotune').prop('disabled', state != "IDLE");
}

function handleStatus(x) {
    lastStatus = x;
    if (x.type == "backlog") { handleBacklog(x); return; }

    $('#act_temp').html(x.sensor_ok === false ? "ERR" : parseInt(x.temperature, 10));
    $('#sim_badge').toggle(!!x.simulate);

    if (x.error) $('#error_bar').html('<span class="glyphicon glyphicon-warning-sign"></span> ' + esc(x.error)).show();
    else $('#error_bar').hide();

    if (editor.active) return;

    state = x.state;
    if (state != state_last) {
        if ((state_last == "RUNNING" || state_last == "PAUSED") && state == "IDLE") {
            $('#target_temp').html('---');
            updateProgress(0);
            notify("<span class=\"glyphicon glyphicon-exclamation-sign\"></span> <b>Run ended</b>" + (x.error ? "<br>" + esc(x.error) : ""), x.error ? "danger" : "success", 0);
        }
        if (state_last == "TUNING" && state == "IDLE") {
            graph.profile.data = profiles[selected_profile] ? profiles[selected_profile].data : [];
            replot();
        }
        setButtons();
    }

    // scheduled start
    if (state == "SCHEDULED" && x.scheduled) {
        var secs = x.scheduled.start_at - Date.now() / 1000;
        var at = new Date(x.scheduled.start_at * 1000);
        $('#scheduled_bar').html('<span class="glyphicon glyphicon-time"></span> <b>' + esc(x.scheduled.profile) +
            '</b> starts at ' + at.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }) +
            ' (in ' + fmtHM(secs) + ') <button class="btn btn-default btn-xs" onclick="abortTask()">Cancel</button>').show();
    } else {
        $('#scheduled_bar').hide();
    }

    // autotune progress / result
    var a = x.autotune;
    if (state == "TUNING" && a) {
        $('#tuning_bar').html('<span class="glyphicon glyphicon-flash"></span> <b>Autotuning</b> at ' + Math.round(a.setpoint) + deg() +
            ' &mdash; ' + (a.phase == "heating" ? "heating up" : "cycle " + a.cycles_done + " of " + a.cycles_needed) +
            ', elements ' + (a.relay_on ? "ON" : "off") + ', ' + fmtHM(a.elapsed) + ' elapsed' +
            (a.peak !== null ? ', last peak ' + Math.round(a.peak) + deg() : '')).show();
        graph.live.data.push([a.elapsed, x.temperature]);
        graph.profile.data = [[0, a.setpoint], [Math.max(a.elapsed, 1800), a.setpoint]];
        replot();
        $('#target_temp').html(parseInt(a.setpoint, 10));
        $('#state').html('<p class="ds-text">TUNING</p>');
    } else if (a && (a.phase == "done" || a.phase == "failed")) {
        $('#tuning_bar').html('<span class="glyphicon glyphicon-flash"></span> <b>Autotune ' + (a.phase == "done" ? "finished" : "failed") + '.</b> ' +
            '<button class="btn btn-default btn-xs" onclick="openSettings(\'tab_pid\')">Review results</button> ' +
            '<button class="btn btn-default btn-xs" onclick="apiPost(\'/api\', {cmd: \'autotune_dismiss\'})">Dismiss</button>').show();
        // pop the results up when we saw it finish
        if (state_last == "TUNING" && shownAutotune != a.elapsed) {
            shownAutotune = a.elapsed;
            $('#autotune_modal_body').html(autotuneResultHtml(a));
            $('#autotuneModal').modal('show');
        }
    } else {
        $('#tuning_bar').hide();
    }
    if ($('#settingsModal').hasClass('in')) renderAutotune(a);

    if (state == "RUNNING" || state == "PAUSED") {
        graph.live.data.push([x.runtime, x.temperature]);
        replot();
        var left = parseInt(x.totaltime - x.runtime, 10);
        updateProgress(parseFloat(x.runtime) / parseFloat(x.totaltime) * 100);
        $('#state').html('<span class="glyphicon glyphicon-' + (state == "PAUSED" ? "pause" : "time") +
                         '" style="font-size: 22px; font-weight: normal"></span><span style="font-family: Digi; font-size: 40px;">' + fmtHMS(left) + '</span>');
        $('#target_temp').html(parseInt(x.target, 10));
        $('#cost').html(fmtMoney(x.cost));
    } else if (state != "TUNING") {
        updateProgress(0);
        $('#state').html('<p class="ds-text">' + esc(state) + '</p>');
    }

    var heat_rate = parseInt(cfg.time_scale_slope == "m" ? x.heat_rate / 60 : x.heat_rate, 10);
    if (heat_rate > 9999) heat_rate = 9999;
    if (heat_rate < -9999) heat_rate = -9999;
    $('#heat_rate').html(isNaN(heat_rate) ? "---" : heat_rate);
    $('#heat').html('<div class="bar" style="height:' + Math.round((x.output || 0) * 100) + '%;"></div>');
    if (x.current && x.current.last) $('#amps').text(Number(x.current.last.amps).toFixed(1) + " A").show();
    else $('#amps').hide();
    if (x.temperature > hazardTemp()) $('#hazard').addClass("ds-led-hazard-active");
    else $('#hazard').removeClass("ds-led-hazard-active");

    state_last = state;
}

function connectStatus() {
    // Server-Sent Events: the browser reconnects by itself
    events = new EventSource("/api/events");
    events.onopen = function () {
        if (connLost) { notify("Reconnected", "success", 2000); loadProfiles(); }
        connLost = false;
    };
    events.onerror = function () {
        if (!connLost) notify("<b>Lost connection to the kiln controller.</b><br>Retrying&hellip;", "danger", 8000);
        connLost = true;
    };
    events.onmessage = function (e) { handleStatus(JSON.parse(e.data)); };
}

$(document).ready(function () {
    if (!("WebSocket" in window)) {
        $('<p>Oh no, you need a browser that supports WebSockets.</p>').appendTo('.container');
        return;
    }

    $("#profile_select").on("change", function () { updateProfile($(this).val()); });
    bindGraphDrag();

    $('#unit_toggle button').on('click', function () { setUnits($(this).data('unit')); });
    $('input[name=start_when], #start_delay_h, #start_delay_m, #start_at_time').on('change keyup click', updateStartWhen);
    $('#start_delay_h, #start_delay_m').on('focus', function () { $('input[name=start_when][value=delay]').prop('checked', true); });
    $('#start_at_time').on('focus', function () { $('input[name=start_when][value=at]').prop('checked', true); });
    $('#edit_time_unit').on('change', function () {
            updateProfileTable();
    });

    $('#library_search').on('keyup search', renderLibrary);
    $('#import_file').on('change', function () { readJsonFile(this, function (d) { importSchedules(d, false); }); });
    $('#restore_file').on('change', function () { readJsonFile(this, restoreBackup); });
    $('#library_table').on('click', 'th.sortable', function () {
        var k = $(this).data('key');
        librarySort.dir = librarySort.key == k ? -librarySort.dir : 1;
        librarySort.key = k;
        renderLibrary();
    }).on('click', '.lib-select', function (e) {
        e.preventDefault();
        selectProfileByIndex($(this).data('i'));
        $('#libraryModal').modal('hide');
    }).on('click', '.lib-edit', function () {
        var i = $(this).data('i');
        if (state != "IDLE") { notify("Can't edit while the kiln is busy", "danger"); return; }
        $('#libraryModal').modal('hide');
        selectProfileByIndex(i);
        enterEditMode(i);
    }).on('click', '.lib-copy', function () {
        copyProfile(profiles[$(this).data('i')].name);
    }).on('click', '.lib-delete', function () {
        confirmDelete(profiles[$(this).data('i')].name);
    });

    $('#settings_tabs a[href="#tab_diag"]').on('shown.bs.tab', function () {
        refreshDiagnostics();
        clearInterval(diagTimer);
        diagTimer = setInterval(function () {
            if ($('#tab_diag').hasClass('active') && $('#settingsModal').hasClass('in')) refreshDiagnostics();
        }, 2000);
    });
    $('#settingsModal').on('hidden.bs.modal', function () { clearInterval(diagTimer); });

    loadConfig().always(function () {
        loadProfiles().always(connectStatus);
    });
});
