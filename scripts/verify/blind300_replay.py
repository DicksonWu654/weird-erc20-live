"""C27 independent fixed 0.01 ETH launch-state replay.

Own ABI construction; C16 v3_blind.py informs V3/V4 routes. No production imports.
All calls in a replay execute in ONE synthetic block (parent B + 1), on canonical
archive state B=first liquidity+300. No token/pool storage overrides or real txs.
Predicted labels and the source parquet are never read by this script.
"""
import argparse
import concurrent.futures as cf
import json
import math
import traceback
from collections import defaultdict
from decimal import Decimal, localcontext

import pandas as pd
from eth_abi import encode,decode
from eth_utils import keccak
from blind300_rpc import ROOT,OUT,rpc

VERSION=2
WETH='0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
ZERO='0x'+'00'*20
EOA='0x'+keccak(text='C27 independent blind fixed .01 ETH launch replay')[-20:].hex()
PEER='0x'+keccak(text='C27 independent plain transfer recipient')[-20:].hex()
BAL='0x'+keccak(text='C27 BALANCE opcode observer')[-20:].hex()
V2R='0x7a250d5630b4cf539739df2c5dacb4c659f2488d'
SUSHI='0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f'
V3R='0xe592427a0aece92de3edee1f18e0157c05861564'
V3Q='0xb27308f9f90d607463bb33ea1bebb41c27ce5ab6'
UR='0x66a9893cc07d91d95644aedd05d03f95e1dba8af'
PM='0x000000000004444c5dc75cb358380d2e3de08a90'
SV='0x7ffe42c4a5deea5b0fec41c94c136cf115597227'
V4Q='0x99bd42b2b22e531f283b7f8a86dff631eb16a9d1'
P2='0x000000000022d473030f116ddee9f6b43ac78ba3'
KEY='(address,address,uint24,int24,address)'
SWAP2='0x'+keccak(text='Swap(address,uint256,uint256,uint256,uint256,address)').hex()
SWAP3='0x'+keccak(text='Swap(address,address,int256,int256,uint160,uint128,int24)').hex()
SWAP4='0x'+keccak(text='Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)').hex()
INIT='0x'+keccak(text='Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)').hex()
MODIFY='0x'+keccak(text='ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)').hex()
PROBE=10**16
MAX=2**256-1


def enc(sig,types=(),args=()):return '0x'+(keccak(text=sig)[:4]+encode(types,args)).hex()
def c(to,data='0x',value=0):return dict(to=to,data=data,value=hex(value),gas=hex(12000000),gasPrice='0x0',**{'from':EOA})
def ok(rc):return rc.get('status')=='0x1'
def uint(rc,i=0):
    data=rc.get('returnData','0x')
    return int(data[2+64*i:66+64*i],16) if ok(rc) and len(data)>=66+64*i else None
def address(rc):
    n=uint(rc)
    if n is None:raise ValueError('address getter unavailable')
    return '0x'+f'{n:040x}'
def bal(asset,who=EOA):return c(BAL,'0x'+encode(['address'],[who]).hex()) if asset==ZERO else c(asset,enc('balanceOf(address)',['address'],[who]))
def approve(asset,spender,amount=MAX):return c(asset,enc('approve(address,uint256)',['address','uint256'],[spender,amount]))
def approve_ok(rc):return ok(rc) and (rc.get('returnData','0x')=='0x' or uint(rc)!=0)
def error(rc):
    data=rc.get('error',{}).get('data') or rc.get('returnData','0x')
    result=dict(error=rc.get('error'),data=data,gas_used=rc.get('gasUsed'))
    try:
        if data.startswith('0x08c379a0'):result['reason']=decode(['string'],bytes.fromhex(data[10:]))[0]
        if data[:10]=='0x'+keccak(text='ExecutionFailed(uint256,bytes)')[:4].hex():
            i,inner=decode(['uint256','bytes'],bytes.fromhex(data[10:]));result.update(command=i,inner=error({'returnData':'0x'+inner.hex()}))
    except Exception:pass
    return result


