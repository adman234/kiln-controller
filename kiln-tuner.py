#!/usr/bin/env python

import os
import sys
import csv
import time
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lib'))
import config
from settings import settings

# NOTE: the web UI has a better autotuner (Settings -> PID & Autotune)
# that measures the kiln at the temperature you choose. This script is
# kept for people who prefer the command line.


def recordprofile(csvfile, targettemp):

    from oven import RealOven, SimulatedOven

    # open the file to log data to
    f = open(csvfile, 'w')
    csvout = csv.writer(f)
    csvout.writerow(['time', 'temperature'])

    # construct the oven
    if settings.simulate:
        oven = SimulatedOven()
        oven.target = targettemp * 2 # insures max heating for simulation
    else:
        oven = RealOven()

    # Main loop:
    #
    # * heat the oven to the target temperature at maximum burn.
    # * when we reach it turn the heating off completely.
    # * wait for it to decay back to the target again.
    # * quit
    #
    # We record the temperature every config.sensor_time_wait
    try:

        # heating to target of 400F
        temp = 0
        sleepfor = settings.sensor_time_wait
        stage = "heating"
        while(temp <= targettemp):
            if settings.simulate:
                oven.heat_then_cool()
            else:
                oven.output.heat(sleepfor)
            temp = oven.current_temp()
            
            print("stage = %s, actual = %.2f, target = %.2f" % (stage,temp,targettemp))
            csvout.writerow([time.time(), temp])
            f.flush()

        # overshoot past target of 400F and then cooling down to 400F
        stage = "cooling"
        if settings.simulate:
            oven.target = 0
        while(temp >= targettemp):
            if settings.simulate:
                oven.heat_then_cool()
            else:
                oven.output.cool(sleepfor)
            temp = oven.current_temp()
            
            print("stage = %s, actual = %.2f, target = %.2f" % (stage,temp,targettemp))
            csvout.writerow([time.time(), temp])
            f.flush()

    finally:
        f.close()
        # ensure we always shut the oven down!
        if not settings.simulate:
            oven.output.cool(0)


def line(a, b, x):
    return a * x + b


def invline(a, b, y):
    return (y - b) / a


def plot(xdata, ydata,
         tangent_min, tangent_max, tangent_slope, tangent_offset,
         lower_crossing_x, upper_crossing_x):
    from matplotlib import pyplot

    minx = min(xdata)
    maxx = max(xdata)
    miny = min(ydata)
    maxy = max(ydata)

    pyplot.scatter(xdata, ydata)

    pyplot.plot([minx, maxx], [miny, miny], '--', color='purple')
    pyplot.plot([minx, maxx], [maxy, maxy], '--', color='purple')

    pyplot.plot(tangent_min[0], tangent_min[1], 'v', color='red')
    pyplot.plot(tangent_max[0], tangent_max[1], 'v', color='red')
    pyplot.plot([minx, maxx], [line(tangent_slope, tangent_offset, minx), line(tangent_slope, tangent_offset, maxx)], '--', color='red')

    pyplot.plot([lower_crossing_x, lower_crossing_x], [miny, maxy], '--', color='black')
    pyplot.plot([upper_crossing_x, upper_crossing_x], [miny, maxy], '--', color='black')

    pyplot.show()


def calculate(filename, tangentdivisor, showplot):
    # parse the csv file
    xdata = []
    ydata = []
    filemintime = None
    with open(filename) as f:
        for row in csv.DictReader(f):
            try:
                time = float(row['time'])
                temp = float(row['temperature'])
                if filemintime is None:
                    filemintime = time

                xdata.append(time - filemintime)
                ydata.append(temp)
            except ValueError:
                continue  # just ignore bad values!

    # gather points for tangent line
    miny = min(ydata)
    maxy = max(ydata)
    midy = (maxy + miny) / 2
    yoffset = int((maxy - miny) / tangentdivisor)
    tangent_min = tangent_max = None
    for i in range(0, len(xdata)):
        rowx = xdata[i]
        rowy = ydata[i]

        if rowy >= (midy - yoffset) and tangent_min is None:
            tangent_min = (rowx, rowy)
        elif rowy >= (midy + yoffset) and tangent_max is None:
            tangent_max = (rowx, rowy)

    # calculate tangent line to the main temperature curve
    tangent_slope = (tangent_max[1] - tangent_min[1]) / (tangent_max[0] - tangent_min[0])
    tangent_offset = tangent_min[1] - line(tangent_slope, 0, tangent_min[0])

    # determine the point at which the tangent line crosses the min/max temperaturess
    lower_crossing_x = invline(tangent_slope, tangent_offset, miny)
    upper_crossing_x = invline(tangent_slope, tangent_offset, maxy)

    # compute parameters
    L = lower_crossing_x - min(xdata)
    T = upper_crossing_x - lower_crossing_x

    # Magic Ziegler-Nicols constants ahead!
    Kp = 1.2 * (T / L)
    Ti = 2 * L
    Td = 0.5 * L
    Ki = Kp / Ti
    Kd = Kp * Td

    # output to the user. The data is in Celsius so these gains are per
    # degree C, which is what the web UI (Settings -> PID) expects.
    print("gains per degree C (enter these in the web UI under Settings -> PID):")
    print("pid_kp = %s" % (Kp))
    print("pid_ki = %s" % (1 / Ki))
    print("pid_kd = %s" % (Kd))
    if config.temp_scale.lower() == "f":
        print("\nthe same gains for config.py (temp_scale = f):")
        print("pid_kp = %s" % (Kp / 1.8))
        print("pid_ki = %s" % (1.8 / Ki))
        print("pid_kd = %s" % (Kd / 1.8))


    if showplot:
        plot(xdata, ydata,
             tangent_min, tangent_max, tangent_slope, tangent_offset,
             lower_crossing_x, upper_crossing_x)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Kiln tuner')
    parser.add_argument('-c', '--calculate_only', action='store_true')
    parser.add_argument('-t', '--target_temp', type=float, default=400, help="Target temperature")
    parser.add_argument('-d', '--tangent_divisor', type=float, default=8, help="Adjust the tangent calculation to fit better. Must be >= 2 (default 8).")
    parser.add_argument('-s', '--showplot', action='store_true', help="draw plot so you can see tanget line and possibly change")
    args = parser.parse_args()

    csvfile = "tuning.csv"
    # --target_temp is given in degrees F, the controller works in C
    target = (args.target_temp - 32) * 5 / 9
    tangentdivisor = args.tangent_divisor 

    # default behavior is to record profile to csv file tuning.csv
    # and then calculate pid values and print them
    if args.calculate_only:
        calculate(csvfile, tangentdivisor, args.showplot)
    else:
        recordprofile(csvfile, target)
        calculate(csvfile, tangentdivisor, args.showplot)
