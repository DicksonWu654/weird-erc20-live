"""Accuracy of the offline CREATE-vs-CREATE2 inference (frame.py create_nonce) against RPC creationMethod on factory-created tokens
found by trace_block in months also covered by Paradigm. Writes data/census/evidence/validate_opcode_inference.json"""
import json, pathlib, sys
import pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from frame import create_nonce
ROOT = pathlib.Path(__file__).resolve().parents[2]
st = pd.read_parquet(ROOT / "data/census/cache/sampled_tokens.parquet")
st = st[(st.block < 16_800_000) & (~st.direct_deploy)]
res = {"n": len(st), "truth_create": int((st.creation_opcode == "create").sum()), "truth_create2": int((st.creation_opcode == "create2").sum())}
inf = [("create" if create_nonce(c, a) is not None else "create2") for c, a in zip(st.creator, st.address)]
st = st.assign(inferred=inf)
res["correct"] = int((st.inferred == st.creation_opcode).sum())
res["create_called_create2"] = int(((st.creation_opcode == "create") & (st.inferred == "create2")).sum())
res["create2_called_create"] = int(((st.creation_opcode == "create2") & (st.inferred == "create")).sum())
print(res); (ROOT / "data/census/evidence/validate_opcode_inference.json").write_text(json.dumps(res, indent=1))
