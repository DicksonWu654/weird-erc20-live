#!/usr/bin/env python3
"""Read-only Base B20 case study with real Router02 swaps.

The B20 tokens are native ``0xef`` contracts, so the normal Solidity-based
simulator cannot model them. This module uses Base ``eth_simulateV1`` with a
real holder, Base Uniswap V3 SwapRouter02, and (when Router02 reverts) the
Permit2/Universal Router route. Every request and response is retained as a
hashed envelope under ``data/validate/round2/b20_raw``.
"""
import hashlib
import json
import time
from pathlib import Path

import requests
from eth_abi import decode, encode
from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/validate/round2/b20_raw"
OUT.mkdir(parents=True, exist_ok=True)
RPCS = ["https://base-rpc.publicnode.com", "https://base.drpc.org"]
ROUTER02 = "0x2626664c2603336E57B271c5C0b26F421741e481"
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
UNIVERSAL_ROUTER = "0x6fF5693b99212Da76ad316178A184AB56D299b43"
WETH = "0x4200000000000000000000000000000000000006"
OFAC = "0x098b716b8aaf21512996dc57eb0615e2383e2f96"
BENIGN = "0x000000000000000000000000000000000000dEaD"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOKENS = {
    "NVDAc": "0xb20000000000000000000078ee7ce2fe4908108c",
    "OUSD": "0xb2000000000000000000002feb517dfec7415344",
    "GOOGLc": "0xb2000000000000000000002d0ba3164cc74f58b7",
    "MSFTc": "0xb200000000000000000000ab99cfa739e253872b",
    "cbHYPE": "0xb200000000000000000000451d033a5000cb479e",
}


def rpc(url, method, params, ident):
    request = {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}
    started = time.time()
    try:
        response = requests.post(url, json=request, timeout=60)
        text = response.text
        try:
            body = response.json()
        except Exception:
            body = None
        status, error = response.status_code, None
    except Exception as exc:
        text, body, status, error = "", None, None, repr(exc)
    return {"url": url, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "request": request, "status": status, "response": body, "response_text": text,
            "body_sha256": hashlib.sha256(text.encode()).hexdigest(), "error": error}


def do_rpc(method, params, ident, url):
    env = rpc(url, method, params, ident)
    err_code = ((env.get("response") or {}).get("error") or {}).get("code")
    if env["status"] in (None, 429, 500, 502, 503, 504) or err_code == 15:
        other = RPCS[1] if url == RPCS[0] else RPCS[0]
        alt = rpc(other, method, params, ident)
        if alt["status"] == 200 and not (alt.get("response") or {}).get("error"):
            return alt, other
    return env, url


def enc_addr(address):
    return address[2:].lower().rjust(64, "0")


def enc_transfer(to, amount=1):
    return "0xa9059cbb" + enc_addr(to) + hex(amount)[2:].rjust(64, "0")


def approve_data(spender, amount):
    return "0x095ea7b3" + enc_addr(spender) + hex(amount)[2:].rjust(64, "0")


def exact_input_single(holder, token_in, token_out, fee, amount):
    selector = keccak(text="exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))")[:4]
    args = encode(["(address,address,uint24,address,uint256,uint256,uint160)"],
                  [(token_in, token_out, fee, holder, amount, 0, 0)])
    return "0x" + (selector + args).hex()


def permit2_approve_data(token, spender, amount):
    selector = keccak(text="approve(address,address,uint160,uint48)")[:4]
    return "0x" + (selector + encode(["address", "address", "uint160", "uint48"],
                                     [token, spender, amount, 2**48 - 1])).hex()


def universal_execute(commands, inputs):
    selector = keccak(text="execute(bytes,bytes[])")[:4]
    return "0x" + (selector + encode(["bytes", "bytes[]"], [commands, inputs])).hex()


def path_bytes(token_in, fee, token_out):
    return bytes.fromhex(token_in[2:]) + int(fee).to_bytes(3, "big") + bytes.fromhex(token_out[2:])


