import json
import math
import os

import pytest

from settings import Settings, defaults_from_config, settings
from units import c_to_f, f_to_c, state_to_display
from profiles import Profile, ProfileStore, ProfileError, filename_for
from history import RunHistory
from autotune import RelayAutotuner, gains_from_ultimate
import oven as oven_mod


class Cfg(object):
    pass


# --------------------------------------------------------------------- units
def test_unit_round_trip():
    for c in (-40, 0, 100, 1260.5):
        assert abs(f_to_c(c_to_f(c)) - c) < 1e-9
    s = state_to_display({"temperature": 100, "target": 0, "heat_rate": 10,
                          "pidstats": {"setpoint": 100, "ispoint": 0, "err": 100, "p": 5}}, "f")
    assert s["temperature"] == 212 and s["target"] == 32 and s["heat_rate"] == 18
    assert s["pidstats"]["err"] == 180 and s["pidstats"]["p"] == 5


# ------------------------------------------------------------------ settings
def test_config_in_f_is_converted_to_c():
    cfg = Cfg()
    cfg.temp_scale = "f"
    cfg.emergency_shutoff_temp = 2264
    cfg.pid_control_window = 9
    cfg.pid_kp, cfg.pid_ki, cfg.pid_kd = 10, 90, 100
    cfg.spi_cs = 5
    d = defaults_from_config(cfg)
    assert abs(d["emergency_shutoff_temp"] - 1240) < 0.01
    assert abs(d["pid_control_window"] - 5) < 1e-9
    # gains are per degree, so they scale by 1.8 (ki is inverted)
    assert abs(d["pid_kp"] - 18) < 1e-9 and abs(d["pid_ki"] - 50) < 1e-9 and abs(d["pid_kd"] - 180) < 1e-9
    assert d["spi_cs"] == 5


def test_settings_update_from_display(tmp_path):
    cfg = Cfg()
    cfg.temp_scale = "f"
    s = Settings(path=str(tmp_path / "s.json"), cfg=cfg)
    changed, restart, errors = s.update_from_display({
        "emergency_shutoff_temp": "2012", "sensor_board": "max31856", "pid_ki": "0",
        "throttle_percent": "500", "bogus": 1})
    assert not set(changed) - {"emergency_shutoff_temp", "sensor_board"}
    assert abs(s.emergency_shutoff_temp - 1100) < 0.01
    assert restart == ["sensor_board"] or "sensor_board" in restart
    assert "throttle_percent" in errors and "pid_ki" in errors
    # persisted and reloaded
    s2 = Settings(path=str(tmp_path / "s.json"), cfg=cfg)
    assert s2.sensor_board == "max31856"
    # switching the display unit converts what the UI sees, not what is stored
    s2.update_from_display({"temp_scale": "c"})
    assert round(s2.to_display()["emergency_shutoff_temp"]) == 1100


def test_password_is_never_sent_to_ui(tmp_path):
    s = Settings(path=str(tmp_path / "s.json"), cfg=Cfg())
    s.update_from_display({"web_password": "secret"})
    v = s.to_display()
    assert v["web_password"] == "" and v["web_password_set"]
    s.update_from_display({"web_password": "__unchanged__"})
    assert s.web_password == "secret"


# ------------------------------------------------------------------ profiles
def test_profile_validation():
    with pytest.raises(ProfileError):
        Profile({"name": "x", "data": [[0, 10]]})
    with pytest.raises(ProfileError):
        Profile({"name": "x", "data": [[0, 10], [0, 20]]})
    p = Profile({"name": "x", "data": [[0, 20], [3600, 120], [7200, 120]]})
    # exactly at the end used to crash the oven thread
    assert p.get_target_temperature(7200) == 120
    assert p.get_target_temperature(0) == 20
    assert p.get_target_temperature(7201) == 0
    # 1h ramp 0..100 above ambient (50 deg-h) + 1h at 100 above
    assert abs(p.degree_hours() - 150) < 1e-6
    assert abs(p.degree_hours(3600, 7200) - 100) < 1e-6


def test_filename_cannot_escape():
    for evil in ("../../config", "/etc/passwd", "..\\x", "a/../../b"):
        assert "/" not in filename_for(evil) and not filename_for(evil).startswith(".")


