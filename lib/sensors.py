'''Temperature sensors.

Every sensor runs in its own thread, samples several times per control
cycle and reports the median in degrees C. Hardware libraries are only
imported when a real sensor is created so simulations run anywhere.
'''
import collections
import logging
import statistics
import threading
import time

from settings import settings

log = logging.getLogger(__name__)


class ThermocoupleError(Exception):
    '''Maps the various library errors onto one set of messages, and
    decides whether the error is ignored based on settings.'''
    IGNORE_SETTING = {
        "not connected": "ignore_tc_lost_connection",
        "short circuit": "ignore_tc_short_errors",
        "unknown": "ignore_tc_unknown_error",
        "cold junction range fault": "ignore_tc_cold_junction_range_error",
        "thermocouple range fault": "ignore_tc_range_error",
        "cold junction temp too high": "ignore_tc_cold_junction_temp_high",
        "cold junction temp too low": "ignore_tc_cold_junction_temp_low",
        "thermocouple temp too high": "ignore_tc_temp_high",
        "thermocouple temp too low": "ignore_tc_temp_low",
        "voltage too high or low": "ignore_tc_voltage_error",
    }
    MAP = {}

    def __init__(self, message):
        self.orig_message = message
        self.message = self.MAP.get(message, "unknown")
        self.ignore = bool(settings.get(self.IGNORE_SETTING.get(self.message, ""), False))
        super().__init__(self.message)


class Max31855_Error(ThermocoupleError):
    # "fault reading" and "Total thermoelectric voltage out of range"
    # are purposely unknown errors
    MAP = {
        "thermocouple not connected": "not connected",
        "short circuit to ground": "short circuit",
        "short circuit to power": "short circuit",
    }


class Max31856_Error(ThermocoupleError):
    MAP = {
        "cj_range": "cold junction range fault",
        "tc_range": "thermocouple range fault",
        "cj_high": "cold junction temp too high",
        "cj_low": "cold junction temp too low",
        "tc_high": "thermocouple temp too high",
        "tc_low": "thermocouple temp too low",
        "voltage": "voltage too high or low",
        "open_tc": "not connected",
    }


class Max6675_Error(ThermocoupleError):
    MAP = {"open": "not connected"}


class Max31865_Error(ThermocoupleError):
    MAP = {
        "rtd_high": "thermocouple temp too high",
        "rtd_low": "thermocouple temp too low",
        "ref_in_low": "voltage too high or low",
        "ref_in_high": "voltage too high or low",
        "rtd_in_low": "not connected",
        "ovuv": "voltage too high or low",
    }


class GenericSensorError(ThermocoupleError):
    MAP = {}


class ThermocoupleTracker(object):
    '''Sliding window of successful/failed reads over the last two
    duty cycles.'''
    def __init__(self, size=None, limit=30):
        self.size = size or settings.temperature_average_samples * 2
        self.status = collections.deque([True] * self.size, maxlen=self.size)
        self.limit = limit

    def good(self):
        self.status.append(True)

    def bad(self):
        self.status.append(False)

    def error_percent(self):
        errors = sum(1 for s in self.status if not s)
        return (errors / self.size) * 100

    def over_error_limit(self):
        return self.error_percent() > self.limit


class TempTracker(object):
    '''sliding window of the last N temperatures, median is reported'''
    def __init__(self, size=None):
        self.size = size or settings.temperature_average_samples
        self.temps = collections.deque(maxlen=self.size)

    def add(self, temp):
        self.temps.append(temp)

    def get_avg_temp(self):
        if not self.temps:
            return None
        return statistics.median(self.temps)


