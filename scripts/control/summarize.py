#!/usr/bin/env python3
"""Summarise a control.jsonl run: upgradeability shares, terminal controller kinds, single-EOA lists.

    summarize.py data/control/ethereum/ [--universe data/universe/ethereum.csv] [--top 200] [--out summary.md]

Token-level classes use the WEAKEST path (any path that can change the token counts):
  upgrade class: eoa > eoa_7702 > multisig (safe/legacy, no timelock on path) > timelock(+multisig/governor) > governor/dao > contract_other > l1_alias > renounced
"""
import argparse
import collections
import csv
import json
import pathlib

SEV = ["eoa", "eoa_7702", "multisig_no_timelock", "timelock", "governor_dao", "contract_other_or_unresolved",
       "l1_alias", "renounced", "authority_unverified"]


def path_class(p):
    kinds = p["kinds"]
    term = p["terminal_kind"]
    has_tl = "timelock" in kinds or bool(p.get("access_manager_delay_seconds"))
    if term.startswith("truncated"):
        return "contract_other_or_unresolved"
    if term == "eoa":
        return "eoa" if not has_tl else "timelock"  # an EOA behind a timelock is still delayed
    if term == "eoa_7702":
        return "eoa_7702" if not has_tl else "timelock"
    if term in ("safe", "multisig_legacy"):
        return "timelock" if has_tl else "multisig_no_timelock"
    if has_tl:
        return "timelock"
    if term in ("governor", "dao_other"):
        return "governor_dao"
    if term == "renounced":
        return "renounced"
    if term == "l1_alias":
        return "l1_alias"
    return "contract_other_or_unresolved"


def weakest(paths, only_verified=True):
    """Weakest class over the paths. Paths whose authority is merely assumed/unverified (UUPS candidate that did not pass
    the eth_call upgrade simulation, or non-standard proxies) do not count unless nothing else exists."""
    if not paths:
        return None
    firm = [p for p in paths if not p.get("assumed_or_unverified")] if only_verified else paths
    if not firm:
        return "authority_unverified"
    cls = [path_class(p) for p in firm]
    return min(cls, key=lambda c: SEV.index(c))


