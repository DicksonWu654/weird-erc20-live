#!/usr/bin/env python3
"""Golden-set harness for the W3 control classifier.

    golden.py run        # probe every token in data/control-golden/expected.csv (per chain) -> data/control-golden/observed/<chain>/
    golden.py etherscan  # fetch the independent Etherscan getsourcecode oracle (cached raw) -> data/control-golden/etherscan/
    golden.py logs       # event-log oracle (last AdminChanged/Upgraded/OwnershipTransferred/role events) via Etherscan logs
    golden.py compare    # expected vs observed -> data/control-golden/results.jsonl + report.md

expected.csv columns (blank = not asserted; never filled from our own tool's output):
  chain, token, symbol, confidence, exp_is_proxy, exp_proxy_type, exp_upgrade_kind, exp_upgrade_addr,
  exp_owner_kind, exp_owner_addr, exp_minter_addr, exp_minter_kind, exp_pauser_addr, exp_pauser_kind,
  exp_blacklister_addr, exp_blacklister_kind, exp_has_blacklist, exp_has_mint, exp_immutable, sources, notes
Kinds use: eoa | safe | multisig | timelock | governor | contract_other | renounced | none.
A kind expectation is satisfied if the expected kind is the kind of ANY node on the observed paths (docs usually name the
nearest controller), and separately reported against the terminal kind of the paths.
"""
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
G = ROOT / "data" / "control-golden"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

GROUP = {"eoa": "eoa", "eoa_7702": "eoa_7702", "safe": "safe", "multisig_legacy": "multisig", "timelock": "timelock",
         "governor": "governor", "dao_other": "dao_other", "other_contract": "contract_other", "ownable_contract": "contract_other",
         "access_control_contract": "contract_other", "aragon_kernel": "contract_other", "renounced": "renounced",
         "l1_alias": "l1_alias"}
EQUIV = {"multisig": {"multisig", "safe"}, "safe": {"safe"}, "governor": {"governor", "dao_other"},
         "contract_other": {"contract_other", "dao_other", "governor", "timelock", "safe", "multisig", "l1_alias"},
         "dao_other": {"governor", "dao_other", "contract_other"}, "timelock": {"timelock"}, "eoa": {"eoa"},
         "renounced": {"renounced"}}


def load_expected():
    return list(csv.DictReader(open(G / "expected.csv")))


def cmd_run():
    from run import main as run_main
    rows = load_expected()
    chains = sorted({r["chain"] for r in rows})
    for ch in chains:
        tf = G / f"tokens_{ch}.txt"
        tf.write_text("\n".join(r["token"] for r in rows if r["chain"] == ch) + "\n")
        run_main(["--chain", ch, "--tokens", str(tf), "--block", "latest", "--out", str(G / "observed" / ch),
                  "--retry-partial"] + (["--rolling"] if ch != "ethereum" else []))


def cmd_etherscan():
    import time
    from ctl_rpc import etherscan_key
    import requests
    key = etherscan_key()
    out = G / "etherscan"
    out.mkdir(parents=True, exist_ok=True)
    ids = {"ethereum": 1, "base": 8453, "arbitrum": 42161, "bsc": 56, "polygon": 137, "optimism": 10}
    for r in load_expected():
        p = out / f"{r['chain']}_{r['token'].lower()}.json"
        if p.exists():
            continue
        for attempt in range(6):
            url = "https://api.etherscan.io/v2/api"
            params = dict(chainid=ids[r["chain"]], module="contract", action="getsourcecode", address=r["token"])
            resp = requests.get(url, params=dict(params, apikey=key), timeout=60)
            b = resp.json()
            if isinstance(b.get("result"), str) and "rate limit" in b["result"].lower():
                time.sleep(2 + attempt)
                continue
            break
        import hashlib
        raw = resp.content
        p.write_text(json.dumps({"request": dict(url=url, params=params), "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "http_status": resp.status_code, "sha256": hashlib.sha256(raw).hexdigest(),
                                 "response": b}))
        time.sleep(1.0)
        print("fetched", r["chain"], r["token"])


