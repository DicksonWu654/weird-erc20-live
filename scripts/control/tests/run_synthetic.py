#!/usr/bin/env python3
"""Unit tests for the classifier on a local anvil chain with synthetic contracts (no real keys, no real funds).

    .venv/bin/python scripts/control/tests/run_synthetic.py

Starts `anvil --hardfork prague` on port 8599, deploys the contracts from tests/forge/src/Synthetic.sol (forge create
with anvil's unlocked dev accounts), sets EIP-7702-style delegation code on one EOA with anvil_setCode, then runs the
real classifier and asserts the expected proxy type / controller kinds.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PORT = 8599
URL = f"http://127.0.0.1:{PORT}"
os.environ["CTL_LOCAL_RPC"] = URL
sys.path.insert(0, str(HERE.parent))
from ctl_classify import Env, probe_token  # noqa: E402
import requests  # noqa: E402

FORGE = str(pathlib.Path.home() / ".local/bin/forge")


def rpc(m, p):
    return requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).json()["result"]


def deploy(name, args=()):
    cmd = [FORGE, "create", f"src/Synthetic.sol:{name}", "--rpc-url", URL, "--unlocked", "--from", ACC[0], "--broadcast",
           "--json"]
    if args:
        cmd += ["--constructor-args", *map(str, args)]
    out = subprocess.run(cmd, cwd=HERE / "forge", capture_output=True, text=True)
    try:
        return json.loads(out.stdout[out.stdout.index("{"):])["deployedTo"].lower()
    except Exception:
        raise RuntimeError(f"deploy {name} failed: {out.stdout[-400:]} {out.stderr[-400:]}")


def main():
    global ACC
    temporary = tempfile.TemporaryDirectory(prefix="weird-erc20-control-")
    outdir = pathlib.Path(temporary.name)
    anvil = subprocess.Popen(["anvil", "--port", str(PORT), "--hardfork", "prague", "--silent"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                rpc("eth_blockNumber", [])
                break
            except Exception:
                time.sleep(0.2)
        ACC = rpc("eth_accounts", [])
        subprocess.run([FORGE, "build"], cwd=HERE / "forge", capture_output=True)
        eoa, eoa7702, other = ACC[1].lower(), ACC[5].lower(), ACC[2].lower()
        deleg = deploy("Tok")  # any contract as delegation target
        rpc("anvil_setCode", [eoa7702, "0xef0100" + deleg[2:]])
        impl = deploy("Tok")
        uups_impl = deploy("TokUUPS")
        safe = deploy("SafeMock", (f"[{ACC[1]},{ACC[2]},{ACC[3]},{ACC[4]},{ACC[6]}]", 3))
        msw = deploy("MultisigLegacyMock", (f"[{ACC[1]},{ACC[2]},{ACC[3]}]", 2))
        tl = deploy("OZTimelockMock", (172800,))
        ctl = deploy("CompoundTimelockMock", (86400, ACC[1]))
        gov = deploy("GovMock")
        cases = {}

        def tok_owned_by(owner):
            a = deploy("Tok")
            subprocess.run(["cast", "send", a, "setOwner(address)", owner, "--rpc-url", URL, "--unlocked", "--from", ACC[0]],
                           capture_output=True)
            return a

        cases["plain_owner_eoa"] = (deploy("Tok"), dict(is_proxy=False, owner_kinds={"eoa"}, summary="controlled"))
        cases["owner_7702"] = (tok_owned_by(ACC[5]), dict(is_proxy=False, owner_kinds={"eoa_7702"}))
        cases["owner_safe"] = (tok_owned_by(safe), dict(is_proxy=False, owner_kinds={"safe"}, owner_summary="Safe 3-of-5 v1.4.1"))
        cases["owner_legacy_msw"] = (tok_owned_by(msw), dict(is_proxy=False, owner_kinds={"multisig_legacy"}))
        cases["owner_oz_timelock"] = (tok_owned_by(tl), dict(is_proxy=False, owner_has_timelock=172800))
        cases["owner_compound_timelock_eoa"] = (tok_owned_by(ctl), dict(is_proxy=False, owner_kinds={"eoa"}, owner_has_timelock=86400))
        cases["owner_governor"] = (tok_owned_by(gov), dict(is_proxy=False, owner_kinds={"governor"}))
        cases["owner_zero"] = (tok_owned_by("0x0000000000000000000000000000000000000000"), dict(is_proxy=False, owner_kinds={"renounced"}))
        cases["owner_dead"] = (tok_owned_by("0x000000000000000000000000000000000000dEaD"), dict(is_proxy=False, owner_kinds={"renounced"}))
        # clone (EIP-1167 bytecode)
        clone_code = "0x363d3d373d3d3d363d73" + impl[2:] + "5af43d82803e903d91602b57fd5bf3"
        clone_addr = "0x" + "c1" * 20
        rpc("anvil_setCode", [clone_addr, clone_code])
        cases["clone_1167"] = (clone_addr, dict(is_proxy=True, proxy_type="eip1167", upgradeable="no", implementation=impl))
        cases["proxy1967_eoa_admin"] = (deploy("Proxy1967", (impl, ACC[3], ACC[4])), dict(
            is_proxy=True, proxy_type="eip1967", implementation=impl, upgrade_kinds={"eoa"}, upgradeable="yes"))
        padmin = deploy("ProxyAdminM", (safe,))
        cases["proxy1967_proxyadmin_safe"] = (deploy("Proxy1967", (impl, padmin, ACC[4])), dict(
            is_proxy=True, proxy_type="eip1967", upgrade_kinds={"safe"}, upgrade_path_kinds=["ownable_contract", "safe"]))
        cases["proxy1967_admin_7702"] = (deploy("Proxy1967", (impl, ACC[5], ACC[4])), dict(
            is_proxy=True, proxy_type="eip1967", upgrade_kinds={"eoa_7702"}))
        cases["proxy1967_admin_zero_frozen"] = (deploy("Proxy1967", (impl, "0x000000000000000000000000000000000000dEaD", ACC[4])),
                                               dict(is_proxy=True, proxy_type="eip1967", upgradeable="frozen"))
        cases["uups"] = (deploy("ProxyUUPS", (uups_impl, ACC[4])), dict(is_proxy=True, proxy_type="eip1967", implementation=uups_impl,
                                                                         basis_contains="uups", upgrade_verified=True))
        hid = deploy("TokUUPSHidden")
        hp = deploy("ProxyUUPS", (hid, ACC[4]))
        subprocess.run(["cast", "send", hp, "setHidden(address)", ACC[8], "--rpc-url", URL, "--unlocked", "--from", ACC[0]], capture_output=True)
        cases["uups_hidden_authority"] = (hp, dict(is_proxy=True, proxy_type="eip1967", upgrade_unverified=True))
        cases["custom_slot"] = (deploy("ProxyCustom", (impl, ACC[4])), dict(is_proxy=True, proxy_type="custom_delegatecall", implementation=impl))
        s0impl = deploy("TokForSlot0")
        s0 = deploy("ProxySlot0", (s0impl,))
        cases["slot0_masterCopy"] = (s0, dict(is_proxy=True, proxy_type="slot0_delegate_proxy", implementation=s0impl))
        beacon = deploy("Beacon", (impl, safe))
        cases["beacon"] = (deploy("ProxyBeacon", (beacon, ACC[4])), dict(is_proxy=True, proxy_type="eip1967_beacon", implementation=impl,
                                                                          upgrade_kinds={"safe"}))
        cases["diamond"] = (deploy("DiamondLike", (impl,)), dict(is_proxy=True, proxy_type="eip2535_diamond"))
        inner = deploy("Proxy1967", (impl, ACC[3], ACC[4]))
        cases["impl_behind_proxy"] = (deploy("ProxyNoInit", (inner, ACC[3])), dict(
            is_proxy=True, proxy_type="eip1967", impl_is_proxy=True, implementation=impl))
        cases["eoa_not_contract"] = (ACC[7].lower(), dict(status="not_a_contract"))
        rpc("anvil_mine", ["0x4"])
        env = Env("local", outdir, l1=False)
        env.pin_block("latest")
        fails = 0
        for name, (addr, exp) in cases.items():
            row = probe_token(env, addr)
            problems = []
            if "status" in exp and row["status"] != exp["status"]:
                problems.append(f"status {row['status']}")
            for k in ("is_proxy", "proxy_type", "implementation", "upgradeable", "impl_is_proxy"):
                if k in exp and row.get(k) != exp[k]:
                    problems.append(f"{k}: expected {exp[k]!r} got {row.get(k)!r}")
            if "owner_kinds" in exp:
                got = {p["terminal_kind"] for p in row.get("owner_paths", [])}
                if got != exp["owner_kinds"]:
                    problems.append(f"owner terminal kinds {got}")
            if "upgrade_kinds" in exp:
                got = {p["terminal_kind"] for p in row.get("upgrade_paths", [])}
                if got != exp["upgrade_kinds"]:
                    problems.append(f"upgrade terminal kinds {got}")
            if "upgrade_path_kinds" in exp and not any(p["kinds"] == exp["upgrade_path_kinds"] for p in row.get("upgrade_paths", [])):
                problems.append(f"upgrade path kinds {[p['kinds'] for p in row.get('upgrade_paths', [])]}")
            if "owner_summary" in exp and not any(p["terminal_summary"] == exp["owner_summary"] for p in row.get("owner_paths", [])):
                problems.append(f"owner summary {[p['terminal_summary'] for p in row.get('owner_paths', [])]}")
            if "owner_has_timelock" in exp and not any(p["timelock_delay_seconds"] == exp["owner_has_timelock"] for p in row.get("owner_paths", [])):
                problems.append(f"timelock delay {[p['timelock_delay_seconds'] for p in row.get('owner_paths', [])]}")
            if exp.get("upgrade_verified") is not None and any(p["verified"] for p in row.get("upgrade_paths", [])) != exp["upgrade_verified"]:
                problems.append(f"upgrade verified flags {[p['verified'] for p in row.get('upgrade_paths', [])]}")
            if exp.get("upgrade_unverified") and (any(p["verified"] for p in row.get("upgrade_paths", [])) or not row.get("upgrade_paths")):
                problems.append("hidden authority was claimed verified or no path reported")
            if "basis_contains" in exp and exp["basis_contains"] not in (row.get("upgrade_authority_basis") or ""):
                problems.append(f"basis {row.get('upgrade_authority_basis')}")
            if "summary" in exp and row.get("control_summary") != exp["summary"]:
                problems.append(f"summary {row.get('control_summary')}")
            print(("PASS " if not problems else "FAIL ") + name, "; ".join(problems), row.get("error", ""), row.get("reason", ""))
            fails += bool(problems)
        print(f"{len(cases) - fails}/{len(cases)} synthetic cases pass")
        sys.exit(1 if fails else 0)
    finally:
        anvil.terminate()
        temporary.cleanup()


if __name__ == "__main__":
    main()
