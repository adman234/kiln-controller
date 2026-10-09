import logging
import os

########################################################################
#
#   config.py holds the DEFAULT settings.
#
#   Most of these can now be changed from the web UI (gear icon). Changes
#   made there are saved in storage/settings.json and override the values
#   in this file. To go back to these defaults, delete that file.
#
#   All temperatures in this file are in temp_scale units (see below).
#   Internally the controller works in Celsius.
#
########################################################################

### Logging
log_level = logging.INFO
log_format = '%(asctime)s %(levelname)s %(name)s: %(message)s'

### Server
listening_port = 8081

########################################################################
# Cost Information
#
# Used for the cost estimate before a run and the actual cost during a
# run. After a few firings the estimate is learned from your kiln's
# real on-time (see lib/history.py).
kwh_rate        = 0.1319  # cost per kilowatt hour
kw_elements     = 9.460   # power of the elements when on, in kilowatts
currency_type   = "$"     # Currency Symbol

########################################################################
#
# Hardware Setup (BCM pin numbers)
#
# Software SPI (default) can use any GPIO pins. Hardware SPI on a pi
# must use SPI0: SCLK = BCM 11, MISO = BCM 9, MOSI = BCM 10 and any pin
# for chip select.
spi_mode  = "software"  # "software" or "hardware"
spi_sclk  = 17    # spi clock
spi_miso  = 27    # spi Microcomputer In Serial Out (DO on the breakout)
spi_cs    = 22    # spi Chip Select
spi_mosi  = 10    # spi Microcomputer Out Serial In (not connected on MAX31855)
gpio_heat = 23    # output that controls relay
gpio_heat_invert = False  # invert the output state

#######################################
### Temperature sensor board
#######################################
#   max31855 - K type thermocouple only
#   max31856 - B, E, J, K, N, R, S, T thermocouples
#   max6675  - K type only, older chip
#   mcp9600  - B, E, J, K, N, R, S, T thermocouples (I2C on SDA/SCL)
#   max31865 - PT100/PT1000 RTD (only good to ~850C)
sensor_board = "max6675"
thermocouple_type = "K"

# MCP9600 I2C address, RTD settings for MAX31865
mcp9600_address = "0x67"
rtd_nominal = 100.0
rtd_ref_resistor = 430.0
rtd_wires = 2

########################################################################
#
# If your kiln is above the starting temperature of the schedule when you
# click the Start button... skip ahead and begin at the first point in
# the schedule matching the current kiln temperature.
seek_start = True

########################################################################
#
# duty cycle of the entire system in seconds
#
# Every N seconds a decision is made about switching the relay[s]
# on & off and for how long. The thermocouple is read
# temperature_average_samples times during and the median value is used.
sensor_time_wait = 2

########################################################################
#
#   PID parameters
#
# Use the Autotune button in the web UI (gear -> PID) to find these.
# Note that the integral pid_ki is inverted so that a smaller number
# means more integral action. These are in temp_scale units.
pid_kp = 10   # Proportional
pid_ki = 80   # Integral
pid_kd = 220.83497910261562  # Derivative

########################################################################
#
#   Simulation parameters
simulate = True
sim_t_env      = 65     # deg
sim_c_heat     = 500.0  # J/K  heat capacity of heat element
sim_c_oven     = 5000.0 # J/K  heat capacity of oven
sim_p_heat     = 5450.0 # W    heating power of oven
sim_R_o_nocool = 0.5    # K/W  thermal resistance oven -> environment
sim_R_ho_noair = 0.1    # K/W  thermal resistance heat element -> oven

# if you want simulations to happen faster than real time, this can be
# set as high as 1000 to speed simulations up by 1000 times.
sim_speedup_factor = 1


########################################################################
#
#   Time and Temperature parameters
#
# temp_scale is the unit the values in THIS FILE are written in, and the
# default display unit. You can switch the display unit any time in
# the web UI without touching this file.
temp_scale          = "f" # c = Celsius | f = Fahrenheit
time_scale_slope    = "h" # m = Minutes | h = Hours - heating rate shown per
time_scale_profile  = "m" # m = Minutes | h = Hours - default editor time unit

# emergency shutoff the profile if this temp is reached or exceeded.
# This just shuts off the profile. If your SSR is working, your kiln will
# naturally cool off. If your SSR has failed/shorted/closed circuit, this
# means your kiln receives full power until your house burns down.
# this should not replace you watching your kiln or use of a kiln-sitter
# 2350F (1288C) leaves room for casting gold, silver, copper and aluminium
# bronze (pours up to ~1200C) and cone 10. Lower it if you only fire lower.
emergency_shutoff_temp = 2350

# If the current temperature is outside the pid control window,
# delay the schedule until it does back inside. This allows for heating
# and cooling as fast as possible and not continuing until temp is reached.
kiln_must_catch_up = True

# This setting defines the window within which PID control occurs.
# Outside this window (N degrees below or above the current target)
# the elements are either 100% on because the kiln is too cold
# or 100% off because the kiln is too hot. No integral builds up
# outside the window.
pid_control_window = 5 #degrees

# thermocouple offset
# If you put your thermocouple in ice water and it reads 36F, you can
# set set this offset to -4 to compensate.
thermocouple_offset=0

# number of samples of temperature to take over each duty cycle.
# The median of these samples is used for the temperature.
temperature_average_samples = 10

# Thermocouple AC frequency filtering - set to True if in a 50Hz locale, else leave at False for 60Hz locale
ac_freq_50hz = False

########################################################################
# Emergencies - or maybe not
########################################################################
# You should only set these to True if you experience a problem
# and WANT to ignore it to complete a firing.
ignore_temp_too_high = False
ignore_tc_lost_connection = False
ignore_tc_cold_junction_range_error = False
ignore_tc_range_error = False
ignore_tc_cold_junction_temp_high = False
ignore_tc_cold_junction_temp_low = False
ignore_tc_temp_high = False
ignore_tc_temp_low = False
ignore_tc_voltage_error = False
ignore_tc_short_errors = False
ignore_tc_unknown_error = False

# This overrides all possible thermocouple errors and prevents the
# process from exiting.
ignore_tc_too_many_errors = False

########################################################################
# automatic restarts - if you have a power brown-out and the raspberry pi
# reboots, this restarts your kiln where it left off in the firing profile.
# This only happens if power comes back before automatic_restart_window
# is exceeded (in minutes). The kiln-controller.py process must start
# automatically on boot-up for this to work.
# DO NOT put automatic_restart_state_file anywhere in /tmp.
automatic_restarts = True
automatic_restart_window = 60 # max minutes since power outage
automatic_restart_state_file = os.path.abspath(os.path.join(os.path.dirname( __file__ ),'state.json'))

########################################################################
# load kiln profiles from this directory
# See https://github.com/jbruce12000/kiln-profiles for shared profiles
kiln_profiles_directory = os.path.abspath(os.path.join(os.path.dirname( __file__ ),"storage", "profiles"))

########################################################################
# low temperature throttling of elements
# kiln elements have lots of power and tend to drastically overshoot
# at low temperatures. When under the set point and outside the PID
# control window and below throttle_below_temp, only throttle_percent
# of the elements are used max.
# To prevent throttling, set throttle_percent to 100.
throttle_below_temp = 300
throttle_percent = 20

########################################################################
# Web UI password. Leave empty for none (anyone on your network can
# control the kiln). Can also be set in the web UI.
web_password = ""
