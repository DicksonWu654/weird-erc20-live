#!/usr/bin/env python3
"""Build the Weird ERC-20 Live launch site (site-v5) into dist/.

Every number on the page comes from a binding token in src/index.html:

  {{L:key}}                       article/numbers.json value (integers get thousands separators)
  {{S:/path}}                     numbers.json -> site_counts value
  {{T:key|row=a,b|col=Name|part=x}} cell of a markdown table stored in numbers.json
  {{P:text}}                      definition constant quoted verbatim from article/paper.md
  {{M:chain|token|column|field}}  per-token matrix cell (site/dist/data/matrix-<chain>.json)

Each binding renders as an element carrying data-b="<binding>" so tools/check_numbers.py can
re-resolve it independently and compare. Nothing numeric is typed by hand.
"""
import csv
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
SRC = SITE / "src"
DIST = SITE / "dist"
LEDGER_PATH = ROOT / "article" / "numbers.json"
PAPER_MD = ROOT / "article" / "paper.md"
DATA = ROOT / "data" / "site"
SITE_URL = "https://weird-erc20-live.shadow654.chatgpt.site/"
OG_IMAGE = SITE_URL + "og.png"   # absolute, as social crawlers need; the file itself is dist/og.png
OG_FILE = "og.png"
REPO_URL = "https://github.com/DicksonWu654/weird-erc20-live"
CHAINS = ["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]
CHAIN_NAMES = {"ethereum": "Ethereum", "base": "Base", "arbitrum": "Arbitrum", "bsc": "BNB Chain",
               "polygon": "Polygon", "optimism": "Optimism"}
CHAIN_SHORT = {"ethereum": "ETH", "base": "Base", "arbitrum": "Arb", "bsc": "BNB", "polygon": "Poly", "optimism": "OP"}

LEDGER = json.loads(LEDGER_PATH.read_text())


def load_phrases():
    out = {}
    for line in (SRC / "phrases.tsv").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        col, raw, phrase = line.split("\t")
        out[(col, raw)] = phrase
    return out


PHRASES = load_phrases()
PAPER = PAPER_MD.read_text()
_MATRIX = {}
USED_KEYS = {}          # ledger key -> True (for the ledger appendix)
USED_S = set()          # site_counts paths bound on the page
EVIDENCE = {}           # "chain|token|col" -> embedded evidence record


def esc(s):
    return html.escape(str(s), quote=True)


def fmt_int(s):
    s = str(s)
    if re.fullmatch(r"-?\d+", s):
        return f"{int(s):,}"
    return s


def matrix(chain):
    if chain not in _MATRIX:
        d = json.loads((DATA / f"matrix-{chain}.json").read_text())
        d["_cols"] = {c["id"]: i for i, c in enumerate(d["columns"])}
        d["_rows"] = {r["token"]: r for r in d["rows"]}
        _MATRIX[chain] = d
    return _MATRIX[chain]


def parse_table(md):
    lines = [l for l in md.strip().splitlines() if l.strip().startswith("|")]
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines]
    header, body = rows[0], [r for r in rows[2:]]
    return header, body


PART_RE = {
    "num": r"^([\d,]+)/",
    "den": r"^[\d,]+/([\d,]+)",
    "pct": r"\(([\d.]+%)\)",
    "point": r"^([\d.,]+%?)",
    "lo": r"\[([\d.,]+%?),",
    "hi": r",\s*([\d.,]+%?)\]",
    "s1": r"^(\d+)/\d+/\d+/\d+$",
    "s2": r"^\d+/(\d+)/\d+/\d+$",
    "s3": r"^\d+/\d+/(\d+)/\d+$",
    "s4": r"^\d+/\d+/\d+/(\d+)$",
}


def table_cell(key, row, col, part=None):
    header, body = parse_table(LEDGER[key]["value"])
    want = [x.strip() for x in row.split(",")]
    ci = header.index(col)
    hits = [r for r in body if r[: len(want)] == want]
    if len(hits) != 1:
        raise SystemExit(f"table {key}: row {row!r} matched {len(hits)} rows")
    cell = hits[0][ci]
    if part:
        m = re.search(PART_RE[part], cell)
        if not m:
            raise SystemExit(f"table {key} {row}/{col}: part {part} not in {cell!r}")
        cell = m.group(1)
    return cell


def universe_count(chain, source):
    with (ROOT / "data" / "universe" / f"{chain}.csv").open() as f:
        return sum(1 for r in csv.DictReader(f) if r["source"] == source)


_UNIV = {}


def universe_rows(chain):
    if chain not in _UNIV:
        with (ROOT / "data" / "universe" / f"{chain}.csv").open() as f:
            _UNIV[chain] = list(csv.DictReader(f))
    return _UNIV[chain]


def universe_row(chain, token):
    hits = [r for r in universe_rows(chain) if r["address"].lower() == token.lower()]
    if len(hits) != 1:
        raise SystemExit(f"universe {chain}: {token} matched {len(hits)} rows")
    return hits[0]


def cell_evidence(chain, token, col):
    d = matrix(chain)
    r = d["_rows"][token]
    i = d["_cols"][col]
    c = r["cells"][i]
    meta = d["columns"][i]
    ev = r["evidence"].get(meta["src"]) or {}
    return r, meta, c, ev


def register_evidence(chain, token, col):
    r, meta, c, ev = cell_evidence(chain, token, col)
    key = f"{chain}|{token}|{col}"
    EVIDENCE[key] = {
        "chain": chain, "token": token, "symbol": r["symbol"], "name": r["name"],
        "rank_band": r["rank_band"], "column": meta, "cell": c,
        "evidence": ev, "forward": r["evidence"].get("forward"),
    }
    return key


def ranked_upgrade_counts():
    """Ranked (not extras) deployments by upgrade_authority_class, all six chains, from the matrix files."""
    if "_up" not in _MATRIX:
        out = {}
        for ch in CHAINS:
            d = matrix(ch)
            ci = d["_cols"]["upgrade_authority_class"]
            for r in d["rows"]:
                if r["rank_band"] != "extras":
                    c = r["cells"][ci][1]
                    out[c] = out.get(c, 0) + 1
        _MATRIX["_up"] = out
    return _MATRIX["_up"]