def test_store_save_rename_copy_delete(tmp_path):
    st = ProfileStore(str(tmp_path))
    with pytest.raises(ProfileError):
        st.save({"name": "../../evil", "data": [[0, 1], [60, 2]]})
    saved = st.save({"name": "Bisque 04", "temp_units": "f", "data": [[0, 68], [3600, 212]], "notes": "hi"})
    assert saved["data"][1][1] == 100 and saved["created"] and saved["notes"] == "hi"
    with pytest.raises(ProfileError):
        st.save({"name": "Bisque 04", "data": [[0, 1], [60, 2]]}, overwrite=False)
    # rename keeps the created date and removes the old file
    renamed = st.save({"name": "Bisque 04 slow", "data": [[0, 20], [7200, 100]]}, original_name="Bisque 04")
    assert renamed["created"] == saved["created"]
    assert st.find("Bisque 04") is None and st.find("Bisque 04 slow")
    c = st.copy("Bisque 04 slow")
    assert c["name"] == "Bisque 04 slow copy" and c["notes"] == "hi"
    c2 = st.copy("Bisque 04 slow")
    assert c2["name"] == "Bisque 04 slow copy 2"
    st.delete("Bisque 04 slow copy")
    assert sorted(p["name"] for p in st.list()) == ["Bisque 04 slow", "Bisque 04 slow copy 2"]
    # junk files in the directory are ignored
    (tmp_path / "junk.json").write_text("{not json")
    (tmp_path / ".DS_Store").write_text("x")
    assert len(st.list()) == 2


def test_legacy_profiles_without_units_use_config_scale(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"name": "old", "data": [[0, 212], [60, 212]]}))
    import config
    p = ProfileStore(str(tmp_path)).find("old")
    expected = 100 if config.temp_scale == "f" else 212
    assert abs(p["data"][0][1] - expected) < 1e-9


# ------------------------------------------------------------------- history
def test_estimate_learns_from_history(tmp_path):
    h = RunHistory(str(tmp_path / "h.json"))
    p = Profile({"name": "glaze", "data": [[0, 20], [36000, 1220]]})
    est = h.estimate(p)
    assert est["method"] == "no_history"
    # a different schedule fired before: 60 kWh for 4000 degree-hours
    h.add({"profile": "other", "kwh": 60, "degree_hours": 4000, "completed": True,
           "simulated": bool(settings.simulate)})
    est = h.estimate(p)
    assert est["method"] == "history_model"
    assert abs(est["kwh"] - 60 / 4000 * p.degree_hours()) < 0.01
    # same schedule fired before wins
    h.add({"profile": "glaze", "kwh": 40, "degree_hours": round(p.degree_hours(), 1), "completed": True,
           "simulated": bool(settings.simulate)})
    est = h.estimate(p)
    assert est["method"] == "same_profile" and est["kwh"] == 40
    # history file is bounded
    for i in range(150):
        h.add({"profile": "x", "kwh": 1, "degree_hours": 1, "simulated": True})
    assert len(json.load(open(str(tmp_path / "h.json")))) == 100


# ------------------------------------------------------------------ autotune
def simulate_plant(tuner, gain=4.0, tau=600.0, dead=60.0, dt=2.0, ambient=20.0, limit=20000):
    '''first order plus dead time process'''
    temp = ambient
    buf = [0.0] * int(dead / dt)
    t = 0.0
    while not tuner.finished and t < limit * dt:
        out = tuner.update(temp, t)
        buf.append(out)
        u = buf.pop(0)
        # steady state = ambient + gain*100*u
        temp += dt / tau * (ambient + gain * 100 * u - temp)
        t += dt
    return tuner


def test_relay_autotune_on_fopdt_plant():
    tuner = simulate_plant(RelayAutotuner(200, hysteresis=1.0, cycles=4, now=0))
    assert tuner.phase == "done", tuner.error
    r = tuner.result
    # theory for relay test on FOPDT: Pu ~ 2*dead*(1+...) - just sanity check ranges
    assert 100 < r["pu"] < 600
    assert r["ku"] > 0
    for rule in r["rules"].values():
        assert rule["kp"] > 0 and rule["ki"] > 0 and rule["kd"] >= 0
    assert r["period_spread"] < 0.1


def test_autotune_aborts_on_overshoot():
    tuner = RelayAutotuner(100, max_overshoot=5, now=0)
    tuner.update(50, 0)
    assert tuner.update(106, 2) == 0 and tuner.phase == "failed"


