#!/usr/bin/env python3
"""Web-research benchmark: end-to-end agentic search through the REAL SearXNG/Tor
stack. Each model gets a web_search tool, runs a search loop on a research
question, and we score the final answer. Captures what raw benchmarks miss:
whether the model formulates good queries, reads noisy results, and how slow the
whole loop is (Tor search dominates). AS-CONFIGURED per .env block.

Metrics per model: accuracy, mean #searches (query efficiency), mean wall-clock,
and sec_per_correct = wall / accuracy. Coarse + noisy (live search) — small N.
"""
import json, os, re, subprocess, time, urllib.parse, argparse

CLA="lair-claude"
DIR=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
CTX="131072"; MAX_ROUNDS=4
MODELS=[
 dict(name="GLM-4.7-Flash",repo="unsloth/GLM-4.7-Flash-GGUF",file="GLM-4.7-Flash-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3.6-35B-A3B",repo="unsloth/Qwen3.6-35B-A3B-GGUF",file="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",temp="0.7",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --presence-penalty 1.5 --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3-Coder-30B",repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",file="Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",temp="0.2",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --repeat-penalty 1.05'),
 dict(name="gpt-oss-20b",repo="unsloth/gpt-oss-20b-GGUF",file="gpt-oss-20b-F16.gguf",temp="1.0",top_p="1.0",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 100'),
 dict(name="Gemma-4-26B",repo="unsloth/gemma-4-26B-A4B-it-GGUF",file="gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --top-k 64'),
 dict(name="Nemotron-3-Nano",repo="unsloth/Nemotron-3-Nano-30B-A3B-GGUF",file="Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf",temp="0.6",top_p="0.95",min_p="0.0",extra='--fit on --jinja --kv-unified'),
]
# (question, accepted-answer substring) — stable, verifiable facts that reward a
# good search. Some are commonly-confused (Australia's capital) to reward checking.
QUESTIONS=[
 ("In what year was the company OpenAI founded?","2015"),
 ("Who is the chief executive officer (CEO) of NVIDIA? Give the full name.","huang"),
 ("In what year was the first Apple iPhone released to the public?","2007"),
 ("What is the name of the tallest building in the world?","burj khalifa"),
 ("What is the capital city of Australia?","canberra"),
 ("Who created the Python programming language? Give the surname.","rossum"),
 ("Who is the author of the book 'Thinking, Fast and Slow'? Give the surname.","kahneman"),
 ("Which chemical element has the atomic number 79?","gold"),
]
TOOLS=[{"name":"web_search","description":"Search the web for information and return result snippets.",
        "input_schema":{"type":"object","properties":{"query":{"type":"string","description":"the search query"}},"required":["query"]}}]

def sh(c,**k): return subprocess.run(c,capture_output=True,text=True,**k)
def load_model(m):
    env=dict(os.environ,LLAMA_HF_REPO=m['repo'],LLAMA_HF_FILE=m['file'],LLAMA_CTX_SIZE=CTX,
             LLAMA_TEMP=m['temp'],LLAMA_TOP_P=m['top_p'],LLAMA_MIN_P=m['min_p'],LLAMA_EXTRA_ARGS=m['extra'])
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=DIR,env=env,capture_output=True)
    for _ in range(100):
        if '"status":"ok"' in sh(["docker","exec",CLA,"curl","-s","-m","3","http://llama:8080/health"]).stdout: return True
        time.sleep(3)
    return False
def web_search(query):
    q=urllib.parse.quote((query or "")[:200])
    r=sh(["docker","exec",CLA,"curl","-s","-m","40",f"http://searxng:8080/search?q={q}&format=json"])
    try:
        rs=json.loads(r.stdout).get("results",[])[:6]
        out="\n".join(f"- {x.get('title','')[:80]}: {x.get('content','')[:170]}" for x in rs)
        return out or "(no results)"
    except Exception: return "(search failed)"
def ask_msgs(messages,max_tokens=1024):
    body={"model":"local","max_tokens":max_tokens,"messages":messages,"tools":TOOLS}
    r=sh(["docker","exec","-i",CLA,"curl","-s","-m","150","http://llama:8080/v1/messages",
          "-H","Content-Type: application/json","-H","anthropic-version: 2023-06-01","-d","@-"],input=json.dumps(body))
    try: return json.loads(r.stdout)
    except Exception: return {"content":[]}
def research(question):
    msgs=[{"role":"user","content":f"Research this question using the web_search tool, then give your final answer on a line beginning with 'FINAL:'. Be concise.\n\nQuestion: {question}"}]
    t0=time.time(); nsearch=0; last=""
    for _ in range(MAX_ROUNDS):
        r=ask_msgs(msgs)
        content=r.get("content",[])
        tus=[b for b in content if b.get("type")=="tool_use"]
        txt="\n".join(b.get("text","") for b in content if b.get("type")=="text")
        if txt: last=txt
        if tus and nsearch<MAX_ROUNDS:
            tu=tus[0]; nsearch+=1
            res=web_search(tu.get("input",{}).get("query",""))
            msgs.append({"role":"assistant","content":[{"type":"tool_use","id":tu.get("id","t"),"name":"web_search","input":tu.get("input",{})}]})
            msgs.append({"role":"user","content":[{"type":"tool_result","tool_use_id":tu.get("id","t"),"content":res[:1800]}]})
        else:
            break
    return last, nsearch, time.time()-t0
def run_model(m,limit=None):
    qs=QUESTIONS[:limit] if limit else QUESTIONS
    ok=0; searches=[]; walls=[]
    for q,ans in qs:
        a,ns,w=research(q); searches.append(ns); walls.append(w)
        good = ans.lower() in (a or "").lower()
        ok+=good
        print(f"    [{'OK ' if good else 'XX '}] {ns} searches {w:5.1f}s  q={q[:42]!r} -> {(a or '')[-60:].strip()!r}",flush=True)
    n=len(qs); acc=ok/n; ms=sum(searches)/n; mw=sum(walls)/n
    spc = mw/acc if acc>0 else float('inf')
    return acc,ms,mw,spc,ok,n
if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--smoke",type=int,default=0);ap.add_argument("--no-reload",action="store_true");ap.add_argument("--only",default="")
    a=ap.parse_args();limit=a.smoke or None
    models=[m for m in MODELS if (not a.only or m["name"] in a.only.split(","))]
    def report(m):
        acc,ms,mw,spc,ok,n=run_model(m,limit)
        spc_s="inf" if spc==float('inf') else f"{spc:.0f}"
        print(f"  >>> {m['name']}: acc {ok}/{n} | {ms:.1f} searches/q | {mw:.0f}s/q wall | {spc_s}s/correct",flush=True)
    if a.no_reload: print(f"### {models[0]['name']} (no reload) ###",flush=True); report(models[0])
    else:
        for m in models:
            print(f"### {m['name']} ###",flush=True)
            if not load_model(m): print("  LOAD FAILED"); continue
            report(m)
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=DIR,capture_output=True)
    print("restored default",flush=True)