def eoa_terminal(paths, include_timelocked=False):
    out = []
    for p in paths:
        if p.get("assumed_or_unverified"):
            continue
        if p["terminal_kind"] in ("eoa", "eoa_7702") and (include_timelocked or ("timelock" not in p["kinds"] and not p.get("access_manager_delay_seconds"))):
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--universe")
    ap.add_argument("--top", type=int)
    ap.add_argument("--out")
    a = ap.parse_args()
    d = pathlib.Path(a.dir)
    rows = [json.loads(l) for l in (d / "control.jsonl").read_text().splitlines()]
    revision = pathlib.Path(__file__).resolve().parents[2] / "data/control/c19/derived.jsonl"
    if d.resolve().parent == revision.parents[1] and revision.exists():
        overlay = {r["token"]: r for r in map(json.loads, revision.read_text().splitlines()) if r["chain"] == d.name}
        rows = [overlay.get(r["token"], r) if overlay.get(r["token"], r)["block"] == r["block"] else r for r in rows]
    uni = {}
    if a.universe:
        for r in csv.DictReader(open(a.universe)):
            uni[r["address"].lower()] = r
    if a.top and uni:
        rows = [r for r in rows if r["token"] in uni and int(uni[r["token"]]["rank"]) <= a.top]
    n = len(rows)
    run = json.loads((d / "run.json").read_text())
    L = [f"# Control summary: {d}", "", f"chain {rows[0]['chain'] if rows else '?'}, block {run['block']}, tokens {n}", ""]
    st = collections.Counter(f"{r['status']}/{r['completeness']}" for r in rows)
    L += ["status/completeness: " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())), ""]
    ok = [r for r in rows if r["status"] == "ok"]
    m = len(ok)

    def mc(r):
        try:
            return float(uni[r["token"]]["market_cap_usd"] or 0)
        except Exception:  # noqa: BLE001
            return 0.0

    tot_mc = sum(mc(r) for r in ok) or 1
    proxies = [r for r in ok if r["is_proxy"]]
    unk = [r for r in ok if r["is_proxy"] is None]
    L += [f"## Proxy / upgradeability (probed ok: {m})", "",
          f"* proxies: {len(proxies)} ({100 * len(proxies) / max(m, 1):.1f}%), unresolved delegatecall: {len(unk)}",
          "* proxy types: " + ", ".join(f"{k}={v}" for k, v in collections.Counter(r["proxy_type"] for r in proxies).most_common()),
          "* upgradeable verdict: " + ", ".join(f"{k}={v}" for k, v in collections.Counter(r["upgradeable"] for r in ok).most_common()),
          ""]
    up = [r for r in ok if r["upgradeable"] == "yes"]
    L += [f"### Upgrade authority class (weakest path), upgradeable=yes: {len(up)}", ""]
    cnt = collections.Counter(weakest(r["upgrade_paths"]) for r in up)
    L += ["| class | tokens | share of upgradeable | market cap share |", "|---|---|---|---|"]
    for c in SEV:
        if cnt.get(c):
            mcs = sum(mc(r) for r in up if weakest(r["upgrade_paths"]) == c)
            L.append(f"| {c} | {cnt[c]} | {100 * cnt[c] / max(len(up), 1):.0f}% | {100 * mcs / tot_mc:.1f}% of probed cap |")
    L += ["", "### Upgradeable behind a single EOA (no timelock on the path)", ""]
    for r in sorted(up, key=lambda r: int(uni[r["token"]]["rank"]) if r["token"] in uni else 9999):
        pe = eoa_terminal(r["upgrade_paths"])
        if pe:
            rk = uni.get(r["token"], {}).get("rank", "?")
            L.append(f"* rank {rk} **{r.get('symbol')}** `{r['token']}` ({r['proxy_type']}): "
                     + "; ".join(f"{' > '.join(p['addresses'])} ({p['terminal_kind']}, nonce {r['controllers'][p['terminal_address']]['details'].get('nonce')})" for p in pe)
                     + f"  mcap ${mc(r):,.0f}")
    L += ["", "### Upgrade authority: all paths per upgradeable token (top 40 by rank)", ""]
    for r in sorted(up, key=lambda r: int(uni[r["token"]]["rank"]) if r["token"] in uni else 9999)[:40]:
        L.append(f"* rank {uni.get(r['token'], {}).get('rank', '?')} {r.get('symbol')}: " +
                 " | ".join(f"{p['terminal_summary']}" + (f" +{p['timelock_delay_seconds']}s timelock" if p['timelock_delay_seconds'] else "") for p in r["upgrade_paths"]))
    L += ["", "## Owner / role control (all tokens)", ""]
    own = [r for r in ok if r["owner_paths"]]
    cnt = collections.Counter(weakest(r["owner_paths"]) for r in own)
    L += [f"* tokens with an owner/admin/wards path: {len(own)} ({100 * len(own) / max(m, 1):.0f}%); class of weakest path: " +
          ", ".join(f"{k}={v}" for k, v in cnt.most_common()),
          "* control_summary: " + ", ".join(f"{k}={v}" for k, v in collections.Counter(r.get("control_summary") for r in ok).most_common()),
          "* capability hints (selector present): " + ", ".join(
              f"{c}={sum(1 for r in ok if c in r['capability_hints'])}" for c in ("mint", "pause", "blacklist", "fee_or_tax_setter", "burn_from_others", "rescue_tokens", "upgrade")),
          ""]
    L += ["### Role holders that are single EOAs (minter / pauser / blacklister / owner)", ""]
    for r in ok:
        flags = []
        for role in ("minter", "minter_admin", "pauser", "blacklister", "bridge"):
            for h in r["roles"].get(role, []):
                if any(t in ("eoa", "eoa_7702") for t in h.get("terminal_kinds", [])):
                    flags.append(f"{role}={h['address'][:10]}")
        if eoa_terminal(r["owner_paths"]):
            flags.append("owner=" + ",".join(p["terminal_address"][:10] for p in eoa_terminal(r["owner_paths"])))
        if flags:
            L.append(f"* rank {uni.get(r['token'], {}).get('rank', '?')} {r.get('symbol')} `{r['token']}`: " + ", ".join(flags))
    if uni:
        import re
        sec_rx = re.compile(r"xstock|tokenized (stock|stocks|fund|treasury|equity)|ondo tokenized|\(ondo|backed|coinbase tokenized", re.I)

        def tags(r):
            u = uni.get(r["token"], {})
            t_ = set(filter(None, (u.get("category_hint") or "").split(";")))
            if sec_rx.search((u.get("name") or "") + " " + (u.get("symbol") or "")):
                t_.add("tokenized_security")
            return t_ or {"uncategorised"}
        L += ["", "## By category (universe `category_hint`; `tokenized_security` = name/symbol regex)", "",
              "| category | tokens | proxies | upgradeable=yes | upgrade class eoa/7702 | multisig no timelock | timelock | governor/dao | B20 | owner path EOA |", "|---|---|---|---|---|---|---|---|---|---|"]
        cats = collections.defaultdict(list)
        for r in ok:
            for t_ in tags(r):
                cats[t_].append(r)
        for cat, rs in sorted(cats.items(), key=lambda x: -len(x[1])):
            cl = collections.Counter(weakest(r["upgrade_paths"]) for r in rs if r["upgradeable"] == "yes")
            L.append(f"| {cat} | {len(rs)} | {sum(1 for r in rs if r['is_proxy'])} | {sum(1 for r in rs if r['upgradeable'] == 'yes')} | "
                     f"{cl.get('eoa', 0) + cl.get('eoa_7702', 0)} | {cl.get('multisig_no_timelock', 0)} | {cl.get('timelock', 0)} | {cl.get('governor_dao', 0)} | "
                     f"{sum(1 for r in rs if r.get('proxy_type') == 'b20_precompile')} | {sum(1 for r in rs if eoa_terminal(r['owner_paths']))} |")
    b20 = [r for r in ok if r.get("proxy_type") == "b20_precompile"]
    if b20:
        L += ["", f"## Base B20 precompile tokens (code == 0xef): {len(b20)}", ""]
        for r in b20:
            L.append(f"* {r.get('symbol')} `{r['token']}` variant={r['b20']['variant']} supply_cap={r['b20']['supply_cap']} policies={r['b20']['policies']} roles_status={r['access_control']['status'][:60]}")
    L += ["", "## Renounced / frozen", ""]
    for r in ok:
        if r["upgradeable"] == "frozen":
            L.append(f"* upgrade frozen: {r.get('symbol')} `{r['token']}`")
    L.append(f"* owner path ends renounced: {sum(1 for r in own if weakest(r['owner_paths']) == 'renounced')} tokens")
    L += ["", "## Partial / error rows", ""]
    for r in rows:
        if r["status"] != "ok" or r["completeness"] != "complete":
            L.append(f"* {r.get('symbol')} `{r['token']}` {r['status']}/{r['completeness']}: " + "; ".join(e["msg"][:60] for e in r["probe_errors"][:2]) + (r.get("reason") or ""))
    text = "\n".join(L) + "\n"
    (pathlib.Path(a.out) if a.out else d / "summary.md").write_text(text)
    print(text[:6000])


if __name__ == "__main__":
    main()
