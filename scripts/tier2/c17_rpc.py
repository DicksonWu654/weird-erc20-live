"""C17 public, read-only RPC with resumable gzip evidence and paced retries.

The simulator's Endpoint interface is preserved. Every HTTP attempt is saved
before its result is interpreted; no transaction-submission method is allowed.
"""
import asyncio
import gzip
import hashlib
import json
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/sim'))
from rpc import Endpoint, RpcTransportError


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode()
    tmp = path.with_suffix(path.suffix + '.tmp')
    with gzip.open(tmp, 'wb', compresslevel=6) as f:
        f.write(body)
    tmp.replace(path)
    return {'raw': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(body).hexdigest()}


class EvidenceEndpoint(Endpoint):
    def __init__(self, url, name, directory, **kwargs):
        super().__init__(url, name, **kwargs)
        self.directory = Path(directory)

    async def _slot(self):
        config=ROOT/'data/tier2/v2/rpc_rates.json'
        if config.exists():
            try:
                value=float(json.loads(config.read_text()).get(self.name,self.rps))
                if 0<value<=5:
                    self.rps=value
            except (ValueError,OSError,TypeError):
                pass
        await super()._slot()

    async def _send(self, items):
        reqs = [r for r, _ in items]
        payload = reqs if len(reqs) > 1 else reqs[0]
        stamp = f'{time.time_ns()}_{self.name}_{reqs[0]["id"]}'
        last = None
        try:
            for attempt in range(self.max_retries + 1):
                if attempt:
                    await self._slot()
                env = {'url': self.url, 'request': payload,
                       'retrieved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                try:
                    self.stats['http'] += 1
                    self.stats['items'] += len(reqs)
                    async with self._sess.post(self.url, json=payload) as resp:
                        body = await resp.read()
                        env.update(http_status=resp.status,
                                   response_body=body.decode('utf8', 'replace'),
                                   body_sha256=hashlib.sha256(body).hexdigest())
                    try:
                        data = json.loads(body)
                        env['response'] = data
                    except ValueError:
                        data = None
                    evidence = dump(self.directory / stamp[:2] / f'{stamp}_{attempt}.json.gz', env)
                    if env['http_status'] == 429 or env['http_status'] >= 500 or data is None:
                        self.stats['429' if env['http_status'] == 429 else 'errors'] += 1
                        raise RpcTransportError(f'HTTP {env["http_status"]}; {evidence["raw"]}')
                    byid = {r.get('id'): r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict)}
                    errors = [r['error'] for r in byid.values() if 'error' in r]
                    if attempt < self.max_retries and any(
                        s in json.dumps(errors).lower() for s in ('rate limit', 'too many', 'capacity', 'timeout', 'try again')
                    ):
                        raise RpcTransportError(f'transient RPC error; {evidence["raw"]}')
                    for req, fut in items:
                        if fut.done():
                            continue
                        if req['id'] not in byid:
                            fut.set_exception(RpcTransportError(f'missing id; {evidence["raw"]}'))
                        else:
                            fut.set_result({'request': req, 'response': byid[req['id']],
                                            'endpoint': self.url, 'retrieved_at': env['retrieved_at'],
                                            'http_status': env['http_status'], 'http_evidence': evidence})
                    return
                except Exception as exc:
                    last = exc
                    if 'response_body' not in env:
                        env['transport_error'] = repr(exc)
                        evidence = dump(self.directory / stamp[:2] / f'{stamp}_{attempt}.json.gz', env)
                    self.stats['retries'] += 1
                    if attempt < self.max_retries:
                        delay = min(2 ** attempt, 30)
                        self._penalty_until = max(self._penalty_until, time.monotonic() + delay)
                        await asyncio.sleep(delay)
            for _, fut in items:
                if not fut.done():
                    fut.set_exception(RpcTransportError(f'{last!r}; evidence {evidence["raw"]}'))
        finally:
            self.inflight.release()


async def result(ep, method, params, log=None, label=None):
    raw = await ep.request(method, params)
    if log is not None:
        log.add(label or method, raw)
    if 'error' in raw['response']:
        raise RpcTransportError(json.dumps(raw['response']['error']) + '; evidence ' + raw['http_evidence']['raw'])
    return raw['response']['result'], raw['http_evidence']
