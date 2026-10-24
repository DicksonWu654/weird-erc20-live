"""C27 read-only RPC with saved HTTP bytes and a process-wide launch gate."""
import hashlib
import json
import threading
import time
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/verify/blind300'
URL='https://eth.drpc.org'
_lock=threading.Lock()
_last={}
RPS=1.5
_local=threading.local()
ALLOWED={'eth_call','eth_simulateV1','eth_getLogs','eth_getBlockByNumber','eth_getCode',
         'eth_getTransactionCount','debug_traceCall'}


def rpc(method,params):
    global _last
    assert method in ALLOWED
    url='https://gateway.tenderly.co/public/mainnet' if method=='eth_getLogs' else URL
    req=dict(jsonrpc='2.0',id=27,method=method,params=params)
    digest=hashlib.sha256(json.dumps([url,req],sort_keys=True).encode()).hexdigest()
    directory=OUT/'raw';directory.mkdir(parents=True,exist_ok=True)
    base=directory/(digest+'.json')
    if base.exists():
        old=json.loads(base.read_text())
        if 'result' in old.get('response',{}):
            return old['response']['result'],str(base.relative_to(ROOT))
    if not hasattr(_local,'session'):
        _local.session=requests.Session()
        _local.session.trust_env=False
    for attempt in range(4):
        with _lock:
            time.sleep(max(0,_last.get(url,0)+1/RPS-time.monotonic()))
            _last[url]=time.monotonic()
        env=dict(url=url,request=req,retrieved_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                 request_start_monotonic=_last[url],attempt=attempt)
        try:
            response=_local.session.post(url,json=req,timeout=75)
            env.update(http_status=response.status_code,response_body=response.text,
                       sha256=hashlib.sha256(response.content).hexdigest())
            try:env['response']=response.json()
            except ValueError:pass
        except requests.RequestException as exc:
            env.update(transport_error=repr(exc))
        path=directory/(digest+f'-{time.time_ns()}-attempt{attempt}.json')
        path.write_text(json.dumps(env,separators=(',',':'))+'\n')
        if 'result' in env.get('response',{}):
            # Immutable attempt retained; deterministic success cache is a copy.
            base.write_text(json.dumps(env,separators=(',',':'))+'\n')
            return env['response']['result'],str(base.relative_to(ROOT))
        msg=str(env.get('response',env.get('transport_error',''))).lower()
        retry=env.get('http_status') in [429,502,503,504] or any(s in msg for s in
              ['timeout','timed out','rate limit','too many','connection','temporarily'])
        if not retry:break
        time.sleep(min(20,2**(attempt+1)))
    raise RuntimeError(f'RPC unavailable: {path.relative_to(ROOT)} {msg[:240]}')