def ranked_upgradeable():
    """Ranked deployments whose upgradeable column is answered (measured) and answered yes, from the matrix files."""
    if "_ug" not in _MATRIX:
        out = {"yes": 0, "measured": 0}
        for ch in CHAINS:
            d = matrix(ch)
            cu = d["_cols"]["upgradeable"]
            for r in d["rows"]:
                if r["rank_band"] != "extras" and r["cells"][cu][0] in ("quirk", "pass"):
                    out["measured"] += 1
                    out["yes"] += r["cells"][cu][0] == "quirk"
        _MATRIX["_ug"] = out
    return _MATRIX["_ug"]


def tokenized_counts():
    """Ranked deployments carrying the matrix tag tokenized_security, and how many of them can be upgraded / have a blocklist."""
    if "_ts" not in _MATRIX:
        out = {"total": 0, "upgradeable": 0, "blocklist": 0}
        for ch in CHAINS:
            d = matrix(ch)
            cu, cb = d["_cols"]["upgradeable"], d["_cols"]["blocklist_any"]
            for r in d["rows"]:
                if r["rank_band"] != "extras" and "tokenized_security" in r["categories"]:
                    out["total"] += 1
                    out["upgradeable"] += r["cells"][cu][0] == "quirk"
                    out["blocklist"] += r["cells"][cb][0] == "quirk"
        _MATRIX["_ts"] = out
    return _MATRIX["_ts"]


def resolve(b):
    """Return display text for a binding string."""
    kind, _, rest = b.partition(":")
    if kind == "L":
        key, _, fmt = rest.partition("|")
        USED_KEYS[key] = True
        v = LEDGER[key]["value"]
        if fmt in ("num", "den"):
            return re.search(PART_RE[fmt], str(v)).group(1)
        return fmt_int(v) if fmt != "raw" else str(v)
    if kind == "S":
        USED_KEYS["site_counts"] = True
        USED_S.add(rest)
        return fmt_int(LEDGER["site_counts"]["value"][rest])
    if kind == "T":
        parts = rest.split("|")
        key = parts[0]
        kw = dict(p.split("=", 1) for p in parts[1:])
        USED_KEYS[key] = True
        v = table_cell(key, kw["row"], kw["col"], kw.get("part"))
        return fmt_int(v) if re.fullmatch(r"\d{5,}", v) else v
    if kind == "H":
        key, i = rest.split("|")
        USED_KEYS[key] = True
        return parse_table(LEDGER[key]["value"])[0][int(i)].replace("–", "-")   # plain hyphen in ranges
    if kind == "Y":
        header, body = parse_table(LEDGER["v2_annual_table"]["value"])
        USED_KEYS["v2_annual_table"] = True
        if not any(r[0].split(" ")[0] == rest for r in body):
            raise SystemExit(f"year {rest} not a row of v2_annual_table")
        return rest
    if kind == "P":
        if rest not in PAPER:
            raise SystemExit(f"paper constant not found verbatim in paper.md: {rest!r}")
        return rest
    if kind == "W":      # whole-token units: 10**decimals, decimals read from a matrix cell
        return f"{10 ** int(resolve(rest)):,}"
    if kind == "U":      # 10**A units shown on a token with B decimals, as an exact plain decimal
        a, c = (int(resolve(x)) for x in rest.split(";"))
        return f"{10 ** (a - c):,}" if a >= c else "0." + "0" * (c - a - 1) + "1"
    if kind == "C":      # incident date cited in the paper intro: month plus bolded year
        month, year = rest.split()
        if not re.search(rf"{month}\s+\*\*{year}\*\*", PAPER):
            raise SystemExit(f"incident date {rest!r} not found in paper.md intro")
        return rest
    if kind == "E":      # quoted evidence phrase: must appear in src/examples.csv, row <id>, tweet field
        eid, _, text = rest.partition("|")
        rows = list(csv.DictReader((SITE / "src" / "examples.csv").open()))
        row = [r for r in rows if r["id"] == eid]
        if len(row) != 1 or text not in row[0]["tweet"]:
            raise SystemExit(f"{text!r} not in examples.csv row {eid}")
        return text
    if kind == "D":      # difference of two bound integers
        op, _, args = rest.partition("|")
        a, c = args.split(";")
        if op not in ("sub", "add"):
            raise SystemExit(f"unknown derived op {op}")
        x, y = int(resolve(a).replace(",", "")), int(resolve(c).replace(",", ""))
        return str(x - y if op == "sub" else x + y)
    if kind == "V":      # universe rows whose inclusion source is X; several chains must agree on one count
        chains, _, source = rest.partition("|")
        counts = {universe_count(ch, source) for ch in chains.split(",")}
        if len(counts) != 1:
            raise SystemExit(f"universe counts differ across {chains}: {counts}")
        return fmt_int(str(counts.pop()))
    if kind == "K":      # code facts read straight from data/universe/<chain>.csv
        what, _, args = rest.partition("|")
        if what == "distinct":
            hashes = {r["code_keccak"] for ch in CHAINS for r in universe_rows(ch) if r["same_asset_group"] == args}
            return str(len(hashes))
        chain, token = args.split("|")
        row = universe_row(chain, token)
        same = [r for r in universe_rows(chain) if r["code_keccak"] == row["code_keccak"]]
        if what == "size":
            return fmt_int(row["code_size"])
        if what == "count":
            return fmt_int(str(len(same)))
        if what == "others":
            return fmt_int(str(len(same) - 1))
        if what == "bridged":   # other tokens sharing this exact code that the matrix marks as bridge or peg issues
            rel = {r["token"]: r["deployment_relationship"] for r in matrix(chain)["rows"]}
            return fmt_int(str(sum(1 for r in same if r["address"].lower() != token.lower()
                                   and rel.get(r["address"].lower()) == "third_party_bridge_or_peg")))
        raise SystemExit(f"unknown K field {what}")
    if kind == "N":      # ranked deployments in the given upgrade-authority classes, from the matrix files
        USED_KEYS["main_upgrade_class_counts"] = True
        if rest.startswith("upgradeable."):
            return fmt_int(str(ranked_upgradeable()[rest.split(".")[1]]))
        counts = ranked_upgrade_counts()
        return fmt_int(str(sum(counts[c] for c in rest.split("+"))))
    if kind == "B":      # tokenized stocks and funds among the ranked deployments, from the matrix tag
        USED_KEYS["tokenized_security_counts"] = True
        return fmt_int(str(tokenized_counts()[rest]))
    if kind == "G":      # 2**a base units shown in whole tokens at b decimals, in billions
        a, c = (int(x) for x in rest.split("|"))
        return f"{round(2 ** a / 10 ** c / 1e9)} billion"
    if kind == "M":
        chain, token, col, field = rest.split("|")
        r, meta, c, ev = cell_evidence(chain, token, col)
        register_evidence(chain, token, col)
        if field == "decoded":
            return c[1]
        if field == "status":
            return c[0]
        if field == "block":
            return fmt_int(ev["block"])
        if field == "call":
            return c[2]
        if field == "sha":
            return ev["sha256"]
        if field == "callsha":
            first = str(c[2]).split(",")[0]
            return (ev.get("call_hashes") or {}).get(first) or ev["sha256"]
        if field == "name":
            return r["name"]
        if field == "symbol":
            return r["symbol"]
        if field == "rank":
            return r["rank_band"]
        raise SystemExit(f"unknown matrix field {field}")
    raise SystemExit(f"unknown binding {b}")


