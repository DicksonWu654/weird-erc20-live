"""Focused offline regression checks for the revised scientific estimand."""
import asyncio
import copy
import unittest
import tempfile
import gzip
import hashlib
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from early_liquidity import Selector,positive,BM,ROOT,search_upper
from reestimate_v2 import Bootstrap,classify_record
from origin_audit import origin
import classify as CL
import engine as E
import run as SR
from abi import WETH,ZERO


def trade(kind='v2',reserve=10**16):
    return dict(pool='0x'+'11'*20,pool_info=dict(kind=kind,quote=WETH,quote_liquidity_eth_equiv_wei=str(reserve)),
                buy=dict(ok=True,received=100000,expected_out=100000,tax=0),
                transfer=None,sells=[dict(id='s10',ok=True,amount=40000,expected_eth=400,
                                         received_eth=400,tax=0,gas=100000,delayed=False)],control=None)


class Rules(unittest.TestCase):
    def test_inconsistent_buy_replay_stays_unknown(self):
        r=trade();r['replay_consistent']=False;r['buy']['tax']=.2
        self.assertEqual(CL.classify(r)[1:3],('unprobeable','buy_replay_mismatch'))
        r['pool_info']['quote_liquidity_eth_equiv_wei']='1'
        self.assertEqual(CL.classify(r)[1],'no_meaningful_liquidity')
    def test_all_venues_and_floor_boundary(self):
        for kind in ('v1','v2','v3','v4'):
            self.assertEqual(CL.classify(trade(kind,10**16-1))[1],'no_meaningful_liquidity')
            self.assertEqual(CL.classify(trade(kind,10**16))[1],'normal')
        r=trade();r['pool_info'].pop('quote_liquidity_eth_equiv_wei')
        self.assertEqual(CL.classify(r)[2],'liquidity_not_measured')

    def test_buy_revert_and_tax_boundary(self):
        r=trade();r['unprobeable']='buy_reverts:Trading not open'
        self.assertEqual(CL.classify(r)[1],'could_not_buy')
        r=trade();r['buy']['tax']=.10
        flags,label,_,_=CL.classify(r)
        self.assertEqual(label,'high_tax');self.assertIn('tax_threshold_boundary',flags)
        self.assertEqual(classify_record(r,strict=True)[0],'normal')
        r['buy']['tax']=.0999
        self.assertIn('tax_threshold_boundary',CL.classify(r)[0])
        self.assertEqual(CL.classify(r)[1],'normal')
        r['buy']['tax']=.05
        self.assertEqual(classify_record(r,tax=.05)[0],'high_tax')

    def test_incompatibility_and_gas_are_not_honeypots(self):
        r=trade('v3');r['buy']['tax']=.02;r['sells'][0].update(ok=False,reason='STF')
        self.assertEqual(CL.classify(r)[1],'v3_fot_incompatible')
        r['sells'][0].update(gas=2500000)
        self.assertEqual(CL.classify(r)[1],'unprobeable')

    def test_lp_fee_flag_is_separate(self):
        r=trade('v4');r['pool_info']['fee']=950000
        flags,label,_,taxes=CL.classify(r)
        self.assertEqual(label,'normal')
        self.assertIn('v4_nonstandard_lp_fee_round_trip_cost',flags)
        self.assertAlmostEqual(taxes['lp_fee_round_trip_cost'],.9975)
        self.assertEqual(classify_record(r,lp_fee=True)[0],'high_tax')
        r['pool_info']['fee']=3000;r['buy']['tax']=.06;r['sells'][0]['tax']=.06
        self.assertEqual(classify_record(r,lp_fee=True)[0],'normal')

    def test_observed_tax_survives_unresolved_exit(self):
        r=trade();r['buy']['tax']=.12
        r['sells'][0].update(ok=False,gas=2500000,reason='out of gas')
        self.assertEqual(CL.classify(r)[1],'high_tax')
        r['unprobeable']='sim_error_phase2:timeout';r['sells']=[]
        self.assertEqual(CL.classify(r)[1],'high_tax')
        r['buy']['received']=0
        self.assertEqual(CL.classify(r)[1],'unprobeable')


