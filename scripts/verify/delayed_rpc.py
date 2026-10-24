"""C23 read-only, saved HTTP evidence. No signing or submission methods."""
import asyncio
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/tier2'))
from c17_rpc import EvidenceEndpoint,result
import rpc
# These two additional Ethereum history methods are reads, scoped to this process.
rpc.READ_ONLY.update({'trace_filter','trace_transaction','debug_traceTransaction'})
class DelayedEndpoint(EvidenceEndpoint):
    async def _slot(self):
        # Do not consume the mutable C17 rate config. Hard ceiling per endpoint.
        self.rps=min(self.rps,2.)
        await rpc.Endpoint._slot(self)

def endpoint(name='history',url='https://eth.drpc.org',rps=1.):
    return DelayedEndpoint(url,'c23_'+name,ROOT/'data/verify/delayed/raw'/name,
                           rps=rps,max_batch=1,inflight=3,timeout=90,max_retries=2)
