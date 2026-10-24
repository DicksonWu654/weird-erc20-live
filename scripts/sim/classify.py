"""Flags + primary label from a simulation result (see README for the rules and precedence).

Thresholds (module constants, recorded into every output line as `rules`):
  TAX_NOISE   0.5%   taxes below this are treated as 0 (rounding / swap-back price-impact noise)
  HIGH_TAX    10%    max(buy tax, sell tax, transfer tax) >= this -> high_tax. (Plain "fee-on-transfer" designs are typically <=5%;
                     golden expectations are recorded in data/sim-golden/expected.csv.)
  HONEYPOT_TAX 95%   sell tax >= this == funds confiscated == honeypot
"""
RULES = {"TAX_NOISE": 0.005, "HIGH_TAX": 0.10, "EXTREME_TAX": 0.50, "HONEYPOT_TAX": 0.95,
         "DUST_FLOOR_WEI": 10_000_000_000_000_000, "TAX_BOUNDARY_EPS": 0.0001,
         "V4_NONSTANDARD_LP_FEE": 0.01, "SELL_GAS_CAP": 2_500_000,
         "LIQUIDITY_UNKNOWN_POLICY": "unprobeable", "REPLAY_MISMATCH_POLICY": "unprobeable",
         "V4_PRIMARY_LIQUIDITY": "all_position_principal_at_sim_price",
         "ZERO_QUOTE_POLICY": "known_zero_without_conversion",
         "version": "3.3", "estimand": "tier2_v2.1"}
# Only "no liquidity" is pool state.  INSUFFICIENT_INPUT_AMOUNT in the fee-on-transfer sell path means the pair received 0 tokens
# (token swallowed the transfer == 100% sell tax); INSUFFICIENT_OUTPUT_AMOUNT means the swap output rounded to 0.  Both are token-side.
PAIR_MATH = ("INSUFFICIENT_LIQUIDITY",)
PRECEDENCE = ["no_meaningful_liquidity", "could_not_buy", "unprobeable", "v3_fot_incompatible",
              "honeypot", "rugged", "high_tax", "owner_controlled", "normal"]


def _t(x):
    return 0.0 if (x is None or x < RULES["TAX_NOISE"]) else x


def _near_high_tax(x):
    return abs(x-RULES['HIGH_TAX']) <= RULES['TAX_BOUNDARY_EPS']+1e-12


def measured_quote_eth_equiv(pi):
    """Resolve only measured quote amounts; zero needs no exchange rate."""
    if (pi.get('kind') == 'v4' and pi.get('quote_liquidity_method') == 'v4_active_virtual_quote_amount'
            and not pi.get('virtual_liquidity_sensitivity')):
        return None
    reserve=pi.get('quote_liquidity_eth_equiv_wei')
    if reserve is not None:return int(reserve)
    raw=pi.get('reserve_quote')
    quote=(pi.get('quote') or '').lower()
    raw_measured=(pi.get('kind')!='v4' or pi.get('quote_liquidity_method')=='v4_all_position_principal_at_sim_price'
                  or pi.get('virtual_liquidity_sensitivity'))
    if raw_measured and raw is not None and (int(raw)==0 or quote in (
            '0x0000000000000000000000000000000000000000',
            '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2')):
        return int(raw)
    return None