class TempSensor(threading.Thread):
    name_long = "sensor"
    min_sample_interval = 0.0

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.time_step = settings.sensor_time_wait
        self.status = ThermocoupleTracker()
        self.recent_errors = collections.deque(maxlen=20)
        self.last_raw = None
        self.last_read_at = None
        self.reads = 0
        self.failures = 0

    def temperature(self):
        raise NotImplementedError

    def offset_temperature(self):
        '''temperature with the user calibration offset applied'''
        t = self.temperature()
        if t is None:
            return None
        return t + settings.thermocouple_offset

    def cold_junction(self):
        return None

    def diagnostics(self):
        return {
            "board": self.name_long,
            "temperature_c": self.temperature(),
            "last_raw_c": self.last_raw,
            "cold_junction_c": self.cold_junction(),
            "offset_c": settings.thermocouple_offset,
            "error_percent": self.status.error_percent(),
            "over_error_limit": self.status.over_error_limit(),
            "reads": self.reads,
            "failures": self.failures,
            "last_read_at": self.last_read_at,
            "recent_errors": list(self.recent_errors),
        }


class TempSensorSimulated(TempSensor):
    name_long = "simulated"

    def __init__(self):
        TempSensor.__init__(self)
        self.simulated_temperature = settings.sim_t_env

    def temperature(self):
        return self.simulated_temperature

    def cold_junction(self):
        return settings.sim_t_env

    def run(self):
        pass


class TempSensorReal(TempSensor):
    '''real sensor that takes temperature_average_samples readings
    per control cycle'''
    # with no good reading for this long the temperature is unknown
    STALE_SECONDS = 10.0

    def __init__(self):
        TempSensor.__init__(self)
        self.last_good = None   # time.monotonic() of the last good read
        self.sleeptime = max(self.time_step / float(settings.temperature_average_samples),
                             self.min_sample_interval)
        self.temptracker = TempTracker()

    def spi_setup(self):
        import board
        import digitalio
        if settings.spi_mode == "software":
            import adafruit_bitbangio as bitbangio
            self.spi = bitbangio.SPI(pin(settings.spi_sclk), pin(settings.spi_mosi), pin(settings.spi_miso))
            log.info("Software SPI selected for reading thermocouple")
        else:
            self.spi = board.SPI()
            log.info("Hardware SPI selected for reading thermocouple")
        self.cs = digitalio.DigitalInOut(pin(settings.spi_cs))

    def get_temperature(self):
        '''read temp in C, track errors'''
        self.reads += 1
        try:
            temp = self.raw_temp()  # provided by subclasses
            self.last_raw = temp
            self.last_read_at = time.time()
            self.last_good = time.monotonic()
            self.status.good()
            return temp
        except ThermocoupleError as tce:
            self.failures += 1
            self.recent_errors.append({"time": time.time(), "error": tce.message,
                                       "raw": str(tce.orig_message), "ignored": tce.ignore})
            if tce.ignore:
                log.error("Problem reading temp (ignored) %s" % (tce.message))
                self.status.good()
            else:
                log.error("Problem reading temp %s" % (tce.message))
                self.status.bad()
        except Exception as e:
            # bus errors etc. count as failures instead of killing the thread
            self.failures += 1
            self.recent_errors.append({"time": time.time(), "error": "read failed",
                                       "raw": repr(e), "ignored": False})
            log.error("Problem reading temp: %r" % (e,))
            self.status.bad()
        return None

    def temperature(self):
        '''median of recent good readings, or None when the reading can not
        be trusted: nothing good for STALE_SECONDS, or too many errors
        (unless ignore_tc_too_many_errors). The oven treats None as a
        thermocouple dropout.'''
        stale = max(self.STALE_SECONDS, 3 * self.time_step)
        if self.last_good is None or time.monotonic() - self.last_good > stale:
            return None
        if self.status.over_error_limit() and not settings.ignore_tc_too_many_errors:
            return None
        return self.temptracker.get_avg_temp()

    def run(self):
        while True:
            temp = self.get_temperature()
            if temp is not None:
                self.temptracker.add(temp)
            time.sleep(self.sleeptime)


class Max31855(TempSensorReal):
    name_long = "MAX31855"

    def __init__(self):
        TempSensorReal.__init__(self)
        self.spi_setup()
        import adafruit_max31855
        self.thermocouple = adafruit_max31855.MAX31855(self.spi, self.cs)

    def raw_temp(self):
        try:
            return self.thermocouple.temperature_NIST
        except RuntimeError as rte:
            if rte.args and rte.args[0]:
                raise Max31855_Error(rte.args[0])
            raise Max31855_Error('unknown')

    def cold_junction(self):
        try:
            return self.thermocouple.reference_temperature
        except Exception:
            return None


