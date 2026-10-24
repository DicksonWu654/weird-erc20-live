#!/usr/bin/env python3
"""Independent check: every number displayed in dist/index.html equals its source value.

1. Every element carrying data-b is re-resolved from the raw sources (article/numbers.json,
   article/paper.md, site/dist/data/matrix-*.json) with code separate from the build, and its
   displayed text (or data-v) must match.
2. Every other text node and aria-label/alt attribute is scanned for digits. A digit is only allowed
   inside a known identifier (ERC-20, EIP-7702, B20, V2, uint96, sha256, 0x..), inside an element
   marked data-axis (chart tick, must look like "40%") or data-nocheck (documented exemption).
3. The matrix files copied into dist/data must be byte-identical to the data contract.
Exit status 1 on any failure.
"""
import csv, hashlib, json, re, sys
from html.parser import HTMLParser
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
DIST = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else SITE / "dist"
FILES = [DIST / "index.html"] if len(sys.argv) > 1 else [DIST / "index.html", SITE / "og" / "card.html"]
PHRASES = {}
for line in (SITE / "src" / "phrases.tsv").read_text().splitlines():
    if line.strip() and not line.startswith("#"):
        col, raw, phrase = line.split("\t")
        PHRASES[(col, raw)] = phrase
LEDGER = json.loads((ROOT / "article" / "numbers.json").read_text())
PAGE = json.loads((DIST / "data" / "numbers.json").read_text())      # the ledger the page ships and links to
PAPER = (ROOT / "article" / "paper.md").read_text()
DATA = ROOT / "data" / "site"
MAT = {}

def mat(chain):
    if chain not in MAT:
        MAT[chain] = json.loads((DATA / f"matrix-{chain}.json").read_text())
    return MAT[chain]

