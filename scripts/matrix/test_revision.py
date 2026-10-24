#!/usr/bin/env python3
"""Offline regressions for the C19 classification/reporting failure modes."""
import json
import math
import pathlib
import sys
import unittest

import pandas as pd
import features as F
from prevalence import fisher_two_sided, holm, stat, security_mask, b20_stock_tokens

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "control"))
from legacy_forward import has_legacy_forward, LEGACY_SELECTORS, apply_legacy_forward
from ctl_classify import upgradeability


def row(test, value, detail=None, status="quirk"):
    return {"test": test, "status": status, "value": value, "detail": detail or {}}


class RevisionTests(unittest.TestCase):
    def test_phantom_is_not_fee_or_other_credited_balance_quirk(self):
        t = {"transfer_return": row("transfer_return", "returns_true_but_recipient_balance_unchanged", {"recipient_before": 900, "recipient_after": 100, "amount": 100}),
             "fee_on_transfer": row("fee_on_transfer", "recipient_receives_less", {"fee_bps": 90000}),
             "full_balance_transfer": row("full_balance_transfer", "leaves_remainder")}
        self.assertEqual(F.f_fresh_nonzero(t, None, None)[0], "yes")
        for f in (F.f_not_credited, F.f_fee_on_transfer, F.f_full_balance):
            self.assertEqual(f(t, None, None)[0], "unprobeable")

    def test_fee_guard(self):
        t = {"transfer_return": row("transfer_return", "returns_true", {"recipient_before": 0, "recipient_after": 90, "amount": 100}),
             "fee_on_transfer": row("fee_on_transfer", "recipient_receives_less", {"fee_bps": 1000})}
        self.assertEqual(F.f_fee_on_transfer(t, None, None)[0], "yes")
        for bad in (10001, -1, float("nan"), None):
            t["fee_on_transfer"]["detail"]["fee_bps"] = bad
            self.assertEqual(F.f_fee_on_transfer(t, None, None)[0], "unprobeable")
        t.pop("transfer_return")
        t["fee_on_transfer"]["detail"]["fee_bps"] = 1000
        self.assertEqual(F.f_fee_on_transfer(t, None, None)[0], "unprobeable")

    def test_binary_shifts_are_not_share_ratios_but_decimal_scaling_is(self):
        for ratio, expected in [(1 / 256, "no"), (0.0039062499989888145, "no"), (4, "no"), (math.nextafter(4, 0), "no"), (1e-18, "yes"), (1.246848, "yes")]:
            t = {"rebasing": row("rebasing", {"evidence": [f"balance_is_not_the_raw_slot_value(balance/slot={ratio}: shares)"], "drift": "no_drift"}, {"slot_to_balance_ratio": ratio})}
            self.assertEqual(F.f_share_ratio(t, None, None)[0], expected)
            self.assertEqual(F.f_share_capability(t, None, None)[0], expected)
            self.assertEqual(F.f_drift(t, None, None)[0], "no")
        t["rebasing"]["value"]["drift"] = "drift"
        self.assertEqual(F.f_drift(t, None, None)[0], "yes")

    def test_weakest_path_display_includes_delay(self):
        p = {"kinds": ["timelock", "eoa"], "terminal_kind": "eoa", "timelock_delay_seconds": 86400, "terminal_summary": "EOA"}
        r = {"upgradeable": "yes", "upgrade_paths": [p]}
        self.assertEqual(F.c_upgrade_class(r)[1], "timelock")
        self.assertIn("authority: timelock; delay=86400s", F.c_upgradeable(r)[1])
        r["upgrade_paths"].append({"kinds": ["safe"], "terminal_kind": "safe", "terminal_summary": "Safe 2-of-3"})
        self.assertEqual(F.c_upgrade_class(r)[1], "multisig_no_timelock")
        self.assertTrue(F.c_upgradeable(r)[1].startswith("authority: multisig_no_timelock"))

    def test_legacy_call_forwarding_does_not_require_delegatecall(self):
        self.assertFalse(has_legacy_forward(["0753c30c"]))
        self.assertTrue(has_legacy_forward(LEGACY_SELECTORS))
        r = {"is_proxy": False, "proxy_type": None, "upgrade_paths": [{"terminal_kind": "multisig_legacy"}], "upgrade_path_type": "legacy_deprecate_forward"}
        self.assertEqual(upgradeability(r, {}, set())[0], "yes")
        r["upgrade_paths"] = [{"terminal_kind": "renounced"}]
        self.assertEqual(upgradeability(r, {}, set())[0], "frozen")

    def test_union_unknown_is_not_negative(self):
        df = pd.DataFrame({"a__status": ["no", "yes", "no"], "b__status": ["unprobeable", "unprobeable", "no"]})
        self.assertEqual(stat(df, ["a", "b"]), (2, 1))
        empty = pd.DataFrame({"categories": pd.Series([], dtype=str), "a__status": []})
        self.assertEqual(len(empty[~security_mask(empty)].columns), 2)

    def test_saved_b20_stock_exclusion_supplement(self):
        stocks = b20_stock_tokens()
        self.assertEqual(len(stocks), 12)
        df = pd.DataFrame({"chain": ["base", "base"], "token": [next(iter(stocks)), "0xb200-non-stock"], "categories": ["", "stablecoin"]})
        self.assertEqual(security_mask(df).tolist(), [True, False])

    def test_fisher_against_integer_combinatorics(self):
        for a, b, c, d in [(1, 9, 11, 3), (0, 25, 8, 18), (7, 491, 0, 2181), (0, 4, 0, 9), (6, 2, 1, 4)]:
            n1, n2, k = a+b, c+d, a+c
            weights = {x: math.comb(n1, x) * math.comb(n2, k-x) for x in range(max(0, k-n2), min(n1, k)+1)}
            exact = sum(v for v in weights.values() if v <= weights[a]) / math.comb(n1+n2, k)
            self.assertAlmostEqual(fisher_two_sided(a, b, c, d), exact, places=10)
        self.assertEqual(holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])


if __name__ == "__main__":
    unittest.main()