def bound(b, cls="n", tag=None, extra=""):
    """HTML element for a binding. Ledger values link to their ledger entry; matrix values open evidence."""
    v = resolve(b)
    kind = b.split(":", 1)[0]
    if tag == "span" or kind in ("P", "W", "U", "C", "E", "D", "V", "K", "G"):
        if kind == "E" and "^" in v:    # a power rendered as a superscript; data-v keeps the checked text
            base, _, exp = v.partition("^")
            return (f'<span class="{cls}" data-b="{esc(b)}" data-v="{esc(v)}"{extra}>{esc(base)}<sup>{esc(exp)}</sup></span>')
        return f'<span class="{cls}" data-b="{esc(b)}"{extra}>{esc(v)}</span>'
    if kind == "M":
        chain, token, col, _ = b[2:].split("|")
        return (f'<a class="{cls} m" href="#explore" data-b="{esc(b)}" '
                f'data-ev="{esc(chain + "|" + token + "|" + col)}"{extra}>{esc(v)}</a>')
    key = {"L": None, "T": None, "H": None, "S": "site_counts", "Y": "v2_annual_table",
           "N": "main_upgrade_class_counts", "B": "tokenized_security_counts"}[kind] or b[2:].split("|")[0]
    return f'<a class="{cls}" href="#src-{esc(key)}" data-b="{esc(b)}"{extra}>{esc(v)}</a>'


def svg_text(b, x, y, cls="", anchor="start", extra=""):
    v = resolve(b)
    return (f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}" data-b="{esc(b)}"{extra}>'
            f"{esc(v)}</text>")


def num(s):
    return float(str(s).replace(",", "").rstrip("%"))


# --------------------------------------------------------------------------------------------
# Generated blocks
# --------------------------------------------------------------------------------------------

SPECIMENS = [
    # chain, token, common name, binomial, mark (code), columns shown (first is the field mark), what breaks
    ("ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7", "The silent transfer", "Transferens tacitus",
     "transfer(to, amount)", "returns nothing at all",
     [("ret_missing_transfer", "transfer() result"), ("approve_race_guard", "approve() twice")],
     "Apps that check for true reject it."),
    ("ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "The gatekeeper", "Custos portae",
     "blacklist(address)", "a blocklist built into the token",
     [("blocklist_capability", "blocklist functions"), ("sanction_block_ronin", "send to tested Ronin-exploit address")],
     "A transfer to a blocked address fails, so an app paying out to one gets a revert."),
    ("ethereum", "0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "The drifting balance", "Saldo fluens",
     "balanceOf(holder)", "changes while nobody transacts",
     [("balance_drift_7d", "same storage, a week later"), ("transfer_rounding", "transfer amount")],
     "An app that records a balance and checks it later finds a different number."),
    ("ethereum", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", "The 96-bit ceiling", "Limes angustus",
     "transfer(to, huge)", "reverts above a uint96",
     [("uint96_limit", "large transfer"), ("approve_max_capped", "approve(max)")],
     "Code that approves the maximum and expects unlimited spending hits a cap."),
    ("ethereum", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "The self-spender", "Sibi ipsi",
     "transferFrom(me, to, x)", "needs no allowance from yourself",
     [("tf_owner_no_allowance", "transferFrom own tokens")],
     "An app that treats an approval as the gate for moving tokens finds no gate for the owner."),
    ("ethereum", "0xb8c77482e45f1f44de1745f52c74426c631bdd52", "The zero refuser", "Nihil recusans",
     "transfer(to, zero)", "reverts on a zero amount",
     [("transfer_zero_reverts", "transfer of zero"), ("ret_missing_transfer", "transfer() result")],
     "A payout batch that includes a zero amount fails as a whole."),
    ("ethereum", "0x6982508145454ce325ddbe47a25d4ec3d2311933", "The renounced blocklist", "Index sine domino",
     "blacklist(address, bool)", "present, owner renounced",
     [("blocklist_capability", "blocklist functions"), ("owner_renounced", "owner path")],
     "A code scan finds a blocklist. The owner role is renounced, so check who else, if anyone, can use it."),
    ("ethereum", "0x7712c34205737192402172409a8f7ccef8aa2aec", "The members-only token", "Clausus sodalibus",
     "transfer(stranger, x)", "reverts unless the receiver is approved",
     [("permissioned_transfer", "transfer to a fresh account"), ("upgrade_single_eoa", "upgrade path ends at")],
     "Sending to an ordinary wallet is rejected, so any app that routes it through a new address fails."),
    ("ethereum", "0x5a98fcbea516cf06857215779fd812ca3bef1b32", "The polite failure", "Errans comis",
     "transfer(to, too_much)", "returns false instead of reverting",
     [("false_on_failure", "failed transfers"), ("approve_race_guard", "approve() twice")],
     "An app that skips the result carries on as if the money moved."),
    ("ethereum", "0xa2b4c0af19cc16a6cfacce81f192b024d625817d", "The skimmer", "Decima vectigalis",
     "transfer(to, x)", "the receiver gets less than x",
     [("fee_on_transfer_observed", "transfer"), ("balance_drift_7d", "same storage, a week later")],
     "An app that books the full amount ends up holding less than it recorded."),
    ("base", "0xd5fac3ea6c5d1133be9325a3d463c987851cf7e2", "The phantom balance", "Saldo spectralis",
     "balanceOf(brand_new_address)", "is never zero",
     [("fresh_address_nonzero_balance", "unused address")],
     "An app that treats a nonzero balance as a funded, existing user is fooled."),
]


