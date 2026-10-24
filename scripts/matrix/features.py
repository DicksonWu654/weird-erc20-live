"""Feature registry for the Tier-1 matrix (A1).

Every matrix column is a *feature*: a yes/no/unprobeable statement about one token, derived from saved probe rows
(data/probe/<chain>/results.final.jsonl), W3 control rows (data/control/<chain>/control.jsonl) and the W2 universe.

Cell = (status, value, ev)
  status  'yes'  the behaviour / property is present   (for capability features: the capability exists in code or views;
                                                         for observed features: it was observed dynamically)
          'no'   measured, not present
          'unprobeable' / 'error'  could not be measured (value = reason). Never guessed, never counted in a denominator.
  value   short string (return shape, number, class, revert text ...)
  ev      evidence references: {'src': 'probe'|'control'|'universe', 'file': rel path, 'cids': [...], 'sha': [...]}

kind: 'capability' = selector/view exists (static or answered view);  'observed' = measured by a live call;
      'control' = W3 classification;  'meta' = information column.

The *feature definitions* (what counts as yes) are the interesting, reviewable part: keep them explicit.
"""
import math
import re

# ---------------------------------------------------------------- helpers
UNP = ("unprobeable", "error")


def ev_probe(*rows):
    rows = [r for r in rows if r]
    if not rows:
        return None
    cids, sha, files = [], [], []
    for r in rows:
        files.append(r.get("evidence"))
        for c, s in zip(r.get("evidence_calls") or [], r.get("evidence_sha256") or []):
            cids.append(c)
            sha.append(s)
    return {"src": "probe", "file": files[0], "cids": cids[:12], "sha": [s[:16] for s in sha[:12]], "tests": [r["test"] for r in rows]}


def _short(v, n=70):
    s = v if isinstance(v, str) else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def C(status, value="", ev=None):
    return (status, _short(value), ev)


def unp_cell(row, name=""):
    if row is None:
        return C("unprobeable", "no_row", None)
    v = row["value"]
    return C(row["status"], v if isinstance(v, str) else f"{name}:{row['status']}", ev_probe(row))


def from_row(T, test, fn, name=None):
    """fn(row)->(bool, value). Unprobeable/error rows propagate their reason."""
    r = T.get(test)
    if r is None or r["status"] in UNP:
        return unp_cell(r, test)
    b, val = fn(r)
    return C("yes" if b else "no", val, ev_probe(r))


def multi(T, tests, fn):
    """fn(rows: dict test->row) -> (bool|None, value). None = not measurable."""
    rows = {t: T.get(t) for t in tests}
    ok = {t: r for t, r in rows.items() if r is not None and r["status"] not in UNP}
    if not ok:
        r0 = next((r for r in rows.values() if r), None)
        return unp_cell(r0, tests[0])
    b, val = fn(ok)
    if b is None:
        return C("unprobeable", val, ev_probe(*ok.values()))
    return C("yes" if b else "no", val, ev_probe(*ok.values()))


def vstr(r):
    v = r["value"]
    return v if isinstance(v, str) else ""


# ---------------------------------------------------------------- revert-text classes
PERM_RX = re.compile(r"registry|kyc|whitelist|allowlist|allow.?list|not allowed|unauthori[sz]ed|not authori[sz]ed|permission|hasnt role|"
                     r"restricted|not approved|compliance|investor|eligib|not verified|validation module|rejected by policy|transfer rejected|"
                     r"must be", re.I)
# custom-error selectors seen in the top tokens (class labels based on selector matches and token names; see scripts/probe/README.md for probe limitations)
SEL_CLASS = {"0xff5e746f": "permissioned", "0xf90e674a": "permissioned", "0x7f63bd0f": "permissioned",
             "0xc02d38cc": "market_closed", "0x3d04e56e": "router_restricted", "0xd93c0665": "paused",
             "0xe450d38c": "funding_artifact_suspect"}  # e450d38c = OZ-v5 ERC20InsufficientBalance: the override funding did not take effect


def revert_class(val):
    v = val or ""
    m = re.search(r"custom:(0x[0-9a-f]{8})", v)
    if m:
        return SEL_CLASS.get(m.group(1), "other_custom_error")
    if re.search(r"pause", v, re.I):
        return "paused"
    if re.search(r"suspend", v, re.I):
        return "suspended"
    if PERM_RX.search(v):
        return "permissioned"
    if re.search(r"frozen|locked|locking", v, re.I):
        return "frozen_or_locked"
    if "empty" in v:
        return "empty_revert"
    if re.search(r"SafeMath|underflow|panic|exceeds balance|exceeds unlocked", v, re.I):
        return "funding_artifact_suspect"
    return "other_revert"


# ---------------------------------------------------------------- feature functions
def f_transfer_missing(T, C_, U):
    return from_row(T, "transfer_return", lambda r: (vstr(r) == "no_return_value", vstr(r) or "?"))


def f_approve_missing(T, C_, U):
    return from_row(T, "approve_return", lambda r: (vstr(r) == "no_return_value", vstr(r) or "?"))


def f_tf_missing(T, C_, U):
    return from_row(T, "transferFrom_return", lambda r: (vstr(r) == "no_return_value", vstr(r) or "?"))


def f_false_on_failure(T, C_, U):
    def fn(rows):
        hits = [t for t, r in rows.items() if vstr(r).startswith("returns_false_no_revert")]
        return bool(hits), ",".join(hits) if hits else "reverts_on_failure"
    return multi(T, ["transfer_insufficient", "transferFrom_no_allowance", "transferFrom_exceed_allowance"], fn)


def _tr_detail(T):
    r = T.get("transfer_return")
    if r is None or r["status"] in UNP:
        return r, None
    d = r.get("detail") or {}
    try:
        return r, (int(d["recipient_before"]), int(d["recipient_after"]), int(d["amount"]))
    except Exception:
        return r, None


def f_not_credited(T, C_, U):
    r, x = _tr_detail(T)
    if x is None:
        return unp_cell(r, "transfer_return")
    rb, ra, amt = x
    if rb > 0:
        return C("unprobeable", "phantom_balance: see fresh_address_nonzero_balance", ev_probe(r))
    ok = not vstr(r).startswith("reverts")
    if not ok:
        return C("unprobeable", "baseline_transfer_reverts", ev_probe(r))
    bad = (ra - rb) <= 0
    return C("yes" if bad else "no", f"recipient {rb}->{ra} after sending {amt}" if bad else "credited", ev_probe(r))