EVENTS = {  # name -> (topic0, role key, where the new holder is: index into the 32-byte words after topics are prepended)
    "AdminChanged": ("0x7e644d79422f17c01e4894b5f4f588d331ebfa28653d42ae832dc59e38c9798f", "upgrade_admin"),
    "Upgraded": ("0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b", "impl"),
    "OwnershipTransferred": ("0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0", "owner"),
    "MasterMinterChanged": ("0xdb66dfa9c6b8f5226fe9aac7e51897ae8ee94ac31dc70bb6c9900b2574b707e6", "minter_admin"),
    "PauserChanged": ("0xb80482a293ca2e013eda8683c9bd7fc8347cfdaeea5ede58cba46df502c2a604", "pauser"),
    "BlacklisterChanged": ("0xc67398012c111ce95ecb7429b933096c977380ee6c421175a71a4a4c6c88c06e", "blacklister"),
    "MinterChanged": ("0x3b0007eb941cf645526cbb3a4fdaecda9d28ce4843167d9263b536a1f1edc0f6", "minter"),
    "AssetProtectionRoleSet": ("0xd0c36a0ac0fe0d375386bd568fa2947a2dae7523a0a0cfdab20b7532a105bd1b", "blacklister"),
    "SupplyControllerSet": ("0x40d53b0b666e4424f29d55244e7e171a1dc332acc11d04ed4abd884629d8cc97", "minter"),
}
LOGCHAINS = {"ethereum": 1, "arbitrum": 42161, "polygon": 137}  # chains the free Etherscan key can query


def cmd_logs():
    """Event-log oracle: the LAST AdminChanged/Upgraded/OwnershipTransferred/role-change event up to the observed block
    gives the current holder through a channel independent of storage reads / eth_call."""
    import hashlib
    import time
    import requests
    from ctl_rpc import etherscan_key
    key = etherscan_key()
    out = G / "logs-oracle"
    out.mkdir(parents=True, exist_ok=True)
    for r in load_expected():
        if r["chain"] not in LOGCHAINS:
            continue
        run = json.loads((G / "observed" / r["chain"] / "run.json").read_text())
        for name, (t0, _) in EVENTS.items():
            p = out / f"{r['chain']}_{r['token'].lower()}_{name}.json"
            if p.exists():
                continue
            params = dict(chainid=LOGCHAINS[r["chain"]], module="logs", action="getLogs", fromBlock=0, toBlock=run["block"],
                          address=r["token"], topic0=t0, page=1, offset=1000)
            for attempt in range(12):
                resp = requests.get("https://api.etherscan.io/v2/api", params=dict(params, apikey=key), timeout=60)
                b = resp.json()
                if isinstance(b.get("result"), str) and ("rate limit" in b["result"].lower() or "max calls" in b["result"].lower()):
                    time.sleep(1 + attempt * 0.7)
                    continue
                break
            p.write_text(json.dumps({"request": dict(url="https://api.etherscan.io/v2/api", params=params),
                                     "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     "http_status": resp.status_code, "sha256": hashlib.sha256(resp.content).hexdigest(),
                                     "response": b}))
            time.sleep(0.8)
        print("logs", r["chain"], r["symbol"], flush=True)


