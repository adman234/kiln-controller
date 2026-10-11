// Raspberry Pi GPIO header diagram and pin picker.
//
// PinPicker.render($el, opts) draws the header the way it looks on the
// board (odd pins on the left, even on the right, pin 1 top left) and lets
// the user click a GPIO to assign it to the selected function.
//
// opts:
//   header:   40 or 26 (original Model A/B)
//   model:    detected model name, or null
//   values:   {role_key: bcm or -1}
//   roles:    role keys the user may change here (first one selected)
//   fixed:    [{bcm, label}] pins in use by a bus (I2C, hardware SPI)
//   onChange: function(role_key, bcm)

var PIN_ROLES = {
    gpio_heat:      { label: "Relay (SSR)", short: "RELAY", color: "#c0392b" },
    gpio_contactor: { label: "Safety contactor", short: "CONTACTOR", color: "#8e44ad" },
    spi_cs:         { label: "Sensor CS", short: "CS", color: "#2471a3" },
    spi_sclk:       { label: "Sensor CLK / SCK", short: "CLK", color: "#1f8a70" },
    spi_miso:       { label: "Sensor DO / MISO", short: "DO", color: "#b9770e" },
    spi_mosi:       { label: "Sensor DI / MOSI", short: "DI", color: "#6c7a89" }
};

// physical pin -> description. t: 3v3, 5v, gnd, gpio
var PI_HEADER_40 = [null,
    { t: "3v3" }, { t: "5v" },
    { t: "gpio", bcm: 2, fn: "I2C SDA" }, { t: "5v" },
    { t: "gpio", bcm: 3, fn: "I2C SCL" }, { t: "gnd" },
    { t: "gpio", bcm: 4 }, { t: "gpio", bcm: 14, fn: "UART TX" },
    { t: "gnd" }, { t: "gpio", bcm: 15, fn: "UART RX" },
    { t: "gpio", bcm: 17 }, { t: "gpio", bcm: 18 },
    { t: "gpio", bcm: 27 }, { t: "gnd" },
    { t: "gpio", bcm: 22 }, { t: "gpio", bcm: 23 },
    { t: "3v3" }, { t: "gpio", bcm: 24 },
    { t: "gpio", bcm: 10, fn: "SPI MOSI" }, { t: "gnd" },
    { t: "gpio", bcm: 9, fn: "SPI MISO" }, { t: "gpio", bcm: 25 },
    { t: "gpio", bcm: 11, fn: "SPI SCLK" }, { t: "gpio", bcm: 8, fn: "SPI CE0" },
    { t: "gnd" }, { t: "gpio", bcm: 7, fn: "SPI CE1" },
    { t: "gpio", bcm: 0, fn: "ID EEPROM", reserved: true }, { t: "gpio", bcm: 1, fn: "ID EEPROM", reserved: true },
    { t: "gpio", bcm: 5 }, { t: "gnd" },
    { t: "gpio", bcm: 6 }, { t: "gpio", bcm: 12 },
    { t: "gpio", bcm: 13 }, { t: "gnd" },
    { t: "gpio", bcm: 19 }, { t: "gpio", bcm: 16 },
    { t: "gpio", bcm: 26 }, { t: "gpio", bcm: 20 },
    { t: "gnd" }, { t: "gpio", bcm: 21 }
];