class Events(unittest.TestCase):
    def test_router_wrapper_of_false_token_return_is_token_side(self):
        token='0x'+'11'*20;router='0x'+'22'*20;pool='0x'+'33'*20
        tree=dict(to=router,error='execution reverted',calls=[dict(to=token,input='0x23b872dd',
                  output='0x'+'00'*32)])
        self.assertEqual(origin(tree,token,pool,router)[0],'token_returned_false')
    def test_creation_cohort_end_does_not_truncate_observation(self):
        head=int(BM.last_block.iloc[-1])+100000
        creation=int(BM.first_block.iloc[-1])
        self.assertEqual(search_upper(creation,head),head)

    def test_positive_event_decoding(self):
        def log(*words):return dict(data='0x'+''.join(f'{w%(2**256):064x}' for w in words),topics=[])
        self.assertTrue(positive('uniswap_v2',log(2,3)))
        self.assertFalse(positive('uniswap_v2',log(0,3)))
        self.assertTrue(positive('uniswap_v3',log(123,4,0,5)))
        self.assertFalse(positive('uniswap_v3',log(123,0,0,5)))
        self.assertTrue(positive('uniswap_v4',log(-10,10,5,0)))
        self.assertFalse(positive('uniswap_v4',log(-10,10,-5,0)))

    def test_activation_order_can_differ_from_pool_creation(self):
        async def one():
            selector=Selector(None,int(BM.last_block.iloc[-1]));base=int(BM.first_block.iloc[40])
            row=dict(sample_id=1,address='0x'+'33'*20,creation_block=base,first_pool_block=base)
            p=[dict(block=base,pool_address='a',dex='uniswap_v2'),
               dict(block=base+10,pool_address='b',dex='uniswap_v2')]
            async def logs(pool,lo,hi,evidence):
                block=base+50 if pool['pool_address']=='a' else base+11
                return [dict(blockNumber=hex(block),transactionHash='0xabc',logIndex='0x0')] if lo<=block<=hi else []
            selector.logs=logs
            r=await selector.select(row,p)
            self.assertEqual(r['pool'],'b');self.assertEqual(r['block'],base+311)
        asyncio.run(one())


class Design(unittest.TestCase):
    def test_repeated_state_preserves_both_evidence_files(self):
        async def one(directory):
            ctx=SR.Ctx();ctx.mode='historical';ctx.offset=300;ctx.head_pinned=1000
            ctx.rug=False;ctx.dust_floor_wei=0;ctx.raw_dir=directory;ctx.unique_raw=True
            ctx.amts=[10**16,5*10**14]
            pair=dict(token='0x'+'11'*20,pool='0x'+'22'*20,dex='uniswap_v2',block=100,
                      created_block=1,extra=dict(sample_id=7))
            async def fake(*args,**kwargs):
                r=trade();r['meta']={};r['pool_info'].update(dex='uniswap_v2')
                log=SR.PL.RawLog();log.add('fixture',dict(request={},response={},retrieved_at='fixture'))
                return r,log
            with patch.object(SR.PL,'simulate_pair',fake):
                a=await SR.process(ctx,pair);b=await SR.process(ctx,pair)
            self.assertNotEqual(a['evidence']['raw'],b['evidence']['raw'])
            for row in (a,b):
                body=gzip.open(row['evidence']['raw'],'rb').read()
                self.assertEqual(hashlib.sha256(body).hexdigest(),row['evidence']['sha256'])
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tier2/v2') as directory:
            asyncio.run(one(directory))

    def test_cluster_members_share_multiplier_and_empty_blocks_retained(self):
        # There are n=1000 sampled blocks in this month; two observed token
        # rows in one block and all remaining blocks are empty for this cut.
        f=pd.DataFrame(dict(month=['2026-04']*2,source=['blocksample']*2,stratum=['B']*2,
                            weight=[100.,100.],n_stratum_sampled=[1000]*2,N_stratum=[100000]*2,
                            creation_block=[25000000]*2))
        b=Bootstrap(f,reps=200)
        self.assertTrue(np.array_equal(b.weights[:,0],b.weights[:,1]))
        self.assertGreater(np.std(b.weights[:,0]),0)
        self.assertTrue((b.weights>=0).all())


if __name__=='__main__':unittest.main()
