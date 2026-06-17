#!/usr/bin/env python3
"""Decode-speed-vs-context-depth curve: how token/sec degrades as a conversation
fills the context. Each model is loaded once at 131072 (as-configured samplers),
then the KV is filled to a series of depths and decode throughput is measured at
each. Growing prompts share a prefix, so llama-server's prompt cache makes the
sweep cheap (only the deepest prefill is paid in full).

Run:  python3 bench/perf_curve.py
Only context/fill is varied — temperature and the rest stay at each .env block.
"""
import json, os, re, subprocess, time, sys

CLA="lair-claude"; LLA="lair-llama"
DIR=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
CTX="131072"
DEPTHS=[2000, 8000, 16000, 32000, 64000, 96000, 120000]   # all < the 131072 window
MODELS=[
 dict(name="GLM-4.7-Flash",repo="unsloth/GLM-4.7-Flash-GGUF",file="GLM-4.7-Flash-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3.6-35B-A3B",repo="unsloth/Qwen3.6-35B-A3B-GGUF",file="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",temp="0.7",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --presence-penalty 1.5 --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3-Coder-30B",repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",file="Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",temp="0.2",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --repeat-penalty 1.05'),
 dict(name="gpt-oss-20b",repo="unsloth/gpt-oss-20b-GGUF",file="gpt-oss-20b-F16.gguf",temp="1.0",top_p="1.0",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 100'),
 dict(name="Gemma-4-26B",repo="unsloth/gemma-4-26B-A4B-it-GGUF",file="gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --top-k 64'),
 dict(name="Nemotron-3-Nano",repo="unsloth/Nemotron-3-Nano-30B-A3B-GGUF",file="Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf",temp="0.6",top_p="0.95",min_p="0.0",extra='--fit on --jinja --kv-unified'),
]
def sh(c,**k): return subprocess.run(c,capture_output=True,text=True,**k)
def load(m):
    env=dict(os.environ,LLAMA_HF_REPO=m['repo'],LLAMA_HF_FILE=m['file'],LLAMA_CTX_SIZE=CTX,
             LLAMA_TEMP=m['temp'],LLAMA_TOP_P=m['top_p'],LLAMA_MIN_P=m['min_p'],LLAMA_EXTRA_ARGS=m['extra'])
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=DIR,env=env,capture_output=True)
    for _ in range(100):
        if '"status":"ok"' in sh(["docker","exec",CLA,"curl","-s","-m","3","http://llama:8080/health"]).stdout: return True
        time.sleep(3)
    return False
# Fixed sentence (~18 tokens after tokenization); repeating it `depth//18` times
# lands the prompt near `depth` tokens. cache_prompt is OFF so each measurement
# does a full prefill to a KNOWN depth — `prompt_n` in the response is then the
# true KV depth the decode is measured at (with caching it under-counts).
SENT="the quick brown fox jumps over the lazy dog and then keeps running along the riverbank . "
def decode_at(depth):
    prompt=SENT*(depth//18)
    body={"prompt":prompt,"n_predict":64,"cache_prompt":False,"ignore_eos":True}
    r=sh(["docker","exec","-i",CLA,"curl","-s","-m","600","http://llama:8080/completion","-H","Content-Type: application/json","-d","@-"],input=json.dumps(body))
    try:
        t=json.loads(r.stdout).get("timings",{})
        return t.get("prompt_n"), round(t.get("predicted_per_second",0),1)
    except Exception: return None,None

if __name__=="__main__":
    only=sys.argv[1].split(",") if len(sys.argv)>1 else None
    print("depths:", DEPTHS, flush=True)
    for m in MODELS:
        if only and m["name"] not in only: continue
        if not load(m): print(f"### {m['name']} ### LOAD FAILED"); continue
        row=[]
        for d in DEPTHS:
            n,dps=decode_at(d); row.append(f"{(n or d)//1000}k:{dps}")
        print(f"{m['name']:18s} | " + "  ".join(row), flush=True)
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=DIR,capture_output=True)
    print("restored default", flush=True)