class Harness:
    def __init__(self,row):
        self.row=row;self.evidence=[];self.k=None;self.quote=None;self.amount=PROBE
        self.d=dict(**row,harness_version=VERSION,eoa=EOA,probe_eth_wei=str(PROBE),
                    synthetic_execution_block=row['block']+1,evidence=self.evidence)
    def rpc(self,method,params):
        result,path=rpc(method,params);self.evidence.append(path);return result
    def sim(self,calls):
        spec=dict(calls=calls,blockOverrides=dict(baseFeePerGas='0x0',gasLimit=hex(500000000)),
             stateOverrides={EOA:dict(balance=hex(100*10**18),nonce='0x0'),
                             BAL:dict(code='0x6000353160005260206000f3')})
        r=self.rpc('eth_simulateV1',[dict(blockStateCalls=[spec],validation=False,traceTransfers=False),hex(self.row['block'])])
        assert len(r)==1 and int(r[0]['number'],16)==self.row['block']+1
        return r[0]['calls']
    def metadata(self):
        row=self.row;dex=row['dex'];pool=row['pool'];token=row['token']
        if dex=='uniswap_v1':
            rr=self.sim([c(pool,enc('tokenAddress()')),bal(ZERO,pool),bal(token,pool)])
            assert address(rr[0])==token
            self.k=(ZERO,token,3000,0,ZERO);self.quote=ZERO
            self.d.update(quote_liquidity_raw=str(uint(rr[1])),quote_liquidity_eth=uint(rr[1])/10**18,pool_token_balance=str(uint(rr[2])))
        elif dex!='uniswap_v4':
            getters=[c(pool,enc(s)) for s in ['token0()','token1()','fee()' if dex=='uniswap_v3' else 'getReserves()']]
            if dex=='uniswap_v3':getters += [c(pool,enc('slot0()')),c(pool,enc('liquidity()'))]
            rr=self.sim(getters);t0,t1=address(rr[0]),address(rr[1]);assert token in [t0,t1]
            self.k=(t0,t1,uint(rr[2]) if dex=='uniswap_v3' else 3000,0,ZERO)
            self.quote=t1 if token==t0 else t0
            if dex=='uniswap_v3':
                qb=self.sim([bal(self.quote,pool),bal(token,pool)])
                reserve=uint(qb[0]);self.d.update(sqrt_price_x96=str(uint(rr[3])),active_liquidity=str(uint(rr[4])),pool_token_balance=str(uint(qb[1])))
            else:
                reserves=decode(['uint112','uint112','uint32'],bytes.fromhex(rr[2]['returnData'][2:]));reserve=reserves[1 if token==t0 else 0]
                self.d.update(reserves=[str(x) for x in reserves],pool_token_balance=str(reserves[0 if token==t0 else 1]))
            self.d['quote_liquidity_raw']=str(reserve)
        else:
            logs=self.rpc('eth_getLogs',[dict(address=PM,topics=[INIT,pool],fromBlock=hex(row['pool_created_block']),toBlock=hex(row['pool_created_block']))])
            l=next(l for l in logs if l['topics'][1].lower()==pool)
            fee,spacing,hook,_,_=decode(['uint24','int24','address','uint160','int24'],bytes.fromhex(l['data'][2:]))
            self.k=('0x'+l['topics'][2][-40:],'0x'+l['topics'][3][-40:],fee,spacing,hook)
            assert '0x'+keccak(encode([KEY],[self.k])).hex()==pool
            assert token in self.k[:2]
            self.quote=self.k[1] if token==self.k[0] else self.k[0]
            rr=self.sim([c(SV,enc('getSlot0(bytes32)',['bytes32'],[bytes.fromhex(pool[2:])])),c(SV,enc('getLiquidity(bytes32)',['bytes32'],[bytes.fromhex(pool[2:])]))])
            sqrt=uint(rr[0]);liq=uint(rr[1]);assert sqrt is not None and liq is not None
            virt=liq*2**96//sqrt if self.quote==self.k[0] else liq*sqrt//2**96
            self.d.update(sqrt_price_x96=str(sqrt),active_liquidity=str(liq),virtual_quote_raw=str(virt),lp_fee=uint(rr[0],3)/10**6)
            self.real_liquidity(sqrt)
            self.d['quote_liquidity_raw']=self.d['real_quote_raw']
        self.d.update(pool_key=list(self.k),quote=self.quote)
        self.value_liquidity()

    def real_liquidity(self,sqrt):
        """Replay all signed position deltas; floor integer amounts at current price.

        Decimal sqrt(1.0001)^tick avoids copying production TickMath. 100-digit
        precision makes numerical error negligible relative to one base unit.
        Net position key includes owner and salt; fees owed are not principal.
        """
        row=self.row;positions=defaultdict(int);events=0
        for lo in range(row['pool_created_block'],row['block']+1,10000):
            logs=self.rpc('eth_getLogs',[dict(address=PM,topics=[MODIFY,row['pool']],fromBlock=hex(lo),toBlock=hex(min(row['block'],lo+9999)))])
            for l in logs:
                low,high,delta,salt=decode(['int24','int24','int256','bytes32'],bytes.fromhex(l['data'][2:]))
                positions[(l['topics'][2],low,high,salt.hex())]+=delta;events+=1
        a0=a1=0
        with localcontext() as ctx:
            ctx.prec=110
            p=Decimal(sqrt)/Decimal(2**96)
            for (_,low,high,_),L in positions.items():
                assert L>=0,'negative reconstructed position liquidity'
                if not L:continue
                a=Decimal('1.0001')**(Decimal(low)/2);b=Decimal('1.0001')**(Decimal(high)/2)
                if p<=a:x=Decimal(L)*(b-a)/(a*b);y=Decimal(0)
                elif p>=b:x=Decimal(0);y=Decimal(L)*(b-a)
                else:x=Decimal(L)*(b-p)/(p*b);y=Decimal(L)*(p-a)
                a0+=int(x);a1+=int(y)
        self.d.update(real_currency0_raw=str(a0),real_currency1_raw=str(a1),
                      real_quote_raw=str(a0 if self.quote==self.k[0] else a1),
                      real_position_count=sum(L>0 for L in positions.values()),modify_liquidity_events=events,
                      real_liquidity_method='net ModifyLiquidity position principal at current sqrtPrice; excludes uncollected fees')

    def value_liquidity(self):
        q=self.quote
        if q in [WETH,ZERO]:num=den=1
        else:
            factory='0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f'
            rr=self.sim([c(factory,enc('getPair(address,address)',['address','address'],[WETH,q]))]);pair=address(rr[0])
            if pair==ZERO:raise ValueError('no canonical V2 WETH/quote conversion pool')
            rr=self.sim([c(pair,enc('token0()')),c(pair,enc('getReserves()'))]);t0=address(rr[0]);a,b,_=decode(['uint112','uint112','uint32'],bytes.fromhex(rr[1]['returnData'][2:]))
            num,den=(a,b) if t0==WETH else (b,a)
            if not den:raise ValueError('zero conversion reserves')
            self.d['quote_conversion']=dict(pair=pair,weth_raw=str(num),quote_raw=str(den))
        self.d['quote_liquidity_eth']=int(self.d['quote_liquidity_raw'])*num/den/10**18
        for k in ['virtual_quote_raw','real_quote_raw']:
            if k in self.d:self.d[k.replace('_raw','_eth')]=int(self.d[k])*num/den/10**18

    def buy_prefix(self):
        dex=self.row['dex'];q=self.quote
        if q==ZERO:return []
        prefix=[c(WETH,'0xd0e30db0',PROBE)]
        if q!=WETH:
            prefix+=[approve(WETH,V2R),c(V2R,enc('swapExactTokensForTokensSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)',
                ['uint256','uint256','address[]','address','uint256'],[PROBE,0,[WETH,q],EOA,2**40]))]
            rr=self.sim(prefix+[bal(q)])
            if not all(ok(x) for x in rr[:-1]) or not uint(rr[-1]):raise ValueError('non-ETH quote funding route failed')
            self.amount=uint(rr[-1])
        router=self.router()
        prefix+=[approve(q,P2 if dex=='uniswap_v4' else router)]
        if dex=='uniswap_v4':prefix+=[self.permit(q)]
        return prefix
    def router(self):return {'uniswap_v1':self.row['pool'],'uniswap_v2':V2R,'sushiswap':SUSHI,'uniswap_v3':V3R,'uniswap_v4':UR}[self.row['dex']]
    def permit(self,token):return c(P2,enc('approve(address,address,uint160,uint48)',['address','address','uint160','uint48'],[token,UR,2**160-1,2**48-1]))
    def approvals(self,n):
        if self.row.get('max_approval',False):n=MAX
        return [approve(self.row['token'],P2,n),self.permit(self.row['token'])] if self.row['dex']=='uniswap_v4' else [approve(self.row['token'],self.router(),n)]
    def trade(self,n,buy):
        r=self.row;dex=r['dex'];i,o=(self.quote,r['token']) if buy else (r['token'],self.quote)
        if dex=='uniswap_v1':
            return c(r['pool'],enc('ethToTokenSwapInput(uint256,uint256)',['uint256','uint256'],[1,2**40]),n) if buy else c(r['pool'],enc('tokenToEthSwapInput(uint256,uint256,uint256)',['uint256']*3,[n,1,2**40]))
        if dex in ['uniswap_v2','sushiswap']:
            return c(self.router(),enc('swapExactTokensForTokensSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)',
                ['uint256','uint256','address[]','address','uint256'],[n,0,[i,o],EOA,2**40]))
        if dex=='uniswap_v3':
            return c(V3R,enc('exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))',
                ['(address,address,uint24,address,uint256,uint256,uint256,uint160)'],[(i,o,self.k[2],EOA,2**40,n,0,0)]))
        single=encode(['('+KEY+',bool,uint128,uint128,bytes)'],[(self.k,i==self.k[0],n,0,b'')])
        actions=encode(['bytes','bytes[]'],[bytes([6,12,15]),[single,encode(['address','uint256'],[i,n]),encode(['address','uint256'],[o,0])]])
        commands=bytes([16]);inputs=[actions]
        if i==ZERO:
            commands+=bytes([4]);inputs.append(encode(['address','address','uint256'],[ZERO,EOA,0]))
        return c(UR,enc('execute(bytes,bytes[])',['bytes','bytes[]'],[commands,inputs]),n if i==ZERO else 0)
    def quote_call(self,n,buy):
        r=self.row;dex=r['dex'];i,o=(self.quote,r['token']) if buy else (r['token'],self.quote)
        if dex=='uniswap_v1':return c(r['pool'],enc('getEthToTokenInputPrice(uint256)' if buy else 'getTokenToEthInputPrice(uint256)',['uint256'],[n]))
        if dex in ['uniswap_v2','sushiswap']:
            return c(self.router(),enc('getAmountsOut(uint256,address[])',['uint256','address[]'],[n,[i,o]]))
        if dex=='uniswap_v3':return c(V3Q,enc('quoteExactInputSingle(address,address,uint24,uint256,uint160)',['address','address','uint24','uint256','uint160'],[i,o,self.k[2],n,0]))
        return c(V4Q,enc('quoteExactInputSingle(('+KEY+',bool,uint128,bytes))',['('+KEY+',bool,uint128,bytes)'],[(self.k,i==self.k[0],n,b'')]))
    def quote_out(self,rc):
        if not ok(rc):return None
        if self.row['dex'] in ['uniswap_v2','sushiswap']:
            try:return decode(['uint256[]'],bytes.fromhex(rc['returnData'][2:]))[0][-1]
            except Exception:return None
        return uint(rc)
    def core(self,rc,buy):
        r=self.row;dex=r['dex'];i=self.quote if buy else r['token']
        if dex=='uniswap_v1':return None
        values=[]
        for l in rc.get('logs',[]):
            ts=l.get('topics',[])
            if not ts:continue
            if dex in ['uniswap_v2','sushiswap'] and l['address'].lower()==r['pool'] and ts[0]==SWAP2 and len(ts)>2 and ts[2][-40:].lower()==EOA[2:]:
                a,b,x,y=decode(['uint256']*4,bytes.fromhex(l['data'][2:]));values.append((a,y) if i==self.k[0] else (b,x))
            elif dex=='uniswap_v3' and l['address'].lower()==r['pool'] and ts[0]==SWAP3 and len(ts)>2 and ts[2][-40:].lower()==EOA[2:]:
                a,b=decode(['int256','int256'],bytes.fromhex(l['data'][2:130]));values.append((abs(a),abs(b)) if i==self.k[0] else (abs(b),abs(a)))
            elif dex=='uniswap_v4' and l['address'].lower()==PM and len(ts)>2 and ts[0]==SWAP4 and ts[1].lower()==r['pool'] and ts[2][-40:].lower()==UR[2:]:
                a,b=decode(['int128','int128'],bytes.fromhex(l['data'][2:130]));values.append((abs(a),abs(b)) if i==self.k[0] else (abs(b),abs(a)))
        return values[0] if len(values)==1 else None
    def taxes(self,core,spent,received):
        if not core or not spent or not core[1]:return None
        return float(max(Decimal(0),1-Decimal(core[0])*Decimal(received)/(Decimal(spent)*Decimal(core[1]))))
    def finish(self,verdict,reason):
        self.d.update(verdict=verdict,reason=reason);return self.d
    def verify(self):
        self.metadata();r=self.row;t=r['token'];q=self.quote
        prefix=self.buy_prefix();self.d['target_quote_input_raw']=str(self.amount)
        rr=self.sim(prefix+[self.quote_call(self.amount,True),bal(t),bal(q),self.trade(self.amount,True),bal(q),bal(t)])
        j=len(prefix);buy=rr[j+3];acquired=uint(rr[-1])-uint(rr[j+1]);spent=uint(rr[j+2])-uint(rr[-2])
        self.d['initial_token_balance']=str(uint(rr[j+1]))
        assert uint(rr[j+1])==0 or r.get('allow_nonzero_initial_balance',False),'derived fresh EOA already holds token'
        self.d['buy']=dict(ok=ok(buy),acquired=str(acquired),spent=str(spent),quote_out=self.quote_out(rr[j]),core=self.core(buy,True),error=error(buy),setup_ok=all(ok(x) for x in rr[:j]))
        if not self.d['buy']['setup_ok']:return self.finish('unprobeable','quote funding or approval setup failed')
        if not ok(buy):
            empty=self.d.get('pool_token_balance')=='0' or 'INSUFFICIENT_LIQUIDITY' in str(error(buy))
            return self.finish('no_liquidity' if empty else 'could_not_buy','fixed 0.01 ETH buy reverted; '+str(error(buy)))
        if acquired<=0:return self.finish('no_liquidity' if not self.core(buy,True) or not self.core(buy,True)[1] else 'could_not_buy','fixed 0.01 ETH buy delivered no token; sell untested')
        bt=self.taxes(self.core(buy,True),spent,acquired)
        if r['dex']=='uniswap_v1':bt=max(0,1-acquired/uint(buy)) if uint(buy) else None
        self.d['buy_tax']=bt
        # Independent plain-transfer branch; direct roundtrip below sells the full
        # acquisition, not a transfer-depleted balance.
        n=max(1,acquired//10)
        tr=self.sim(prefix+[self.trade(self.amount,True),bal(t,PEER),c(t,enc('transfer(address,uint256)',['address','uint256'],[PEER,n])),bal(t,PEER)])
        transfer_received=uint(tr[-1])-uint(tr[-3])
        tt=max(0,1-transfer_received/n) if ok(tr[-2]) else None
        self.d['transfer']=dict(ok=ok(tr[-2]),requested=str(n),received=str(transfer_received),tax=tt,error=error(tr[-2]))
        self.d['transfer_tax']=tt
        self.d['sell_attempts']=[]
        for divisor in [1,10,100]:
            n=max(1,acquired//divisor);aps=self.approvals(acquired)
            # The quoter precedes approvals; positive quotes alone do not assert
            # transferFrom compatibility. Failed approvals are tested separately.
            extra=[bal(t,r['pool']),c(r['pool'],enc('getReserves()'))] if r['dex'] in ['uniswap_v2','sushiswap'] else []
            seq=prefix+[self.trade(self.amount,True),self.quote_call(n,False)]+aps+extra+[bal(t),bal(q),self.trade(n,False),bal(q),bal(t)]
            rr=self.sim(seq);j=len(prefix);sell=rr[-3];got=uint(rr[-2])-uint(rr[-4]);used=uint(rr[-5])-uint(rr[-1])
            cr=self.core(sell,False);surplus=0
            if extra and cr:
                si=j+2+len(aps);res=decode(['uint112','uint112','uint32'],bytes.fromhex(rr[si+1]['returnData'][2:]))
                surplus=uint(rr[si])-res[0 if t==self.k[0] else 1]
                cr=(max(0,cr[0]-surplus),cr[1])
            st=self.taxes(cr,used,got)
            if r['dex']=='uniswap_v1':
                expected=self.quote_out(rr[j+1]);st=max(0,1-got/expected) if expected else None
            a=dict(divisor=divisor,amount=str(n),buy_replayed_ok=ok(rr[j]),approvals_ok=all(approve_ok(x) for x in rr[j+2:j+2+len(aps)]),
                   quote_out=self.quote_out(rr[j+1]),ok=ok(sell),received_quote=str(got),spent=str(used),core=cr,pre_sell_surplus=str(surplus),tax=st,error=error(sell))
            self.d['sell_attempts'].append(a)
            if not a['buy_replayed_ok']:return self.finish('unprobeable','buy replay changed across identical archive-state branches')
            if ok(sell) and got>0 and used>0:
                self.d['sell_tax']=st
                known=[v for v in [bt,st,tt] if v is not None]
                if max(known,default=0)>=.1:return self.finish('high_tax','positive sell proceeds; independently measured maximum leg/plain-transfer tax >=10%; LP fee excluded')
                if bt is None or st is None:return self.finish('unprobeable','sell succeeds but independent swap-event tax denominator unavailable')
                return self.finish('sellable','positive wallet sell proceeds at fixed 0.01 ETH buy; buy/sell/plain-transfer tax <10%; LP fee excluded')
        positive=[a for a in self.d['sell_attempts'] if (a['quote_out'] or 0)>0]
        if positive and all(not a['ok'] and a['approvals_ok'] and int(a['error']['gas_used'],16)<12000000*.98 for a in positive):
            return self.finish('honeypot','buy delivered tokens; full/10%/1% same-block sells fail despite positive untaxed quotes and successful approvals')
        if all(a['ok'] and int(a['received_quote'])==0 for a in self.d['sell_attempts']):return self.finish('no_liquidity','no reverse-direction quote output at full/10%/1% acquired tokens')
        return self.finish('unprobeable','sell failure lacks a positive quote or successful approval; infrastructure/token origin unresolved')


def main():
    if (OUT/'verdict_freeze.json').exists():
        raise SystemExit('Verdicts already frozen; refusing post-unblinding replay changes')
    ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int);ap.add_argument('--only',type=int);ap.add_argument('--workers',type=int,default=4);ap.add_argument('--retry-unknown',action='store_true');a=ap.parse_args()
    rows=pd.read_csv(OUT/'sample.csv').to_dict('records')
    if a.only is not None:rows=[r for r in rows if r['sample_id']==a.only]
    if a.limit:rows=rows[:a.limit]
    dest=OUT/'details';dest.mkdir(exist_ok=True)
    def one(row):
        p=dest/(str(row['sample_id'])+'.json')
        if p.exists():
            old=json.loads(p.read_text())
            if old.get('harness_version')==VERSION and not(a.retry_unknown and old.get('verdict')=='unprobeable'):return
            history=dest/'history';history.mkdir(exist_ok=True)
            (history/(p.stem+f'-v{old.get("harness_version",0)}.json')).write_bytes(p.read_bytes())
        h=Harness(row)
        try:d=h.verify()
        except Exception as exc:
            traceback.print_exc();d=h.finish('unprobeable','RPC or verifier exception: '+repr(exc));d['exception']=traceback.format_exc()
        p.write_text(json.dumps(d,indent=2)+'\n')
        print(row['sample_id'],row['dex'],d['verdict'],d.get('buy_tax'),d.get('sell_tax'),flush=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:list(ex.map(one,rows))


if __name__=='__main__':main()
