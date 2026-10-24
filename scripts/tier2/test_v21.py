"""Independent arithmetic and transition regressions for rules 3.3."""
import copy
import asyncio
from decimal import Decimal,localcontext
import unittest
from liquidity_v21 import reconstruct,reserve_direction,TOPICS,Collector
from reestimate_v2 import measured_record,classify_record
from test_v2 import trade
from v4 import position_amounts,sqrt_ratio_at_tick


def modify(index,lo,hi,delta,salt=1,owner=1):
    return dict(blockNumber='0x64',logIndex=hex(index),transactionHash='0x'+'ab'*32,
                topics=[TOPICS['uniswap_v4'],'0x'+'11'*32,'0x'+f'{owner:064x}'],
                data='0x'+''.join(f'{v%(1<<256):064x}' for v in (lo,hi,delta,salt)))


class RealLiquidity(unittest.TestCase):
    def test_tickmath_published_endpoints(self):
        self.assertEqual(sqrt_ratio_at_tick(-887272),4295128739)
        self.assertEqual(sqrt_ratio_at_tick(0),1<<96)
        self.assertEqual(sqrt_ratio_at_tick(887272),1461446703485210103287273052203988822378723970342)

    def test_amounts_against_decimal_geometric_price(self):
        # Independent real-number formula, away from integer boundaries.
        with localcontext() as ctx:
            ctx.prec=100
            liquidity=10**12
            for price_tick in (-200,0,200):
                lo,hi=-100,100
                a=Decimal('1.0001')**(Decimal(lo)/2)
                b=Decimal('1.0001')**(Decimal(hi)/2)
                p=max(a,min(Decimal('1.0001')**(Decimal(price_tick)/2),b))
                expected=(int(Decimal(liquidity)*(1/p-1/b)),int(Decimal(liquidity)*(p-a)))
                observed=position_amounts(liquidity,lo,hi,sqrt_ratio_at_tick(price_tick))
                self.assertEqual(observed,expected)

    def test_all_positions_salts_removals_and_out_of_range(self):
        logs=[modify(1,-100,100,100000),modify(2,200,300,400000),
              modify(3,-100,100,200000,salt=2),modify(4,-100,100,-25000)]
        first,final=reconstruct(logs,'v4',(100,1),1<<96,1<<96)
        self.assertEqual(first['n_positions'],1)
        self.assertEqual(final['n_positions'],3)
        self.assertEqual(int(final['active_liquidity']),275000)
        self.assertGreater(int(final['amount0']),int(final['amount1']))
        self.assertEqual([p['liquidity'] for p in final['positions'] if p['salt'].endswith('1') and p['tick_lower']==-100],['75000'])

    def test_incomplete_history_is_unknown(self):
        with self.assertRaisesRegex(ValueError,'negative position'):
            reconstruct([modify(1,-100,100,-1)],'v4',None,1<<96,1<<96)

    def test_zero_amount_needs_no_conversion_quote(self):
        self.assertEqual(asyncio.run(Collector(None,None).convert('0x'+'88'*20,0,100,{},[])),'0')
        for kind in ('v2','v3','v4'):
            rec=trade(kind);rec['pool_info'].update(quote='0x'+'88'*20,reserve_quote=0)
            if kind=='v4':rec['pool_info']['quote_liquidity_method']='v4_all_position_principal_at_sim_price'
            rec['pool_info'].pop('quote_liquidity_eth_equiv_wei')
            self.assertEqual(classify_record(rec)[0],'no_meaningful_liquidity')

    def test_empty_reference_is_unknown_not_zero_price(self):
        from abi import WETH
        collector=Collector(None,None)
        async def call(to,data,block,evidence):
            return [0,1,0] if data=='0x0902f1ac' else [int(WETH,16)]
        collector.call=call
        with self.assertRaisesRegex(ValueError,'empty historical'):
            asyncio.run(collector.convert('0x'+'88'*20,10**18,100,
                        {'quote_conversion_reference_pool':'0x'+'22'*20},[]))

    def test_virtual_only_rejected_and_sensitivity_restores(self):
        rec=trade('v4',10**18)
        rec['pool_info'].update(quote_liquidity_method='v4_active_virtual_quote_amount',reserve_quote=10**18)
        self.assertEqual(classify_record(rec)[0],'unprobeable')
        self.assertEqual(classify_record(measured_record(rec,virtual=True))[0],'normal')
        measurement=dict(status='ok',pool=rec['pool'],block=100,final=dict(quote_eth_equiv_wei='1',reserve_quote='1'))
        rec['block']=100
        real=measured_record(rec,measurement)
        self.assertEqual(classify_record(real)[0],'no_meaningful_liquidity')
        self.assertEqual(rec['pool_info']['reserve_quote'],10**18)
        measurement['pool']='different'
        self.assertEqual(classify_record(measured_record(rec,measurement))[0],'unprobeable')

    def test_direction_does_not_invent_attribution(self):
        self.assertEqual(reserve_direction(100,100,1,1),'withdrawal')
        self.assertEqual(reserve_direction(100,100,1,101),'dumping')
        self.assertEqual(reserve_direction(100,100,1,100),'other')
        self.assertEqual(reserve_direction(100,None,1,100),'unknown')

if __name__=='__main__':unittest.main()