def f_fresh_nonzero(T, C_, U):
    r, x = _tr_detail(T)
    if x is None:
        return unp_cell(r, "transfer_return")
    rb = x[0]
    return C("yes" if rb > 0 else "no", f"balanceOf(never-used address)={rb}" if rb > 0 else "0", ev_probe(r))


def f_transfer_reverts_fresh(T, C_, U):
    r = T.get("transfer_return")
    if r is None or r["status"] in UNP:
        return unp_cell(r, "transfer_return")
    v = vstr(r)
    if v.startswith("reverts"):
        cl = revert_class(v)
        if cl == "funding_artifact_suspect":
            return C("unprobeable", "funding_artifact_suspect: " + v[8:], ev_probe(r))
        return C("yes", cl + ": " + v[8:], ev_probe(r))
    return C("no", v, ev_probe(r))


def mk_class_feature(cls_set, label):
    def f(T, C_, U):
        r = T.get("transfer_return")
        if r is None or r["status"] in UNP:
            return unp_cell(r, "transfer_return")
        v = vstr(r)
        if v.startswith("reverts"):
            c = revert_class(v)
            return C("yes" if c in cls_set else "no", c if c in cls_set else "other:" + c, ev_probe(r))
        return C("no", "transfer_ok", ev_probe(r))
    return f


def f_fee_on_transfer(T, C_, U):
    tr, x = _tr_detail(T)
    if x is not None and x[0] > 0:
        return C("unprobeable", "phantom_balance: see fresh_address_nonzero_balance", ev_probe(tr, T.get("fee_on_transfer")))
    def fn(r):
        v = vstr(r)
        if v.startswith("recipient_receives_less") or "sender_debited_less" in v:
            d = r.get("detail") or {}
            return True, f"{v} fee_bps={d.get('fee_bps')}"
        return False, v
    r = T.get("fee_on_transfer")
    if r and r["status"] not in UNP and fn(r)[0]:
        fee = (r.get("detail") or {}).get("fee_bps")
        if x is None or x[0] != 0:
            return C("unprobeable", "recipient_prebalance_not_zero; no delta-of-delta control", ev_probe(tr, r))
        if fee is None or not math.isfinite(float(fee)) or not 0 <= float(fee) <= 10000:
            return C("unprobeable", "fee_bps_outside_0_10000", ev_probe(tr, r))
    return from_row(T, "fee_on_transfer", fn)


def f_rounding(T, C_, U):
    return from_row(T, "fee_on_transfer", lambda r: (vstr(r).startswith("rounding"), vstr(r)))


def f_fee_switch(T, C_, U):
    def fn(r):
        v = r["value"]
        if isinstance(v, dict):
            return True, ",".join(v.get("accessors", {}))
        return False, "none"
    return from_row(T, "fee_parameters", fn)


def f_fee_param_nonzero(T, C_, U):
    def fn(r):
        v = r["value"]
        if isinstance(v, dict):
            nz = v.get("nonzero") or []
            return bool(nz), json_short(nz)
        return False, "none"
    return from_row(T, "fee_parameters", fn)


def json_short(x):
    import json
    return _short(json.dumps(x, separators=(",", ":")), 60)


def f_approve_race(T, C_, U):
    return from_row(T, "approve_nonzero_to_nonzero", lambda r: (vstr(r).startswith("reverts"), vstr(r)))


def f_approve_zero_addr_reverts(T, C_, U):
    return from_row(T, "approve_zero_address", lambda r: (vstr(r) == "reverts", vstr(r)))


def f_approve_zero_value_reverts(T, C_, U):
    return from_row(T, "approve_zero_value", lambda r: (vstr(r) == "reverts", vstr(r)))


def f_approve_max_capped(T, C_, U):
    return from_row(T, "approve_max", lambda r: (vstr(r) != "uint256_max_ok", vstr(r)))


def f_allowance_helpers(T, C_, U):
    def fn(r):
        v = r["value"]
        hits = [k for k, x in v.items() if x] if isinstance(v, dict) else []
        return bool(hits), ",".join(hits) or "none"
    return from_row(T, "allowance_helpers", fn)


def f_tf_owner_no_allowance(T, C_, U):
    return from_row(T, "transferFrom_by_owner", lambda r: (vstr(r).startswith("owner_can_transferFrom"), vstr(r)))


def f_infinite_decrements(T, C_, U):
    return from_row(T, "transferFrom_infinite_allowance", lambda r: (vstr(r).startswith("decremented_even_when_max"), vstr(r)[:60]))


def f_tf_zero_reverts(T, C_, U):
    return from_row(T, "transferFrom_zero_value", lambda r: (vstr(r) == "reverts", vstr(r)))


def f_transfer_zero_reverts(T, C_, U):
    return from_row(T, "transfer_zero_value", lambda r: (vstr(r) == "reverts", vstr(r)))


def f_transfer_zero_false(T, C_, U):
    return from_row(T, "transfer_zero_value", lambda r: (vstr(r) == "returns_false", vstr(r)))


def f_to_zero_reverts(T, C_, U):
    return from_row(T, "transfer_to_zero_address", lambda r: (vstr(r) == "reverts", vstr(r)))


def f_to_zero_burns(T, C_, U):
    return from_row(T, "transfer_to_zero_address", lambda r: (vstr(r).startswith("allowed(burns"), vstr(r)))


def f_self_transfer(T, C_, U):
    return from_row(T, "self_transfer", lambda r: (r["status"] == "quirk", vstr(r)))


def f_self_transfer_changes(T, C_, U):
    return from_row(T, "self_transfer", lambda r: (vstr(r).startswith("balance_delta"), vstr(r)))


def f_to_token_reverts(T, C_, U):
    return from_row(T, "transfer_to_token_contract", lambda r: (vstr(r) == "reverts", vstr(r)))


def f_to_dead_reverts(T, C_, U):
    return from_row(T, "transfer_to_dead_address", lambda r: (vstr(r) == "reverts", vstr(r)))