var PinPicker = (function () {
    function esc(s) {
        return String(s === undefined || s === null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function roleOf(values, bcm) {
        for (var k in values) if (values.hasOwnProperty(k) && PIN_ROLES[k] && Number(values[k]) === bcm) return k;
        return null;
    }

    function fixedOf(fixed, bcm) {
        for (var i = 0; i < (fixed || []).length; i++) if (fixed[i].bcm === bcm) return fixed[i];
        return null;
    }

    function pinCell(n, p, o, side) {
        var cls = "pp-pin pp-" + p.t, label, title, style = "";
        if (p.t === "gpio") {
            var role = roleOf(o.values, p.bcm), fix = fixedOf(o.fixed, p.bcm);
            label = "GPIO " + p.bcm + (p.fn ? " <span class='pp-fn'>" + esc(p.fn) + "</span>" : "");
            title = "Pin " + n + ": GPIO " + p.bcm + " (BCM)" + (p.fn ? ", " + p.fn : "");
            if (role) {
                cls += " pp-assigned";
                style = "background:" + PIN_ROLES[role].color + ";border-color:" + PIN_ROLES[role].color;
                label = "<b>" + esc(PIN_ROLES[role].short) + "</b> GPIO " + p.bcm;
                title += " - " + PIN_ROLES[role].label;
            } else if (fix) {
                cls += " pp-fixed";
                label = "<b>" + esc(fix.label) + "</b> GPIO " + p.bcm;
                title += " - in use: " + fix.label;
            } else if (p.reserved) {
                cls += " pp-reserved";
                title += " (reserved for HAT EEPROMs, avoid)";
            } else if (o.roles.length) {
                cls += " pp-free";
            }
        } else {
            label = { "3v3": "3.3V", "5v": "5V", "gnd": "GND" }[p.t];
            title = "Pin " + n + ": " + label + (p.t === "5v" ? " (do not connect to a GPIO)" : "");
        }
        var dot = '<span class="pp-dot" style="' + style + '">' + n + '</span>';
        var text = '<span class="pp-label">' + label + '</span>';
        return '<div class="' + cls + ' pp-' + side + '" data-pin="' + n + '"' +
               (p.bcm !== undefined ? ' data-bcm="' + p.bcm + '"' : '') + ' title="' + esc(title) + '">' +
               (side === "left" ? text + dot : dot + text) + '</div>';
    }

    function render($el, opts) {
        var o = $.extend({ header: 40, model: null, values: {}, roles: [], fixed: [], onChange: function () {} }, opts);
        var active = $el.data("pp-active");
        if (o.roles.indexOf(active) < 0) active = o.roles[0];
        $el.data("pp-active", active);
        var pins = Math.min(o.header || 40, 40);

        var html = '<div class="pp">';
        html += '<div class="pp-model small">' + (o.model ? "<b>" + esc(o.model) + "</b> detected, " + pins + "-pin header."
                : "No Raspberry Pi detected (simulation). Showing the standard 40-pin header.") +
                " Pin 1 is the square pad" + (pins === 40 ? ", at the end of the header nearest the SD card on a Zero." : ".") + '</div>';
        if (o.roles.length) {
            html += '<div class="pp-roles">Click a pin for: ';
            $.each(o.roles, function (_, k) {
                var r = PIN_ROLES[k], v = Number(o.values[k]);
                html += '<button type="button" class="btn btn-xs pp-role' + (k === active ? ' active' : '') + '" data-role="' + k +
                        '" style="border-color:' + r.color + (k === active ? ';background:' + r.color + ';color:#fff' : '') + '">' +
                        esc(r.label) + ': ' + (v >= 0 ? 'GPIO ' + v : 'none') + '</button> ';
            });
            html += '</div>';
        }
        html += '<div class="pp-header">';
        for (var n = 1; n <= pins; n += 2) {
            html += '<div class="pp-row">' + pinCell(n, PI_HEADER_40[n], o, "left") + pinCell(n + 1, PI_HEADER_40[n + 1], o, "right") + '</div>';
        }
        html += '</div><div class="pp-msg small text-danger"></div></div>';
        $el.html(html);

        $el.find(".pp-role").on("click", function () {
            $el.data("pp-active", $(this).data("role"));
            render($el, o);
        });
        $el.find(".pp-gpio").on("click", function () {
            var key = $el.data("pp-active");
            if (!key) return;
            var bcm = Number($(this).data("bcm")), p = PI_HEADER_40[Number($(this).data("pin"))];
            var other = roleOf(o.values, bcm), fix = fixedOf(o.fixed, bcm);
            var msg = "";
            if (p.reserved) msg = "GPIO " + bcm + " is reserved for HAT EEPROMs. Pick another pin.";
            else if (fix) msg = "GPIO " + bcm + " is in use by " + fix.label + ".";
            else if (other && other !== key) msg = "GPIO " + bcm + " is already the " + PIN_ROLES[other].label + " pin.";
            if (msg) { $el.find(".pp-msg").text(msg); return; }
            o.values[key] = bcm;
            o.onChange(key, bcm);
            // move on to the next role that has no pin yet
            for (var i = 0; i < o.roles.length; i++) {
                if (!(Number(o.values[o.roles[i]]) >= 0)) { $el.data("pp-active", o.roles[i]); break; }
            }
            render($el, o);
        });
    }

    // pins taken by buses, given the (display) settings values
    function fixedPins(v) {
        var fixed = [];
        var i2c = v.ct_sensor && v.ct_sensor !== "none";
        if (i2c || v.sensor_board === "mcp9600") {
            var who = [i2c ? "current sensor" : null, v.sensor_board === "mcp9600" ? "MCP9600" : null].filter(Boolean).join(" + ");
            fixed.push({ bcm: 2, label: "I2C SDA (" + who + ")" }, { bcm: 3, label: "I2C SCL (" + who + ")" });
        }
        if (v.sensor_board !== "mcp9600" && v.spi_mode === "hardware") {
            fixed.push({ bcm: 11, label: "SPI CLK (sensor)" }, { bcm: 9, label: "SPI MISO (sensor)" }, { bcm: 10, label: "SPI MOSI" });
        }
        return fixed;
    }

    return { render: render, fixedPins: fixedPins };
})();
