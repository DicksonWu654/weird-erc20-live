"""Independent C16 V3/V4 round trips. No production engine or classifier imports.

Only eth_simulateV1 is used. State overrides fund a fresh EOA and install an
ETH-balance view helper; token/pool state is never changed by overrides.
Production labels are joined only by the separate reporting script.
"""
import argparse
import concurrent.futures as cf
import json
import traceback
from decimal import Decimal

import pandas as pd
from eth_abi import encode, decode
from eth_utils import keccak
from dust_rpc import ROOT, DUST, V3, WETH, ZERO, rpc, enc, call, uint

EOA='0x'+keccak(text='C16 independent blind roundtrip October 2026')[-20:].hex()
BAL='0x'+keccak(text='C16 native balance observer')[-20:].hex()
V3R='0xe592427a0aece92de3edee1f18e0157c05861564'
V3Q='0xb27308f9f90d607463bb33ea1bebb41c27ce5ab6'
UR='0x66a9893cc07d91d95644aedd05d03f95e1dba8af'
PM='0x000000000004444c5dc75cb358380d2e3de08a90'
V4Q='0x99bd42b2b22e531f283b7f8a86dff631eb16a9d1'
P2='0x000000000022d473030f116ddee9f6b43ac78ba3'
KEY='(address,address,uint24,int24,address)'
SWAP3='0x'+keccak(text='Swap(address,address,int256,int256,uint160,uint128,int24)').hex()
SWAP4='0x'+keccak(text='Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)').hex()
INIT='0x'+keccak(text='Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)').hex()


def c(to,data='0x',value=0):return call(to,data,value,EOA)
def bal(token):
    return c(BAL,'0x'+EOA[2:].rjust(64,'0')) if token==ZERO else c(token,enc('balanceOf(address)',['address'],[EOA]))
def ok(c):return c.get('status')=='0x1'


def simulate(block,blocks):
    for b in blocks:b.setdefault('blockOverrides',{}).update(baseFeePerGas='0x0',gasLimit=hex(200000000))
    blocks[0]['stateOverrides']={EOA:dict(balance=hex(100*10**18),nonce='0x0'),BAL:dict(code='0x6000353160005260206000f3')}
    res,p=rpc('eth_simulateV1',[dict(blockStateCalls=blocks,validation=False,traceTransfers=False),hex(block)],V3/'raw_blind')
    return [b for b in res if b.get('calls')],p


