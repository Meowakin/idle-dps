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
    assert a.gold > b.gold          # more ore to mint -> more gold
    assert b.dps() >= a.dps()       # more ore to ammo -> no less damage


def test_upgrade_purchase_rules():
    s = Sim()
    assert s.buy_upgrade("turret.damage")[:2] == (True, "ok")
    assert s.buy_upgrade("turret.crit")[1] == "insufficient_gold"
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
