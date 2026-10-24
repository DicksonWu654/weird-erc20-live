"""Chain configuration: RPC endpoints (from PLAN.md), per-endpoint batch limits and capabilities."""

# (url, archive?, max_batch). archive=True: serves historical state (needed for the 7-day drift test);
# archive=False: latest-only; None: unknown (learned at runtime).
CHAINS = {
    "ethereum": dict(chain_id=1, native="ETH",
                     rpcs=[("https://eth.drpc.org", True, 3), ("https://ethereum-rpc.publicnode.com", False, 20)],
                     rich="0xF977814e90dA44bFA03b6295A0616a897441aceC"),
    "base": dict(chain_id=8453, native="ETH",
                 rpcs=[("https://base.drpc.org", None, 3), ("https://base-rpc.publicnode.com", False, 20)],
                 rich="0xF977814e90dA44bFA03b6295A0616a897441aceC"),
    # arbitrum: free nodes only keep state for the newest ~20-60 blocks (~5-15 s); measured with eth_call offsets (empirically measured)
    "arbitrum": dict(chain_id=42161, native="ETH", pin_ttl=2.0, pin_lag=1.0,
                     rpcs=[("https://arbitrum.drpc.org", None, 3), ("https://arbitrum-one-rpc.publicnode.com", False, 20)],
                     rich="0xF977814e90dA44bFA03b6295A0616a897441aceC"),
    "bsc": dict(chain_id=56, native="BNB",
                rpcs=[("https://bsc-rpc.publicnode.com", False, 20), ("https://bsc-dataseed.bnbchain.org", False, 20)],
                rich="0xF977814e90dA44bFA03b6295A0616a897441aceC"),
    "polygon": dict(chain_id=137, native="POL",
                    rpcs=[("https://polygon.drpc.org", None, 3), ("https://polygon-bor-rpc.publicnode.com", False, 20)],
                    rich="0xF977814e90dA44bFA03b6295A0616a897441aceC"),
    "optimism": dict(chain_id=10, native="ETH",
                     rpcs=[("https://optimism.drpc.org", None, 3), ("https://optimism-rpc.publicnode.com", False, 20)],
                     rich="0xF977814e90dA44bFA03b6295A0616a897441aceC"),
}