def short(h, n=8):
    return f"{h[:n]}…{h[-4:]}" if h and len(h) > n + 4 else (h or "")


def evidence_phrase(chain, token, col):
    """Inline highlighted phrase for a saved call; opens the evidence drawer like a specimen card value."""
    b = f"M:{chain}|{token}|{col}|decoded"
    raw = resolve(b)
    if (col, raw) not in PHRASES:
        raise SystemExit(f"no card phrase in src/phrases.tsv for {col} = {raw!r}")
    key = register_evidence(chain, token, col)
    return (f'<a class="n m dec" href="#explore" data-b="{esc(b)}" data-v="{esc(raw)}" data-ev="{esc(key)}" '
            f'title="Raw decoded value: {esc(raw)}">{esc(PHRASES[(col, raw)])}</a>')


def evidence_link(chain, token, col, label="saved call"):
    """Trailing '(saved call)' link: opens the same evidence drawer. A hidden span keeps the decoded phrase bound and checked."""
    b = f"M:{chain}|{token}|{col}|decoded"
    raw = resolve(b)
    if (col, raw) not in PHRASES:
        raise SystemExit(f"no card phrase in src/phrases.tsv for {col} = {raw!r}")
    key = register_evidence(chain, token, col)
    return (f'<span class="vh" aria-hidden="true" data-b="{esc(b)}" data-v="{esc(raw)}">{esc(PHRASES[(col, raw)])}</span>'
            f'<a class="evlink" href="#explore" data-ev="{esc(key)}" title="Saved value: {esc(raw)}">({esc(label)})</a>')


def hex_texture():
    """Background texture: real sha256 digests of saved evidence from the specimen rows."""
    digests = []
    for chain, token, *_ in SPECIMENS:
        r = matrix(chain)["_rows"][token]
        for src in ("probe", "control"):
            ev = r["evidence"].get(src) or {}
            digests += list((ev.get("call_hashes") or {}).values())
            if ev.get("sha256"):
                digests.append(ev["sha256"])
    seen, uniq = set(), []
    for d in digests:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return "\n".join(uniq[:60])


CROSS = [
    # (label, group id, column, plain sentence)
    ("PYUSD", "paypal-usd", "sanction_block_tornado", "refuses the Tornado Cash router"),
    ("UNI", "uniswap", "approve_max_capped", "caps approve(max) at a uint96"),
    ("PEPE", "pepe", "blocklist_capability", "has blocklist functions"),
    ("USDe", "ethena-usde", "permit_any", "accepts gasless permit() approvals"),
    ("LINK", "chainlink", "to_token_contract_reverts", "rejects sends to its own address"),
    ("WBTC", "wrapped-bitcoin", "approve_zero_addr_reverts", "rejects approve(0x0), as modern code does; the Ethereum original does not"),
]


def family_rows(group):
    """First deployment of an asset group on each chain, preferring ranked rows over extras."""
    found = {}
    for ch in CHAINS:
        rows = [r for r in matrix(ch)["rows"] if r.get("group") == group]
        rows.sort(key=lambda r: r["rank_band"] == "extras")
        if rows:
            found[ch] = rows[0]
    return found


def xbutton(ch, token, col, label, word_for=None):
    st = cell_evidence(ch, token, col)[2][0]
    key = register_evidence(ch, token, col)
    word = {"quirk": "yes", "pass": "no", "unprobeable": "unknown", "meta": "value"}[st]
    shown = (word_for or {}).get(st, word)
    return (f'<button type="button" class="xbtn s-{st}" data-ev="{esc(key)}" '
            f'data-b="{esc(f"M:{ch}|{token}|{col}|status")}" data-v="{st}" '
            f'aria-label="{esc(label)} on {esc(CHAIN_NAMES[ch])}: {esc(shown)}"><span class="glyph" aria-hidden="true"></span>'
            f'<span class="xw">{esc(word)}</span></button>'), st


def gen_crosschain(only=None):
    head = "".join(f'<th scope="col"><span class="long">{esc(CHAIN_NAMES[c])}</span><abbr class="short" title="{esc(CHAIN_NAMES[c])}">{esc(CHAIN_SHORT[c])}</abbr></th>' for c in CHAINS)
    body = []
    for label, group, col, sentence in CROSS:
        if only and label not in only:
            continue
        fam = family_rows(group)
        cells = []
        for ch in CHAINS:
            r = fam.get(ch)
            if not r:
                cells.append('<td class="xc none"><span class="vh">not in the tested set</span></td>')
                continue
            btn, _ = xbutton(ch, r["token"], col, label)
            cells.append(f'<td class="xc">{btn}</td>')
        body.append(f'<tr><th scope="row"><b>{esc(label)}</b><span>{esc(sentence)}</span></th>{"".join(cells)}</tr>')
    return f'<table class="xtab"><thead><tr><th scope="col"><span class="vh">Token and behaviour</span></th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


FAMILIES = {
    "paypal-usd": ("sanction_block_tornado", "refuses the Tornado Cash router", "lets it through", "PYUSD"),
    "uniswap": ("approve_max_capped", "caps approve(max)", "no cap", "UNI"),
}


def gen_family(group):
    """One asset on each chain: its code size, how many tokens share that exact code, and what the test found."""
    col, yes, no, label = FAMILIES[group]
    fam = family_rows(group)
    rows = []
    for ch in CHAINS:
        r = fam.get(ch)
        if not r:
            rows.append(f'<li class="fam-row none"><span class="fc">{esc(CHAIN_NAMES[ch])}</span>'
                        f'<span class="fk">not in our tested set</span><span class="fv"></span></li>')
            continue
        tok = r["token"]
        n = int(resolve(f"K:count|{ch}|{tok}").replace(",", ""))
        size = bound(f"K:size|{ch}|{tok}", tag="span", cls="n")
        if n > 1:
            detail = f'{size} bytes of code, the same code as {bound(f"K:count|{ch}|{tok}", tag="span", cls="n")} tokens here'
        else:
            detail = f'{size} bytes of code, used by no other token here'
        btn, st = xbutton(ch, tok, col, label, {"quirk": yes, "pass": no})
        rows.append(f'<li class="fam-row"><span class="fc">{esc(CHAIN_NAMES[ch])}</span>'
                    f'<span class="fk">{detail}</span><span class="fv s-{st}">{btn}<span class="fw">{esc(yes if st == "quirk" else no)}</span></span></li>')
    return f'<ul class="fam" aria-label="{esc(label)} on each chain">{"".join(rows)}</ul>'


