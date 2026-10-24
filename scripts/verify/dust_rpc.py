"""C16 evidence-preserving, globally paced read-only JSON-RPC client."""
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

import requests
from eth_abi import encode
from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[2]
DUST = ROOT / 'data/verify/dust'
V3 = ROOT / 'data/verify/v3'
WETH = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
ZERO = '0x' + '00' * 20
URL = 'https://eth.drpc.org'


def enc(sig, types=(), args=()):
    return '0x' + (keccak(text=sig)[:4] + encode(types, args)).hex()


def rpc(method, params, directory=DUST / 'raw', refresh=False, url=URL):
    assert method in {'eth_call', 'eth_simulateV1', 'eth_getBlockByNumber', 'eth_getLogs', 'eth_getCode', 'eth_getTransactionReceipt'}
    directory.mkdir(parents=True, exist_ok=True)
    req = dict(jsonrpc='2.0', id=16, method=method, params=params)
    digest = hashlib.sha256(json.dumps([url, req], sort_keys=True).encode()).hexdigest()
    path = directory / (digest + '.json')
    if path.exists() and not refresh:
        old = json.loads(path.read_text())
        if 'result' in old.get('response', {}):
            return old['response']['result'], str(path.relative_to(ROOT))
    for attempt in range(5):
        # Shared across every C16 subprocess; at most 2.5 HTTP requests/s.
        with (DUST / 'rpc_pace.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            lock.seek(0)
            last = float(lock.read() or '0')
            time.sleep(max(0, last + .4 - time.time()))
            lock.seek(0); lock.truncate(); lock.write(str(time.time())); lock.flush()
        env = dict(url=url, request=req, retrieved_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        try:
            res = requests.post(url, json=req, timeout=75)
            env.update(http_status=res.status_code, response_body=res.text,
                       sha256=hashlib.sha256(res.content).hexdigest())
            env['response'] = res.json()
        except Exception as exc:
            env.update(transport_error=repr(exc))
        dest = path if attempt == 0 else path.with_name(path.stem + f'_attempt{attempt}.json')
        dest.write_text(json.dumps(env))
        if 'result' in env.get('response', {}):
            if dest != path:
                path.write_text(json.dumps(env))
            return env['response']['result'], str(dest.relative_to(ROOT))
        time.sleep(min(2 ** attempt, 16))
    raise RuntimeError(f'RPC failed; evidence {dest.relative_to(ROOT)}: {env.get("response", env.get("transport_error"))}')


def call(to, data='0x', value=0, sender=None, gas=8000000):
    c = dict(to=to, data=data, value=hex(value), gas=hex(gas), gasPrice='0x0')
    if sender:
        c['from'] = sender
    return c


def uint(c, i=0):
    if c.get('status') != '0x1' or len(c.get('returnData', '')) < 2 + 64 * (i + 1):
        return None
    return int(c['returnData'][2 + 64*i:2 + 64*(i+1)], 16)


def sim(calls, block, directory, state=None):
    b = dict(calls=calls, blockOverrides=dict(baseFeePerGas='0x0', gasLimit=hex(200000000)))
    if state:
        b['stateOverrides'] = state
    out, path = rpc('eth_simulateV1', [dict(blockStateCalls=[b], validation=False, traceTransfers=False), hex(block)], directory)
    return out[0]['calls'], path