def last_event_holder(ch, tok, name):
    """Returns (status, address|None, evidence_path): status ok | none (no such events) | truncated | missing."""
    p = G / "logs-oracle" / f"{ch}_{tok.lower()}_{name}.json"
    if not p.exists():
        return "missing", None, None
    b = json.loads(p.read_text())["response"]
    res = b.get("result")
    if not isinstance(res, list):
        return "missing", None, str(p.relative_to(ROOT))
    if not res:
        return "none", None, str(p.relative_to(ROOT))
    if len(res) >= 1000:
        return "truncated", None, str(p.relative_to(ROOT))
    lg = max(res, key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16) if x["logIndex"] != "0x" else 0))
    words = [t[2:] for t in lg["topics"][1:]] + [lg["data"][2 + 64 * i: 2 + 64 * (i + 1)] for i in range(len(lg["data"][2:]) // 64)]
    # new holder is the LAST address-shaped word for the 2-arg events, the only word for the 1-arg ones
    w = words[-1] if words else None
    return "ok", ("0x" + w[-40:].lower()) if w else None, str(p.relative_to(ROOT))


def observed_row(ch, tok):
    f = G / "observed" / ch / "control.jsonl"
    if not f.exists():
        return None
    for line in f.read_text().splitlines():
        j = json.loads(line)
        if j["token"] == tok.lower():
            return j
    return None


def kinds_of(paths, chain_nodes):
    allk = {GROUP.get(n["kind"], n["kind"]) for n in chain_nodes}
    # include nested L1 node of aliases
    for n in chain_nodes:
        l1 = n.get("details", {}).get("l1_node")
        if l1:
            allk.add("l1:" + GROUP.get(l1["kind"], l1["kind"]))
            allk.add(GROUP.get(l1["kind"], l1["kind"]))
    term = set()
    for p in paths:
        tk = p["terminal_kind"]
        term.add(GROUP.get(tk, tk))
    return allk, term


def addrs(chain_nodes):
    return {n["address"] for n in chain_nodes}


def role_holders(row, name):
    return row.get("roles", {}).get(name, [])


def check_kind(exp, anyk, term):
    ok_set = EQUIV.get(exp, {exp})
    return bool(ok_set & anyk), bool(ok_set & term)


def cmd_compare():
    rows = load_expected()
    results = []

    def add(r, check, expected, observed, status, note=""):
        results.append({"chain": r["chain"], "token": r["token"], "symbol": r["symbol"], "check": check,
                        "expected": expected, "observed": observed, "status": status, "note": note})

    for r in rows:
        ch, tok = r["chain"], r["token"].lower()
        o = observed_row(ch, tok)
        if o is None or o["status"] != "ok":
            add(r, "probe", "ok", None if o is None else o["status"], "fail", "no observed row")
            continue
        if r["exp_is_proxy"]:
            e = r["exp_is_proxy"].lower() == "true"
            add(r, "is_proxy", e, o["is_proxy"], "pass" if o["is_proxy"] == e else "fail")
        if r["exp_proxy_type"]:
            STANDARD = {"eip1967", "zos_legacy", "eip1967_beacon", "eip1822_uups", "aragon_app_proxy", "eip2535_diamond"}
            if r["exp_proxy_type"] == "custom":  # documented as a custom delegating/forwarding proxy
                okp = bool(o["is_proxy"]) and o["proxy_type"] not in STANDARD
            else:
                okp = o["proxy_type"] == r["exp_proxy_type"]
            add(r, "proxy_type", r["exp_proxy_type"], o["proxy_type"], "pass" if okp else "fail")
        if r["exp_immutable"]:
            e = r["exp_immutable"].lower() == "true"
            obs = o["control_summary"] == "immutable"
            add(r, "immutable", e, o["control_summary"], "pass" if obs == e else "fail")
        for flag, caps in (("exp_has_blacklist", ("blacklist",)), ("exp_has_mint", ("mint",))):
            if r[flag]:
                e = r[flag].lower() == "true"
                obs = any(c in o["capability_hints"] for c in caps)
                add(r, flag[4:], e, obs, "pass" if obs == e else "fail", "selector hint")
        # upgrade authority
        up_nodes, up_paths = o["upgrade_admin_chain"], o["upgrade_paths"]
        if r["exp_upgrade_addr"]:
            e = r["exp_upgrade_addr"].lower()
            add(r, "upgrade_addr", e, sorted(addrs(up_nodes))[:6], "pass" if e in addrs(up_nodes) else "fail")
        if r["exp_upgrade_kind"]:
            anyk, term = kinds_of(up_paths, up_nodes)
            if r["exp_upgrade_kind"] == "none":
                add(r, "upgrade_kind", "none", o["upgradeable"], "pass" if o["upgradeable"] == "no" else "fail")
            else:
                a, t = check_kind(r["exp_upgrade_kind"], anyk, term)
                add(r, "upgrade_kind(any node)", r["exp_upgrade_kind"], sorted(anyk), "pass" if a else "fail")
                add(r, "upgrade_kind(terminal)", r["exp_upgrade_kind"], sorted(term), "pass" if t else "fail",
                    "informational: docs often name the nearest controller")
        # owner
        ow_nodes, ow_paths = o["owner_chain"], o["owner_paths"]
        if r["exp_owner_addr"]:
            e = r["exp_owner_addr"].lower()
            add(r, "owner_addr", e, sorted(addrs(ow_nodes))[:6], "pass" if e in addrs(ow_nodes) else "fail")
        if r["exp_owner_kind"]:
            if r["exp_owner_kind"] == "none":
                add(r, "owner_kind", "none", sorted({p["terminal_kind"] for p in ow_paths}),
                    "pass" if not ow_paths else "fail")
            else:
                anyk, term = kinds_of(ow_paths, ow_nodes)
                a, t = check_kind(r["exp_owner_kind"], anyk, term)
                add(r, "owner_kind(any node)", r["exp_owner_kind"], sorted(anyk), "pass" if a else "fail")
                add(r, "owner_kind(terminal)", r["exp_owner_kind"], sorted(term), "pass" if t else "fail",
                    "informational")
        for role in ("minter", "pauser", "blacklister"):
            ea, ek = r[f"exp_{role}_addr"], r[f"exp_{role}_kind"]
            hs = role_holders(o, role) + (role_holders(o, "minter_admin") if role == "minter" else [])
            if ea:
                add(r, f"{role}_addr", ea.lower(), [h["address"] for h in hs],
                    "pass" if ea.lower() in [h["address"] for h in hs] else "fail")
            if ek:
                kinds = set()
                for h in hs:
                    kinds.add(GROUP.get(h.get("kind"), h.get("kind")))
                    for t in h.get("terminal_kinds", []):
                        kinds.add(GROUP.get(t, t))
                    for t in h.get("terminals", []):
                        kinds.add(GROUP.get(t["kind"], t["kind"]))
                add(r, f"{role}_kind(any)", ek, sorted(kinds), "pass" if EQUIV.get(ek, {ek}) & kinds else "fail")
        # independent event-log oracle (ethereum / arbitrum / polygon)
        if ch in LOGCHAINS:
            for name, (_, role) in EVENTS.items():
                st, who, path = last_event_holder(ch, tok, name)
                if st != "ok":
                    continue
                if role == "impl":
                    if o["is_proxy"] and o["proxy_type"] in ("eip1967", "zos_legacy", "eip1822_uups"):
                        add(r, "oracle:logs implementation (last Upgraded)", who, o["implementation"],
                            "pass" if who == o["implementation"] else "fail", path)
                elif role == "upgrade_admin":
                    cand = {n["address"] for n in o["upgrade_admin_chain"] if n["depth"] == 1}
                    if o["is_proxy"]:
                        add(r, "oracle:logs upgrade admin (last AdminChanged)", who, sorted(cand),
                            "pass" if who in cand else "fail", path)
                elif role == "owner":
                    cand = {n["address"] for n in o["owner_chain"] if n["depth"] == 1}
                    add(r, "oracle:logs owner (last OwnershipTransferred)", who, sorted(cand),
                        "pass" if who in cand else ("pass" if who in {n["address"] for n in o["owner_chain"]} else "fail"), path)
                else:
                    hs = [h["address"] for h in role_holders(o, role)] + (
                        [h["address"] for h in role_holders(o, "default_admin")] if False else [])
                    add(r, f"oracle:logs {role} (last event)", who, hs, "pass" if who in hs else "fail", path)
        # independent Etherscan oracle
        ef = G / "etherscan" / f"{ch}_{tok}.json"
        if ef.exists():
            e = json.loads(ef.read_text())["response"]
            res = e.get("result")
            if isinstance(res, list) and res and isinstance(res[0], dict):
                x = res[0]
                eproxy = x.get("Proxy") == "1"
                eimpl = (x.get("Implementation") or "").lower() or None
                note = f"Etherscan contract name: {x.get('ContractName')!r}"
                if eproxy:
                    ok = o["is_proxy"] is True
                    add(r, "oracle:etherscan is_proxy", True, o["is_proxy"], "pass" if ok else "fail", note)
                    if eimpl:
                        add(r, "oracle:etherscan implementation", eimpl, o["implementation"],
                            "pass" if eimpl == o["implementation"] else "fail", note)
                else:
                    ok = o["is_proxy"] is not True or not x.get("SourceCode")
                    add(r, "oracle:etherscan is_proxy", False, o["is_proxy"], "pass" if o["is_proxy"] is False else "disagree",
                        note + (" (Etherscan has no proxy flag; may be a heuristic miss)" if o["is_proxy"] else ""))
    exc = json.loads((G / "oracle_exceptions.json").read_text()) if (G / "oracle_exceptions.json").exists() else []
    for x in results:
        if x["status"] in ("fail", "disagree") and x["check"].startswith("oracle"):
            for e in exc:
                if e["chain"] == x["chain"] and e["token"].lower() == x["token"].lower() and x["check"].startswith(e["check_prefix"]):
                    x["status"], x["note"] = "oracle_stale", e["reason"]
    (G / "results.jsonl").write_text("\n".join(json.dumps(x) for x in results) + "\n")
    # ---- report ----------------------------------------------------------------------------------------------------
    by = {}
    for x in results:
        by.setdefault(x["status"], []).append(x)
    core = [x for x in results if "terminal" not in x["check"] and not x["check"].startswith("oracle")]
    cp = sum(1 for x in core if x["status"] == "pass")
    orc = [x for x in results if x["check"].startswith("oracle")]
    op = sum(1 for x in orc if x["status"] == "pass")
    tm = [x for x in results if "terminal" in x["check"]]
    tp = sum(1 for x in tm if x["status"] == "pass")
    stale = [x for x in results if x["status"] == "oracle_stale"]
    L = ["# W3 golden set: expected vs observed", "",
         f"Tokens in expected.csv: {len(rows)} ({len({r['chain'] for r in rows})} chains). "
         f"Observed pinned blocks: " + ", ".join(
             f"{p.parent.name}={json.loads(p.read_text())['block']}" for p in sorted((G / 'observed').glob('*/run.json'))),
         "", f"* Core checks (expected value sourced independently of the tool): **{cp}/{len(core)} pass**",
         f"* Terminal-kind checks (stricter, informational): {tp}/{len(tm)} pass",
         f"* Independent Etherscan oracle (proxy flag / implementation / event logs): {op}/{len(orc)} agree; {len(stale)} explained as oracle-stale (see below, `oracle_exceptions.json`), "
         f"{len(orc) - op - len(stale)} unexplained", "",
         "## Failures and disagreements", ""]
    bad = [x for x in results if x["status"] != "pass"]
    if not bad:
        L.append("none")
    for x in bad:
        L.append(f"* `{x['chain']}` {x['symbol']} `{x['token']}` **{x['check']}** expected `{x['expected']}` observed "
                 f"`{x['observed']}` ({x['status']}) {x['note']}")
    L += ["", "## All checks", "", "| chain | token | check | expected | observed | status |", "|---|---|---|---|---|---|"]
    for x in results:
        L.append(f"| {x['chain']} | {x['symbol']} | {x['check']} | {str(x['expected'])[:60]} | {str(x['observed'])[:80]} | {x['status']} |")
    (G / "report.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:12]))


if __name__ == "__main__":
    {"run": cmd_run, "etherscan": cmd_etherscan, "logs": cmd_logs, "compare": cmd_compare}[sys.argv[1]]()