def f_uint96(T, C_, U):
    """W1's own `large_uint96` label is NOT used: it matches the digits "96" anywhere in the revert data, including the hex of custom-error
    arguments (e.g. OZ-v5 ERC20InsufficientBalance 0xe450d38c...), which produced ~100 false positives. Here: yes only if (a) the decoded
    revert *string* of transfer(2^96) mentions 96, or (b) the balance-slot width probes show a narrow (<=128-bit) balance field."""
    tr = T.get("transfer_return")
    if tr is not None and tr["status"] == "fail":
        return C("unprobeable", "baseline_transfer_fails(possible_funding_artifact)", ev_probe(tr))

    def fn(r):
        d = r.get("detail") or {}
        txt = d.get("detail") or ""
        text_hit = txt.startswith("error_string:") and "96" in txt[len("error_string:"):]
        bits = d.get("balance_bits_upper_bound")
        narrow = isinstance(bits, int) and bits <= 96
        refused = d.get("return_shape") in ("reverts", "returns_false")
        if r["status"] == "fail" or not refused:
            return False, "transfer(2^96)_not_refused"
        if text_hit:
            return True, "revert_string:" + txt[13:70]
        if narrow:
            return True, f"balance_field<={bits}_bits"
        return False, "no_96_bit_limit_detected"
    return from_row(T, "large_uint96", fn)


def f_uint128(T, C_, U):
    tr = T.get("transfer_return")
    if tr is not None and tr["status"] == "fail":
        return C("unprobeable", "baseline_transfer_fails(possible_funding_artifact)", ev_probe(tr))

    def fn(r):
        d = r.get("detail") or {}
        bits = d.get("balance_bits_upper_bound")
        ok = isinstance(bits, int) and 96 < bits <= 128
        return ok, f"balance_field<={bits}_bits" if ok else "wider_or_unknown"
    return from_row(T, "large_uint96", fn)


def f_max_uint_transfer(T, C_, U):
    return from_row(T, "max_uint_transfer", lambda r: (vstr(r).startswith("succeeds"), vstr(r)))


def f_full_balance(T, C_, U):
    tr, x = _tr_detail(T)
    if x is not None and x[0] > 0:
        return C("unprobeable", "phantom_balance: see fresh_address_nonzero_balance", ev_probe(tr, T.get("full_balance_transfer")))
    return from_row(T, "full_balance_transfer", lambda r: (r["status"] == "quirk", vstr(r)))


def f_max_tx(T, C_, U):
    return from_row(T, "max_tx_limit", lambda r: (r["status"] == "quirk", vstr(r)))


def f_decimals_value(T, C_, U):
    r = T.get("decimals")
    if r is None or r["status"] in UNP:
        return unp_cell(r, "decimals")
    return C("yes", str(r["value"]), ev_probe(r))


def f_decimals_low(T, C_, U):
    return from_row(T, "decimals", lambda r: (isinstance(r["value"], int) and r["value"] <= 5, str(r["value"])))


def f_decimals_high(T, C_, U):
    return from_row(T, "decimals", lambda r: (isinstance(r["value"], int) and r["value"] > 18, str(r["value"])))


def f_metadata_nonstring(T, C_, U):
    def fn(r):
        v = r["value"]
        k = (v.get("name_kind"), v.get("symbol_kind")) if isinstance(v, dict) else None
        return r["status"] == "quirk", f"{k}"
    return from_row(T, "name_symbol", fn)


INJ_RX = re.compile(r"[<>`]|javascript:|\bscript\b|on(error|load)\s*=|[\x00-\x08\x0b-\x1f\x7f]|\$\{", re.I)


def f_name_injection(T, C_, U):
    def fn(r):
        v = r["value"]
        txt = f"{v.get('name', '')}\u0001{v.get('symbol', '')}" if isinstance(v, dict) else ""
        m = INJ_RX.search(txt.replace("\u0001", " "))
        return bool(m), repr(m.group(0)) if m else "clean"
    return from_row(T, "name_symbol", fn)


def f_permit_kind(T, C_, U):
    r = T.get("permit")
    if r is None or r["status"] in UNP:
        return unp_cell(r, "permit")
    v = vstr(r)
    return C("yes" if v != "none" else "no", v.split("(")[0], ev_probe(r))


def f_permit_dai(T, C_, U):
    return from_row(T, "permit", lambda r: (vstr(r) == "dai_permit", vstr(r)))


PAUSE_STRICT = {"pause()", "unpause()", "paused()", "setPaused(bool)", "pauseTransfers()", "unpauseTransfers()", "togglePause()",
                "pause(bool)", "emergencyStop()", "stop()", "pausedPublic()"}
PAUSE_ACC_STRICT = {"paused()", "isPaused()", "stopped()", "isStopped()", "isTransferPaused()", "transfersPaused()", "transferPaused()",
                    "emergencyStop()", "pausedPublic()", "pausedOwnerAdmin()", "tradingPaused()", "pausedTransfers()"}


def _pause_sets(r):
    v = r["value"]; d = r.get("detail") or {}
    acc = set((v.get("accessors") or {}).keys()) if isinstance(v, dict) else set()
    sel = set((d.get("pause_selectors_in_code") or {}).keys())
    return acc, sel


def f_pause_cap(T, C_, U):
    def fn(r):
        acc, sel = _pause_sets(r)
        strict = (acc & PAUSE_ACC_STRICT) | (sel & PAUSE_STRICT)
        return bool(strict), ",".join(sorted(strict)) or "none"
    return from_row(T, "pause", fn)


def f_trading_gate(T, C_, U):
    def fn(r):
        acc, sel = _pause_sets(r)
        other = (sel - PAUSE_STRICT) | (acc - PAUSE_ACC_STRICT)
        return bool(other), ",".join(sorted(other)) or "none"
    return from_row(T, "pause", fn)


def f_paused_now(T, C_, U):
    def fn(r):
        v = r["value"]
        acc = (v.get("accessors") or {}) if isinstance(v, dict) else {}
        paused = any(x == 1 for k, x in acc.items() if k in PAUSE_ACC_STRICT)
        return paused, json_short(v.get("accessors")) if isinstance(v, dict) else ""
    return from_row(T, "pause", fn)


def f_pause_w1(T, C_, U):
    return from_row(T, "pause", lambda r: (r["status"] in ("quirk", "fail"), r["status"]))


BOT_NAMES = {"isBot", "bots", "addBots", "setBots", "delBot"}


def f_block_cap(T, C_, U):
    def fn(r):
        v = r["value"]
        names = (set(v.get("view_functions", [])) | set(v.get("selectors_in_code", []))) if isinstance(v, dict) else set()
        strict = {n for n in names if n not in BOT_NAMES}
        return bool(strict), ",".join(sorted(strict))[:70] or "none"
    return from_row(T, "blocklist", fn)


