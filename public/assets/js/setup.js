// First-launch setup wizard and the SETUP menu (re-run the wizard, or
// reset everything to defaults first). Uses helpers from picoreflow.js
// (apiGet, apiPost, esc, notify, settingsData, loadConfig).

var Setup = (function () {
    var wiz = null;       // values being edited (display units of the chosen scale)
    var step = 0;
    var board = { header: 40, model: null, detected: false };
    var serverScale = "c";

    var BOARDS_SPI = ["max31855", "max31856", "max6675", "max31865"];
    var MAX6675_C = 1023;

    function toC(t, scale) { return scale === "f" ? (t - 32) * 5 / 9 : t; }
    function fromC(c, scale) { return scale === "f" ? c * 9 / 5 + 32 : c; }
    function u() { return "&deg;" + wiz.temp_scale.toUpperCase(); }

    // ---- steps -------------------------------------------------------
    var STEPS = [
        { id: "welcome", title: "Welcome", render: function () {
            return '<p class="lead">Let&rsquo;s set up your kiln controller.</p>' +
                '<p>A few questions about your kiln and how it is wired: temperature units, the thermocouple board, the relay ' +
                'and its pin, an optional safety contactor and current sensor, the emergency temperature and phone alerts. ' +
                'It takes a couple of minutes and you can change everything later in Settings.</p>' +
                '<p class="text-muted small">Skipping keeps the defaults: simulation mode, so nothing heats up.</p>';
        } },
        { id: "units", title: "Units and cost", render: function () {
            return field("Temperature units", '<label class="radio-inline"><input type="radio" name="wz_scale" value="c"' + (wiz.temp_scale === "c" ? " checked" : "") + '> &deg;C</label>' +
                       '<label class="radio-inline"><input type="radio" name="wz_scale" value="f"' + (wiz.temp_scale === "f" ? " checked" : "") + '> &deg;F</label>') +
                field("Element power (kW)", num("wz_kw", wiz.kw_elements, "0.1"), "On the kiln nameplate, or volts &times; amps / 1000.") +
                field("Electricity price per kWh", num("wz_rate", wiz.kwh_rate, "0.001")) +
                field("Currency symbol", '<input type="text" id="wz_currency" class="form-control input-sm" maxlength="4" value="' + esc(wiz.currency_type) + '">');
        }, collect: function () {
            var scale = $('input[name=wz_scale]:checked').val();
            wiz.temp_scale = scale;
            wiz.kw_elements = $('#wz_kw').val();
            wiz.kwh_rate = $('#wz_rate').val();
            wiz.currency_type = $('#wz_currency').val();
        } },
        { id: "sensor", title: "Temperature sensor", render: function () {
            var opts = "";
            $.each(schemaOptions("sensor_board"), function (_, o) {
                opts += '<option value="' + esc(o[0]) + '"' + (o[0] === wiz.sensor_board ? " selected" : "") + '>' + esc(o[1]) + '</option>';
            });
            var html = field("Thermocouple board", '<select id="wz_board" class="form-control input-sm">' + opts + '</select>');
            if (wiz.sensor_board === "max6675") {
                html += '<div class="alert alert-warning small">The MAX6675 can only read up to ' + Math.round(fromC(MAX6675_C, wiz.temp_scale)) + u() +
                        '. Schedules that go hotter can not be started. For stoneware and porcelain use a MAX31855 or MAX31856.</div>';
            }
            var types = (settingsData.board_tc_types || {})[wiz.sensor_board] || [];
            if (types.length > 1) {
                var t = "";
                $.each(types, function (_, x) { t += '<option' + (x === wiz.thermocouple_type ? " selected" : "") + '>' + x + '</option>'; });
                html += field("Thermocouple type", '<select id="wz_tc" class="form-control input-sm">' + t + '</select>');
            } else if (types.length === 1) {
                wiz.thermocouple_type = types[0];
                html += field("Thermocouple type", '<p class="form-control-static">Type ' + types[0] + '</p>');
            }
            if (BOARDS_SPI.indexOf(wiz.sensor_board) >= 0) {
                html += field("How is it wired?", '<label class="radio-inline"><input type="radio" name="wz_spi" value="software"' + (wiz.spi_mode === "software" ? " checked" : "") + '> Any GPIO pins (software SPI)</label>' +
                        '<label class="radio-inline"><input type="radio" name="wz_spi" value="hardware"' + (wiz.spi_mode === "hardware" ? " checked" : "") + '> Hardware SPI pins</label>');
                html += '<p class="small">Pick the pins the board is connected to. Its power goes to a 3.3V pin and GND.</p><div id="wz_pins"></div>';
            } else if (wiz.sensor_board === "mcp9600") {
                html += field("I2C address", '<input type="text" id="wz_mcp_addr" class="form-control input-sm" value="' + esc(wiz.mcp9600_address) + '">',
                              "The MCP9600 uses the I2C pins (SDA = pin 3, SCL = pin 5), shown below.");
                html += '<div id="wz_pins"></div>';
            }
            return html;
        }, after: function () {
            $('#wz_board').on('change', function () { collect(); wiz.sensor_board = $(this).val(); redraw(); });
            $('input[name=wz_spi]').on('change', function () { collect(); redraw(); });
            var roles = [];
            if (BOARDS_SPI.indexOf(wiz.sensor_board) >= 0) {
                roles = wiz.spi_mode === "hardware" ? ["spi_cs"] : ["spi_cs", "spi_sclk", "spi_miso"];
                if (wiz.spi_mode === "software" && (wiz.sensor_board === "max31856" || wiz.sensor_board === "max31865")) roles.push("spi_mosi");
            }
            drawPins(roles);
        }, collect: function () {
            wiz.sensor_board = $('#wz_board').val() || wiz.sensor_board;
            if ($('#wz_tc').length) wiz.thermocouple_type = $('#wz_tc').val();
            if ($('input[name=wz_spi]:checked').length) wiz.spi_mode = $('input[name=wz_spi]:checked').val();
            if ($('#wz_mcp_addr').length) wiz.mcp9600_address = $('#wz_mcp_addr').val();
        } },
        { id: "relay", title: "Relay", render: function () {
            var html = field("Is a relay (SSR) connected to switch the elements?", yesno("wz_relay", wiz.has_relay));
            if (wiz.has_relay) {
                html += '<p class="small">Pick the GPIO pin that drives the relay (through its driver board or transistor).</p><div id="wz_pins"></div>' +
                        '<div class="checkbox small"><label><input type="checkbox" id="wz_heat_inv"' + (wiz.gpio_heat_invert ? " checked" : "") +
                        '> Invert the output (the relay switches on when the pin is low)</label></div>';
            } else {
                html += '<div class="alert alert-info small">Without a relay the controller runs in <b>simulation mode</b>: you can try everything, nothing heats up.</div>';
            }
            return html;
        }, after: function () {
            $('input[name=wz_relay]').on('change', function () { collect(); redraw(); });
            if (wiz.has_relay) drawPins(["gpio_heat"]);
        }, collect: function () {
            wiz.has_relay = $('input[name=wz_relay]:checked').val() === "yes";
            if ($('#wz_heat_inv').length) wiz.gpio_heat_invert = $('#wz_heat_inv').is(':checked');
        } },
        { id: "contactor", title: "Safety contactor", render: function () {
            var html = '<p>A <b>safety contactor</b> is a heavy mechanical relay wired in series with the SSR. SSRs usually fail ' +
                       '<i>stuck on</i>; the contactor is only closed while firing, and opened when a firing ends or is stopped, so a ' +
                       'stuck SSR can not keep heating the kiln. Recommended.</p>' +
                       field("Do you have a safety contactor?", yesno("wz_contactor", wiz.has_contactor));
            if (wiz.has_contactor) {
                html += '<p class="small">Pick the GPIO pin that drives the contactor coil (through a relay module or transistor).</p><div id="wz_pins"></div>' +
                        '<div class="checkbox small"><label><input type="checkbox" id="wz_cont_inv"' + (wiz.gpio_contactor_invert ? " checked" : "") +
                        '> Invert the output</label></div>';
            }
            return html;
        }, after: function () {
            $('input[name=wz_contactor]').on('change', function () { collect(); redraw(); });
            if (wiz.has_contactor) drawPins(["gpio_contactor"]);
        }, collect: function () {
            wiz.has_contactor = $('input[name=wz_contactor]:checked').val() === "yes";
            if ($('#wz_cont_inv').length) wiz.gpio_contactor_invert = $('#wz_cont_inv').is(':checked');
        } },
        { id: "current", title: "Current sensor", render: function () {
            var html = '<p>An optional clamp-on current sensor (CT) on one element wire, read by an ADS1115 on the I2C pins. ' +
                       'It checks that the kiln really draws power: a 1 second test when a firing starts, no current while firing, ' +
                       'and current flowing with the elements off (stuck relay).</p>' +
                       field("Do you have a current sensor?", yesno("wz_ct", wiz.ct_sensor !== "none"));
            if (wiz.ct_sensor !== "none") {
                html += field("ADS1115 I2C address", '<input type="text" id="wz_ct_addr" class="form-control input-sm" value="' + esc(wiz.ct_i2c_address) + '">') +
                        field("CT calibration (amps per volt)", num("wz_ct_apv", wiz.ct_amps_per_volt, "any"), "SCT-013-030: 30, SCT-013-050: 50, SCT-013-000 with a 33&Omega; burden: 60.6") +
                        field("Mains voltage (V)", num("wz_mains", wiz.mains_voltage, "1")) +
                        '<div id="wz_pins"></div>';
            }
            return html;
        }, after: function () {
            $('input[name=wz_ct]').on('change', function () { collect(); redraw(); });
            if (wiz.ct_sensor !== "none") drawPins([]);
        }, collect: function () {
            wiz.ct_sensor = $('input[name=wz_ct]:checked').val() === "yes" ? "ads1115" : "none";
            if ($('#wz_ct_addr').length) {
                wiz.ct_i2c_address = $('#wz_ct_addr').val();
                wiz.ct_amps_per_volt = $('#wz_ct_apv').val();
                wiz.mains_voltage = $('#wz_mains').val();
            }
        } },
        { id: "limits", title: "Emergency temperature", render: function () {
            var html = '<p>Any firing is stopped if the kiln reaches this temperature. Set it a little above your hottest firing. ' +
                       'You get a warning when you start a schedule that comes within ' + Math.round(wiz.temp_scale === "f" ? 90 : 50) + u() + ' of it.</p>' +
                       field("Emergency shutoff temperature", '<div class="input-group input-group-sm" style="max-width:200px">' +
                             '<input type="number" id="wz_emerg" class="form-control" value="' + Math.round(fromC(wiz.emergency_c, wiz.temp_scale)) + '">' +
                             '<span class="input-group-addon">' + u() + '</span></div>',
                             "Cone 6 is about " + Math.round(fromC(1222, wiz.temp_scale)) + u() + ", cone 10 about " + Math.round(fromC(1305, wiz.temp_scale)) + u() + ".");
            if (wiz.sensor_board === "max6675") {
                html += '<div class="alert alert-warning small">Your MAX6675 reads up to ' + Math.round(fromC(MAX6675_C, wiz.temp_scale)) + u() +
                        '. Schedules above that are refused whatever this is set to.</div>';
            }
            return html;
        }, collect: function () {
            var v = parseFloat($('#wz_emerg').val());
            if (!isNaN(v)) wiz.emergency_c = toC(v, wiz.temp_scale);
        } },
        { id: "alerts", title: "Phone alerts", render: function () {
            var opts = "";
            $.each(schemaOptions("notify_service"), function (_, o) {
                opts += '<option value="' + esc(o[0]) + '"' + (o[0] === wiz.notify_service ? " selected" : "") + '>' + esc(o[1]) + '</option>';
            });
            var html = '<p>The controller can send alerts to your phone: firing finished, a stuck relay, the kiln not heating, ' +
                       'thermocouple problems and more. <b>ntfy</b> is free: install the ntfy app and subscribe to a long, random topic name.</p>' +
                       field("Send alerts with", '<select id="wz_notify" class="form-control input-sm">' + opts + '</select>');
            if (wiz.notify_service === "ntfy" || wiz.notify_service === "webhook") {
                html += field(wiz.notify_service === "ntfy" ? "ntfy topic URL" : "Webhook URL",
                              '<input type="text" id="wz_notify_url" class="form-control input-sm" value="' + esc(wiz.notify_url) + '" placeholder="https://ntfy.sh/my-kiln-' + Math.random().toString(36).slice(2, 10) + '">');
            } else if (wiz.notify_service === "pushover") {
                html += field("Pushover user key", '<input type="text" id="wz_po_user" class="form-control input-sm" value="' + esc(wiz.pushover_user) + '">') +
                        field("Pushover app token", '<input type="password" id="wz_po_token" class="form-control input-sm" placeholder="' + (settingsData.values.pushover_token_set ? "(set - leave blank to keep)" : "") + '">');
            }
            return html;
        }, after: function () {
            $('#wz_notify').on('change', function () { collect(); redraw(); });
        }, collect: function () {
            wiz.notify_service = $('#wz_notify').val() || wiz.notify_service;
            if ($('#wz_notify_url').length) wiz.notify_url = $('#wz_notify_url').val();
            if ($('#wz_po_user').length) wiz.pushover_user = $('#wz_po_user').val();
            if ($('#wz_po_token').length && $('#wz_po_token').val()) wiz.pushover_token = $('#wz_po_token').val();
        } },
        { id: "review", title: "Review", render: function () {
            var boardName = optionLabel("sensor_board", wiz.sensor_board);
            var rows = [
                ["Units", "&deg;" + wiz.temp_scale.toUpperCase()],
                ["Elements / price", esc(wiz.kw_elements) + " kW, " + esc(wiz.currency_type) + esc(wiz.kwh_rate) + " per kWh"],
                ["Sensor", esc(boardName) + (wiz.thermocouple_type ? ", type " + esc(wiz.thermocouple_type) : "") + pinText()],
                ["Relay", wiz.has_relay ? "GPIO " + wiz.gpio_heat + (wiz.gpio_heat_invert ? " (inverted)" : "") : "none"],
                ["Safety contactor", wiz.has_contactor ? "GPIO " + wiz.gpio_contactor + (wiz.gpio_contactor_invert ? " (inverted)" : "") : "none"],
                ["Current sensor", wiz.ct_sensor !== "none" ? "ADS1115 at " + esc(wiz.ct_i2c_address) : "none"],
                ["Emergency shutoff", Math.round(fromC(wiz.emergency_c, wiz.temp_scale)) + u()],
                ["Alerts", esc(optionLabel("notify_service", wiz.notify_service))]
            ];
            var html = '<table class="table table-condensed">';
            $.each(rows, function (_, r) { html += '<tr><td>' + r[0] + '</td><td><b>' + r[1] + '</b></td></tr>'; });
            html += '</table>';
            var sim = wiz.simulate;
            html += '<div class="checkbox"><label><input type="checkbox" id="wz_sim"' + (sim ? " checked" : "") + '> Simulation mode (nothing heats up)</label>' +
                    '<p class="help-block small">' + (board.detected ? "" : "No Raspberry Pi detected, so simulation stays on. ") +
                    'Wiring changes take effect after the controller restarts.</p></div>';
            return html;
        }, collect: function () {
            if ($('#wz_sim').length) wiz.simulate = $('#wz_sim').is(':checked');
        } }
    ];

    // ---- helpers -------------------------------------------------------
    function field(label, control, help) {
        return '<div class="form-group"><label>' + label + '</label><div>' + control + '</div>' +
               (help ? '<p class="help-block small">' + help + '</p>' : '') + '</div>';
    }
    function num(id, v, stepv) {
        return '<input type="number" step="' + stepv + '" id="' + id + '" class="form-control input-sm" style="max-width:200px" value="' + esc(v) + '">';
    }
    function yesno(name, v) {
        return '<label class="radio-inline"><input type="radio" name="' + name + '" value="yes"' + (v ? " checked" : "") + '> Yes</label>' +
               '<label class="radio-inline"><input type="radio" name="' + name + '" value="no"' + (!v ? " checked" : "") + '> No</label>';
    }
    function schemaItem(key) {
        var found = null;
        $.each(settingsData.schema, function (_, i) { if (i.key === key) found = i; });
        return found;
    }
    function schemaOptions(key) { var i = schemaItem(key); return i ? i.options : []; }
    function optionLabel(key, v) {
        var l = v;
        $.each(schemaOptions(key), function (_, o) { if (String(o[0]) === String(v)) l = o[1]; });
        return l;
    }
    function pinText() {
        if (BOARDS_SPI.indexOf(wiz.sensor_board) < 0) return " (I2C)";
        if (wiz.spi_mode === "hardware") return " (hardware SPI, CS GPIO " + wiz.spi_cs + ")";
        return " (CS GPIO " + wiz.spi_cs + ", CLK GPIO " + wiz.spi_sclk + ", DO GPIO " + wiz.spi_miso + ")";
    }

    function drawPins(roles) {
        if (!$('#wz_pins').length) return;
        var values = {};
        $.each(["gpio_heat", "spi_cs", "spi_sclk", "spi_miso", "spi_mosi"], function (_, k) { values[k] = wiz[k]; });
        if (wiz.has_contactor) values.gpio_contactor = wiz.gpio_contactor;
        if (!wiz.has_relay && roles.indexOf("gpio_heat") < 0) delete values.gpio_heat;
        if (wiz.spi_mode === "hardware" || BOARDS_SPI.indexOf(wiz.sensor_board) < 0) {
            delete values.spi_sclk; delete values.spi_miso; delete values.spi_mosi;
            if (BOARDS_SPI.indexOf(wiz.sensor_board) < 0) delete values.spi_cs;
        } else if (roles.indexOf("spi_mosi") < 0) {
            delete values.spi_mosi;
        }
        PinPicker.render($('#wz_pins'), {
            header: board.header_pins, model: board.model, values: values, roles: roles,
            fixed: PinPicker.fixedPins(wiz),
            onChange: function (key, bcm) { wiz[key] = bcm; }
        });
    }

    function collect() {
        var s = STEPS[step];
        if (s.collect) s.collect();
    }

    function redraw() {
        var s = STEPS[step];
        $('#setup_title').text(s.title);
        $('#setup_step').text(step === 0 ? "" : "Step " + step + " of " + (STEPS.length - 1));
        $('#setup_body').html(s.render());
        if (s.after) s.after();
        $('#setup_next, #setup_skip').show();
        $('#setup_back').toggle(step > 0);
        $('#setup_next').html(step === 0 ? "Start setup" : step === STEPS.length - 1 ? "Save and finish" : "Next");
        $('#setup_skip').text(step === 0 ? "Skip – use defaults" : "Skip the rest");
        $('#setup_msg').text("");
    }

    function validate() {
        var s = STEPS[step].id;
        if (s === "relay" && wiz.has_relay && !(Number(wiz.gpio_heat) >= 0)) return "Pick the relay pin on the diagram.";
        if (s === "contactor" && wiz.has_contactor) {
            if (!(Number(wiz.gpio_contactor) >= 0)) return "Pick the contactor pin on the diagram.";
            if (Number(wiz.gpio_contactor) === Number(wiz.gpio_heat) && wiz.has_relay) return "The contactor needs its own pin.";
        }
        if (s === "limits" && !(wiz.emergency_c > 100)) return "Enter the emergency temperature.";
        return "";
    }

    // ---- open / finish -----------------------------------------------
    function initFrom(values) {
        serverScale = values.temp_scale;
        var w = $.extend({}, values);
        w.emergency_c = toC(Number(values.emergency_shutoff_temp), serverScale);
        w.has_relay = !values.simulate;
        w.has_contactor = Number(values.gpio_contactor) >= 0;
        if (!w.has_contactor) w.gpio_contactor = -1;
        delete w.pushover_token;
        return w;
    }

    function open() {
        apiGet("/api/board").always(function (b) {
            if (b && b.header_pins) board = b;
            apiGet("/api/settings").done(function (resp) {
                settingsData = resp;
                wiz = initFrom(resp.values);
                step = 0;
                redraw();
                $('#setupModal').modal({ backdrop: "static", keyboard: false });
            });
        });
    }

    function markDone() {
        return apiPost("/api/setup/done");
    }

    function skip() {
        markDone().always(function () {
            $('#setupModal').modal('hide');
            notify("Setup skipped. You can run it any time from the <b>Setup</b> button.", "info");
        });
    }

    function finish() {
        var v = wiz;
        var values = {
            temp_scale: v.temp_scale, kw_elements: v.kw_elements, kwh_rate: v.kwh_rate, currency_type: v.currency_type,
            sensor_board: v.sensor_board, spi_mode: v.spi_mode, spi_cs: v.spi_cs, spi_sclk: v.spi_sclk,
            spi_miso: v.spi_miso, spi_mosi: v.spi_mosi, mcp9600_address: v.mcp9600_address,
            gpio_heat: v.gpio_heat, gpio_heat_invert: v.gpio_heat_invert,
            gpio_contactor: v.has_contactor ? v.gpio_contactor : -1, gpio_contactor_invert: v.gpio_contactor_invert,
            ct_sensor: v.ct_sensor, ct_i2c_address: v.ct_i2c_address, ct_amps_per_volt: v.ct_amps_per_volt,
            mains_voltage: v.mains_voltage,
            // the server reads temperatures in the scale it had before this save
            emergency_shutoff_temp: Math.round(fromC(v.emergency_c, serverScale) * 10) / 10,
            notify_service: v.notify_service, notify_url: v.notify_url, pushover_user: v.pushover_user,
            simulate: v.simulate
        };
        if (v.thermocouple_type) values.thermocouple_type = v.thermocouple_type;
        if (v.pushover_token) values.pushover_token = v.pushover_token;
        $('#setup_next').prop('disabled', true);
        apiPost("/api/settings", { values: values }).done(function (resp) {
            markDone();
            var errs = $.map(resp.errors || {}, function (e) { return esc(e); });
            if (errs.length) notify("<b>Some settings were not saved</b><br>" + errs.join("<br>"), "danger", 10000);
            if (resp.restart_required && resp.restart_required.length) {
                $('#setup_title').text("Almost done");
                $('#setup_step').text("");
                $('#setup_body').html('<p>Settings saved. The wiring changes take effect when the controller restarts.</p>' +
                    '<p><button type="button" class="btn btn-primary" id="setup_restart">Restart the controller now</button></p>' +
                    '<p class="small text-muted">The page reconnects by itself after about 30 seconds.</p>');
                $('#setup_back, #setup_next, #setup_skip').hide();
                $('#setup_restart').on('click', function () {
                    apiPost("/api", { cmd: "restart" }).done(function () {
                        $('#setup_body').html('<p>Restarting&hellip;</p>');
                        setTimeout(function () { window.location.reload(); }, 20000);
                    }).fail(fail("Could not restart"));
                });
            } else {
                $('#setupModal').modal('hide');
                window.location.reload();
            }
        }).fail(fail("Could not save")).always(function () { $('#setup_next').prop('disabled', false); });
    }

    function next() {
        collect();
        var err = validate();
        if (err) { $('#setup_msg').text(err); return; }
        if (STEPS[step].id === "relay") {
            // with a relay on a real Pi the controller drives the kiln; without one it simulates
            wiz.simulate = !(wiz.has_relay && board.detected);
        }
        if (step === STEPS.length - 1) { finish(); return; }
        step += 1;
        redraw();
    }

    function back() {
        collect();
        if (step > 0) { step -= 1; redraw(); }
    }

    // ---- SETUP menu: re-run, or reset to defaults first -----------------
    function openMenu() {
        $('#setup_reset_list').empty().hide();
        $('#setup_reset_confirm').hide();
        $('#setup_menu_buttons').show();
        $('#setupMenuModal').modal('show');
    }

    function previewReset() {
        apiGet("/api/settings/reset-preview").done(function (resp) {
            var c = resp.changes;
            var html;
            if (!c.length) {
                html = '<p>All settings are already at their defaults.</p>';
            } else {
                html = '<p>These <b>' + c.length + '</b> settings go back to their defaults:</p><div class="table-responsive" style="max-height:45vh;overflow-y:auto">' +
                       '<table class="table table-condensed table-striped small"><tr><th>Setting</th><th>Now</th><th>Default</th></tr>';
                $.each(c, function (_, r) {
                    html += '<tr><td>' + esc(r.group) + ': ' + esc(r.label) + '</td><td>' + esc(r.current) + '</td><td>' + esc(r.default) + '</td></tr>';
                });
                html += '</table></div>';
            }
            $('#setup_menu_buttons').hide();
            $('#setup_reset_list').html(html).show();
            $('#setup_reset_confirm').show();
        }).fail(fail("Could not load settings"));
    }

    function doReset() {
        apiPost("/api/settings/reset", { keep_hardware: false }).done(function () {
            $('#setupMenuModal').modal('hide');
            loadConfig();
            open();
        }).fail(fail("Could not reset"));
    }

    function bind() {
        $('#setup_next').on('click', next);
        $('#setup_back').on('click', back);
        $('#setup_skip').on('click', skip);
    }

    return { open: open, openMenu: openMenu, previewReset: previewReset, doReset: doReset, bind: bind };
})();