class Max31856(TempSensorReal):
    name_long = "MAX31856"
    # a one-shot conversion takes ~250ms
    min_sample_interval = 0.05

    def __init__(self):
        TempSensorReal.__init__(self)
        self.spi_setup()
        import adafruit_max31856
        tc_type = getattr(adafruit_max31856.ThermocoupleType, settings.thermocouple_type)
        self.thermocouple = adafruit_max31856.MAX31856(self.spi, self.cs, thermocouple_type=tc_type)
        try:
            self.thermocouple.noise_rejection = 50 if settings.ac_freq_50hz else 60
        except AttributeError:
            log.warning("installed adafruit_max31856 does not support noise_rejection")

    def raw_temp(self):
        # The adafruit library does not raise for thermocouple faults,
        # they are in the fault dict.
        temp = self.thermocouple.temperature
        for k, v in self.thermocouple.fault.items():
            if v:
                raise Max31856_Error(k)
        return temp

    def cold_junction(self):
        try:
            return self.thermocouple.reference_temperature
        except Exception:
            return None


class Max6675(TempSensorReal):
    '''old K-type only chip. Reading faster than one conversion
    (~220ms) restarts the conversion, so samples are spaced out.'''
    name_long = "MAX6675"
    min_sample_interval = 0.25

    def __init__(self):
        TempSensorReal.__init__(self)
        self.spi_setup()
        from adafruit_bus_device.spi_device import SPIDevice
        self.device = SPIDevice(self.spi, self.cs, baudrate=1000000, polarity=0, phase=0)
        self.buf = bytearray(2)

    def raw_temp(self):
        with self.device as spi:
            spi.readinto(self.buf)
        value = (self.buf[0] << 8) | self.buf[1]
        if value & 0x4:
            raise Max6675_Error("open")
        return (value >> 3) * 0.25


class Mcp9600(TempSensorReal):
    name_long = "MCP9600"

    def __init__(self):
        TempSensorReal.__init__(self)
        import board
        import busio
        import adafruit_mcp9600
        i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)
        address = int(str(settings.mcp9600_address), 0)
        self.thermocouple = adafruit_mcp9600.MCP9600(i2c, address=address,
                                                     tctype=settings.thermocouple_type)

    def raw_temp(self):
        try:
            return self.thermocouple.temperature
        except (OSError, RuntimeError) as e:
            raise GenericSensorError(str(e))

    def cold_junction(self):
        try:
            return self.thermocouple.ambient_temperature
        except Exception:
            return None


class Max31865(TempSensorReal):
    '''platinum RTD. These top out around 850C so they are only useful
    for low fire / glass work.'''
    name_long = "MAX31865 (RTD)"

    def __init__(self):
        TempSensorReal.__init__(self)
        self.spi_setup()
        import adafruit_max31865
        self.thermocouple = adafruit_max31865.MAX31865(
            self.spi, self.cs, rtd_nominal=settings.rtd_nominal,
            ref_resistor=settings.rtd_ref_resistor, wires=int(settings.rtd_wires))

    def raw_temp(self):
        temp = self.thermocouple.temperature
        faults = self.thermocouple.fault
        names = ["rtd_high", "rtd_low", "ref_in_low", "ref_in_high", "rtd_in_low", "ovuv"]
        for name, bad in zip(names, faults):
            if bad:
                self.thermocouple.clear_faults()
                raise Max31865_Error(name)
        return temp


SENSORS = {
    "max31855": Max31855,
    "max31856": Max31856,
    "max6675": Max6675,
    "mcp9600": Mcp9600,
    "max31865": Max31865,
}


def pin(bcm):
    '''BCM pin number -> blinka pin object'''
    import board
    return getattr(board, "D%d" % int(bcm))


def create_sensor():
    if settings.simulate:
        return TempSensorSimulated()
    cls = SENSORS.get(settings.sensor_board)
    if cls is None:
        raise ValueError("unknown sensor_board %s" % settings.sensor_board)
    log.info("thermocouple board %s" % cls.name_long)
    return cls()