def f_bot_cap(T, C_, U):
    def fn(r):
        v = r["value"]
        names = (set(v.get("view_functions", [])) | set(v.get("selectors_in_code", []))) if isinstance(v, dict) else set()
        b = names & BOT_NAMES
        return bool(b), ",".join(sorted(b)) or "none"
    return from_row(T, "blocklist", fn)


def f_block_view_answers(T, C_, U):
    def fn(r):
        v = r["value"]
        vf = [n for n in v.get("view_functions", []) if n not in BOT_NAMES] if isinstance(v, dict) else []
        return bool(vf), ",".join(vf) or "none"
    return from_row(T, "blocklist", fn)


def _sanc(r, addr_idx):
    d = (r.get("detail") or {}).get("sanctioned_transfer_reverts") or {}
    ks = list(d.keys())
    if len(ks) <= addr_idx:
        return None
    return d[ks[addr_idx]]


def f_sanction_block_ronin(T, C_, U):
    def fn(r):
        x = _sanc(r, 1)
        return (x is True), {True: "reverts", False: "ok", None: "n/a"}[x]
    r = T.get("blocklist")
    if r is None or r["status"] in UNP:
        return unp_cell(r, "blocklist")
    x = _sanc(r, 1)
    if x is None:
        return C("unprobeable", "base_transfer_reverts_or_not_run", ev_probe(r))
    return C("yes" if x else "no", "reverts" if x else "ok", ev_probe(r))


def f_sanction_block_tornado(T, C_, U):
    r = T.get("blocklist")
    if r is None or r["status"] in UNP:
        return unp_cell(r, "blocklist")
    x = _sanc(r, 0)
    if x is None:
        return C("unprobeable", "base_transfer_reverts_or_not_run", ev_probe(r))
    return C("yes" if x else "no", "reverts" if x else "ok", ev_probe(r))


def f_block_any(T, C_, U):
    cap = f_block_cap(T, C_, U)
    r1, r0 = f_sanction_block_ronin(T, C_, U), f_sanction_block_tornado(T, C_, U)
    cells = [cap, r1, r0]
    ev = ev_probe(T.get("blocklist"))
    if any(c[0] == "yes" for c in cells):
        why = [n for n, c in zip(("capability", "ronin_revert", "tornado_revert"), cells) if c[0] == "yes"]
        return C("yes", "+".join(why), ev)
    if cap[0] == "no":
        return C("no", f"cap=no,dyn={r1[0]}", ev)
    return C(cap[0], cap[1], ev)


def f_share_ratio(T, C_, U):
    def fn(r):
        ratio = (r.get("detail") or {}).get("slot_to_balance_ratio")
        if ratio is not None and float(ratio) != 1.0 and is_power_of_two_ratio(ratio):
            return False, f"storage_packing_power_of_two(balance/slot={ratio})"
        v = r["value"]
        ev = v.get("evidence") if isinstance(v, dict) else None
        if isinstance(ev, list):
            for e in ev:
                if e.startswith("balance_is_not_the_raw_slot_value"):
                    return True, e[len("balance_is_not_the_raw_slot_value("):].split(":")[0]
        return False, "slot_identity" if isinstance(v, dict) else vstr(r)
    return from_row(T, "rebasing", fn)


def is_power_of_two_ratio(ratio):
    """Binary shifts of packed storage fields are not evidence of share accounting."""
    ratio = float(ratio)
    if not math.isfinite(ratio) or ratio <= 0:
        return False
    mantissa, _ = math.frexp(ratio)
    # Packed low flag bits and integer calibration can perturb a binary shift by
    # a few parts in 1e10 (AUSD on BSC). Allow 1e-8 relative rounding, not an
    # absolute tolerance that would erase genuine tiny decimal share ratios.
    return (math.isclose(mantissa, 0.5, rel_tol=1e-8, abs_tol=0)
            or math.isclose(mantissa, 1.0, rel_tol=1e-8, abs_tol=0))


def f_share_capability(T, C_, U):
    cells = [f_share_ratio(T, C_, U), f_share_accessors(T, C_, U)]
    if any(c[0] == "yes" for c in cells):
        return C("yes", "; ".join(c[1] for c in cells if c[0] == "yes"), ev_probe(T.get("rebasing")))
    if all(c[0] == "no" for c in cells):
        return C("no", "no_share_accounting_evidence", ev_probe(T.get("rebasing")))
    return next(c for c in cells if c[0] in UNP)


def f_share_accessors(T, C_, U):
    def fn(r):
        v = r["value"]
        ev = v.get("evidence") if isinstance(v, dict) else None
        if isinstance(ev, list):
            for e in ev:
                if e.startswith("share/gons/reflection/scaled accessors present"):
                    return True, e.split(": ", 1)[-1]
        if vstr(r).startswith("accessor_evidence_only"):
            return True, vstr(r)
        return False, "none"
    return from_row(T, "rebasing", fn)


def f_drift(T, C_, U):
    r = T.get("rebasing")
    if r is None or r["status"] in UNP:
        return unp_cell(r, "rebasing")
    v = r["value"]
    if not isinstance(v, dict):
        return C("unprobeable", "drift_not_measured(" + vstr(r)[:40] + ")", ev_probe(r))
    d = v.get("drift")
    if d == "drift":
        return C("yes", "same_slot_value_different_balance_7d", ev_probe(r))
    if d == "no_drift":
        return C("no", "no_drift_7d", ev_probe(r))
    return C("unprobeable", _short(str(d), 60), ev_probe(r))


def f_rebasing_any(T, C_, U):
    # Compatibility union, never labelled observed drift in prevalence outputs.
    cells = [f_share_capability(T, C_, U), f_drift(T, C_, U)]
    if any(c[0] == "yes" for c in cells):
        return C("yes", "share_capability_or_observed_drift", ev_probe(T.get("rebasing")))
    if any(c[0] == "no" for c in cells):
        return C("no", "no_positive_share_or_drift_evidence", ev_probe(T.get("rebasing")))
    return cells[0]


def f_hook_callbacks(T, C_, U):
    def fn(r):
        v = vstr(r)
        return ("hook_called" in v), v[:70]
    return from_row(T, "erc777_hooks", fn)


def f_erc777_static(T, C_, U):
    def fn(r):
        v = vstr(r)
        return ("registered_ERC777" in v or "erc777_accessor" in v), v[:70]
    return from_row(T, "erc777_hooks", fn)


def f_contract_callback(T, C_, U):
    return from_row(T, "contract_recipient", lambda r: ("calls_back_into_recipient_contract" in vstr(r), vstr(r)[:70]))


