r"""Pure simulation model: no I/O, no telemetry. Telemetry wraps it in server.py.

Flow per tick:  Miners -> ore -> (split lever) -> Mint -> gold -> upgrades/Lab
                                              \-> ammo -> Turrets -> damage
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

# upgrade path -> (source, base cost, max level)
UPGRADES = {
    "miner.yield": ("miner", 15, 20),
    "miner.speed": ("miner", 40, 15),
    "miner.automation": ("miner", 120, 10),  # cheaper miner units
    "mint.rate": ("mint", 25, 20),
    "mint.multiplier": ("mint", 80, 15),
    "lab.efficiency": ("lab", 60, 15),
    "turret.damage": ("turret", 30, 20),
    "turret.firerate": ("turret", 70, 15),
    "turret.crit": ("turret", 150, 10),
}
UNIT_BASE_COST = {"miner": 10, "mint": 25, "lab": 80, "turret": 40}
LEVERS = {
    "ore_split": (0.0, 1.0),  # fraction of ore routed to the mint; rest is ammo
    "power": (0.5, 2.0),      # turret fire-rate multiplier; costs extra ammo per hit
    "priority": ("focus", "spread"),
}
PHASE_SECONDS = 30  # dummy field alternates boss (favors focus) / swarm (favors spread)


@dataclass
class Sim:
    t: float = 0.0
    ore: float = 0.0
    ammo: float = 5.0
    gold: float = 30.0
    research: float = 0.0
    units: dict = field(default_factory=lambda: {"miner": 1, "mint": 1, "lab": 0, "turret": 1})
    levels: dict = field(default_factory=lambda: {k: 0 for k in UPGRADES})
    levers: dict = field(default_factory=lambda: {"ore_split": 0.5, "power": 1.0, "priority": "focus"})
    total_damage: float = 0.0
    window: deque = field(default_factory=lambda: deque(maxlen=10))  # last 10 tick damages
    last: dict = field(default_factory=dict)

    # ---- derived ----
    def lvl(self, k): return self.levels[k]

    def phase(self) -> str:
        return "boss" if int(self.t // PHASE_SECONDS) % 2 == 0 else "swarm"

    def unit_cost(self, kind: str) -> float:
        base = UNIT_BASE_COST[kind] * 1.15 ** self.units[kind]
        if kind == "miner":
            base *= 0.9 ** self.lvl("miner.automation")
        return round(base, 1)

    def upgrade_cost(self, path: str) -> float:
        return round(UPGRADES[path][1] * 1.6 ** self.levels[path], 1)

    def tier(self) -> int: return sum(self.levels.values()) // 4

    def slo_target(self) -> float: return 6 * 2.2 ** self.tier()

    def dps(self) -> float: return sum(self.window) / max(len(self.window), 1)

    # ---- tick stages (separate so each can be its own span) ----
    def stage_miners(self, dt):
        out = self.units["miner"] * 1.0 * (1 + .25 * self.lvl("miner.yield")) * (1 + .2 * self.lvl("miner.speed")) * dt
        self.ore += out
        return {"produced": out}

    def stage_mint(self, dt):
        offered = self.ore * self.levers["ore_split"]
        capacity = self.units["mint"] * 2.0 * (1 + .3 * self.lvl("mint.rate")) * dt
        converted = min(offered, capacity)
        gold = converted * 0.8 * (1 + .2 * self.lvl("mint.multiplier"))
        self.gold += gold
        self.ore -= converted
        # ore offered beyond mint capacity stays in the ore pool: it flows to ammo below
        return {"offered": offered, "capacity": capacity, "converted": converted,
                "gold": gold, "overflow": offered - converted}

    def stage_lab(self, dt):
        want = self.units["lab"] * 0.5 * dt
        spent = min(want, self.gold)
        self.gold -= spent
        rp = spent * 2 * (1 + .25 * self.lvl("lab.efficiency"))
        self.research += rp
        return {"gold_spent": spent, "rp": rp, "starved": want - spent}

    def research_bonus(self) -> float:
        return 1 + 0.1 * math.sqrt(self.research)

    def stage_turrets(self, dt):
        # whatever ore was not converted becomes ammo
        self.ammo += self.ore
        self.ore = 0.0
        p = self.levers["power"]
        crit_chance = min(0.05 * self.lvl("turret.crit"), 0.5)
        crit = 1 + crit_chance * 2.0
        match = self.matchup()
        potential = (self.units["turret"] * 3.0 * (1 + .25 * self.lvl("turret.damage"))
                     * (1 + .2 * self.lvl("turret.firerate")) * crit * self.research_bonus() * p * match)
        need = potential * dt * 0.1 * p  # ammo per damage rises with power
        fired = 1.0 if need <= 0 else min(1.0, self.ammo / need)
        self.ammo -= need * fired
        dmg = potential * dt * fired
        self.total_damage += dmg
        return {"potential_dps": potential, "ammo_need": need, "fire_fraction": fired,
                "damage": dmg, "matchup": match, "crit_factor": crit}

    def matchup(self) -> float:
        good = (self.phase() == "boss") == (self.levers["priority"] == "focus")
        return 1.25 if good else 0.8

    def bottleneck(self, mint, turret) -> str:
        if turret["fire_fraction"] < 0.99: return "ammo"
        if mint["overflow"] > 1e-9: return "mint-capacity"
        return "turret-output"

    def tick(self, dt: float = 1.0, tracer=None) -> dict:
        """Run one tick. `tracer` (optional) makes each stage a child span."""
        def stage(name, fn):
            if tracer is None:
                return fn(dt)
            with tracer.start_as_current_span(name) as s:
                r = fn(dt)
                for k, v in r.items(): s.set_attribute(f"sim.{name.split('.')[-1]}.{k}", v)
                return r
        miner = stage("sim.source.miner", self.stage_miners)
        mint = stage("sim.convert.mint", self.stage_mint)
        lab = stage("sim.source.lab", self.stage_lab)
        turret = stage("sim.turret.fire", self.stage_turrets)
        self.t += dt
        self.window.append(turret["damage"] / dt)
        self.last = {"miner": miner, "mint": mint, "lab": lab, "turret": turret,
                     "bottleneck": self.bottleneck(mint, turret),
                     "ore_rate": miner["produced"] / dt, "gold_rate": mint["gold"] / dt}
        return self.last

    # ---- player actions ----
    def buy_unit(self, kind: str) -> tuple[bool, str, float]:
        if kind not in self.units: return False, "unknown", 0
        cost = self.unit_cost(kind)
        if self.gold < cost: return False, "insufficient_gold", cost
        self.gold -= cost; self.units[kind] += 1
        return True, "ok", cost

    def buy_upgrade(self, path: str) -> tuple[bool, str, float]:
        if path not in UPGRADES: return False, "unknown", 0
        cost = self.upgrade_cost(path)
        if self.levels[path] >= UPGRADES[path][2]: return False, "max_level", cost
        if self.gold < cost: return False, "insufficient_gold", cost
        self.gold -= cost; self.levels[path] += 1
        return True, "ok", cost

    def set_lever(self, name: str, value):
        if name not in LEVERS: raise ValueError(f"unknown lever {name}")
        lo, hi = LEVERS[name]
        if name == "priority":
            if value not in LEVERS["priority"]: raise ValueError("priority must be focus|spread")
        else:
            value = min(max(float(value), lo), hi)
        old = self.levers[name]; self.levers[name] = value
        return old, value

    def snapshot(self) -> dict:
        return {
            "t": self.t, "ore": self.ore, "ammo": self.ammo, "gold": self.gold,
            "research": self.research, "dps": self.dps(), "total_damage": self.total_damage,
            "slo_target": self.slo_target(), "slo_met": self.dps() >= self.slo_target(),
            "tier": self.tier(), "phase": self.phase(), "levers": dict(self.levers),
            "units": dict(self.units), "levels": dict(self.levels),
            "unit_costs": {k: self.unit_cost(k) for k in self.units},
            "upgrade_costs": {k: self.upgrade_cost(k) for k in UPGRADES},
            "upgrade_max": {k: v[2] for k, v in UPGRADES.items()},
            "last": self.last,
        }
