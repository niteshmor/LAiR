#!/usr/bin/env python3
"""Context-size audit: VRAM/RAM + prefill/decode speed at 128K vs 200K per model."""
import json, os, re, subprocess, time, sys

CLA="lair-claude"; LLA="lair-llama"
DIR=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
MODELS=[
 dict(name="GLM-4.7-Flash",repo="unsloth/GLM-4.7-Flash-GGUF",file="GLM-4.7-Flash-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3.6-35B-A3B",repo="unsloth/Qwen3.6-35B-A3B-GGUF",file="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",temp="0.7",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --presence-penalty 1.5 --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3-Coder-30B",repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",file="Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",temp="0.2",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --repeat-penalty 1.05'),
 dict(name="gpt-oss-20b",repo="unsloth/gpt-oss-20b-GGUF",file="gpt-oss-20b-F16.gguf",temp="1.0",top_p="1.0",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 100'),
 dict(name="Gemma-4-26B",repo="unsloth/gemma-4-26B-A4B-it-GGUF",file="gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --top-k 64'),
 dict(name="Nemotron-3-Nano",repo="unsloth/Nemotron-3-Nano-30B-A3B-GGUF",file="Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf",temp="0.6",top_p="0.95",min_p="0.0",extra='--fit on --jinja --kv-unified'),
]
def sh(c,**k): return subprocess.run(c,capture_output=True,text=True,**k)
def load(m,ctx):
    env=dict(os.environ,LLAMA_HF_REPO=m['repo'],LLAMA_HF_FILE=m['file'],LLAMA_CTX_SIZE=str(ctx),
             LLAMA_TEMP=m['temp'],LLAMA_TOP_P=m['top_p'],LLAMA_MIN_P=m['min_p'],LLAMA_EXTRA_ARGS=m['extra'])
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=DIR,env=env,capture_output=True)
    for _ in range(120):
        if '"status":"ok"' in sh(["docker","exec",CLA,"curl","-s","-m","3","http://llama:8080/health"]).stdout: return "ok"
        if LLA not in sh(["docker","ps","--format","{{.Names}}"]).stdout: return "crash"
        time.sleep(3)
    return "timeout"
def loglines(): return sh(["docker","logs",LLA]).stdout+sh(["docker","logs",LLA]).stderr
def n_ctx_train():
    m=re.search(r'n_ctx_train\s*=\s*(\d+)',loglines()); return int(m.group(1)) if m else None
def vram():
    o=sh(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"]).stdout.strip().split("\n")[0]
    return o.strip()
def ram():
    o=sh(["docker","exec",LLA,"sh","-c","grep ^Rss: /proc/1/smaps_rollup 2>/dev/null"]).stdout
    m=re.search(r'(\d+)',o); return round(int(m.group(1))/1048576,1) if m else None
def big(fill):
    prompt=("the quick brown fox jumps over the lazy dog and then keeps running along the riverbank . "*(fill//18))
    body={"prompt":prompt,"n_predict":64,"cache_prompt":False,"ignore_eos":True}
    r=sh(["docker","exec","-i",CLA,"curl","-s","-m","500","http://llama:8080/completion","-H","Content-Type: application/json","-d","@-"],input=json.dumps(body))
    try:
        d=json.loads(r.stdout); t=d.get("timings",{})
        return t.get("prompt_n"),round(t.get("prompt_per_second",0),1),round(t.get("predicted_per_second",0),1)
    except Exception: return None,None,(r.stdout or r.stderr)[:120]

def measure(m,ctx,fill):
    st=load(m,ctx)
    if st!="ok":
        return dict(ctx=ctx,status=st)
    nct=n_ctx_train(); v=vram(); ra=ram()
    pn,pps,dps=big(fill)
    return dict(ctx=ctx,status="ok",n_ctx_train=nct,vram=v,ram=ra,fill=pn,prefill=pps,decode=dps)

if __name__=="__main__":
    targets=[int(x) for x in (sys.argv[1].split(",") if len(sys.argv)>1 else ["131072","200000"])]
    only=sys.argv[2].split(",") if len(sys.argv)>2 else None
    for m in MODELS:
        if only and m["name"] not in only: continue
        print(f"### {m['name']} ###",flush=True)
        nct=None
        for ctx in targets:
            # skip 200k if model trained ctx is below it (would exceed training)
            if nct is not None and ctx>nct:
                print(f"  ctx={ctx}: SKIP (exceeds n_ctx_train={nct})",flush=True); continue
            fill=int(ctx*0.92)
            r=measure(m,ctx,fill)
            if r["status"]!="ok":
                print(f"  ctx={ctx}: {r['status'].upper()}",flush=True); continue
            nct=r["n_ctx_train"]
            print(f"  ctx={ctx}: train={r['n_ctx_train']} vram={r['vram']}MiB ram={r['ram']}GiB | fill={r['fill']}tok prefill={r['prefill']}t/s decode={r['decode']}t/s",flush=True)
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=DIR,capture_output=True)
    print("restored default",flush=True)