def f_contract_recipient_reverts(T, C_, U):
    return from_row(T, "contract_recipient", lambda r: (vstr(r).startswith("reverts_to_plain_contract_recipient"), vstr(r)[:70]))


def f_gas(T, C_, U):
    r = T.get("gas_transfer")
    if r is None or r["status"] in UNP:
        return unp_cell(r, "gas_transfer")
    return C("yes" if r["status"] == "quirk" else "no", str(r["value"]), ev_probe(r))


def f_anti_bot(T, C_, U):
    def fn(r):
        v = r["value"]
        if isinstance(v, dict):
            return True, ",".join(list(v.get("classes", {}))[:3] + list(v.get("accessors", {}))[:4])
        return False, vstr(r)
    return from_row(T, "anti_bot_limits", fn)


def f_cooldown(T, C_, U):
    return from_row(T, "same_block_repeat", lambda r: (r["status"] == "quirk", vstr(r)[:60]))


def f_eoa_vs_contract(T, C_, U):
    return from_row(T, "eoa_vs_contract", lambda r: (r["status"] == "quirk", vstr(r)))


def f_forwarding(T, C_, U):
    return from_row(T, "forwarding", lambda r: (r["status"] == "quirk", json_short(r["value"])))


def f_external_storage(T, C_, U):
    def fn(r):
        v = vstr(r)
        return v.startswith("external_storage"), v[:50]
    return from_row(T, "balance_storage", fn)


def f_native_repr(T, C_, U):
    return from_row(T, "balance_storage", lambda r: (vstr(r) == "native_balance_of_address", vstr(r)))


FLASH_SELS = {"5cffe9de": "flashLoan(IERC3156FlashBorrower,address,uint256,bytes)", "613255ab": "maxFlashLoan(address)",
              "d9d98ce4": "flashFee(address,uint256)"}


def f_flash_mint(T, C_, U):
    r = T.get("bytecode_selectors")
    if r is None or r["status"] in UNP:
        return unp_cell(r, "bytecode_selectors")
    if r["status"] == "quirk":
        return C("unprobeable", "no_evm_code(0xef)", ev_probe(r))
    d = r.get("detail") or {}
    sels = set(d.get("own_selectors") or []) | set(d.get("impl_selectors") or [])
    hit = sorted(FLASH_SELS[s].split("(")[0] for s in sels if s in FLASH_SELS)
    return C("yes" if "flashLoan" in hit or "maxFlashLoan" in hit else "no", ",".join(hit) or "none", ev_probe(r))


def f_funding(T, C_, U):
    r = T.get("funding")
    if r is None:
        return C("unprobeable", "no_row", None)
    return C("yes" if r["status"] == "pass" else r["status"], vstr(r)[:60], ev_probe(r))


# ---------------------------------------------------------------- control (W3)
SEV = ["eoa", "eoa_7702", "multisig_no_timelock", "timelock", "governor_dao", "contract_other_or_unresolved", "l1_alias", "renounced", "authority_unverified"]


def path_class(p):
    kinds = p["kinds"]; term = p["terminal_kind"]
    has_tl = "timelock" in kinds or bool(p.get("access_manager_delay_seconds"))
    if term.startswith("truncated"):
        return "contract_other_or_unresolved"
    if term == "eoa":
        return "eoa" if not has_tl else "timelock"
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


def weakest(paths):
    """weakest class over firm paths (same rule as scripts/control/summarize.py)."""
    if not paths:
        return None
    firm = [p for p in paths if not p.get("assumed_or_unverified")]
    if not firm:
        return "authority_unverified"
    return min((path_class(p) for p in firm), key=lambda c: SEV.index(c))


def _collect_ev(obj, out, limit=6):
    if len(out) >= limit:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "ev":
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, list):
                    out.extend(x for x in v if isinstance(x, str))
            else:
                _collect_ev(v, out, limit)
    elif isinstance(obj, list):
        for v in obj:
            _collect_ev(v, out, limit)


def ev_ctl(row, *parts):
    if row is None:
        return None
    out = []
    for p in parts:
        _collect_ev(p, out)
    out = list(dict.fromkeys(out))[:6]
    return {"src": "control", "file": None, "refs": out, "block": row.get("block")}


def ctl_unavailable(C_):
    if C_ is None:
        return C("unprobeable", "no_control_row", None)
    if C_.get("status") != "ok":
        return C("unprobeable", f"control_status:{C_.get('status')}", ev_ctl(C_))
    if C_.get("completeness") != "complete":
        return C("unprobeable", "control_partial(" + _short("; ".join(e.get("msg", "")[:40] for e in (C_.get("probe_errors") or [])[:1]), 50) + ")", ev_ctl(C_))
    return None


def ctl(fn):
    def f(T, C_, U):
        bad = ctl_unavailable(C_)
        if bad:
            return bad
        return fn(C_)
    return f


def c_is_proxy(r):
    v = r.get("is_proxy")
    if v is None:
        return C("unprobeable", "proxy_status_unresolved", ev_ctl(r, r.get("code")))
    return C("yes" if v else "no", r.get("proxy_type") or "not_proxy", ev_ctl(r, r.get("code")))


def c_upgradeable(r):
    u = r.get("upgradeable")
    ev = ev_ctl(r, r.get("upgrade_paths"), r.get("upgrade_admin_chain"), r.get("code"))
    if u == "yes":
        paths = r.get("upgrade_paths") or []
        w = weakest(paths) or "none"
        p = next((p for p in paths if not p.get("assumed_or_unverified") and path_class(p) == w), {})
        # W3's timelock_delay_seconds already includes AccessManager delay.
        delay = p.get("timelock_delay_seconds") or p.get("access_manager_delay_seconds") or 0
        val = f"authority: {w}"
        if delay:
            val += f"; delay={delay}s"
        if r.get("upgrade_path_type") == "legacy_deprecate_forward":
            val += "; legacy deprecate-forward"
        else:
            val += "; " + p.get("terminal_summary", "unverified")
        return C("yes", val, ev)
    if u in ("no", "frozen", "protocol_only"):
        return C("no", u, ev)
    return C("unprobeable", f"upgradeable={u}", ev)


def c_upgrade_class(r):
    if r.get("upgradeable") != "yes":
        return C("no", str(r.get("upgradeable")), ev_ctl(r))
    w = weakest(r.get("upgrade_paths") or [])
    return C("yes", w or "none", ev_ctl(r, r.get("upgrade_paths")))


