from idle_dps.sim import Sim


def run(s, n):
    for _ in range(n): s.tick()


def test_dps_positive_and_ore_flows():
    s = Sim(); run(s, 20)
    assert s.dps() > 0 and s.total_damage > 0


def test_ore_split_tradeoff():
    a, b = Sim(), Sim()
    a.set_lever("ore_split", 1.0); b.set_lever("ore_split", 0.0)
    run(a, 30); run(b, 30)
    assert a.coin > b.coin          # more ore to mint -> more coin
    assert b.dps() >= a.dps()       # more ore to ammo -> no less damage


def test_upgrade_purchase_rules():
    s = Sim()
    assert s.buy_upgrade("turret.damage")[:2] == (True, "ok")
    assert s.buy_upgrade("turret.crit")[1] == "insufficient_coin"
    assert s.buy_upgrade("nope")[1] == "unknown"


def test_lever_clamped_and_validated():
    s = Sim()
    assert s.set_lever("power", 99)[1] == 2.0
    try: s.set_lever("priority", "x"); assert False
    except ValueError: pass


def test_priority_matches_phase():
    s = Sim(); s.set_lever("priority", "focus")
    assert s.matchup() > 1            # t=0 is boss phase
    s.t = 31; assert s.matchup() < 1  # swarm phase punishes focus


def test_unit_build_is_queued_not_instant():
    s = Sim(); s.coin = 1000; s.ore = 1000
    assert s.buy_unit("turret")[:2] == (True, "ok")
    assert s.units["turret"] == 1 and s.coin == 1000      # nothing paid or built yet
    run(s, 3); assert s.units["turret"] == 1              # 10s build
    run(s, 10); assert s.units["turret"] == 2 and not s.queue


def test_build_stalls_when_starved_and_reports_it():
    s = Sim(); s.coin = 0
    s.buy_unit("lab"); r = s.tick()
    assert r["build"]["stall_reason"] == "coin" and r["bottleneck"] in ("build-starved", "ammo")
    assert s.queue[0]["stall_s"] > 0


def test_queue_is_fifo_and_bounded():
    s = Sim(); s.coin = 1e6; s.ore = 1e6
    for _ in range(5): assert s.buy_unit("miner")[0]
    assert s.buy_unit("miner")[1] == "queue_full"
    ids = []
    for _ in range(60):
        b = s.tick()["build"]["completed"]
        if b: ids.append(b["id"])
    assert ids == [1, 2, 3, 4, 5]
    assert ids and s.units["miner"] == 6
