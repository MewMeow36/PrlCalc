#!/usr/bin/env python3
"""Offline asserts for pearlcalc math. Run: python test_pearlcalc.py"""
import time

import pearlcalc as pc


def cls(cid, name, high, med, low, batch):
    return {"id": cid, "name": name, "prices": [
        {"priority": "high", "price": high}, {"priority": "medium", "price": med},
        {"priority": "low", "price": low}, {"priority": "batch", "price": batch}]}


CLASSES = [
    cls("A", "RTX 5080 (16 GB)", "0.26", "0.223", "0.187", "0.15"),
    cls("B", "RTX 5070 Ti Laptop (12 GB)", "0.13", "0.107", "0.083", "0.06"),
    cls("C", "RTX 5090 (32 GB)", "0.5", "0.417", "0.333", "0.25"),
]

NOW = time.time() * 1000

BUNDLE = {
    "price": {"usd": 1.35, "usd_24h_change": -4.0},
    "account": {
        "connected_workers": [
            {"gpu_info": [{"name": "NVIDIA GeForce RTX 5080", "hashrate": 200e12},
                          {"name": "NVIDIA GeForce RTX 5080", "hashrate": 210e12}]},
            {"gpu_info": [{"name": "NVIDIA GeForce RTX 5070 Ti Laptop GPU", "hashrate": 90e12}]},
        ],
        "balance_transactions": [
            {"amount": 0.5, "reason": "Epoch credit", "timestamp": NOW - 3600_000},
            {"amount": -1.7, "reason": "Auto Payment", "timestamp": NOW - 7200_000},
            {"amount": 5.0, "reason": "Epoch credit", "timestamp": NOW - 2 * 86_400_000},
            {"amount": 0.25, "reason": "Epoch 2026 credit", "timestamp": NOW - 600_000},
        ],
        "pending_rewards": {"total_pending_prl": 0.8,
                            "epochs": [{"amount": 10.0}, {"amount": 0.9}, {"amount": 1.0}]},
    },
    "classes": CLASSES,
    "stats": {"hashrate": 5.0e19, "total_workers": 100},
    "chain": {"networkhashps": 5.0e19, "avg_block_time_s": 191.6, "difficulty": 34556874.16947253},
    "block_reward": 2264.77,
    "pool_fee_pct": 1.0,
    "groups": [
        {"name": "lowgroup", "priority": "low", "replicas": 2,
         "container": {"resources": {"gpu_classes": ["A", "C"]}}},
        {"name": "batchgroup", "priority": "batch", "replicas": 2,
         "container": {"resources": {"gpu_classes": ["B"]}}},
    ],
    "instances": {
        "lowgroup": [{"state": "running"}, {"state": "running"}, {"state": "allocating"}],
        "batchgroup": [{"state": "running"}],
    },
}


def test_normalizer():
    assert pc.normalize_gpu("NVIDIA GeForce RTX 5070 Ti Laptop GPU") == "rtx 5070 ti laptop"
    assert pc.normalize_gpu("RTX 5070 Ti Laptop (12 GB)") == "rtx 5070 ti laptop"
    assert pc.normalize_gpu("NVIDIA GeForce RTX 5080") == "rtx 5080"
    assert pc.normalize_gpu("RTX 5080 (16 GB)") == "rtx 5080"
    assert pc.normalize_gpu("RTX 5080 Laptop (16 GB)") == "rtx 5080 laptop"


def test_matching():
    assert pc.match_class("NVIDIA GeForce RTX 5080", CLASSES)["id"] == "A"
    assert pc.match_class("NVIDIA GeForce RTX 5070 Ti Laptop GPU", CLASSES)["id"] == "B"
    assert pc.match_class("NVIDIA GeForce RTX 5080 Laptop GPU", CLASSES) is None