def mk_upgrade_class_in(classes, label):
    def f(r):
        if r.get("upgradeable") != "yes":
            return C("no", f"upgradeable={r.get('upgradeable')}", ev_ctl(r))
        w = weakest(r.get("upgrade_paths") or [])
        if w == "authority_unverified":
            return C("unprobeable", w, ev_ctl(r, r.get("upgrade_paths")))
        return C("yes" if w in classes else "no", w or "none", ev_ctl(r, r.get("upgrade_paths"), r.get("upgrade_admin_chain")))
    return f


def c_owner_class(r):
    p = r.get("owner_paths") or []
    if not p:
        return C("no", "no_owner_path", ev_ctl(r))
    return C("yes", weakest(p) or "none", ev_ctl(r, p))


def c_owner_eoa(r):
    p = r.get("owner_paths") or []
    firm = [x for x in p if not x.get("assumed_or_unverified") and x["terminal_kind"] in ("eoa", "eoa_7702")
            and "timelock" not in x["kinds"] and not x.get("access_manager_delay_seconds")]
    return C("yes" if firm else "no", ",".join(x["terminal_address"][:10] for x in firm) or "none", ev_ctl(r, p))


def c_owner_renounced(r):
    p = r.get("owner_paths") or []
    if not p:
        return C("no", "no_owner_path", ev_ctl(r))
    return C("yes" if weakest(p) == "renounced" else "no", weakest(p) or "", ev_ctl(r, p))


def c_control_summary(r):
    return C("yes" if r.get("control_summary") == "controlled" else "no", str(r.get("control_summary")), ev_ctl(r))


def mk_hint(name):
    def f(r):
        h = (r.get("capability_hints") or {}).get(name)
        return C("yes" if h else "no", ",".join(h)[:60] if h else "none", ev_ctl(r, r.get("code")))
    return f


def mk_role_eoa(role):
    def f(r):
        hs = (r.get("roles") or {}).get(role) or []
        if not hs:
            return C("no", "no_holder_found", ev_ctl(r))
        eoa = [h for h in hs if any(t in ("eoa", "eoa_7702") for t in h.get("terminal_kinds", []))]
        return C("yes" if eoa else "no", f"{len(hs)} holders, {len(eoa)} EOA", ev_ctl(r, hs))
    return f


def c_paused_ctl(r):
    return C("yes" if r.get("paused") is True else "no", str(r.get("paused")), ev_ctl(r))


# ---------------------------------------------------------------- registry
# (id, family, kind, label, fn, dxo-tag or None)
F = []


NATIVE_SENSITIVE = {"transfer_return", "transferFrom", "approve", "zero_self", "large_amounts", "fee_on_transfer", "hooks", "permissioned"}


def _native_guard(fn):
    """The Polygon 0x..1010 facade (POL) answers ERC-20 calls but needs a value transfer: its transfer semantics are not those of an ERC-20
    contract (scripts/probe/README.md). Transfer-behaviour features are unprobeable for it rather than counted."""
    def g(T, C_, U):
        b = T.get("balance_storage")
        if b is not None and isinstance(b.get("value"), str) and b["value"] == "native_balance_of_address":
            return C("unprobeable", "native_gas_token_facade", ev_probe(b))
        return fn(T, C_, U)
    return g


def reg(id_, family, kind, label, fn, src="probe"):
    if src == "probe" and family in NATIVE_SENSITIVE:
        fn = _native_guard(fn)
    F.append({"id": id_, "family": family, "kind": kind, "label": label, "fn": fn, "src": src})