UPGRADE_CLASSES = [
    ("eoa", "One externally owned account", "c-eoa"),
    ("eoa_7702", "One account with an EIP-7702 delegation", "c-eoa"),
    ("multisig_no_timelock", "Multisig, no timelock", "c-ms"),
    ("timelock", "Behind a timelock", "c-tl"),
    ("governor_dao", "Governor or DAO", "c-tl"),
    ("frozen", "Frozen", "c-tl"),
    ("l1_alias", "Layer-one alias account", "c-other"),
    ("no_known_path", "No known path", "c-other"),
    ("contract_other_or_unresolved", "Other contract or unresolved", "c-other"),
    ("authority_unverified", "Authority unverified", "c-other"),
    ("unresolved", "Unresolved", "c-other"),
    ("not_applicable", "No upgrade mechanism", "c-none"),
]


def gen_upgrade_bar():
    counts = LEDGER["site_counts"]["value"]
    total = sum(counts[f"/upgrade_paths/counts/{k}"] for k, *_ in UPGRADE_CLASSES)
    if str(total) != LEDGER["n_universe"]["value"]:
        raise SystemExit("upgrade class counts do not sum to n_universe")
    segs, legend = [], []
    for k, label, cls in UPGRADE_CLASSES:
        n = counts[f"/upgrade_paths/counts/{k}"]
        w = 100 * n / total
        segs.append(f'<span class="seg {cls}" style="flex-grow:{n}" title="{esc(label)}"></span>')
        legend.append(f'<li class="{cls}"><span class="sw" aria-hidden="true"></span>'
                      f'<span class="lab">{esc(label)}</span>{bound("S:/upgrade_paths/counts/" + k, cls="n cnt")}</li>')
    return (f'<div class="ubar" role="img" aria-label="Upgrade authority class across all deployments; counts listed below">'
            f'{"".join(segs)}</div><ul class="ulegend">{"".join(legend)}</ul>')


def gen_named_upgrades():
    header, body = parse_table(LEDGER["named_upgrade_table"]["value"])
    USED_KEYS["named_upgrade_table"] = True
    rows = []
    for r in body:
        tok = r[0]
        cell = lambda col: bound(f"T:named_upgrade_table|row={tok}|col={col}", tag="span")
        rows.append(f"<tr><th scope='row'>{esc(tok)}</th><td><code>{cell('Reproduced method')}</code></td>"
                    f"<td class='addr'><code>{cell('Terminal EOA')}</code></td><td>{cell('Pinned block')}</td></tr>")
    return ("<table class='named'><thead><tr><th scope='col'>Ethereum token</th><th scope='col'>Upgrade call</th>"
            "<th scope='col'>Account it succeeds from</th><th scope='col'>Block</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def gen_upgrade_story():
    """Compact bar for the story over the ranked (top) tokens: one key / multisig / timelock-type / other / none."""
    counts = ranked_upgrade_counts()
    total = sum(counts.values())
    if str(total) != LEDGER["n_main"]["value"]:
        raise SystemExit("ranked upgrade class counts do not sum to n_main")
    if str(counts["eoa"] + counts["eoa_7702"]) != LEDGER["main_upgrade_eoa_n_yes"]["value"]:
        raise SystemExit("eoa + eoa_7702 != main_upgrade_eoa_n_yes")
    USED_KEYS["main_upgrade_eoa_n_yes"] = True
    segs = []
    for k, label, cls in UPGRADE_CLASSES:
        segs.append(f'<span class="seg {cls}" style="flex-grow:{counts.get(k, 0)}" title="{esc(label)}"></span>')
    one = counts["eoa"] + counts["eoa_7702"]
    w = 100 * one / total
    legend = [
        ("c-eoa", "One key", "eoa+eoa_7702"),
        ("c-ms", "Multisig, no timelock", "multisig_no_timelock"),
        ("c-tl", "Timelock, DAO or frozen", "timelock+governor_dao+frozen"),
        ("c-other", "Other or unresolved", "l1_alias+no_known_path+contract_other_or_unresolved+authority_unverified+unresolved"),
        ("c-none", "No upgrade mechanism", "not_applicable"),
    ]
    got = sum(sum(counts[c] for c in spec.split("+")) for _, _, spec in legend)
    if got != total:
        raise SystemExit("legend groups do not cover every ranked deployment")
    lis = "".join(f'<li class="{c}"><span class="sw" aria-hidden="true"></span><span class="lab">{esc(l)}</span>{bound("N:" + spec, cls="n cnt")}</li>' for c, l, spec in legend)
    return (f'<p class="fig-k">Weakest account that can swap each token&#8217;s code, the {bound("L:n_main", tag="span")} top tokens</p>'
            f'<div class="ubar story-ubar" role="img" aria-label="Upgrade authority class across the top tokens; counts listed below">'
            f'<span class="bracket" style="width:{w:.3f}%"><span>{bound("N:eoa+eoa_7702", cls="n")} one key</span></span><div class="ubar-in">{"".join(segs)}</div></div>'
            f'<ul class="ulegend slim">{lis}</ul>')


YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]


def gen_switches():
    """Share of ranked tokens that carry a switch someone else holds, one bar per capability on a 0 to 100% track."""
    key = "prevalence_table"
    col = "Ranked: yes / measured (rate)"
    rows = []
    for row, label, tail in (("Pausable tokens", "can be paused", "One call and nobody can move them."),
                             ("Blocklist capability or tested-address rejection", "can block specific addresses", "Your tokens can be frozen in your own wallet.")):
        pct = lambda part: bound(f"T:{key}|row={row}|col={col}|part={part}", tag="span")
        w = num(table_cell(key, row, col, "pct"))
        USED_KEYS[key] = True
        rows.append(f'<div class="swrow"><p class="swpct">{pct("pct")}</p><div class="swbody"><p class="swlab"><b>{label}.</b> {tail}</p>'
                    f'<span class="track"><span class="bfill" style="width:{w:.1f}%"></span></span>'
                    f'<p class="swn">{pct("num")} of {pct("den")} ranked tokens measured</p></div></div>')
    return f'<div class="switches">{"".join(rows)}</div>'


