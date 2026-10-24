"""USDT-style CALL forwarding is an upgrade path even without DELEGATECALL.

The complete interface fingerprint is required, not deprecate(address) alone.
Views and an owner/stranger differential are recorded by revise_legacy_forward.py.
"""
import copy

LEGACY_SELECTORS = {"0753c30c", "0e136b19", "26976e3f", "6e18980a", "8b477adb", "aee92d33"}


def has_legacy_forward(selectors):
    return LEGACY_SELECTORS <= {s.removeprefix("0x") for s in selectors}


def apply_legacy_forward(row, check):
    """Return a derived row, preserving the original block, controller evidence and proxy flag."""
    out = copy.deepcopy(row)
    if check.get("status") != "confirmed" or check["block"] != row["block"]:
        return out
    owner = check["owner"]
    paths = [copy.deepcopy(p) for p in row.get("owner_paths", [])
             if p["addresses"] and p["addresses"][0].lower() == owner.lower()
             and p.get("via") in ("owner()", "getOwner()")]
    for p in paths:
        p["via"] = "deprecate(address) onlyOwner > " + p["via"]
        p["labels"][0] = p["via"]
        p["verified"] = True
        p["ev"] = check["ev"]
    out["legacy_forward"] = check
    out["upgrade_path_type"] = "legacy_deprecate_forward"
    out["upgrade_paths"] = paths
    out["upgrade_admin_chain"] = copy.deepcopy(row.get("owner_chain", []))
    out["upgrade_authority_basis"] = "legacy deprecate-forward: owner() path; owner/stranger eth_call differential"
    out["upgrade_authority_firm"] = any(not p.get("assumed_or_unverified") for p in paths)
    out["upgrade_terminal_kinds"] = sorted({p["terminal_kind"] for p in paths})
    if paths and set(out["upgrade_terminal_kinds"]) <= {"renounced"}:
        out["upgradeable"] = "frozen"
    else:
        out["upgradeable"] = "yes" if paths else "no_known_path"
    out["upgradeable_reason"] = "legacy deprecate-forward; authority = owner() path"
    out["capability_hints"].setdefault("upgrade", []).append("0x0753c30c")
    out["privileged_capabilities"] = sorted(set(out.get("privileged_capabilities", [])) | {"upgrade"})
    return out