def test_group_mapping():
    models = pc.wallet_models(BUNDLE["account"]["connected_workers"])
    assert set(models) == {"rtx 5080", "rtx 5070 ti laptop"}
    mapped = {m["name"]: m for m in pc.map_groups(BUNDLE["groups"], CLASSES, models)}
    assert mapped["lowgroup"]["class"] == "RTX 5080 (16 GB)"
    assert mapped["batchgroup"]["class"] == "RTX 5070 Ti Laptop (12 GB)"
    assert not mapped["lowgroup"]["ambiguous"]

    ambiguous_groups = [
        {"name": "g", "priority": "low", "replicas": 1,
         "container": {"resources": {"gpu_classes": ["A", "B"]}}},
    ]
    m = pc.map_groups(ambiguous_groups, CLASSES, models)[0]
    assert m["ambiguous"] and m["class"] == "RTX 5070 Ti Laptop (12 GB)"


def test_override():
    models = pc.wallet_models(BUNDLE["account"]["connected_workers"])
    groups = [{"name": "g", "priority": "low", "replicas": 1,
               "container": {"resources": {"gpu_classes": ["A", "B"]}}}]
    auto = pc.map_groups(groups, CLASSES, models)[0]
    assert auto["ambiguous"] and auto["class"] == "RTX 5070 Ti Laptop (12 GB)"
    pinned = pc.map_groups(groups, CLASSES, models, {"g": "RTX 5080 (16 GB)"})[0]
    assert pinned["class"] == "RTX 5080 (16 GB)" and not pinned["ambiguous"]


def test_compute():
    out = pc.compute(BUNDLE, now_ms=NOW)
    assert out["total_ths"] == 500.0
    assert out["gpu_count"] == 3
    assert out["spend"]["hr"] == 0.434
    assert out["spend"]["day"] == 10.42
    assert out["revenue"]["prl_24h"] == 0.75
    assert out["revenue"]["usd_24h"] == 1.01
    assert out["profit"]["day"] == -9.41
    assert out["revenue"]["runrate_prl_day"] == 24.0
    assert out["per_th"]["spend_day"] == round(10.42 / 500.0, 4)
    g = next(x for x in out["grid"]
             if x["model"] == "NVIDIA GeForce RTX 5080" and x["priority"] == "low")
    assert g["price_hr"] == 0.187
    assert g["cost_gpu_day"] == 4.488
    assert g["cost_th_day"] == round(0.187 * 24 / 205.0, 4)
    assert g["eff_profit_gpu_day"] == round(0.9 * (g["rev_gpu_day"] - g["cost_gpu_day"]), 3)

    assert out["analytics"]["pace_yield_prl_day"] == round(24.0 / 500.0, 6)
    ny = (1e12 / 5.0e19) * (86400 / 191.6) * 2264.77 * 0.99
    assert out["analytics"]["network_yield_prl_day"] == round(ny, 6)
    assert out["analytics"]["block_reward_prl"] == 2264.77
    assert out["analytics"]["difficulty"] == 34556874.16947253
    assert out["per_th"]["rev_day"] == round(out["analytics"]["pace_yield_prl_day"] * 1.35, 4)
    exp_rev = round(500 * ny * 1.35, 2)
    assert out["profit"]["expected_rev_day"] == exp_rev
    assert out["profit"]["expected_day"] == round(exp_rev - 10.42, 2)
    pg = next(x for x in out["per_gpu"] if "5080" in x["model"])
    assert pg["instances"] == 2 and pg["mining"] == 2
    assert pg["profit_day"] == round(pg["rev_day"] - pg["cost_day"], 3)

    assert len(out["workers"]) == 3
    assert out["workers"][0]["ths"] == 210.0
    assert out["workers"][0]["gpu"] == "NVIDIA GeForce RTX 5080"
    assert out["workers"][-1]["ths"] == 90.0
    assert all(out["workers"][i]["ths"] >= out["workers"][i + 1]["ths"] for i in range(2))


def test_uptime_scaling():
    full = pc.compute(BUNDLE, uptimes={"low": 1.0}, now_ms=NOW)
    half = pc.compute(BUNDLE, uptimes={"low": 0.5}, now_ms=NOW)
    f = next(x for x in full["grid"] if x["priority"] == "low" and x["class"] == "RTX 5080 (16 GB)")
    h = next(x for x in half["grid"] if x["priority"] == "low" and x["class"] == "RTX 5080 (16 GB)")
    assert abs(h["eff_profit_gpu_day"] - f["eff_profit_gpu_day"] / 2) < 1e-3