reg("funding", "meta", "meta", "How the harness funded the probe account", f_funding)
# transfer return
reg("ret_missing_transfer", "transfer_return", "observed", "transfer() returns no value (USDT-style)", f_transfer_missing)
reg("ret_missing_approve", "approve", "observed", "approve() returns no value", f_approve_missing)
reg("ret_missing_transferFrom", "transferFrom", "observed", "transferFrom() returns no value", f_tf_missing)
reg("false_on_failure", "transfer_return", "observed", "invalid transfer/transferFrom returns false instead of reverting", f_false_on_failure)
reg("recipient_not_credited", "transfer_return", "observed", "transfer succeeds but the recipient balance does not increase ('lies')", f_not_credited)
reg("fresh_address_nonzero_balance", "transfer_return", "observed", "balanceOf(never-used address) is nonzero (phantom balance)", f_fresh_nonzero)
reg("transfer_reverts_fresh_contract", "permissioned", "observed", "plain transfer from an unknown funded contract reverts", f_transfer_reverts_fresh)
reg("permissioned_transfer", "permissioned", "observed", "transfer reverts with a permissioning/KYC/policy/registry reason", mk_class_feature({"permissioned"}, "permissioned"))
reg("transfer_reverts_paused", "permissioned", "observed", "transfer reverts with a paused reason", mk_class_feature({"paused", "suspended"}, "paused"))
reg("transfer_reverts_market_or_router", "permissioned", "observed", "transfer reverts: market closed / router-restricted", mk_class_feature({"market_closed", "router_restricted"}, "venue"))
# fee
reg("fee_on_transfer_observed", "fee_on_transfer", "observed", "recipient receives less than sent / sender debited differently (live)", f_fee_on_transfer)
reg("transfer_rounding", "fee_on_transfer", "observed", "debit/credit differs from the requested amount by 1-2 base units (share-based rounding)", f_rounding)
reg("fee_switch_capability", "fee_on_transfer", "capability", "fee/tax accessor exists (may be 0 now)", f_fee_switch)
reg("fee_param_nonzero", "fee_on_transfer", "observed", "a fee/tax accessor returns nonzero now", f_fee_param_nonzero)
# approve
reg("approve_race_guard", "approve", "observed", "approve(nonzero->nonzero) reverts (USDT/KNC race protection)", f_approve_race)
reg("approve_zero_addr_reverts", "approve", "observed", "approve(address(0)) reverts", f_approve_zero_addr_reverts)
reg("approve_zero_value_reverts", "approve", "observed", "approve(0) reverts", f_approve_zero_value_reverts)
reg("approve_max_capped", "approve", "observed", "approve(uint256.max) reverts or is capped (uint96)", f_approve_max_capped)
reg("allowance_helpers", "approve", "capability", "increaseAllowance/decreaseAllowance (or *Approval) work", f_allowance_helpers)
# transferFrom
reg("tf_owner_no_allowance", "transferFrom", "observed", "owner can transferFrom own tokens without allowance (DSToken)", f_tf_owner_no_allowance)
reg("allowance_max_decrements", "transferFrom", "observed", "uint256.max allowance is decremented (not infinite)", f_infinite_decrements)
reg("tf_zero_reverts", "transferFrom", "observed", "transferFrom(0) reverts", f_tf_zero_reverts)
# zero/self
reg("transfer_zero_reverts", "zero_self", "observed", "transfer(0) reverts", f_transfer_zero_reverts)
reg("transfer_zero_returns_false", "zero_self", "observed", "transfer(0) returns false", f_transfer_zero_false)
reg("to_zero_reverts", "zero_self", "observed", "transfer to address(0) reverts", f_to_zero_reverts)
reg("to_zero_burns", "zero_self", "observed", "transfer to address(0) allowed and reduces totalSupply", f_to_zero_burns)
reg("self_transfer_anomaly", "zero_self", "observed", "self-transfer reverts or changes balance/supply", f_self_transfer)
reg("self_transfer_changes_balance", "zero_self", "observed", "self-transfer changes the balance", f_self_transfer_changes)
reg("to_token_contract_reverts", "zero_self", "observed", "transfer to the token's own address reverts", f_to_token_reverts)
reg("to_dead_reverts", "zero_self", "observed", "transfer to 0x..dEaD reverts", f_to_dead_reverts)
# large
reg("uint96_limit", "large_amounts", "observed", "balance field narrower than 256 bits (uint96/104/128)", f_uint96)
reg("balance_field_le128", "large_amounts", "observed", "balance storage field is 97-128 bits wide (uint104/uint128): transfers above 2^bits revert", f_uint128)
reg("max_uint_transfer_succeeds", "large_amounts", "observed", "transfer(uint256.max) succeeds (cUSDCv3-style 'max = all')", f_max_uint_transfer)
reg("full_balance_anomaly", "large_amounts", "observed", "transfer of entire balance reverts/leaves remainder/returns false", f_full_balance)
reg("max_tx_limit", "large_amounts", "observed", "5%-of-supply transfer reverts (max-tx / anti-whale)", f_max_tx)
# metadata
reg("decimals", "metadata", "meta", "decimals() value", f_decimals_value)
reg("decimals_low", "metadata", "observed", "decimals <= 5", f_decimals_low)
reg("decimals_high", "metadata", "observed", "decimals > 18", f_decimals_high)
reg("metadata_nonstring", "metadata", "observed", "name/symbol as bytes32 or missing", f_metadata_nonstring)
reg("name_injection", "metadata", "observed", "name/symbol contains markup/quote/control characters", f_name_injection)
reg("permit_any", "metadata", "capability", "permit() present (EIP-2612 or DAI-style)", f_permit_kind)
reg("permit_dai_style", "metadata", "capability", "DAI-style permit (non-2612 signature)", f_permit_dai)
# pause
reg("pause_capability", "pause", "capability", "pause()/paused() or equivalent accessor/selector present (strict)", f_pause_cap)
reg("trading_gate_capability", "pause", "capability", "enableTrading/openTrading/setTransfersEnabled style gate present", f_trading_gate)
reg("paused_now", "pause", "observed", "pause accessor returns true now", f_paused_now)
reg("pause_w1_any", "pause", "capability", "W1 definition: any pause/trading-gate accessor or selector", f_pause_w1)
# blocklist
reg("blocklist_capability", "blocklist", "capability", "blacklist/freeze/blocklist selector or view present (strict, excludes anti-bot lists)", f_block_cap)
reg("blocklist_view_answers", "blocklist", "observed", "a blocklist view function answers an eth_call", f_block_view_answers)
reg("botlist_capability", "blocklist", "capability", "anti-bot list (isBot/addBots) present", f_bot_cap)
reg("blocklist_any", "blocklist", "observed", "blocklist capability OR observed sanctions-address revert (union)", f_block_any)
reg("sanction_block_ronin", "blocklist", "observed", "transfer to the Ronin-hack (Lazarus) address reverts while a normal transfer works", f_sanction_block_ronin)
reg("sanction_block_tornado", "blocklist", "observed", "transfer to the Tornado Cash router reverts while a normal transfer works", f_sanction_block_tornado)
# rebasing
reg("share_accounting_ratio", "rebasing", "capability", "non-binary-shift balance/slot ratio (share-accounting evidence)", f_share_ratio)
reg("share_accessors", "rebasing", "capability", "sharesOf/gons/scaledBalanceOf/tokenFromReflection accessor present", f_share_accessors)
reg("share_accounting_capability", "rebasing", "capability", "non-binary-shift slot ratio OR strong share/gons/scaled accessor; not observed drift", f_share_capability)
reg("balance_drift_7d", "rebasing", "observed", "same raw slot value gives a different balance 7 days apart where archive calls succeeded", f_drift)
reg("rebasing_w1_any", "rebasing", "capability", "compatibility union: corrected share capability OR observed drift (use separate rates)", f_rebasing_any)
# hooks
reg("hook_callbacks_observed", "hooks", "observed", "transfer triggers tokensReceived/tokensToSend-style hook calls", f_hook_callbacks)
reg("erc777_static", "hooks", "capability", "registered as ERC777 in ERC-1820 or exposes ERC777 accessors", f_erc777_static)
reg("callback_into_recipient", "hooks", "observed", "token calls back into a contract recipient (ERC-677/223 style)", f_contract_callback)
reg("contract_recipient_reverts", "hooks", "observed", "transfer to a plain contract recipient reverts", f_contract_recipient_reverts)
# gas
reg("gas_transfer", "gas", "observed", "gas of first transfer (>120k = quirk)", f_gas)
# misc
reg("anti_bot_controls", "other", "observed", "anti-bot / limit accessors or revert classes", f_anti_bot)
reg("same_block_cooldown", "other", "observed", "second transfer in the same block reverts", f_cooldown)
reg("eoa_vs_contract_differs", "other", "observed", "EOA sender and contract sender behave differently", f_eoa_vs_contract)
reg("forwarding_proxy", "other", "observed", "delegate()/target() accessor: calls forwarded to another contract (multiple addresses)", f_forwarding)
reg("external_balance_storage", "other", "observed", "balances live in a different contract than the token", f_external_storage)
reg("native_currency_repr", "other", "observed", "balanceOf is the native balance (ERC-20 facade of the gas token)", f_native_repr)
reg("flash_mint_capability", "other", "capability", "ERC-3156 flashLoan/maxFlashLoan selector present", f_flash_mint)
# control
reg("is_proxy", "control", "control", "token is a proxy / clone (W3)", ctl(c_is_proxy), "control")
reg("upgradeable", "control", "control", "an upgrade authority exists (W3; no_known_path/unknown -> unprobeable)", ctl(c_upgradeable), "control")
reg("upgrade_authority_class", "control", "meta", "weakest firm upgrade path class", ctl(c_upgrade_class), "control")
reg("upgrade_single_eoa", "control", "control", "upgradeable by a single EOA/7702 with no timelock on the path", ctl(mk_upgrade_class_in({"eoa", "eoa_7702"}, "eoa")), "control")
reg("upgrade_no_timelock", "control", "control", "upgradeable with no timelock (EOA, 7702 or multisig without delay)", ctl(mk_upgrade_class_in({"eoa", "eoa_7702", "multisig_no_timelock"}, "no_tl")), "control")
reg("owner_path_class", "control", "meta", "weakest class of the owner/admin/wards path", ctl(c_owner_class), "control")
reg("owner_single_eoa", "control", "control", "owner/admin path ends at a single EOA without timelock", ctl(c_owner_eoa), "control")
reg("owner_renounced", "control", "control", "owner path ends renounced (0x0/0xdead)", ctl(c_owner_renounced), "control")
reg("controlled", "control", "control", "has any privileged controller (control_summary=controlled)", ctl(c_control_summary), "control")
reg("mint_capability", "control", "capability", "mint selector present (hint)", ctl(mk_hint("mint")), "control")
reg("burn_from_others_capability", "control", "capability", "burn-from-others selector present (hint)", ctl(mk_hint("burn_from_others")), "control")
reg("rescue_capability", "control", "capability", "rescue-tokens selector present (hint)", ctl(mk_hint("rescue_tokens")), "control")
reg("blacklist_hint", "control", "capability", "W3 blacklist selector hint", ctl(mk_hint("blacklist")), "control")
reg("pauser_role_eoa", "control", "control", "a pauser role holder is a single EOA", ctl(mk_role_eoa("pauser")), "control")
reg("blacklister_role_eoa", "control", "control", "a blacklister role holder is a single EOA", ctl(mk_role_eoa("blacklister")), "control")
reg("minter_role_eoa", "control", "control", "a minter role holder is a single EOA", ctl(mk_role_eoa("minter")), "control")