def gen_bars(items, cls=""):
    """Horizontal bars on a 0 to 100 percent track; each value is a bound ledger number."""
    out = []
    for label, b in items:
        w = num(resolve(b))
        out.append(f'<div class="brow"><span class="bl">{label}</span><span class="track"><span class="bfill {cls}" style="width:{w:.1f}%"></span></span>'
                   f'<span class="bv">{bound(b, tag="span", cls="n")}</span></div>')
    return f'<div class="bars">{"".join(out)}</div>'


def year_label(y):
    return "2026 Jan–Sep" if y == "2026" else y


def gen_scissors(W=720, H=380, L=52, R=150, T=24, B=44, variant="wide"):
    pw, ph = W - L - R, H - T - B
    ymax = 80.0
    x = lambda i: L + pw * i / (len(YEARS) - 1)
    y = lambda v: T + ph * (1 - v / ymax)
    harm = [(i, f"L:v2_{yr}_measurable_share") for i, yr in enumerate(YEARS)]
    empt = [(i, f"L:v2_{yr}_no_meaningful_liquidity_pooled") for i, yr in enumerate(YEARS)]
    parts = [f'<svg class="scissors {variant}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="sc-t-{variant} sc-d-{variant}">',
             f'<title id="sc-t-{variant}">Launch traps fell while empty pools rose</title>',
             f'<desc id="sc-d-{variant}">Two lines by creation year. Values are labelled on the chart and listed in the launch series table.</desc>']
    for t in (0, 20, 40, 60, 80):
        parts.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(f'<text class="axis" data-axis="1" x="{L - 8}" y="{y(t) + 4:.1f}" text-anchor="end">{t}%</text>')
    for i, yr in enumerate(YEARS):
        b = f"Y:{yr}"
        v = resolve(b)
        if variant == "narrow" and yr not in ("2020", "2022", "2024", "2026"):
            continue
        parts.append(f'<text class="axis yr" x="{x(i):.1f}" y="{H - 18}" text-anchor="middle" data-b="{esc(b)}">{esc(v)}</text>')
    for series, cls in ((empt, "s-empty"), (harm, "s-harm")):
        pts = [(x(i), y(num(resolve(b)))) for i, b in series]
        d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        parts.append(f'<path class="line {cls}" d="{d}"/>')
        for (px, py), (i, b) in zip(pts, series):
            parts.append(f'<circle class="dot {cls}" cx="{px:.1f}" cy="{py:.1f}" r="4"/>')
            dy = -12 if cls == "s-empty" else 22
            show = ("2024", "2025") if cls == "s-empty" else ("2021", "2024", "2025")
            if variant == "narrow":
                show = ("2024",) if cls == "s-empty" else ("2021", "2024")
            if YEARS[i] in show:
                parts.append(svg_text(b, f"{px:.1f}", f"{py + dy:.1f}", cls=f"val {cls}", anchor="middle"))
    parts.append(f'<text class="axis yr" x="{x(len(YEARS) - 1):.1f}" y="{H - 4}" text-anchor="middle">so far</text>')
    # end labels
    ex = x(len(YEARS) - 1) + 14
    ye = y(num(resolve(empt[-1][1])))
    yh = y(num(resolve(harm[-1][1])))
    parts.append(svg_text(empt[-1][1], f"{ex:.1f}", f"{ye - 4:.1f}", cls="val big s-empty"))
    parts.append(f'<text class="lab s-empty" x="{ex:.1f}" y="{ye + 14:.1f}">pool already</text>')
    parts.append(f'<text class="lab s-empty" x="{ex:.1f}" y="{ye + 30:.1f}">drained</text>')
    parts.append(svg_text(harm[-1][1], f"{ex:.1f}", f"{yh - 4:.1f}", cls="val big s-harm"))
    parts.append(f'<text class="lab s-harm" x="{ex:.1f}" y="{yh + 14:.1f}">can’t sell, or</text>')
    parts.append(f'<text class="lab s-harm" x="{ex:.1f}" y="{yh + 30:.1f}">taxed heavily</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def gen_table(key, keycols=1, caption=None, rows=None, cols=None, cls="dtab", label=None, ylabel=False, rowlabels=None):
    header, body = parse_table(LEDGER[key]["value"])
    USED_KEYS[key] = True
    cols = cols or header
    th = "".join(f'<th scope="col">{bound(f"H:{key}|{header.index(h)}", tag="span")}</th>' for h in cols)
    out = []
    for r in body:
        rk = ",".join(r[:keycols])
        if rows and rk not in rows:
            continue
        tds = []
        for h in cols:
            ci = header.index(h)
            if ci < keycols and ylabel and ci == 0:
                yr = r[0].split(" ")[0]
                tds.append(f'<th scope="row">{bound("Y:" + yr, tag="span")}{" so far" if " " in r[0] else ""}</th>')
            elif ci < keycols and rowlabels and ci == 0:
                tds.append(f'<th scope="row">{esc(rowlabels[rk])}</th>')
            elif ci < keycols:
                tds.append(f'<th scope="row">{bound(f"T:{key}|row={rk}|col={h}", tag="span")}</th>')
            else:
                tds.append(f'<td>{bound(f"T:{key}|row={rk}|col={h}", tag="span")}</td>')
        out.append("<tr>" + "".join(tds) + "</tr>")
    cap = f'<p class="tcap">{caption}</p>' if caption else ""
    return (f'<div class="tscroll" tabindex="0" role="region" aria-label="{esc(label or key)}">{cap}<table class="{cls}"><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(out)}</tbody></table></div>')


def gen_prevalence():
    """Taxonomy index: ranked prevalence rows drawn as interval bars."""
    key = "prevalence_table"
    header, body = parse_table(LEDGER[key]["value"])
    USED_KEYS[key] = True
    col = "Ranked: yes / measured (rate)"
    ci_col = "Descriptive Wilson 95% interval"
    xcol = "Excluding securities: yes / measured (rate)"
    items = []
    normal = {"Revert on transfer to zero address", "Revert on approval to zero address"}
    for r in body:
        name = r[0]
        pct = num(table_cell(key, name, col, "pct"))
        lo, hi = [float(v.rstrip("%")) for v in r[header.index(ci_col)].split("–")]
        scale = lambda v: max(0.0, min(100.0, v))
        items.append(f"""<li>
  <span class="pv-name">{bound(f"T:{key}|row={name}|col={header[0]}", tag="span")}{' <em class="norm">normal behaviour, not weird</em>' if name in normal else ''}</span>
  <span class="pv-bar" aria-hidden="true"><span class="pv-fill" style="width:{scale(pct)}%"></span><span class="pv-ci" style="left:{scale(lo)}%;width:{max(scale(hi) - scale(lo), 0.4)}%"></span><span class="pv-pt" style="left:{scale(pct)}%"></span></span>
  <span class="pv-val">{bound(f"T:{key}|row={name}|col={col}|part=num", tag="span")}<span class="of">/</span>{bound(f"T:{key}|row={name}|col={col}|part=den", tag="span")} <b>{bound(f"T:{key}|row={name}|col={col}|part=pct", tag="span")}</b><span class="ex">without securities {bound(f"T:{key}|row={name}|col={xcol}|part=pct", tag="span")}</span></span>
</li>""")
    return f'<ol class="prev">{"".join(items)}</ol>'


# The page ledger: dist/data/numbers.json holds only the entries this page binds. Values are copied unchanged from
# article/numbers.json (site_counts is cut down to the paths used), two entries are computed from the matrix files,
# and source files that belong to other studies are not listed. check_numbers.py re-derives every value.
BANNED = re.compile(r"\bb20\b|b20_|honeypot|goplus|0xef|coinbase-listed|\bpaper\b", re.I)


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def synth_entries():
    files = [f"data/matrix-{ch}.json" for ch in CHAINS]
    shas = {f"data/matrix-{ch}.json": sha256_file(DATA / f"matrix-{ch}.json") for ch in CHAINS}
    return {
        "main_upgrade_class_counts": {
            "value": {**dict(sorted(ranked_upgrade_counts().items())), "upgradeable_yes": ranked_upgradeable()["yes"],
                      "upgradeable_measured": ranked_upgradeable()["measured"]},
            "query": ("matrix column upgrade_authority_class, rows whose rank band is not extras, all six chains; count of rows by class. "
                      "upgradeable_measured = rows where column upgradeable is answered yes or no; upgradeable_yes = rows answered yes"),
            "source_file": files, "source_sha256": shas},
        "tokenized_security_counts": {
            "value": dict(tokenized_counts()),
            "query": ("matrix rows whose categories include tokenized_security and whose rank band is not extras, all six chains. "
                      "total = those rows; upgradeable = column upgradeable answers yes; blocklist = column blocklist_any answers yes. "
                      "The tag groups tokenized stocks and funds by issuer name or listing; a listing does not prove who issued a token."),
            "source_file": files, "source_sha256": shas},
    }


def page_entry(key):
    synth = synth_entries()
    if key in synth:
        return synth[key]
    e = json.loads(json.dumps(LEDGER[key]))
    srcs = e.get("source_file") or []
    one = isinstance(srcs, str)
    srcs = [srcs] if one else srcs
    keep = [x for x in srcs if "0xef" not in x]
    e["source_file"] = keep[0] if (one and keep) else keep
    if isinstance(e.get("source_sha256"), dict):
        e["source_sha256"] = {k: v for k, v in e["source_sha256"].items() if "0xef" not in k}
    e["query"] = str(e.get("query", "")).replace("honeypot-or-high-tax", "trap-or-high-tax").replace("–", "-").replace("—", "-")
    if key == "site_counts":
        e["value"] = {k: v for k, v in e["value"].items() if k in USED_S}
    return e


def page_ledger():
    out = {k: page_entry(k) for k in sorted(USED_KEYS)}
    bad = []

    def walk(x, k):
        if isinstance(x, dict):
            for kk, v in x.items():
                walk(kk, k); walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, k)
        elif isinstance(x, str) and not re.fullmatch(r"[0-9a-f]{64}", x) and BANNED.search(x):
            bad.append((k, x[:100]))
    for k, e in out.items():
        walk(e, k)
    if bad:
        raise SystemExit(f"page ledger still carries banned wording: {bad[:5]}")
    return out


