#!/usr/bin/env python3
'''Apply a settings file (e.g. kiln-settings.json from the SD card's boot
partition) to storage/settings.json, validating every value exactly like
the web UI does.

    tools/merge-settings.py kiln-settings.json storage/settings.json

Temperatures in the file are in the unit given by its "temp_scale"
("c" or "f"; Celsius if it has none). Unknown keys are reported and
skipped.
'''
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

from settings import Settings, SCHEMA_BY_KEY  # noqa: E402


def main(src, dest):
    with open(src) as f:
        values = json.load(f)
    if not isinstance(values, dict):
        sys.exit("%s must contain a JSON object" % src)
    values = {k: v for k, v in values.items() if not k.startswith("_")}
    unknown = [k for k in values if k not in SCHEMA_BY_KEY]
    for k in unknown:
        print("skipping unknown setting %r" % k)
        values.pop(k)
    s = Settings(path=dest)
    # the display unit first, so temperatures below are read in that unit
    s.update_from_display({"temp_scale": values.pop("temp_scale", "c")})
    changed, restart, errors = s.update_from_display(values)
    for k, e in errors.items():
        print("not applied: %s" % e)
    print("applied %d setting(s) to %s" % (len(changed) + 1, dest))
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