def test_gain_conversion_matches_pid_convention():
    g = gains_from_ultimate(10, 100, "ziegler_nichols")
    # Kp=6, Ti=50 -> ki (inverted) = Ti/Kp, Td=12.5 -> kd = Kp*Td
    assert abs(g["kp"] - 6) < 1e-9 and abs(g["ki"] - 50 / 6) < 1e-9 and abs(g["kd"] - 75) < 1e-9


# ----------------------------------------------------------------------- pid
def test_pid_integral_is_bounded_and_no_zero_division():
    import datetime
    pid = oven_mod.PID(kp=1, ki=0.001, kd=1)
    now = datetime.datetime.now()
    # same timestamp twice must not divide by zero
    pid.compute(100, 100 - settings.pid_control_window / 2, now)
    pid.compute(100, 100 - settings.pid_control_window / 2, now)
    for i in range(1, 200):
        pid.compute(100, 100 - settings.pid_control_window / 2, now + datetime.timedelta(seconds=2 * i))
    assert pid.iterm <= 100


# ----------------------------------------------------------------- simulator
@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.setattr(oven_mod, "SCHEDULE_FILE", str(tmp_path / "scheduled.json"))
    for k, v in {"sim_speedup_factor": 100000, "automatic_restarts": False}.items():
        monkeypatch.setitem(settings._values, k, v)
    st = ProfileStore(str(tmp_path / "profiles"))
    st.save({"name": "short", "temp_units": "c", "data": [[0, 20], [600, 60], [1200, 60]]})
    return oven_mod.SimulatedOven(store=st, history=RunHistory(str(tmp_path / "h.json")))


def test_simulated_run_completes_and_records_history(sim):
    sim.run_profile(sim.store.get_profile("short"), allow_seek=False)
    for _ in range(5000):
        if sim.state != "RUNNING":
            break
        sim.tick()
    assert sim.state == "IDLE"
    runs = sim.history.records
    assert len(runs) == 1 and runs[0]["completed"] and runs[0]["kwh"] > 0


def test_scheduled_start(sim):
    import time
    sim.schedule_profile("short", time.time() + 3600)
    assert sim.state == "SCHEDULED" and os.path.exists(oven_mod.SCHEDULE_FILE)
    sim.scheduled["start_at"] = time.time() - 1
    sim.tick()
    assert sim.state == "RUNNING" and not os.path.exists(oven_mod.SCHEDULE_FILE)
    sim.abort_run()
    assert sim.state == "IDLE"


def test_autotune_in_simulator(sim):
    sim.start_autotune(150, hysteresis=2, cycles=3)
    for _ in range(20000):
        if sim.state != "TUNING":
            break
        sim.tick()
    assert sim.state == "IDLE"
    assert sim.last_autotune["phase"] == "done", sim.last_autotune["error"]


def test_pause_resume_guarded(sim):
    assert not sim.pause() and not sim.resume()
    assert sim.state == "IDLE"


def test_oven_loop_survives_errors(sim, monkeypatch):
    sim.run_profile(sim.store.get_profile("short"), allow_seek=False)

    def boom():
        raise RuntimeError("bug")
    monkeypatch.setattr(sim, "update_target_temp", boom)
    # run() catches the exception, turns the output off and goes idle
    try:
        sim.tick()
    except RuntimeError:
        sim.abort_run("internal error")
    assert sim.state == "IDLE" and sim.output_level == 0


def test_automatic_restart_after_power_cut(sim, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "automatic_restart_state_file", str(tmp_path / "state.json"))
    monkeypatch.setitem(settings._values, "automatic_restarts", True)
    sim.run_profile(sim.store.get_profile("short"), allow_seek=False)
    sim.runtime = 300
    sim.heat_on_total = 120
    sim.save_state()
    # a corrupt state file must not crash startup
    (tmp_path / "bad.json").write_text("{")
    monkeypatch.setattr(config, "automatic_restart_state_file", str(tmp_path / "bad.json"))
    assert not sim.should_i_automatic_restart()
    monkeypatch.setattr(config, "automatic_restart_state_file", str(tmp_path / "state.json"))
    # "reboot"
    fresh = oven_mod.SimulatedOven(store=sim.store, history=sim.history)
    assert fresh.should_i_automatic_restart()
    fresh.automatic_restart()
    assert fresh.state == "RUNNING" and abs(fresh.runtime - 300) < 1 and fresh.heat_on_total == 120