def md_table(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and not re.fullmatch(r"\|[\s\-|:]+\|", line):
            rows.append([c.strip() for c in line[1:-1].split("|")])
    return rows[0], rows[1:]

PARTS = {"num": r"^([\d,]+)/", "den": r"/([\d,]+)", "pct": r"\(([\d.]+%)\)", "point": r"^([\d.,]+%?)",
         "s1": r"^(\d+)/\d+/\d+/\d+$", "s2": r"^\d+/(\d+)/\d+/\d+$", "s3": r"^\d+/\d+/(\d+)/\d+$", "s4": r"^\d+/\d+/\d+/(\d+)$"}

def expected(b):
    kind, rest = b.split(":", 1)
    if kind == "L":
        key, _, fmt = rest.partition("|")
        v = str(LEDGER[key]["value"])
        if fmt in ("num", "den"):
            return re.search(PARTS[fmt], v).group(1)
        return v
    if kind == "S":
        return str(LEDGER["site_counts"]["value"][rest])
    if kind == "T":
        key, *kv = rest.split("|")
        kv = dict(x.split("=", 1) for x in kv)
        head, body = md_table(LEDGER[key]["value"])
        want = kv["row"].split(",")
        match = [r for r in body if r[:len(want)] == want]
        assert len(match) == 1, f"{b}: {len(match)} rows"
        cell = match[0][head.index(kv["col"])]
        if "part" in kv:
            cell = re.search(PARTS[kv["part"]], cell).group(1)
        return cell
    if kind == "P":
        assert rest in PAPER, f"{b}: not in paper.md"
        return rest
    if kind == "H":
        key, i = rest.split("|")
        return md_table(LEDGER[key]["value"])[0][int(i)].replace("–", "-")
    if kind == "Y":
        head, body = md_table(LEDGER["v2_annual_table"]["value"])
        assert any(r[0].startswith(rest) for r in body), b
        return rest
    if kind == "M":
        chain, token, col, field = rest.split("|")
        d = mat(chain)
        ci = [c["id"] for c in d["columns"]].index(col)
        row = next(r for r in d["rows"] if r["token"] == token)
        cell = row["cells"][ci]
        ev = row["evidence"][d["columns"][ci]["src"]]
        return {"decoded": lambda: cell[1], "status": lambda: cell[0], "block": lambda: str(ev["block"]),
                "call": lambda: cell[2], "sha": lambda: ev["sha256"], "rank": lambda: row["rank_band"],
                "symbol": lambda: row["symbol"], "name": lambda: row["name"],
                "callsha": lambda: (ev.get("call_hashes") or {}).get(str(cell[2]).split(",")[0]) or ev["sha256"]}[field]()
    if kind == "W":      # whole-token units: 10**decimals, decimals read from a matrix cell
        return f"{10 ** int(norm(expected(rest))):,}"
    if kind == "U":      # 10**A units shown on a token with B decimals, as an exact plain decimal
        a, c = (int(norm(expected(x))) for x in rest.split(";"))
        return f"{10 ** (a - c):,}" if a >= c else "0." + "0" * (c - a - 1) + "1"
    if kind == "C":      # incident date cited in the paper intro: month plus bolded year
        month, year = rest.split()
        assert re.search(rf"{month}\s+\*\*{year}\*\*", PAPER), f"{b}: date not in paper intro"
        return rest
    if kind == "E":      # quoted evidence phrase: must appear in src/examples.csv, row <id>, tweet field
        eid, text = rest.split("|", 1)
        rows = [r for r in csv.DictReader((SITE / "src" / "examples.csv").open()) if r["id"] == eid]
        assert len(rows) == 1 and text in rows[0]["tweet"], f"{b}: phrase not in examples.csv"
        return text
    if kind == "V":      # universe rows by inclusion source, read straight from data/universe/<chain>.csv
        chains, source = rest.split("|")
        vals = set()
        for ch in chains.split(","):
            with (ROOT / "data" / "universe" / f"{ch}.csv").open() as f:
                vals.add(sum(1 for r in csv.DictReader(f) if r["source"] == source))
        assert len(vals) == 1, f"{b}: chains disagree {vals}"
        return str(vals.pop())
    if kind == "D":      # difference or sum of two bound integers
        op, args = rest.split("|", 1)
        a, c = args.split(";")
        assert op in ("sub", "add")
        x, y = int(norm(expected(a))), int(norm(expected(c)))
        return str(x - y if op == "sub" else x + y)
    if kind == "K":      # code facts, read straight from data/universe/<chain>.csv
        what, _, args = rest.partition("|")
        def rows(ch):
            with (ROOT / "data" / "universe" / f"{ch}.csv").open() as f:
                return list(csv.DictReader(f))
        if what == "distinct":
            chains = ["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]
            return str(len({r["code_keccak"] for ch in chains for r in rows(ch) if r["same_asset_group"] == args}))
        chain, token = args.split("|")
        rs = rows(chain)
        me = [r for r in rs if r["address"].lower() == token.lower()]
        assert len(me) == 1, f"{rest}: {len(me)} rows"
        if what == "size":
            return me[0]["code_size"]
        same = [r for r in rs if r["code_keccak"] == me[0]["code_keccak"]]
        if what == "count":
            return str(len(same))
        if what == "others":
            return str(len(same) - 1)
        if what == "bridged":
            rel = {r["token"]: r["deployment_relationship"] for r in mat(chain)["rows"]}
            return str(sum(1 for r in same if r["address"].lower() != token.lower()
                           and rel.get(r["address"].lower()) == "third_party_bridge_or_peg"))
        raise AssertionError(what)
    if kind == "N":      # ranked deployments by upgrade authority class (or upgradeable yes / measured), straight from the matrix
        if rest.startswith("upgradeable."):
            c = ranked_matrix()["upg"]
            return str(c["yes"] if rest.endswith("yes") else c["yes"] + c["no"])
        return str(sum(ranked_matrix()["cls"].get(x, 0) for x in rest.split("+")))
    if kind == "B":      # tokenized stocks and funds among the ranked deployments, from the matrix tag
        return str(ranked_matrix()["ts"][rest])
    if kind == "G":      # 2**a base units in whole tokens at b decimals, in billions
        a, b2 = (int(x) for x in rest.split("|"))
        return f"{round(2 ** a / 10 ** b2 / 1e9)} billion"
    raise AssertionError(f"unknown binding {b}")

_RM = {}
def ranked_matrix():
    """Independent pass over the six matrix files: ranked rows only (rank band other than extras)."""
    if not _RM:
        cls, upg, ts = {}, {"yes": 0, "no": 0}, {"total": 0, "upgradeable": 0, "blocklist": 0}
        for ch in ["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]:
            d = mat(ch)
            ids = [c["id"] for c in d["columns"]]
            ia, iu, ib = ids.index("upgrade_authority_class"), ids.index("upgradeable"), ids.index("blocklist_any")
            for r in d["rows"]:
                if r["rank_band"] == "extras":
                    continue
                cls[r["cells"][ia][1]] = cls.get(r["cells"][ia][1], 0) + 1
                st = r["cells"][iu][0]
                if st in ("quirk", "pass"):
                    upg["yes" if st == "quirk" else "no"] += 1
                if "tokenized_security" in r["categories"]:
                    ts["total"] += 1
                    ts["upgradeable"] += r["cells"][iu][0] == "quirk"
                    ts["blocklist"] += r["cells"][ib][0] == "quirk"
        _RM.update(cls=cls, upg=upg, ts=ts)
    return _RM

def norm(s):
    return re.sub(r"\s+", " ", s.replace(",", "")).strip()

class P(HTMLParser):
    VOID = {"area","base","br","col","embed","hr","img","input","link","meta","source","track","wbr","circle","line","path","rect"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []      # (tag, attrs)
        self.bound = []      # [binding, attrs, text]
        self.free = []       # (text, context)
        self.cur = None
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("aria-label", "alt"):
            if a.get(k) and not self._skip(a):
                self.free.append((a[k], f"<{tag} {k}>"))
        if tag in self.VOID:
            return
        self.stack.append((tag, a))
        for pk in ("data-b", "data-lk", "data-lq", "data-lf", "data-ls"):
            if pk in a:
                self.cur = [(pk, a[pk]) if pk != "data-b" else a[pk], a, ""]
                self.bound.append(self.cur)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:
            t, a = self.stack.pop()
            if any(k in a for k in ("data-b", "data-lk", "data-lq", "data-lf", "data-ls")):
                self.cur = None
            if t == tag:
                break
    def _skip(self, extra=None):
        for t, a in self.stack + ([("x", extra)] if extra else []):
            if t in ("script", "style", "title") or "data-nocheck" in a:
                return True
        return False
    def handle_data(self, data):
        if self.cur is not None:
            self.cur[2] += data
            return
        if self._skip():
            return
        axis = any("data-axis" in a for _, a in self.stack)
        if axis:
            if not re.fullmatch(r"\s*\d+%\s*", data):
                self.free.append((data, "axis"))
            return
        self.free.append((data, "/".join(t for t, _ in self.stack[-3:])))

IDENT = re.compile(r"ERC-\d+s?|EIP-\d+|\bB20\b|\bV[1-4]\b|\buint\d+\b|\b\d+-bit\b|sha256|0x[0-9a-fA-F]*|\bH\d\b")
fails = []
kinds = {}
prov = 0
phr = 0
bound_total = free_total = 0
for FILE in FILES:
  HTML = FILE.read_text()
  p = P(); p.feed(HTML)
  bound_total += len(p.bound)
  free_total += len(p.free)
  if FILE.name == "index.html":
    metas = dict(re.findall(r'<meta (?:property|name)="((?:og|twitter):[a-z:]+)" content="([^"]*)"', HTML))
    for need in ("og:title", "og:description", "og:image", "og:url", "twitter:card", "twitter:image"):
        if need not in metas:
            fails.append(f"META missing {need}")
    if metas.get("twitter:card") != "summary_large_image":
        fails.append("META twitter:card is not summary_large_image")
    PUBLIC = "https://weird-erc20-live.shadow654.chatgpt.site/"
    if metas.get("og:url") != PUBLIC:
        fails.append(f"META og:url is {metas.get('og:url')!r}, expected {PUBLIC!r}")
    img = metas.get("og:image", "")
    if img != metas.get("twitter:image") or not img.startswith(PUBLIC) or not (DIST / img[len(PUBLIC):]).is_file():
        fails.append("META og:image/twitter:image must be the same absolute URL under the site root, and the file must exist in dist")
  for b, attrs, text in p.bound:
      if isinstance(b, tuple):
          pk, key = b; e = PAGE[key]; prov += 1
          srcs = e.get("source_file") or []
          srcs = [srcs] if isinstance(srcs, str) else srcs
          shas = e.get("source_sha256") or {}
          shas = list(shas.values()) if isinstance(shas, dict) else [shas]
          ok = {"data-lk": lambda: text == key, "data-lq": lambda: text == str(e.get("query", ""))[:400],
                "data-lf": lambda: text in srcs, "data-ls": lambda: text == "" or text in shas}[pk]()
          if not ok:
              fails.append(f"PROVENANCE {pk}={key}: {text[:80]!r}")
          continue
      kinds[b[0]] = kinds.get(b[0], 0) + 1
      try:
          exp = expected(b)
      except Exception as e:
          fails.append(f"RESOLVE {b}: {e}"); continue
      shown = attrs.get("data-v", text)
      if attrs.get("data-trunc"):
          a, _, z = shown.partition("…")
          ok = exp.startswith(a) and exp.endswith(z) and len(a) >= 6
      else:
          ok = norm(shown) == norm(exp)
      if not ok:
          fails.append(f"MISMATCH {b}: shown {shown!r} != source {exp!r}")
      if "data-v" in attrs and b.startswith("M:") and b.endswith("|decoded"):
          phr += 1
          col = b.split("|")[2]
          want = PHRASES.get((col, attrs["data-v"]))
          if want is None or norm(text) != norm(want):
              fails.append(f"PHRASE {b}: card shows {text!r}, table has {want!r} for raw {attrs['data-v']!r}")

  unbound = []
  for text, ctx in p.free:
    t = IDENT.sub("", text)
    if re.search(r"\d", t):
        unbound.append(f"UNBOUND digit in {FILE.name} {ctx}: {text.strip()[:120]!r}")
  fails += unbound

for f in sorted((DIST / "data").glob("matrix-*.json")):
    if hashlib.sha256(f.read_bytes()).digest() != hashlib.sha256((DATA / f.name).read_bytes()).digest():
        fails.append(f"DATA COPY differs: {f.name}")

# the page ledger must hold the same values as the full ledger, only the keys the page binds, and the synthetic
# matrix entries must equal an independent recount
page_used = set(re.findall(r'id="src-([^"]+)"', (DIST / "index.html").read_text()))
if set(PAGE) != page_used:
    fails.append(f"PAGE LEDGER keys differ from ledger entries listed on the page: {sorted(set(PAGE) ^ page_used)}")
SYN = {"main_upgrade_class_counts", "tokenized_security_counts"}
for k, e in PAGE.items():
    if k in SYN:
        continue
    full = LEDGER[k]["value"]
    if k == "site_counts":
        ok = all(full.get(a) == b for a, b in e["value"].items())
    else:
        ok = e["value"] == full
    if not ok:
        fails.append(f"PAGE LEDGER value differs from article/numbers.json: {k}")
rm = ranked_matrix()
if "main_upgrade_class_counts" in PAGE:
    want = {**rm["cls"], "upgradeable_yes": rm["upg"]["yes"], "upgradeable_measured": rm["upg"]["yes"] + rm["upg"]["no"]}
    if PAGE["main_upgrade_class_counts"]["value"] != want:
        fails.append("PAGE LEDGER main_upgrade_class_counts differs from recount")
if "tokenized_security_counts" in PAGE and PAGE["tokenized_security_counts"]["value"] != rm["ts"]:
    fails.append("PAGE LEDGER tokenized_security_counts differs from recount")
for k in SYN & set(PAGE):
    for f, h in PAGE[k]["source_sha256"].items():
        if hashlib.sha256((DIST / f).read_bytes()).hexdigest() != h:
            fails.append(f"PAGE LEDGER sha256 for {k} {f} is stale")

# banned wording anywhere in dist (word-aware; 64-hex digests and hex addresses are ignored)
BAN = re.compile(r"\bb20\b|b20_|honeypot|goplus|0xef(?![0-9a-fA-F])|coinbase-listed|\bpaper\b", re.I)
# unrelated tokens that carry these names in the matrix files (the token B.20, the GoPlus Security token)
LEGIT = re.compile(r"B\.20|\"group\":\"b20\"|\"asset_family\":\"b20\"|\"symbol\":\"B20\"|goplus-security|GoPlus Security|Papertrade|Coinbase Wrapped", re.I)
RESID = []
for f in sorted((DIST).rglob("*")):
    if not f.is_file() or f.suffix in (".woff2", ".png", ".jpg", ".ico"):
        continue
    try:
        t = f.read_text()
    except Exception:
        continue
    t = re.sub(r"0x[0-9a-fA-F]{40}|\b[0-9a-fA-F]{64}\b", "", t)
    for m in BAN.finditer(t):
        ctx = t[max(0, m.start() - 30): m.end() + 30].replace("\n", " ")
        if LEGIT.search(t[max(0, m.start() - 80): m.end() + 80]):
            continue
        RESID.append((("dist/" + f.relative_to(DIST).as_posix()), m.group(0), ctx))
by_file = {}
for fn, w, ctx in RESID:
    by_file.setdefault((fn, w), []).append(ctx)
for (fn, w), ctxs in by_file.items():
    if fn == "dist/data/matrix-base.json" and w.lower().startswith(("b20", "0xef")):
        print(f"RESIDUAL (raw proxy field left as instructed): {fn} {w!r} x{len(ctxs)}")
    else:
        fails.append(f"BANNED wording {w!r} in {fn} x{len(ctxs)}: ...{ctxs[0]}...")

print(f"files: {[str(f) for f in FILES]}")
print(f"bound values checked: {bound_total}  by kind: {dict(sorted(kinds.items()))}")
print(f"card phrases verified against src/phrases.tsv (raw value + phrase): {phr}")
print(f"ledger provenance lines verified (key, query, source path, sha256): {prov}")
print(f"free text nodes scanned: {free_total}")
if fails:
    print(f"FAIL: {len(fails)} problem(s)")
    for f in fails: print("  " + f)
    sys.exit(1)
print("PASS: every displayed number matches its ledger, paper or matrix source; no unbound digits.")
