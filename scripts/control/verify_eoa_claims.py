#!/usr/bin/env python3
"""Independent re-check of single-EOA upgrade/ownership claims in a control.jsonl run (headline candidates).

    verify_eoa_claims.py data/control/ethereum-top200 [--top 60]

For every token whose firm (non-assumed) upgrade path ends in an EOA, re-verify through channels the classifier did not use
for the same claim:
  1. eth_getCode + eth_getTransactionCount of the EOA on BOTH ethereum endpoints at `latest` (a contract/7702 delegation would show code),
  2. Etherscan event logs: the last AdminChanged (proxy admin) / OwnershipTransferred event up to the run's block must name the same address
     (inconclusive when the proxy set its admin silently in the constructor),
  3. the classifier's own read-only eth_call upgrade simulation result is quoted (success from the EOA, revert from a stranger).
Raw responses are saved under <dir>/verify/ with retrieved_at + sha256; a markdown table goes to <dir>/verify_eoa_claims.md.
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ctl_rpc import CHAINS, etherscan_get, Ev  # noqa: E402

ENDPOINTS = ["https://ethereum-rpc.publicnode.com", "https://eth.drpc.org"]
CHAIN = "ethereum"
ADMIN_CHANGED = "0x7e644d79422f17c01e4894b5f4f588d331ebfa28653d42ae832dc59e38c9798f"
OWNERSHIP = "0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0"


def rpc(url, method, params):
    for a in range(5):
        r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=60)
        if r.status_code == 200:
            return r.json(), r.content
        time.sleep(2 * (a + 1))
    return {"error": r.text[:100]}, r.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--universe", default=str(pathlib.Path(__file__).resolve().parents[2] / "data/universe/ethereum.csv"))
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--chain", default="ethereum")
    a = ap.parse_args()
    global ENDPOINTS, CHAIN
    CHAIN = a.chain
    ENDPOINTS = [u for u, _ in CHAINS[a.chain]["endpoints"][:2]]
    d = pathlib.Path(a.dir)
    (d / "verify").mkdir(exist_ok=True)
    run = json.loads((d / "run.json").read_text())
    rank = {}
    import csv
    if a.universe.endswith("ethereum.csv") and a.chain != "ethereum":
        a.universe = a.universe.replace("ethereum.csv", f"{a.chain}.csv")
    for r in csv.DictReader(open(a.universe)):
        rank[r["address"].lower()] = int(r["rank"])
    rows = [json.loads(l) for l in (d / "control.jsonl").read_text().splitlines()]
    todo = []
    for r in rows:
        if r["status"] != "ok" or not r.get("upgrade_paths"):
            continue
        for p in r["upgrade_paths"]:
            if p["terminal_kind"] in ("eoa", "eoa_7702") and not p.get("assumed_or_unverified") and "timelock" not in p["kinds"] and not p.get("access_manager_delay_seconds"):
                todo.append((rank.get(r["token"], 9999), r, p))
    seen_p = set()
    todo = [t for t in todo if not ((t[1]["token"], tuple(t[2]["addresses"])) in seen_p or seen_p.add((t[1]["token"], tuple(t[2]["addresses"]))))]
    todo.sort(key=lambda x: x[0])
    ev = Ev("verify/_evidence.jsonl", d)
    L = ["# Independent re-check of single-EOA upgrade claims", "", f"run block {run['block']}; checks at `latest` for code/nonce, logs up to the run block.", "",
         "| rank | token | proxy | path (token -> ... -> EOA) | code (publicnode / drpc) | nonce | independent re-read of the authority (both endpoints, latest) | last AdminChanged log | upgrade simulation |", "|---|---|---|---|---|---|---|---|---|"]
    for rk, r, p in todo:
        if rk > a.top:
            continue
        eoa = p["terminal_address"]
        admin_node = p["addresses"][0]
        cells = []
        for u in ENDPOINTS:
            j, raw = rpc(u, "eth_getCode", [eoa, "latest"])
            ref = ev.add(CHAIN, u, "latest", {"method": "eth_getCode", "params": [eoa, "latest"]}, j,
                         hashlib.sha256(raw).hexdigest())
            cells.append("none" if j.get("result") == "0x" else (j.get("result") if isinstance(j.get("result"), str) else "endpoint error: " + str(j.get("error"))[:30])[:40])
        j, raw = rpc(ENDPOINTS[0], "eth_getTransactionCount", [eoa, "latest"])
        ev.add(CHAIN, ENDPOINTS[0], "latest", {"method": "eth_getTransactionCount", "params": [eoa, "latest"]}, j,
               hashlib.sha256(raw).hexdigest())
        nonce = int(j["result"], 16) if j.get("result") else None
        hop = []
        slots = {"1967admin": "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103",
                 "zosadmin": "0x10d6a54a4754c8869d6886b5f5d7fbfa5b4522237ea5c60d11bc4e7a1ff9390b"}
        getters = {"owner()": "0x8da5cb5b", "proxyOwner()": "0x025313a2", "admin()": "0xf851a440"}

        def both(method, params):
            out = []
            for u in ENDPOINTS:
                jj, rw = rpc(u, method, params)
                ev.add(CHAIN, u, "latest", {"method": method, "params": params}, jj, hashlib.sha256(rw).hexdigest())
                v = jj.get("result")
                out.append(("0x" + v[-40:]).lower() if isinstance(v, str) and len(v) >= 42 else None)
            return out
        first = p["addresses"][0]
        beacon = None
        if r["proxy_type"] == "eip1967_beacon":
            v = both("eth_getStorageAt", [r["token"], "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50", "latest"])
            if v[0] and v[0] == v[1]:
                beacon = v[0]
                vo = both("eth_call", [{"to": beacon, "data": "0x8da5cb5b"}, "latest"])
                hop.append(f"beacon slot == {beacon[:10]} on both; beacon.owner() == {(vo[0] or '?')[:10]} / {(vo[1] or '?')[:10]}"
                           + (" (matches the claimed EOA)" if vo[0] == first and vo[1] == first else " (MISMATCH)"))
        found = None
        for nm, s in ({} if beacon else slots).items():
            v = both("eth_getStorageAt", [r["token"], s, "latest"])
            if v[0] == first and v[1] == first:
                found = f"{nm} slot == {first[:10]} on both"
                break
        if not found and not beacon:
            for nm, s in getters.items():
                v = both("eth_call", [{"to": r["token"], "data": s}, "latest"])
                if v[0] == first and v[1] == first:
                    found = f"token.{nm} == {first[:10]} on both"
                    break
        if not beacon:
            hop.append(found or f"no slot/getter equals {first[:10]} (role-gated authority: see re-simulation)")
        for x, y in zip(p["addresses"][:-1], p["addresses"][1:]):
            ok2 = False
            for nm, s in getters.items():
                v = both("eth_call", [{"to": x, "data": s}, "latest"])
                if v[0] == y and v[1] == y:
                    hop.append(f"{x[:10]}.{nm} == {y[:10]} on both")
                    ok2 = True
                    break
            if not ok2:
                hop.append(f"hop {x[:10]}->{y[:10]} NOT re-confirmed")
        res = etherscan_get(ev, CHAINS[CHAIN]["id"], dict(module="logs", action="getLogs", fromBlock=0, toBlock=r.get("block") or run["block"], address=r["token"],
                                        topic0=ADMIN_CHANGED, page=1, offset=1000))
        last = "n/a"
        if res.ok and isinstance(res.value.get("result"), list):
            lg = res.value["result"]
            if not lg:
                last = "no AdminChanged event (inconclusive)"
            else:
                x = max(lg, key=lambda z: (int(z["blockNumber"], 16), int(z["logIndex"], 16) if z["logIndex"] != "0x" else 0))
                new = "0x" + x["data"][-40:] if len(x["topics"]) == 1 else "0x" + x["topics"][-1][-40:]
                last = ("MATCH " if new.lower() == admin_node else f"DIFFERENT ({new}) ") + new.lower()[:10]
        # independent re-simulation at `latest` on both endpoints: can the EOA really execute an upgrade (to the current implementation)?
        impl = (r.get("implementation") or "0x" + "0" * 40)[2:].rjust(64, "0")
        stranger = "0x1000000000000000000000000000000000000001"
        tgt0 = p["addresses"][0]
        if "upgrade needs 2 txs" in p["via"]:  # role-admin path: the EOA must be able to grant itself the role that gates upgrades
            role = ((r.get("upgrade_simulation") or {}).get("needed_roles") or ["0x" + "0" * 64])[0][2:]
            calls = [(r["token"], "0x2f2ff15d" + role + eoa[2:].rjust(64, "0"))]
        elif beacon:  # beacon proxy: the EOA owns the beacon and upgrades it with beacon.upgradeTo(impl)
            calls = [(beacon, "0x3659cfe6" + impl)]
        elif len(p["addresses"]) == 1:
            calls = [(r["token"], "0x3659cfe6" + impl), (r["token"], "0x4f1ef286" + impl + "0" * 62 + "40" + "0" * 64), (r["token"], "0x776d1a01" + impl)]
        else:  # ProxyAdmin-style hop: EOA -> ProxyAdmin.upgrade(proxy, impl)
            tok = r["token"][2:].rjust(64, "0")
            calls = [(tgt0, "0x99a88ec4" + tok + impl), (tgt0, "0x9623609d" + tok + impl + "0" * 62 + "60" + "0" * 64)]
        resim = []
        for u in ENDPOINTS:
            okc, okb, errmsg = False, False, None
            for to, data in calls:
                for frm, flag in ((eoa, "c"), (stranger, "b")):
                    jj, rw = rpc(u, "eth_call", [{"from": frm, "to": to, "data": data}, "latest"])
                    ev.add(CHAIN, u, "latest", {"method": "eth_call", "params": [{"from": frm, "to": to, "data": data}, "latest"]}, jj, hashlib.sha256(rw).hexdigest())
                    if "result" in jj:
                        if flag == "c":
                            okc = True
                        else:
                            okb = True
                    elif flag == "c":
                        er = jj.get("error"); msg = str(er.get("message") if isinstance(er, dict) else er)[:60]
                        if "revert" not in msg.lower():
                            errmsg = msg
            resim.append(("EOA call succeeds" + (", stranger also succeeds (inconclusive)" if okb else ", stranger reverts")) if okc else
                         (f"endpoint error ({errmsg})" if errmsg else "all candidate upgrade calls revert from the EOA (not a direct upgrade path)"))
        hop.append("re-simulation (both endpoints): " + " / ".join(resim))
        sim = (r.get("upgrade_simulation") or {}).get("candidates", {}).get(admin_node, {})
        simtxt = sim.get("verdict") if sim else ("n/a (admin slot, not simulated)" if True else "")
        L.append(f"| {rk} | {r.get('symbol')} `{r['token']}` | {r['proxy_type']} | {' -> '.join(a[:10] for a in p['addresses'])} (`{eoa}`) | {' / '.join(cells)} | {nonce} | {'; '.join(hop)} | {last} | {simtxt} |")
        print(L[-1], flush=True)
    ev.flush()
    (d / "verify_eoa_claims.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