def test_unmapped_fallback():
    bundle = {
        "price": {"usd": 1.35},
        "account": {"connected_workers": [
            {"gpu_info": [{"name": "NVIDIA GeForce RTX 5080", "hashrate": 200e12}]}]},
        "classes": CLASSES,
        "groups": [
            {"name": "mystery", "priority": "batch", "replicas": 1,
             "container": {"resources": {"gpu_classes": ["C"]}}},
        ],
        "instances": {"mystery": [{"state": "running"}]},
    }
    out = pc.compute(bundle, now_ms=NOW)
    g = out["groups"][0]
    assert g["class"] is None
    assert g["estimated_cost"] is True
    assert g["price_hr"] == pc.price_of(CLASSES[2], "batch")
    assert out["spend"]["hr"] == 0.25


def test_mask_and_save_config():
    import json as _json
    import os
    import tempfile

    assert pc.mask_key("") == "—"
    assert pc.mask_key("short") == "•" * 5
    key = "abcdefghijklmnopqrstuvwxyz1234567890"
    masked = pc.mask_key(key)
    assert masked.startswith("abcdefghijklmnopqr")
    assert masked.endswith("7890")
    assert "stuvwxyz" not in masked[18:]

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            _json.dump({"port": 8000, "salad_api_key": "old"}, f)
        pc.save_config({"salad_api_key": "new", "pearl_address": "prl1x"}, path=path)
        cfg = _json.load(open(path, encoding="utf-8"))
        assert cfg["salad_api_key"] == "new"
        assert cfg["pearl_address"] == "prl1x"
        assert cfg["port"] == 8000  # unknown fields preserved
        pc.save_config({"salad_api_key": "", "nothing": None}, path=path)
        cfg = _json.load(open(path, encoding="utf-8"))
        assert cfg["salad_api_key"] == "new"  # blank values are ignored, not written


def test_build_container_patch():
    group = {
        "priority": "low",
        "container": {
            "command": ["/bin/sh", "-c", "run"],
            "environment_variables": {"A": "1"},
            "image": "me/x:m",
            "image_caching": True,
            "resources": {"cpu": 1, "memory": 1024, "shm_size": 64, "gpu_classes": ["A", "B"]},
        },
    }
    p = pc.build_container_patch(group, priority="high")
    assert p["priority"] == "high"
    assert p["image"] == "me/x:m"
    assert p["command"] == ["/bin/sh", "-c", "run"]
    assert p["environment_variables"] == {"A": "1"}
    assert p["resources"] == {"cpu": 1, "memory": 1024, "shm_size": 64, "gpu_classes": ["A", "B"]}
    assert "registry_authentication" not in p

    p2 = pc.build_container_patch(group, gpu_classes=["C"])
    assert p2["resources"]["gpu_classes"] == ["C"]
    assert p2["priority"] == "low"
    assert p2["image"] == "me/x:m"

    p3 = pc.build_container_patch(
        group, image="me/x:2",
        registry_auth={"docker_hub": {"username": "u", "personal_access_token": "t"}})
    assert p3["image"] == "me/x:2"
    assert p3["registry_authentication"]["docker_hub"]["username"] == "u"
    assert p3["resources"]["gpu_classes"] == ["A", "B"]


def test_wallet_missing():
    out = pc.compute({"price": {"usd": 1.35}, "account": {}, "classes": CLASSES,
                      "groups": [], "instances": {}}, now_ms=NOW)
    assert out["total_ths"] == 0.0
    assert out["per_th"]["spend_day"] is None
    assert out["profit"]["expected_day"] is None
    assert out["profit"]["expected_rev_day"] is None


if __name__ == "__main__":
    test_normalizer()
    test_matching()
    test_group_mapping()
    test_override()
    test_compute()
    test_uptime_scaling()
    test_unmapped_fallback()
    test_mask_and_save_config()
    test_build_container_patch()
    test_wallet_missing()
    print("all tests passed")
