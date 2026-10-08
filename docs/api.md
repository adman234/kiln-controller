start a run

    curl -d '{"cmd":"run", "profile":"cone-05-long-bisque"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

skip the first part of a run
restart the kiln on a specific profile and start at minute 60

    curl -d '{"cmd":"run", "profile":"cone-05-long-bisque","startat":60}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

stop a schedule

    curl -d '{"cmd":"stop"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

post a memo

    curl -d '{"cmd":"memo", "memo":"some significant message"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

stats for currently running schedule

    curl -X GET http://0.0.0.0:8081/api/stats

pause a run (maintain current temperature until resume)

    curl -d '{"cmd":"pause"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

resume a paused run
    
    curl -d '{"cmd":"resume"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

## New endpoints

The old websockets (`/control`, `/storage`, `/config`, `/status`) have been
removed. Live status is a Server-Sent Events stream:

    curl -N http://0.0.0.0:8081/api/events

Each `data:` line is a JSON state; the first one is a `backlog` with the
current profile and log.

Temperatures in requests and responses are in the display unit chosen in
Settings (`temp_scale`), unless noted. If a web password is set, add
`-u any:yourpassword` to the curl commands.

schedule a run to start later (the controller keeps the schedule, it survives a reboot)

    curl -d '{"cmd":"schedule", "profile":"cone-6-long-glaze", "delay_seconds": 21600}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

cancel a scheduled run

    curl -d '{"cmd":"cancel_schedule"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

start a PID autotune (kiln must be idle)

    curl -d '{"cmd":"autotune_start", "setpoint": 932, "output_percent": 100, "hysteresis": 5, "cycles": 3}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

apply the autotune result (rule: tyreus_luyben, no_overshoot, some_overshoot, ziegler_nichols)

    curl -d '{"cmd":"autotune_apply", "rule":"tyreus_luyben"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

current state (temperature, target, autotune progress, scheduled start, ...)

    curl http://0.0.0.0:8081/api/state

schedules with metadata (created, modified, notes, duration, peak, cost estimate, last fired)

    curl http://0.0.0.0:8081/api/profiles

save / rename (`original_name`) / copy / delete a schedule

    curl -d '{"profile": {"name":"my bisque", "temp_units":"c", "data":[[0,20],[3600,100]], "notes":""}, "overwrite": false}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/profiles
    curl -d '{"name":"my bisque", "new_name":"my bisque slow"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/profiles/copy
    curl -d '{"name":"my bisque slow"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/profiles/delete

cost estimate for a schedule, learned from previous firings

    curl 'http://0.0.0.0:8081/api/estimate?profile=cone-6-long-glaze'

firing history (short summary per firing, last 100)

    curl http://0.0.0.0:8081/api/history

settings (values + schema), change settings

    curl http://0.0.0.0:8081/api/settings
    curl -d '{"values": {"temp_scale":"c", "kwh_rate": 0.21}}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/settings

sensor and system diagnostics

    curl http://0.0.0.0:8081/api/diagnostics

pulse the relay (idle only, max 10s) / restart the service

    curl -d '{"cmd":"relay_test", "seconds": 2}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api
    curl -d '{"cmd":"restart"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

send a test alert with the configured notification service

    curl -d '{"cmd":"notify_test"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

backup (settings without secrets, schedules, history) and restore

    curl -o kiln-backup.json http://0.0.0.0:8081/api/backup
    curl -d @kiln-backup.json -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/restore

download schedules (one, or all without a name) and import them

    curl -o bisque.json 'http://0.0.0.0:8081/api/profiles/export?name=cone-05-long-bisque'
    curl -d '{"profiles": [ ... ], "overwrite": false}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/profiles/import