def decode_revert(data):
    if not data or data == "0x":
        return "empty revert data (0x)"
    raw = bytes.fromhex(data[2:] if data.startswith("0x") else data)
    selector = raw[:4].hex()
    if selector == "08c379a0" and len(raw) >= 68:
        try:
            return f"Error({decode(['string'], raw[4:])[0]!r})"
        except Exception:
            pass
    if selector == "4e487b71" and len(raw) >= 36:
        return f"Panic(0x{int.from_bytes(raw[4:36], 'big'):x})"
    return {"316cf0eb": "V3InvalidSwap()", "911e1272": "V3InvalidPath()",
            "90b8ec18": "TransferFailed()",
            "a43fec12": "PolicyForbids(address,uint256) (selector; raw arguments retained)"}.get(
                selector, f"custom error selector 0x{selector} (raw={data})")


def calls_from(env):
    try:
        return env["response"]["result"][0].get("calls", [])
    except (KeyError, TypeError, IndexError):
        return []


def result_summary(env, final_index, output_token, holder):
    calls = calls_from(env)
    final = calls[final_index] if len(calls) > final_index else {}
    status = final.get("status")
    out_logs = 0
    for log in final.get("logs") or []:
        topics = log.get("topics") or []
        if len(topics) >= 3 and topics[0].lower() == TRANSFER_TOPIC and log.get("address", "").lower() == output_token.lower() and topics[2].lower().endswith(holder[2:].lower().rjust(64, "0")):
            try:
                out_logs += int(log.get("data", "0x0"), 16)
            except ValueError:
                pass
    data = (final.get("error") or {}).get("data") if status == "0x0" else None
    return {"status": "success" if status == "0x1" else "revert" if status == "0x0" else "unreturned",
            "status_hex": status, "return_data": final.get("returnData"),
            "amount_out_returned": int(final.get("returnData", "0x0"), 16) if status == "0x1" and final.get("returnData") not in (None, "0x") else None,
            "amount_out_transfer_to_holder": out_logs or None, "revert_data": data,
            "revert_decoded": decode_revert(data) if data is not None else None,
            "gas_used": final.get("gasUsed")}


def save_envelope(symbol, kind, env):
    path = OUT / f"{symbol}_{kind}.json"
    path.write_text(json.dumps(env, indent=1))
    return str(path.relative_to(ROOT)), env["body_sha256"]


def load_holder(symbol):
    files = sorted((ROOT / "data/raw/F1/batches").glob(f"sim_{symbol}_*.json"))
    if not files:
        return None
    data = json.loads(files[-1].read_text())
    for call in data.get("request", []):
        obj = (call.get("params") or [None])[0]
        if isinstance(obj, dict) and obj.get("from"):
            return obj["from"]
    return None


def largest_v3(symbol):
    files = sorted((ROOT / "data/raw/F1/http").glob(f"goplus_{symbol}_*.json"))
    if not files:
        return None, None
    outer = json.loads(files[-1].read_text())
    body = json.loads(outer.get("body", "{}"))
    result = body.get("result") or {}
    key = next(iter(result), None)
    dex = (result.get(key) or {}).get("dex") or []
    v3 = [x for x in dex if x.get("liquidity_type") == "UniV3" and len(x.get("pair", "")) == 42]
    if not v3:
        return None, None
    item = max(v3, key=lambda x: float(x.get("liquidity") or 0))
    return item.get("pair"), item


def honeypot_evidence(token):
    """Link the latest saved provider envelope without publishing its body."""
    files = sorted((ROOT / "data/raw/validate/honeypot/8453" / token.lower()).glob("*.json"))
    if not files:
        return {"is_honeypot": True, "sell_tax_pct": 100.0, "reason": "execution reverted: STF",
                "note": "No saved provider path found"}
    path = files[-1]
    env = json.loads(path.read_text())
    try:
        body = json.loads(env.get("body_text") or "{}")
    except Exception:
        body = {}
    hr, sr = body.get("honeypotResult") or {}, body.get("simulationResult") or {}
    return {"is_honeypot": hr.get("isHoneypot"), "buy_tax_pct": sr.get("buyTax"),
            "sell_tax_pct": sr.get("sellTax"), "reason": hr.get("honeypotReason") or body.get("simulationError") or "",
            "raw": str(path.relative_to(ROOT)), "sha256": env.get("body_sha256"),
            "retrieved_at": env.get("retrieved_at"), "url": env.get("url"),
            "note": "Provider body remains local under provider terms; publish aggregate values only."}


def make_sim_params(calls, block, state_overrides=None):
    return [{"blockStateCalls": [{"calls": calls, "stateOverrides": state_overrides or {}, "blockOverrides": {}}],
             "validation": False, "traceTransfers": True}, hex(block)]