def gen_ledger():
    out = []
    PL = page_ledger()
    for key, e in PL.items():
        srcs = e.get("source_file") or []
        if isinstance(srcs, str):
            srcs = [srcs]
        shas = e.get("source_sha256") or {}
        if isinstance(shas, str):
            shas = {srcs[0] if srcs else "": shas}
        files = "".join(
            f"<li><code data-lf='{esc(key)}'>{esc(s)}</code> <span class='sha'>sha256 <span data-ls='{esc(key)}'>{esc(shas.get(s, ''))}</span></span></li>" for s in srcs)
        val = e["value"]
        if isinstance(val, dict) or (isinstance(val, str) and "\n" in val):
            shown = '<span class="tbl">table or map, shown where used</span>'
        else:
            shown = bound(f"L:{key}|raw", tag="span", cls="n lv")
        out.append(f"""<li id="src-{esc(key)}" class="lg">
  <div class="lg-k"><code data-lk="{esc(key)}">{esc(key)}</code> {shown}</div>
  <div class="lg-q" data-lq="{esc(key)}">{esc(str(e.get('query', ''))[:400])}</div>
  <ul class="lg-f">{files}</ul>
</li>""")
    return "\n".join(out)


def readme_text():
    t = (DATA / "README.txt").read_text()
    t = t.replace("headline_trade_harm is honeypot union high_tax (10% or more), first positive liquidity +300 blocks, floor 0.01 ETH.",
                  "headline_trade_harm means the sale fails or is taxed 10% or more, 300 blocks after first liquidity, with a 0.01 ETH floor.")
    t = t.replace("Findings come only from article/numbers.json.", "Findings come only from the ledger in numbers.json.")
    if BANNED.search(t):
        raise SystemExit("README still carries banned wording")
    return t


def gen_downloads():
    rows = []
    for ch in CHAINS:
        p = DATA / f"matrix-{ch}.json"
        mb = p.stat().st_size / 1e6
        rows.append((f"data/matrix-{ch}.json", f"{CHAIN_NAMES[ch]} matrix: every token, every test, evidence pointers", mb))
    rows.append(("data/numbers.json", "The ledger for this page: every number it uses, with its query, source files and sha256",
                 len(json.dumps(page_ledger(), indent=1).encode()) / 1e6))
    rows.append(("data/README.txt", "Data contract notes", len(readme_text().encode()) / 1e6))
    out = []
    for href, label, mb in rows:
        out.append(f'<li><a href="{href}" download><code>{esc(href)}</code></a><span>{esc(label)}</span>'
                   f'<span class="size" data-nocheck="file size">{"under 0.1" if mb < 0.05 else f"{mb:.1f}"} MB</span></li>')
    return f'<ul class="dl">{"".join(out)}</ul>'