def setup(row):
    if row['dex']=='uniswap_v3':
        rc,p=simulate(row['block'],[dict(calls=[c(row['pool'],'0x0dfe1681'),c(row['pool'],'0xd21220a7'),c(row['pool'],'0xddca3f43'),c(row['pool'],enc('slot0()')),c(row['pool'],enc('liquidity()'))])])
        rr=rc[0]['calls'];t0='0x'+f'{uint(rr[0]):040x}';t1='0x'+f'{uint(rr[1]):040x}'
        k=(t0,t1,uint(rr[2]),0,ZERO);sqrt=uint(rr[3]);liq=uint(rr[4])
    else:
        logs,p=rpc('eth_getLogs',[dict(address=PM,topics=[INIT,row['pool']],fromBlock=hex(row['pool_created_block']),toBlock=hex(row['pool_created_block']))],V3/'raw_blind')
        l=next(l for l in logs if l['topics'][1].lower()==row['pool'].lower())
        fee,spacing,hook,sqrt,tick=decode(['uint24','int24','address','uint160','int24'],bytes.fromhex(l['data'][2:]))
        k=('0x'+l['topics'][2][-40:],'0x'+l['topics'][3][-40:],fee,spacing,hook)
        assert '0x'+keccak(encode([KEY],[k])).hex()==row['pool'].lower()
        liq=None
    assert row['token'].lower() in k[:2]
    quote=k[1] if k[0]==row['token'].lower() else k[0]
    assert quote in [ZERO,WETH], 'non ETH quote requires explicit conversion route'
    virt=(liq*2**96//sqrt if quote==k[0] else liq*sqrt//2**96) if sqrt and liq else None
    amount=max(10**7,min(10**13,virt//10000)) if virt else 10**13
    return k,quote,amount,p


def trade(row,k,quote,amount,buy):
    token=row['token'];i,o=(quote,token) if buy else (token,quote)
    if row['dex']=='uniswap_v3':
        sig='exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))'
        return [c(V3R,enc(sig,['(address,address,uint24,address,uint256,uint256,uint256,uint160)'],[(i,o,k[2],EOA,2**40,amount,0,0)]),amount if buy else 0)]
    z=i==k[0]
    single=encode(['('+KEY+',bool,uint128,uint128,bytes)'],[(k,z,amount,0,b'')])
    settle=encode(['address','uint256'],[i,amount]);take=encode(['address','uint256'],[o,0])
    v4=encode(['bytes','bytes[]'],[bytes([6,12,15]),[single,settle,take]])
    return [c(UR,enc('execute(bytes,bytes[])',['bytes','bytes[]'],[bytes([16]),[v4]]),amount if buy and quote==ZERO else 0)]


def prep(row,quote,amount):
    if row['dex']=='uniswap_v3' or quote==ZERO:return []
    return [c(WETH,'0xd0e30db0',amount),c(WETH,enc('approve(address,uint256)',['address','uint256'],[P2,2**256-1])),
            c(P2,enc('approve(address,address,uint160,uint48)',['address','address','uint160','uint48'],[WETH,UR,2**160-1,2**48-1]))]


def approvals(row):
    token=row['token']
    allowance=row.get('bounded_approval',2**256-1)
    if row['dex']=='uniswap_v3':return [c(token,enc('approve(address,uint256)',['address','uint256'],[V3R,allowance]))]
    return [c(token,enc('approve(address,uint256)',['address','uint256'],[P2,allowance])),
            c(P2,enc('approve(address,address,uint160,uint48)',['address','address','uint160','uint48'],[token,UR,2**160-1,2**48-1]))]


def quoter(row,k,quote,amount,buy):
    i,o=(quote,row['token']) if buy else (row['token'],quote)
    if row['dex']=='uniswap_v3':return c(V3Q,enc('quoteExactInputSingle(address,address,uint24,uint256,uint160)',['address','address','uint24','uint256','uint160'],[i,o,k[2],amount,0]))
    return c(V4Q,enc('quoteExactInputSingle(('+KEY+',bool,uint128,bytes))',['('+KEY+',bool,uint128,bytes)'],[(k,i==k[0],amount,b'')]))


def core(row,rc,k,input_token):
    matches=[]
    for l in rc.get('logs',[]):
        topics=l.get('topics',[])
        if row['dex']=='uniswap_v3' and l['address'].lower()==row['pool'].lower() and topics and topics[0]==SWAP3:
            a0,a1=decode(['int256','int256'],bytes.fromhex(l['data'][2:130]));matches.append((abs(a0),abs(a1)))
        if row['dex']=='uniswap_v4' and l['address'].lower()==PM and len(topics)>2 and topics[0]==SWAP4 and topics[1].lower()==row['pool'].lower() and topics[2][-40:]==UR[2:]:
            a0,a1=decode(['int128','int128'],bytes.fromhex(l['data'][2:130]));matches.append((abs(a0),abs(a1)))
    if len(matches)!=1:return None
    return matches[0] if input_token==k[0] else matches[0][::-1]


def tax(core,spent,received):
    if not core or not spent or not core[1]:return None
    return max(0.,float(1-Decimal(core[0])*Decimal(received)/(Decimal(spent)*Decimal(core[1]))))


def verify(row):
    k,quote,amount,p=setup(row);d=dict(**row,harness_version=2,eoa=EOA,pool_key=k,quote=quote,evidence=[p],buy_attempts=[],sell_attempts=[])
    # Small trade, two smaller retries, then executable-lot retries for integer tokens.
    amounts=list(dict.fromkeys([amount,max(10**4,amount//100),max(1,amount//10000),10**14,10**15,10**16]))
    bought=0
    for amt in amounts:
        before=prep(row,quote,amt)
        wallet_quote=ZERO if row['dex']=='uniswap_v3' else quote
        calls=before+[quoter(row,k,quote,amt,True),bal(wallet_quote),*trade(row,k,quote,amt,True),bal(wallet_quote),bal(row['token'])]
        rr,p=simulate(row['block'],[dict(calls=calls)]);r=rr[0]['calls'];j=len(before);buy=r[j+2]
        acquired=uint(r[-1]);q0,q1=uint(r[j+1]),uint(r[-2]);spent=(q0-q1) if q0 is not None and q1 is not None else None
        cr=core(row,buy,k,quote)
        bt=tax(cr,spent,acquired) if acquired is not None else None
        attempt=dict(amount=amt,ok=ok(buy),acquired=acquired,spent=spent,core=cr,tax=bt,quote_out=uint(r[j]),evidence=p,revert=buy.get('returnData'),error=buy.get('error'))
        d['buy_attempts'].append(attempt);d['evidence'].append(p)
        if ok(buy) and acquired and acquired>0:
            bought=acquired;amount=amt;d['buy_tax']=bt;break
        if ok(buy) and cr and cr[1]>0 and acquired==0:
            d.update(verdict='honeypot',reason='positive core buy output entirely withheld',buy_tax=1.0);return d
    if not bought:
        zero=all(a['ok'] and a.get('acquired')==0 and (not a.get('core') or a['core'][1]==0) for a in d['buy_attempts'])
        d.update(verdict='no_liquidity' if zero else 'unprobeable',reason='no positive buy output' if zero else 'buy or infrastructure failure; sell restriction untested');return d
    row=dict(row,bounded_approval=bought)
    for divisor,delay in [(2,1),(20,1),(200,1),(20,26)]:
        amt=max(1,bought//divisor)
        first=prep(row,quote,amount)+trade(row,k,quote,amount,True)+[bal(row['token'])]+approvals(row)
        second=[quoter(row,k,quote,amt,False),bal(row['token']),bal(quote),*trade(row,k,quote,amt,False),bal(quote),bal(row['token'])]
        rr,p=simulate(row['block'],[dict(calls=first),dict(calls=second,blockOverrides=dict(number=hex(row['block']+1+delay)))])
        r=rr[-1]['calls'];sell=r[3];q0,q1=uint(r[2]),uint(r[4]);t0,t1=uint(r[1]),uint(r[5])
        got=q1-q0 if q0 is not None and q1 is not None else None;spent=t0-t1 if t0 is not None and t1 is not None else None
        cr=core(row,sell,k,row['token']);st=tax(cr,spent,got) if got is not None else None
        a=dict(amount=amt,divisor=divisor,delay=delay,ok=ok(sell),received_quote=got,spent=spent,core=cr,tax=st,
               quote_out=uint(r[0]),evidence=p,revert=sell.get('returnData'),error=sell.get('error'),approvals_ok=all(ok(c) for c in rr[0]['calls'][-len(approvals(row)):]))
        d['sell_attempts'].append(a);d['evidence'].append(p)
        if ok(sell) and got and got>0 and spent and spent>0:
            d['sell_tax']=st
            taxes=[v for v in [d.get('buy_tax'),st] if v is not None]
            d.update(verdict='honeypot' if max(taxes,default=0)>=.95 else 'high_tax' if max(taxes,default=0)>.1 else 'sellable',reason='positive wallet sell proceeds; tax excludes core LP fee');return d
    positive=[a for a in d['sell_attempts'] if a.get('quote_out',0) and a['quote_out']>0]
    if positive and all(not a['ok'] and a['approvals_ok'] for a in positive):d.update(verdict='honeypot',reason='positive-quote full/small/delayed sells fail after successful buy and approvals')
    elif all(a['ok'] and not a.get('received_quote') for a in d['sell_attempts']):d.update(verdict='no_liquidity',reason='no executable reverse-direction output at tested amounts')
    else:d.update(verdict='unprobeable',reason='sell failure lacks positive executable quote')
    return d


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int);ap.add_argument('--workers',type=int,default=3);ap.add_argument('--only',type=int);a=ap.parse_args()
    rows=pd.read_csv(V3/'blind_sample.csv').to_dict('records')
    if a.only:rows=[r for r in rows if r['sample_id']==a.only]
    if a.limit:rows=rows[:a.limit]
    details=V3/'blind_details';details.mkdir(exist_ok=True)
    def one(row):
        dest=details/(str(row['sample_id'])+'.json')
        if dest.exists():
            old=json.loads(dest.read_text())
            if not a.only and 'exception' not in old and old.get('harness_version')==2:return
        try:d=verify(row)
        except Exception as exc:
            traceback.print_exc();d=dict(**row,verdict='unprobeable',exception=repr(exc))
        dest.write_text(json.dumps(d,indent=2));print(row['sample_id'],d['verdict'],d.get('buy_tax'),d.get('sell_tax'),flush=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:list(ex.map(one,rows))


if __name__=='__main__':main()