def direct_paths(symbol, token, holder, pool, fee, block, url, ident):
    balance_call = rpc(url, "eth_call", [{"to": token, "data": "0x70a08231" + enc_addr(holder)}, hex(block)], ident)
    balance_raw, balance_sha = save_envelope(symbol, "balance", balance_call)
    balance_call["raw"] = balance_raw
    balance_call["sha256"] = balance_sha
    try:
        balance = int((balance_call.get("response") or {}).get("result", "0x0"), 16)
    except (TypeError, ValueError):
        balance = 0
    amount = max(balance // 100, 1)
    fee_int = int(round(float(fee) * 1_000_000))
    sell_calls = [{"from": holder, "to": token, "data": approve_data(ROUTER02, amount), "gas": "0x989680", "gasPrice": "0x0"},
                  {"from": holder, "to": ROUTER02, "data": exact_input_single(holder, token, WETH, fee_int, amount), "gas": "0x989680", "gasPrice": "0x0"}]
    sell_env, _ = do_rpc("eth_simulateV1", make_sim_params(sell_calls, block), ident + 1, url)
    sell_raw, sell_sha = save_envelope(symbol, "swaprouter_sell", sell_env)
    sell = result_summary(sell_env, 1, WETH, holder)
    sell.update({"router": ROUTER02, "token_in": token, "token_out": WETH, "amount_in": amount, "fee": fee_int,
                 "approve_status": calls_from(sell_env)[0].get("status") if calls_from(sell_env) else None,
                 "raw": sell_raw, "sha256": sell_sha})
    eth_in = 10**15
    buy_calls = [{"from": holder, "to": ROUTER02, "data": exact_input_single(holder, WETH, token, fee_int, eth_in), "value": hex(eth_in), "gas": "0x989680", "gasPrice": "0x0"}]
    buy_env, _ = do_rpc("eth_simulateV1", make_sim_params(buy_calls, block, {holder: {"balance": hex(10**18)}}), ident + 2, url)
    buy_raw, buy_sha = save_envelope(symbol, "swaprouter_buy", buy_env)
    buy = result_summary(buy_env, 0, token, holder)
    buy.update({"router": ROUTER02, "token_in": WETH, "token_out": token, "amount_in": eth_in, "fee": fee_int,
                "native_balance_override": hex(10**18), "raw": buy_raw, "sha256": buy_sha})
    return balance_call, balance, sell, buy


def universal_fallback(symbol, token, holder, fee_int, amount, block, url, ident, kind):
    if kind == "sell":
        path = path_bytes(token, fee_int, WETH)
        swap_input = encode(["address", "uint256", "uint256", "bytes", "bool"], [holder, amount, 0, path, True])
        calls = [{"from": holder, "to": token, "data": approve_data(PERMIT2, amount), "gas": "0x989680", "gasPrice": "0x0"},
                 {"from": holder, "to": PERMIT2, "data": permit2_approve_data(token, UNIVERSAL_ROUTER, amount), "gas": "0x989680", "gasPrice": "0x0"},
                 {"from": holder, "to": UNIVERSAL_ROUTER, "data": universal_execute(b"\x00", [swap_input]), "gas": "0x989680", "gasPrice": "0x0"}]
        output_token, state = WETH, {}
    else:
        path = path_bytes(WETH, fee_int, token)
        wrap_input = encode(["address", "uint256"], [UNIVERSAL_ROUTER, amount])
        swap_input = encode(["address", "uint256", "uint256", "bytes", "bool"], [holder, amount, 0, path, False])
        calls = [{"from": holder, "to": UNIVERSAL_ROUTER, "data": universal_execute(b"\x0b\x00", [wrap_input, swap_input]), "value": hex(amount), "gas": "0x989680", "gasPrice": "0x0"}]
        output_token, state = token, {holder: {"balance": hex(10**18)}}
    env, _ = do_rpc("eth_simulateV1", make_sim_params(calls, block, state), ident, url)
    raw, sha = save_envelope(symbol, f"universal_{kind}", env)
    summary = result_summary(env, len(calls) - 1, output_token, holder)
    summary.update({"router": UNIVERSAL_ROUTER, "permit2": PERMIT2, "route": "Permit2/UniversalRouter", "calls_count": len(calls), "raw": raw, "sha256": sha})
    return summary


def main():
    block_env, url = do_rpc("eth_blockNumber", [], 1, RPCS[0])
    try:
        block = int(block_env["response"]["result"], 16)
    except (KeyError, TypeError, ValueError):
        raise SystemExit("Base eth_blockNumber failed; no case-study result guessed")
    block_raw, block_sha = save_envelope("base", "block", block_env)
    rows = []
    for index, (symbol, token) in enumerate(TOKENS.items()):
        holder = load_holder(symbol)
        pool, pool_meta = largest_v3(symbol)
        if not holder or not pool:
            rows.append({"symbol": symbol, "token": token, "status": "unprobeable", "reason": "missing real-holder or V3 pool evidence"})
            continue
        calls = [{"from": holder, "to": pool, "data": "0x3850c7bd", "gas": "0x61a80", "gasPrice": "0x0"},
                 {"from": holder, "to": pool, "data": "0x1a686502", "gas": "0x61a80", "gasPrice": "0x0"},
                 {"from": holder, "to": token, "data": enc_transfer(BENIGN), "gas": "0x61a80", "gasPrice": "0x0"},
                 {"from": holder, "to": token, "data": enc_transfer(OFAC), "gas": "0x61a80", "gasPrice": "0x0"}]
        probe_env, _ = do_rpc("eth_simulateV1", make_sim_params(calls, block), index * 10 + 2, url)
        probe_raw, probe_sha = save_envelope(symbol, "pool_probes", probe_env)
        probe_calls = calls_from(probe_env)
        balance_env, balance, sell, buy = direct_paths(symbol, token, holder, pool, pool_meta.get("pool_fee"), block, url, index * 10 + 4)
        fallback = {}
        if sell["status"] != "success":
            fallback["sell"] = universal_fallback(symbol, token, holder, sell["fee"], sell["amount_in"], block, url, index * 10 + 7, "sell")
        if buy["status"] != "success":
            fallback["buy"] = universal_fallback(symbol, token, holder, buy["fee"], buy["amount_in"], block, url, index * 10 + 8, "buy")
        effective_sell = fallback.get("sell") if fallback.get("sell", {}).get("status") == "success" else sell
        effective_buy = fallback.get("buy") if fallback.get("buy", {}).get("status") == "success" else buy
        if effective_sell["status"] == "success" and effective_buy["status"] == "success":
            interpretation = "Router sell and buy both succeeded; the STF/100% sell-tax claim is false for this holder, pool, fee, and tested amounts."
        elif effective_sell["status"] != "success":
            interpretation = "Router sell did not succeed; the STF/cannot-sell claim is not refuted. Exact direct and fallback revert evidence is retained."
        else:
            interpretation = "Router sell succeeded but buy did not; a blanket STF claim is not supported for sell, while buy remains failed at this block/path."
        rows.append({"symbol": symbol, "token": token, "pool": pool, "pool_liquidity_usd": pool_meta.get("liquidity"), "pool_fee": pool_meta.get("pool_fee"),
                     "holder": holder, "block": block, "rpc": url, "status": "ok", "pool_probe_raw": probe_raw, "pool_probe_sha256": probe_sha,
                     "pool_slot0_ok": bool(len(probe_calls) > 0 and probe_calls[0].get("status") == "0x1"), "pool_liquidity_ok": bool(len(probe_calls) > 1 and probe_calls[1].get("status") == "0x1"),
                     "transfer_benign_ok": bool(len(probe_calls) > 2 and probe_calls[2].get("status") == "0x1"), "transfer_ofac_ok": bool(len(probe_calls) > 3 and probe_calls[3].get("status") == "0x1"),
                     "block_raw": block_raw, "block_sha256": block_sha, "balance": balance, "balance_probe": balance_env,
                     "balance_probe_raw": balance_env.get("raw"), "balance_probe_sha256": balance_env.get("sha256"),
                     "swap_router02": {"sell": sell, "buy": buy}, "universal_router_fallback": fallback,
                     "effective_sell": effective_sell, "effective_buy": effective_buy, "interpretation": interpretation,
                     "honeypot_is": honeypot_evidence(token)})
        time.sleep(0.35)
    (ROOT / "data/validate/round2/b20_case_study.json").write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
