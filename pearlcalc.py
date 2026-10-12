#!/usr/bin/env python3
"""Pearl (PRL) x SaladCloud profit monitor.

Run: python pearlcalc.py [port]  ->  http://localhost:8000

Cost model: sum of running Salad instances x price-per-hour for the GPU class
each group is mapped to. Mapping = intersect(group's allowed GPU classes,
GPU models your wallet is actively hashing); config.group_class_overrides wins.
Salad has no invoice API, so spend is a live-burn estimate (per-second billing
while running). Revenue comes from pearlhash's own account + price endpoints.
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = ROOT


def _set_paths():
    """Frozen (PyInstaller) builds: runtime data lives next to the exe, bundled
    static files live in the extraction dir."""
    global ROOT, ASSETS
    if getattr(sys, "frozen", False):
        ROOT = os.path.dirname(sys.executable)
        ASSETS = getattr(sys, "_MEIPASS", ROOT)


_set_paths()
SALAD_API = "https://api.salad.com/api/public/organizations"
PEARL_API = "https://pearlhash.xyz/api"
PRIORITIES = ("high", "medium", "low", "batch")
DEFAULT_UPTIME = {"high": 0.99, "medium": 0.95, "low": 0.90, "batch": 0.80}
CACHE_SECONDS = 30
UA = {"User-Agent": "pearlcalc/1.0"}

_lock = threading.Lock()
_bundle = {"data": None, "ts": 0.0, "errors": [], "stale": False}


def load_config(path=None):
    path = path or os.path.join(ROOT, "config.json")
    if not os.path.exists(path):
        src = os.path.join(ASSETS, "config.json")
        if not os.path.exists(src):
            src = os.path.join(ASSETS, "config.example.json")
        with open(src, encoding="utf-8") as f:
            template = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(template)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(updates, path=None):
    """Merge updates into config.json, preserving unknown fields. None values are ignored."""
    path = path or os.path.join(ROOT, "config.json")
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except ValueError:
            cfg = {}
    cfg.update({k: v for k, v in updates.items() if v not in (None, "")})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def mask_key(key):
    key = str(key or "")
    if not key:
        return "—"
    if len(key) <= 8:
        return "•" * len(key)
    return key[:18] + "•" * 8 + key[-4:]


def get_json(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def salad_action(cfg, name, action):
    """Start or stop a container group. action is validated by the caller."""
    org, proj = cfg["salad_org"], cfg["salad_project"]
    url = f"{SALAD_API}/{org}/projects/{proj}/containers/{name}/{action}"
    req = urllib.request.Request(url, method="POST",
                                 headers={"Salad-Api-Key": cfg["salad_api_key"], **UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status


def normalize_gpu(name):
    s = str(name or "").lower().replace("nvidia", " ").replace("geforce", " ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace("gpu", " ")
    s = re.sub(r"\d+\s*gb", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def price_of(cls, priority):
    for p in (cls or {}).get("prices", []):
        if p.get("priority") == priority:
            return float(p["price"])
    return None


def match_class(name, classes):
    if not name:
        return None
    n = normalize_gpu(name)
    for c in classes:
        if normalize_gpu(c["name"]) == n:
            return c
    return None


def wallet_models(workers):
    models = {}
    for w in workers or []:
        for g in w.get("gpu_info", []):
            m = models.setdefault(normalize_gpu(g.get("name")),
                                  {"name": g.get("name"), "count": 0, "hashes": 0.0})
            m["count"] += 1
            m["hashes"] += float(g.get("hashrate") or 0)
    for key, m in models.items():
        m["key"] = key
        m["ths"] = round(m["hashes"] / 1e12, 2)
        m["avg_ths"] = round(m["hashes"] / 1e12 / m["count"], 2) if m["count"] else 0.0
    return models


def map_groups(groups, classes, models, overrides=None):
    by_id = {c["id"]: c for c in classes}
    model_keys = set(models)
    mapped = []
    for g in groups or []:
        allowed = [by_id[i] for i in g.get("container", {}).get("resources", {}).get("gpu_classes", [])
                   if i in by_id]
        cls = match_class((overrides or {}).get(g["name"]), allowed)
        ambiguous = False
        if cls is None:
            cands = [c for c in allowed if normalize_gpu(c["name"]) in model_keys]
            if len(cands) > 1:
                ambiguous = True
                cls = min(cands, key=lambda c: (price_of(c, g.get("priority")) or 1e9))
            elif cands:
                cls = cands[0]
        warning = None
        if cls is None:
            warning = "no allowed class matches a mining GPU — cost estimated"
        elif ambiguous:
            warning = "multiple allowed classes in wallet — picked cheapest"
        mapped.append({
            "name": g["name"],
            "priority": g.get("priority"),
            "desired": g.get("replicas"),
            "class": cls["name"] if cls else None,
            "class_id": cls["id"] if cls else None,
            "allowed": [c["name"] for c in allowed],
            "ambiguous": ambiguous,
            "warning": warning,
        })
    return mapped


def build_bundle(cfg):
    if not (cfg.get("pearl_address") and cfg.get("salad_api_key")
            and cfg.get("salad_org") and cfg.get("salad_project")):
        raise ValueError("not configured — open Settings")
    address = cfg["pearl_address"]
    account = get_json(f"{PEARL_API}/account/{address}")
    price = get_json(f"{PEARL_API}/pearl-price")

    def optional(url):
        try:
            return get_json(url)
        except Exception:
            return None

    stats = optional(f"{PEARL_API}/stats")
    chain = optional(f"{PEARL_API}/chain-info")
    pool = optional(f"{PEARL_API}/pool-wallet-txs?page=1")
    block_reward = None
    try:
        coinbases = sorted(t["receivedGrains"] for t in pool["transactions"]
                           if t.get("type") == "coinbase" and t.get("receivedGrains"))
        if coinbases:
            block_reward = round(coinbases[len(coinbases) // 2] / 1e8, 2)  # grains = 1e-8 PRL
    except (TypeError, KeyError):
        pass
    headers = {"Salad-Api-Key": cfg["salad_api_key"]}
    org, proj = cfg["salad_org"], cfg["salad_project"]
    classes = get_json(f"{SALAD_API}/{org}/gpu-classes", headers)["items"]
    groups = get_json(f"{SALAD_API}/{org}/projects/{proj}/containers", headers)["items"]
    instances = {}
    for g in groups:
        url = f"{SALAD_API}/{org}/projects/{proj}/containers/{g['name']}/instances"
        instances[g["name"]] = get_json(url, headers).get("instances", [])
    return {"account": account, "price": price, "stats": stats, "chain": chain,
            "block_reward": block_reward, "pool_fee_pct": float(cfg.get("pool_fee_pct", 1.0)),
            "classes": classes, "groups": groups, "instances": instances}


def get_bundle(cfg, ttl=CACHE_SECONDS):
    with _lock:
        now = time.time()
        if _bundle["data"] and now - _bundle["ts"] < ttl:
            return _bundle
        try:
            _bundle.update(data=build_bundle(cfg), ts=now, errors=[], stale=False)
        except Exception as e:
            _bundle["errors"] = [f"{type(e).__name__}: {e}"]
            if _bundle["data"]:
                _bundle["stale"] = True
            else:
                raise
        return _bundle


def compute(bundle, uptimes=None, overrides=None, now_ms=None):
    uptimes = {**DEFAULT_UPTIME, **(uptimes or {})}
    now_ms = now_ms or time.time() * 1000
    account = bundle.get("account") or {}
    price = bundle.get("price") or {}
    classes = bundle.get("classes") or []
    groups = bundle.get("groups") or []
    instances = bundle.get("instances") or {}

    models = wallet_models(account.get("connected_workers"))
    total_ths = round(sum(m["ths"] for m in models.values()), 2)
    pearl_usd = float(price.get("usd") or 0)

    gmapped = map_groups(groups, classes, models, overrides)
    by_name = {m["name"]: m for m in gmapped}
    spend_hr = 0.0
    group_rows = []
    for g in groups:
        row = by_name[g["name"]]
        running = sum(1 for i in instances.get(g["name"], []) if i.get("state") == "running")
        cls = next((c for c in classes if c["id"] == row["class_id"]), None)
        p = price_of(cls, g.get("priority")) if cls else None
        estimated = False
        if p is None and running:
            allowed_ids = g.get("container", {}).get("resources", {}).get("gpu_classes", [])
            cand = [price_of(c, g.get("priority")) for c in classes if c["id"] in allowed_ids]
            cand = [x for x in cand if x is not None]
            if cand:
                p = sum(cand) / len(cand)  # unmapped: charge a priced estimate, never $0
                estimated = True
        cost = (p or 0.0) * running
        spend_hr += cost
        group_rows.append({**row, "running": running, "price_hr": p,
                           "estimated_cost": estimated, "cost_hr": round(cost, 4),
                           "salad_state": ((g.get("current_state") or {}).get("status"))})
    spend_hr = round(spend_hr, 4)
    spend_day = round(spend_hr * 24, 2)

    cutoff = now_ms - 86_400_000
    txs = account.get("balance_transactions") or []

    def is_matured_credit(t):
        amount = t.get("amount")
        return (isinstance(amount, (int, float)) and amount > 0
                and "epoch" in str(t.get("reason", "")).lower()
                and float(t.get("timestamp") or 0) > cutoff)

    rev24_prl = round(sum(t["amount"] for t in txs if is_matured_credit(t)), 6)
    rewards = account.get("pending_rewards") or {}
    pending_prl = float(rewards.get("total_pending_prl") or 0)
    epochs = rewards.get("epochs") or []
    epoch_amounts = sorted(float(e.get("amount") or 0) for e in epochs)
    if epoch_amounts:
        mid = len(epoch_amounts) // 2
        median_amount = (epoch_amounts[mid] if len(epoch_amounts) % 2
                         else (epoch_amounts[mid - 1] + epoch_amounts[mid]) / 2)
        runrate_prl_day = round(median_amount * 24, 6)
    else:
        runrate_prl_day = None

    rev24_usd = round(rev24_prl * pearl_usd, 2)
    profit_day = round(rev24_usd - spend_day, 2)
    runrate_usd_day = round(runrate_prl_day * pearl_usd, 2) if runrate_prl_day is not None else None
    margin = round(profit_day / rev24_usd, 4) if rev24_usd > 0 else None

    yield_realized = rev24_prl / total_ths if total_ths else None
    pace_yield = (runrate_prl_day / total_ths) if (runrate_prl_day and total_ths) else yield_realized
    spend_per_th = round(spend_day / total_ths, 4) if total_ths else None
    rev_per_th = round(pace_yield * pearl_usd, 4) if pace_yield else None
    profit_per_th = (round(rev_per_th - spend_per_th, 4)
                     if rev_per_th is not None and spend_per_th is not None else None)

    total_hashes = total_ths * 1e12
    stats = bundle.get("stats") or {}
    chain = bundle.get("chain") or {}
    pool_hps = float(stats.get("hashrate") or 0)
    net_hps = float(chain.get("networkhashps") or 0)
    block_reward = bundle.get("block_reward")
    block_time = chain.get("avg_block_time_s")
    pool_fee = float(bundle.get("pool_fee_pct") or 1.0) / 100
    net_yield = None
    if block_reward and net_hps and block_time:
        net_yield = (1e12 / net_hps) * (86400 / block_time) * block_reward * (1 - pool_fee)
    analytics = {
        "pace_yield_prl_day": round(pace_yield, 6) if pace_yield else None,
        "realized_yield_prl_day": round(yield_realized, 6) if yield_realized else None,
        "network_yield_prl_day": round(net_yield, 6) if net_yield else None,
        "block_reward_prl": block_reward,
        "difficulty": chain.get("difficulty"),
        "break_even_usd": round(spend_day / runrate_prl_day, 4) if (runrate_prl_day and spend_day) else None,
        "break_even_yield_prl_day": (round(spend_day / pearl_usd / total_ths, 6)
                                     if (total_ths and pearl_usd and spend_day) else None),
        "share_pool_pct": round(100 * total_hashes / pool_hps, 5) if (pool_hps and total_hashes) else None,
        "share_network_pct": round(100 * total_hashes / net_hps, 5) if (net_hps and total_hashes) else None,
        "pool_hashrate_ths": round(pool_hps / 1e12, 2) if pool_hps else None,
        "pool_workers": stats.get("total_workers"),
        "network_hashrate_ths": round(net_hps / 1e12, 2) if net_hps else None,
        "network_avg_block_time_s": block_time,
    }

    base_yield = net_yield if net_yield else pace_yield
    exp_rev_day = (round(total_ths * base_yield * pearl_usd, 2)
                   if (total_ths and base_yield) else None)
    expected_day = round(exp_rev_day - spend_day, 2) if exp_rev_day is not None else None
    expected_margin = round(expected_day / exp_rev_day, 4) if (exp_rev_day and exp_rev_day > 0) else None

    per_gpu = []
    for m in models.values():
        cls = match_class(m["name"], classes)
        inst = 0
        cost_hr = 0.0
        for gr in group_rows:
            if cls and gr["class_id"] == cls["id"]:
                inst += gr["running"]
                cost_hr += gr["cost_hr"]
        rev_day = (round(pace_yield * pearl_usd * m["avg_ths"] * m["count"], 3)
                   if pace_yield else None)
        cost_day = round(cost_hr * 24, 3)
        den = inst or m["count"] or 1
        per_gpu.append({
            "model": m["name"],
            "class": cls["name"] if cls else None,
            "mining": m["count"],
            "instances": inst,
            "avg_ths": m["avg_ths"],
            "rev_day": rev_day,
            "cost_day": cost_day,
            "profit_day": round(rev_day - cost_day, 3) if rev_day is not None else None,
            "rev_day_gpu": round(rev_day / m["count"], 3) if (rev_day is not None and m["count"]) else None,
            "cost_day_gpu": round(cost_day / inst, 3) if inst else None,
            "profit_day_gpu": round((rev_day - cost_day) / den, 3) if rev_day is not None else None,
        })

    grid = []
    for m in models.values():
        cls = match_class(m["name"], classes)
        rev_gpu_day = (round(pace_yield * pearl_usd * m["avg_ths"], 3)
                       if pace_yield is not None else None)
        for pr in PRIORITIES:
            p = price_of(cls, pr)
            cost_gpu_day = round(p * 24, 4) if p else None
            u = uptimes.get(pr, DEFAULT_UPTIME[pr])
            eff = (round(u * (rev_gpu_day - cost_gpu_day), 3)
                   if rev_gpu_day is not None and cost_gpu_day is not None else None)
            grid.append({
                "model": m["name"],
                "class": cls["name"] if cls else None,
                "priority": pr,
                "price_hr": p,
                "cost_th_day": round(p * 24 / m["avg_ths"], 4) if p and m["avg_ths"] else None,
                "uptime": u,
                "cost_gpu_day": cost_gpu_day,
                "rev_gpu_day": rev_gpu_day,
                "eff_profit_gpu_day": eff,
            })

    class_rows = [{
        "id": c["id"], "key": normalize_gpu(c["name"]), "name": c["name"],
        "prices": {p["priority"]: float(p["price"]) for p in c.get("prices", [])},
    } for c in classes]

    workers = []
    for w in account.get("connected_workers") or []:
        for gi in w.get("gpu_info") or []:
            workers.append({
                "worker": w.get("worker_name"),
                "ip": w.get("ip"),
                "gpu": gi.get("name"),
                "ths": round(float(gi.get("hashrate") or 0) / 1e12, 2),
            })
    workers.sort(key=lambda x: -x["ths"])

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pearl_usd": pearl_usd,
        "pearl_24h_change": price.get("usd_24h_change"),
        "total_ths": total_ths,
        "gpu_count": sum(m["count"] for m in models.values()),
        "models": list(models.values()),
        "workers": workers,
        "groups": group_rows,
        "revenue": {
            "prl_24h": rev24_prl,
            "usd_24h": rev24_usd,
            "pending_prl": round(pending_prl, 6),
            "pending_usd": round(pending_prl * pearl_usd, 2),
            "runrate_prl_day": runrate_prl_day,
            "runrate_usd_day": runrate_usd_day,
        },
        "spend": {"hr": spend_hr, "day": spend_day},
        "profit": {
            "day": profit_day,
            "margin": margin,
            "runrate_day": (round(runrate_usd_day - spend_day, 2)
                            if runrate_usd_day is not None else None),
            "expected_day": expected_day,
            "expected_margin": expected_margin,
            "expected_rev_day": exp_rev_day,
        },
        "per_th": {
            "spend_day": spend_per_th,
            "rev_day": rev_per_th,
            "profit_day": profit_per_th,
            "yield_prl_day": round(pace_yield, 6) if pace_yield is not None else None,
        },
        "grid": grid,
        "classes": class_rows,
        "per_gpu": per_gpu,
        "analytics": analytics,
        "running_total": sum(r["running"] for r in group_rows),
        "uptimes": uptimes,
    }


FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/calc": ("index.html", "text/html; charset=utf-8"),
    "/calc.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    cfg = {}

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/api/settings":
            self._send(200, json.dumps({
                "pearl_address": self.cfg.get("pearl_address", ""),
                "salad_org": self.cfg.get("salad_org", ""),
                "salad_project": self.cfg.get("salad_project", ""),
                "pool_fee_pct": self.cfg.get("pool_fee_pct", 1),
                "salad_api_key_masked": mask_key(self.cfg.get("salad_api_key")),
            }).encode(), "application/json")
            return
        if route.path == "/api/data":
            q = parse_qs(route.query)
            uptimes = {}
            for pr in PRIORITIES:
                v = q.get("u_" + pr, [None])[0]
                if v not in (None, ""):
                    try:
                        uptimes[pr] = max(0.0, min(1.0, float(v)))
                    except ValueError:
                        pass
            try:
                fresh = q.get("fresh", ["0"])[0] in ("1", "true")
                b = get_bundle(self.cfg, ttl=0 if fresh else CACHE_SECONDS)
                data = compute(b["data"], uptimes, self.cfg.get("group_class_overrides"))
            except Exception as e:
                self._send(502, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(),
                           "application/json")
                return
            data["stale"] = bool(b.get("stale"))
            data["errors"] = list(b.get("errors") or [])
            self._send(200, json.dumps(data).encode(), "application/json")
        elif route.path in FILES:
            fname, ctype = FILES[route.path]
            with open(os.path.join(ASSETS, fname), "rb") as f:
                body = f.read()
            self._send(200, body, ctype)
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        route = urlparse(self.path)
        if route.path == "/api/settings":
            self._post_settings()
            return
        if route.path != "/api/group":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            self._send(400, json.dumps({"error": "invalid json"}).encode(), "application/json")
            return
        name = str(body.get("name") or "")
        action = str(body.get("action") or "")
        if action not in ("start", "stop"):
            self._send(400, json.dumps({"error": "action must be 'start' or 'stop'"}).encode(),
                       "application/json")
            return
        try:
            b = get_bundle(self.cfg)
            known = {g["name"] for g in (b["data"].get("groups") or [])}
        except Exception as e:
            self._send(502, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(),
                       "application/json")
            return
        if name not in known:
            self._send(404, json.dumps({"error": f"unknown container group: {name}"}).encode(),
                       "application/json")
            return
        try:
            status = salad_action(self.cfg, name, action)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            self._send(e.code, json.dumps({"error": f"salad {e.code}: {detail}"}).encode(),
                       "application/json")
            return
        except Exception as e:
            self._send(502, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(),
                       "application/json")
            return
        with _lock:
            _bundle["ts"] = 0.0  # reflect the change on the next poll
        self._send(200, json.dumps({"ok": True, "name": name, "action": action,
                                    "salad_status": status}).encode(), "application/json")

    def _post_settings(self):
        origin = self.headers.get("Origin") or ""
        if origin and not (origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1")):
            self._send(403, json.dumps({"error": "forbidden origin"}).encode(), "application/json")
            return
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._send(415, json.dumps({"error": "content-type must be application/json"}).encode(),
                       "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            self._send(400, json.dumps({"error": "invalid json"}).encode(), "application/json")
            return
        updates = {}
        for field in ("pearl_address", "salad_org", "salad_project", "salad_api_key"):
            v = str(body.get(field) or "").strip()
            if v:
                updates[field] = v
        if body.get("pool_fee_pct") not in (None, ""):
            try:
                updates["pool_fee_pct"] = max(0.0, min(100.0, float(body["pool_fee_pct"])))
            except (TypeError, ValueError):
                self._send(400, json.dumps({"error": "pool_fee_pct must be a number"}).encode(),
                           "application/json")
                return
        save_config(updates)
        with _lock:
            Handler.cfg = load_config()
            _bundle["data"] = None
            _bundle["ts"] = 0.0
            _bundle["errors"] = []
            _bundle["stale"] = False
        self._send(200, json.dumps({"ok": True}).encode(), "application/json")


def make_server(port, cfg):
    Handler.cfg = cfg
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    cfg = load_config()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(cfg.get("port", 8000))
    server = make_server(port, cfg)
    print(f"pearlcalc on http://localhost:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