FEATURES = {f["id"]: f for f in F}
FEATURE_ORDER = [f["id"] for f in F]

# d-xo/weird-erc20 section -> list of feature ids (ANY yes). Definitions and feature lists are recorded below.
DXO = [
    ("Reentrant calls (hooks)", ["hook_callbacks_observed", "erc777_static"], "observed hook call or ERC777 registration/accessors"),
    ("Missing return values", ["ret_missing_transfer", "ret_missing_approve", "ret_missing_transferFrom"], "transfer/approve/transferFrom returns no data"),
    ("Fee on transfer", ["fee_on_transfer_observed"], "live: recipient receives less / sender debited differently"),
    ("Balance modifications outside transfers (observed drift)", ["balance_drift_7d"], "same overridden raw slot value yields different balance 7 days apart; observed only where archive calls succeeded"),
    ("Share-accounting capability", ["share_accounting_capability"], "non-binary-shift slot ratio or strong share/gons/scaled accessor; capability, not observed drift"),
    ("Upgradable tokens", ["upgradeable"], "W3: an upgrade authority exists"),
    ("Flash mintable tokens", ["flash_mint_capability"], "ERC-3156 selector present (capability only)"),
    ("Tokens with blocklists", ["blocklist_any"], "strict blocklist capability OR tested Ronin-exploiter/formerly sanctioned Tornado-router revert; not a legal sanctions determination"),
    ("Pausable tokens", ["pause_capability"], "capability: strict pause accessor/selector"),
    ("Approval race protections", ["approve_race_guard"], "live: nonzero->nonzero approve reverts"),
    ("Revert on approval to zero address", ["approve_zero_addr_reverts"], "live"),
    ("Revert on zero-value approvals", ["approve_zero_value_reverts"], "live"),
    ("Revert on zero-value transfers", ["transfer_zero_reverts"], "live"),
    ("Multiple token addresses / forwarding", ["forwarding_proxy", "external_balance_storage"], "delegate()/target() or external balance storage"),
    ("Low decimals (<=5)", ["decimals_low"], "live"),
    ("High decimals (>18)", ["decimals_high"], "live"),
    ("transferFrom with src == msg.sender", ["tf_owner_no_allowance"], "live: owner needs no allowance"),
    ("Non-string metadata", ["metadata_nonstring"], "live: bytes32/missing name or symbol"),
    ("Revert on transfer to zero address", ["to_zero_reverts"], "live"),
    ("No revert on failure", ["false_on_failure"], "live: returns false instead of reverting"),
    ("Revert on large approvals/transfers (uint96)", ["uint96_limit", "approve_max_capped"], "live: uint96 revert string / <=96-bit balance field, or uint96-capped max approve"),
    ("Code injection via token name", ["name_injection"], "name/symbol contains markup, quotes or control chars"),
    ("Unusual permit (DAI-style)", ["permit_dai_style"], "capability"),
    ("Transfer of less than amount (max-uint = all)", ["max_uint_transfer_succeeds"], "live: transfer(uint256.max) succeeds"),
    ("ERC-20 representation of native currency", ["native_currency_repr"], "live: balanceOf is native balance"),
]


# custom-error selectors resolved by keccak match against a candidate list (scripts/matrix/findings.py, spot-verified; candidate spellings are recorded below)
SEL_NAMES = {"0x349e7009": "UserSanctioned()", "0x8ed6a70e": "UserBlocked()", "0x09550c77": "Blacklisted()", "0xd33f19e7": "BlacklistedAddress(address)",
             "0xae170cc2": "AddressBlocked()", "0x6bc671fd": "AccountBlocked()", "0x571f7b49": "AccountBlacklisted(address)",
             "0xa6add803": "RecipientBlacklisted()", "0x4f2a367e": "AccountFrozen(address)", "0x1fd1cc44": "AddressFrozen()",
             "0xec442f05": "ERC20InvalidReceiver(address)", "0x8e4a23d6": "Unauthorized(address)", "0xe450d38c": "ERC20InsufficientBalance(address,uint256,uint256)"}