def main():
    tpl = (SRC / "index.html").read_text()

    def bars(years, key, cls=""):
        items = []
        for yr in years:
            lab = bound(f"Y:{yr}", tag="span", cls="n") + (" so far" if yr == "2026" else "")
            items.append((lab, key.format(yr=yr)))
        return gen_bars(items, cls)

    gens = {
        "hex": hex_texture,
        "family_pyusd": lambda: gen_family("paypal-usd"),
        "family_uni": lambda: gen_family("uniswap"),
        "crosschain_more": lambda: gen_crosschain(only={"USDe", "LINK", "WBTC"}),
        "upgradebar": gen_upgrade_bar,
        "named": gen_named_upgrades,
        "upgradestory": gen_upgrade_story,
        "switches": gen_switches,
        "goodnews": lambda: bars(["2024", "2025", "2026"], "L:v2_{yr}_measurable_share"),
        "twistbars": lambda: bars(["2024", "2025", "2026"], "L:v21_{yr}_all_harm_or_emptied", "flag"),
        "scissors": lambda: gen_scissors() + gen_scissors(W=360, H=330, L=34, R=96, T=20, B=40, variant="narrow"),
        "prevalence": gen_prevalence,
        "chainrank": lambda: gen_table("chain_rank_table", 2, "Behaviour and control by chain and rank band. Each cell: positive / measured (rate).", label="Behaviour by chain and rank band"),
        "annual": lambda: gen_table("v2_annual_table", 1, "Share of new tokens with real liquidity an hour in where the sale failed or the tax was " + bound("P:10% or more", tag="span") + ", and share of all new tokens, by creation year.",
                                    label="Launch-state harm by creation year", ylabel=True,
                                    cols=["Creation year", "Primary \u22650.01 ETH [approx. 95%]", "All-new [approx. 95%]"],
                                    rows={"2020", "2021", "2022", "2023", "2024", "2025", "2026 Jan\u2013Sep"}),
        "combined": lambda: gen_table("v21_combined_table", 2, "Share of pooled tokens that were harmful or below the liquidity floor within the hour, and share of initially funded tokens that were harmful or emptied, by year and venue.",
                                      label="Harmful or emptied by year and venue",
                                      cols=["Year", "Venue", "Harm or below floor / pooled [approx. 95%]", "Harmful or emptied / initially funded [approx. 95%]"],
                                      rows={f"{y},{v}" for y in ("2024", "2025", "2026") for v in ("All", "V1/V2", "V3", "V4")}),
        "blind": lambda: gen_table("blind300_table", 2, "Independent blind replay at first liquidity plus " + bound("P:300 blocks", tag="span") + ".", label="Independent blind replay",
                                   rows={"2024,all", "2025,all", "2026,all", "all,all"}),
        "universe": lambda: gen_table("universe_table", 1, "Deployments tested, by chain and CoinGecko rank band.", label="Deployments tested by chain"),
        "crosstests": lambda: gen_table("crosschain_tests", 1, "Ethereum against the other five chains combined, the top five hundred of each. Each cell: tokens showing the behaviour / tokens measured.",
                                        label="Ethereum against the other chains", cols=["Feature", "Ethereum", "Rest"],
                                        rowlabels={"Missing transfer return": "transfer() says nothing back", "False on failure": "Says false instead of failing",
                                                   "Approval race guard": "Must reset an approval to zero first", "Bounded-integer limit": "Large transfers revert"}),
    }
    for name, fn in gens.items():
        tag = f"<!--GEN:{name}-->"
        if tag in tpl:
            tpl = tpl.replace(tag, fn())

    def sub(m):
        spec = m.group(1).strip()
        if spec.startswith("XS:"):
            return evidence_link(*spec[3:].split("|"))
        if spec.startswith("X:"):
            return evidence_phrase(*spec[2:].split("|"))
        cls = "n"
        if spec.startswith("big:"):
            spec, cls = spec[4:], "n big"
        return bound(spec, cls=cls)

    tpl = re.sub(r"\{\{(.+?)\}\}", sub, tpl)
    tpl = tpl.replace("%%OG_IMAGE%%", OG_IMAGE).replace("%%SITE_URL%%", SITE_URL).replace("%%REPO%%", REPO_URL)
    og = re.sub(r"\{\{(.+?)\}\}", sub, (SRC / "og.html").read_text())
    og = og.replace("<!--GEN:hex-->", hex_texture())
    (SITE / "og").mkdir(exist_ok=True)
    (SITE / "og" / "card.html").write_text(og)
    tpl = tpl.replace("<!--GEN:ledger-->", gen_ledger())
    tpl = tpl.replace("<!--GEN:downloads-->", gen_downloads())
    tpl = tpl.replace("<!--GEN:evidence-->",
                      '<script type="application/json" id="ev-embed">'
                      + json.dumps(EVIDENCE, separators=(",", ":")).replace("</", "<\\/") + "</script>")
    if "{{" in tpl or "<!--GEN:" in tpl:
        raise SystemExit("unresolved template tokens remain")

    if DIST.exists():
        for p in DIST.iterdir():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
    DIST.mkdir(exist_ok=True)
    (DIST / "index.html").write_text(tpl)
    for f in ("styles.css", "app.js"):
        shutil.copy2(SRC / f, DIST / f)
    shutil.copytree(SRC / "fonts", DIST / "fonts")
    (DIST / "data").mkdir()
    for ch in CHAINS:
        shutil.copy2(DATA / f"matrix-{ch}.json", DIST / "data" / f"matrix-{ch}.json")
    (DIST / "data" / "README.txt").write_text(readme_text())
    (DIST / "data" / "numbers.json").write_text(json.dumps(page_ledger(), indent=1))
    if (SRC / OG_FILE).exists():
        shutil.copy2(SRC / OG_FILE, DIST / OG_FILE)
    else:
        print("warning: src/og.png missing; run tools/og.mjs")
    if (SRC / "favicon.svg").exists():
        shutil.copy2(SRC / "favicon.svg", DIST / "favicon.svg")
    manifest = {p.relative_to(DIST).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(DIST.rglob("*")) if p.is_file()}
    (DIST / "build-manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"built {DIST/'index.html'}: {len(USED_KEYS)} ledger keys, {len(EVIDENCE)} evidence records")


if __name__ == "__main__":
    main()