def classify(res, control=None, rug=None, dust_floor_wei=None):
    """res: pipeline result dict.  control: control.analyse dict.  rug: rug_verdict dict.
    Returns (flags:list, primary:str, reason:str, taxes:dict)."""
    flags, taxes = [], {}
    rugv = (rug or {}).get("verdict")
    rugged = rugv in ("rug_lp_pull", "rug_lp_pull_unattributed")
    if rugv:
        flags.append("rug:" + rugv)
    if rugged:
        flags.append("rugged")
    if rugv == "lp_pulled_late":
        flags.append("lp_pulled_late")
    if rugv == "died_by_sells":
        flags.append("died_by_sells")

    ctl_flag = False
    if control:
        if control.get("risky_categories"):
            if control.get("owner_state") in ("live_eoa", "live_contract", "live", "unknown"):
                ctl_flag = True
            elif control.get("owner_state") == "none":
                flags.append("admin_fn_without_owner")  # custom/role-based admin: W3's classifier decides
        flags += ["owner:" + str(control.get("owner_state"))]
        flags += ["fn:" + c for c in control.get("risky_categories", [])]
        if "proxy_like" in control.get("flags", []):
            flags.append("proxy_like")
    if ctl_flag:
        flags.append("owner_controlled")

    primary, reason = None, ""
    floor = RULES["DUST_FLOOR_WEI"] if dust_floor_wei is None else dust_floor_wei
    pi = res.get("pool_info", {}) or {}
    # A reserve is comparable to wei only for a native ETH or WETH quote.
    # V4's singleton balance and liquidity value are not pool quote reserves.
    reserve = measured_quote_eth_equiv(pi)
    if pi.get("kind") == "v4":
        lp_fee = pi.get("current_lp_fee", pi.get("fee"))
        if lp_fee is not None and 0 <= int(lp_fee) <= 1_000_000:
            taxes["lp_fee"] = int(lp_fee) / 1_000_000
            taxes["lp_fee_round_trip_cost"] = 1 - (1-taxes["lp_fee"])**2
            if taxes["lp_fee"] > RULES["V4_NONSTANDARD_LP_FEE"]:
                flags.append("v4_nonstandard_lp_fee_round_trip_cost")
    if floor and reserve is not None and int(reserve) < floor:
        return flags + ["no_meaningful_liquidity"], "no_meaningful_liquidity", "quote_liquidity_below_floor", taxes
    if floor and reserve is None:
        return flags + ["unprobeable:liquidity_not_measured"], "unprobeable", "liquidity_not_measured", taxes
    if res.get('replay_consistent') is False:
        return flags+['unprobeable:buy_replay_mismatch'],'unprobeable','buy_replay_mismatch',taxes
    def known_tax_despite_missing_exit(why):
        # A failed exit probe does not erase an independently observed tax
        # paid on a positive-output buy or wallet transfer. It does prevent
        # attributing that exit failure to the token as a honeypot.
        b=res.get('buy') or {};x=res.get('transfer') or {}
        bt=_t(b.get('tax')) if b.get('ok') and (b.get('received') or 0)>0 else 0.
        tt=_t(x.get('tax')) if x.get('ok') and (x.get('sent') or 0)>0 else 0.
        if max(bt,tt)>=RULES['HIGH_TAX']:
            taxes.update(buy=bt,transfer=tt)
            ff=flags+['high_tax','exit_unresolved']
            if any(_near_high_tax(t) for t in (bt,tt)):
                ff.append('tax_threshold_boundary')
            return ff,'high_tax','observed high tax; '+why,taxes
        return None
    buy_confiscatory = False
    if res.get("unprobeable"):
        flags.append("unprobeable:" + res["unprobeable"].split(":")[0])
        if res["unprobeable"].startswith("buy_reverts:"):
            return flags + ["could_not_buy"], "could_not_buy", res["unprobeable"], taxes
        if not rugged:
            observed=known_tax_despite_missing_exit(res['unprobeable'])
            if observed:return observed
        if rugged:
            primary, reason = "rugged", f"no sim ({res['unprobeable']}); liquidity pulled by insiders"
        else:
            return flags, "unprobeable", res["unprobeable"], taxes

    buy, xfer, sells = res.get("buy"), res.get("transfer"), res.get("sells", [])
    if pi.get("kind") == "v4":
        hooks = pi.get("hooks")
        if hooks and str(hooks).lower() != "0x" + "0" * 40:
            flags.append("v4_hook")
            if pi.get("hook_permissions"):
                flags += ["v4_hook_permission:" + str(x) for x in pi["hook_permissions"]]
        origin = pi.get("hook_revert_origin")
        if origin == "hook":
            flags.append("v4_hook_revert")
        elif origin == "token":
            flags.append("v4_token_revert")
    if buy and buy.get("ok"):
        flags.append("buy_ok")
        if not (buy.get("received") or 0) > 0:
            why = "zero_output_buy" if (buy.get("expected_out") or 0) > 0 else "no_active_liquidity"
            return flags + ["unprobeable:" + why], "unprobeable", why, taxes
        bt = _t(buy.get("tax"))
        taxes["buy"] = bt
        if _near_high_tax(bt):
            flags.append("tax_threshold_boundary")
        if bt >= RULES["HIGH_TAX"]:
            flags.append("buy_tax_high")
        buy_confiscatory = bt >= RULES["HONEYPOT_TAX"]
        if buy_confiscatory:
            flags.append("buy_tax_ge95")
    if xfer:
        if not xfer["ok"]:
            flags.append("transfer_blocked")
        else:
            tt = _t(xfer.get("tax"))
            taxes["transfer"] = tt
            if _near_high_tax(tt):
                flags.append("tax_threshold_boundary")
            if tt >= RULES["HIGH_TAX"]:
                flags.append("transfer_tax_high")
            if xfer.get("returned_false"):
                flags.append("transfer_returns_false")
            if xfer.get("sender_debit") is not None and xfer["sent"] and xfer["sender_debit"] > xfer["sent"] * 1.005:
                flags.append("transfer_overdebit")
    ap = res.get("approve") or {}
    if ap and not ap.get("ok"):
        flags.append("approve_reverts")
    first = next((s for s in sells if s["id"] == "s10"), None)
    ok_sells = [s for s in sells if s.get("ok") and (s.get("amount") or 0) > 0 and (s.get("expected_eth") or 0) > 0]
    honeypot = False
    quoted_sells = [s for s in sells if (s.get("amount") or 0) > 0 and (s.get("expected_eth") or 0) > 0]
    if sells and not quoted_sells:
        observed=known_tax_despite_missing_exit('no_positive_sell_quote')
        if observed:return observed
        return flags + ["unprobeable:no_positive_sell_quote"], "unprobeable", "no_positive_sell_quote", taxes
    if quoted_sells:
        if not ok_sells:
            reasons = {s.get("reason") for s in quoted_sells}
            if all(any(p in (s.get("reason") or "") for p in PAIR_MATH) for s in quoted_sells):
                observed=known_tax_despite_missing_exit('sell_pool_math')
                if observed:return observed
                return flags + ["sell_pool_math"], primary or "unprobeable", "sell_pool_math:" + sorted(reasons)[0], taxes
            if any(s.get("revert_origin") in ("router", "out_of_gas") or
                   (s.get("gas") or 0) >= RULES["SELL_GAS_CAP"] * .98 for s in quoted_sells):
                observed=known_tax_despite_missing_exit('sell_router_or_gas_failure')
                if observed:return observed
                return flags + ["sell_origin_unresolved"], "unprobeable", "sell_router_or_gas_failure", taxes
            honeypot = True
            flags += ["sell_reverts_all"]
            if any("INSUFFICIENT_INPUT_AMOUNT" in (r or "") for r in reasons):
                flags.append("sell_swallowed_transfer")
            if any("INSUFFICIENT_OUTPUT_AMOUNT" in (r or "") for r in reasons):
                flags.append("sell_zero_output")
            taxes["sell"] = None
            reason = f"all {len(quoted_sells)} quoted sell attempts revert: {sorted(r for r in reasons if r)[:2]}"
            if res.get("pool_info", {}).get("kind") == "v3" and any((s["reason"] or "") in ("IIA", "STF") or "IIA" in (s["reason"] or "") for s in sells) \
                    and (taxes.get("buy", 0) > 0 or taxes.get("transfer", 0) > 0):
                flags.append("v3_fot_incompatible")
                if max(taxes.get("buy") or 0, taxes.get("transfer") or 0) < RULES["HIGH_TAX"]:
                    return flags, "v3_fot_incompatible", "V3 rejects fee-on-transfer settlement", taxes
                honeypot = False
        else:
            ref = first if (first and first["ok"]) else ok_sells[0]
            st = _t(ref.get("tax"))
            if ref.get("received_eth") == 0 and ref.get("expected_eth"):
                st = 1.0
            taxes["sell"] = st
            if _near_high_tax(st):
                flags.append("tax_threshold_boundary")
            taxes["sell_max"] = max(_t(s.get("tax")) for s in ok_sells)
            if first and not first["ok"]:
                later = [s for s in ok_sells if s["delayed"]]
                if later:
                    flags.append("sell_delay_gate")
                else:
                    flags.append("sell_size_limit")
            if any((not s["ok"]) for s in sells if not s["delayed"] and s["id"] != "s10") and "sell_size_limit" not in flags and first and first["ok"]:
                flags.append("sell_partial_failures")
            if st >= RULES["HONEYPOT_TAX"]:
                honeypot = True
                flags.append("sell_tax_ge95")
                reason = f"sell tax {st:.1%} >= 95%"
            elif st >= RULES["HIGH_TAX"]:
                flags.append("sell_tax_high")
            if any(s.get("gas", 0) > 1_500_000 for s in sells):
                flags.append("gas_heavy")
    if buy_confiscatory and quoted_sells:
        return flags + ["high_tax", "extreme_tax"], "honeypot", f"buy tax {taxes['buy']:.1%} >= 95%", taxes
    if buy and buy.get("ok") and not sells:
        flags.append("no_sell_data")
        if not primary:
            observed=known_tax_despite_missing_exit('no_sell_data')
            if observed:return observed
            return flags, "unprobeable", "buy ok but no sell data", taxes
    hi = max(taxes.get("buy") or 0, taxes.get("sell") or 0)
    if hi >= RULES["HIGH_TAX"]:
        flags.append("high_tax")
    if hi >= RULES["EXTREME_TAX"]:
        flags.append("extreme_tax")

    if honeypot:
        return flags, "honeypot", reason, taxes
    if rugged:
        return flags, "rugged", reason or f"rug verdict {rugv}", taxes
    if hi >= RULES["HIGH_TAX"] or _t((taxes.get("transfer") or 0)) >= RULES["HIGH_TAX"]:
        return flags, "high_tax", f"buy {taxes.get('buy', 0):.1%} sell {taxes.get('sell') or 0:.1%} transfer {taxes.get('transfer', 0):.1%}", taxes
    if ctl_flag:
        return flags, "owner_controlled", f"owner={control.get('owner_state')} fn={control.get('risky_categories')}", taxes
    return flags, "normal", "", taxes
