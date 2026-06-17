#!/usr/bin/env python3
"""Real-task / perceived-performance benchmark. AS-CONFIGURED per .env block.

For each task we record OUTPUT TOKENS (verbosity + thinking cost), WALL-CLOCK time
(real latency), and CORRECT? (auto-graded). The headline metric is
  sec_per_solved = mean_wall_seconds / accuracy
— expected wall-clock to a correct answer, which penalizes both chattiness and
inaccuracy. Tasks are bucketed Quick (S) / Medium (M) / Large (L).
"""
import json, os, re, subprocess, time, argparse

CLA="lair-claude"
DIR=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (parent of bench/)
CTX="131072"
MODELS=[
 dict(name="GLM-4.7-Flash",repo="unsloth/GLM-4.7-Flash-GGUF",file="GLM-4.7-Flash-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3.6-35B-A3B",repo="unsloth/Qwen3.6-35B-A3B-GGUF",file="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",temp="0.7",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --presence-penalty 1.5 --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3-Coder-30B",repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",file="Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",temp="0.2",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --repeat-penalty 1.05'),
 dict(name="gpt-oss-20b",repo="unsloth/gpt-oss-20b-GGUF",file="gpt-oss-20b-F16.gguf",temp="1.0",top_p="1.0",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 100'),
 dict(name="Gemma-4-26B",repo="unsloth/gemma-4-26B-A4B-it-GGUF",file="gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --top-k 64'),
 dict(name="Nemotron-3-Nano",repo="unsloth/Nemotron-3-Nano-30B-A3B-GGUF",file="Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf",temp="0.6",top_p="0.95",min_p="0.0",extra='--fit on --jinja --kv-unified'),
]
def T(s): return lambda t: s.lower() in t.lower().replace(',','')
# task = (prompt, kind, check)   kind: 'text' -> check(text)->bool ; 'code' -> check is a test string
TASKS={
"S":[
 ("What is 47 * 13? Reply with only the number.","text",T("611")),
 ("Reverse the string 'hello'. Reply with only the result.","text",T("olleh")),
 ("This function should add two numbers but is wrong: `def add(a, b): return a - b`. Reply with only the corrected return line.","text",lambda t:"a+b" in t.replace(' ','')),
 ("Extract the email address from this text: 'reach me at jane@acme.io anytime'. Reply with only the email.","text",T("jane@acme.io")),
 ("What does the Python expression type(3.14) return? Reply with one word.","text",T("float")),
 ("Convert the decimal number 25 to binary. Reply with only the binary digits.","text",lambda t:"11001" in t.replace(' ','')),
 ("What is the last element of the list [10, 20, 30, 40]? Reply with only the number.","text",lambda t:bool(re.search(r'\b40\b',t))),
 ("Capitalize the first letter of the word 'python'. Reply with only the resulting word.","text",lambda t:"Python" in t),
],
"M":[
 ("Write a Python function `is_palindrome(s)` that ignores case and non-alphanumeric characters. Output only a Python code block.","code",
  "assert is_palindrome('A man, a plan, a canal: Panama')==True; assert is_palindrome('race a car')==False"),
 ("Write a Python function `word_count(s)` returning a dict mapping each lowercase word to its count (split on whitespace). Output only a Python code block.","code",
  "assert word_count('the cat the dog')=={'the':2,'cat':1,'dog':1}"),
 ("Write a Python function `celsius_to_fahrenheit(c)` returning the Fahrenheit value. Output only a Python code block.","code",
  "assert celsius_to_fahrenheit(0)==32 and celsius_to_fahrenheit(100)==212"),
 ("Rewrite this as a single list comprehension assigned to `result`: a loop over range(10) appending x*x when x is even. Output only the one assignment line.","code",
  "assert result==[0,4,16,36,64]"),
 ("Provide Python: `import re` and a function `is_phone(s)` returning True iff s matches a US phone like 123-456-7890. Output only a Python code block.","code",
  "assert is_phone('123-456-7890')==True and is_phone('1234567890')==False"),
 ("A train leaves at 2:15 PM and arrives at 4:45 PM the same day. How many minutes was the trip? Reply with only the number.","text",lambda t:"150" in t.replace(',','')),
 ("Write `flatten(lst)` that flattens one level of nesting, e.g. [[1,2],[3,4]] -> [1,2,3,4]. Output only a Python code block.","code",
  "assert flatten([[1,2],[3,4]])==[1,2,3,4] and flatten([[1],[2,3],[]])==[1,2,3]"),
 ("Write a Python assignment to `r` giving the sorted unique values of [3,1,4,1,5,9,2,6]. Output only the one assignment line.","code",
  "assert r==[1,2,3,4,5,6,9]"),
],
"L":[
 ("Write a Python class `Stack` with methods push(x), pop(), peek(), is_empty(), and size(). pop() and peek() on an empty stack must raise IndexError. Output only a Python code block.","code",
  "s=Stack(); assert s.is_empty(); s.push(1); s.push(2); assert s.size()==2 and s.peek()==2 and s.pop()==2 and s.pop()==1 and s.is_empty()\ntry:\n s.pop(); assert False\nexcept IndexError: pass"),
 ("Implement `merge_sort(lst)` returning a new sorted list (use a merge helper). Output only a Python code block.","code",
  "assert merge_sort([3,1,2])==[1,2,3] and merge_sort([])==[] and merge_sort([5,5,1,1])==[1,1,5,5]"),
 ("Write two inverse functions `int_to_roman(n)` (1..3999) and `roman_to_int(s)`. Output only a Python code block.","code",
  "assert int_to_roman(1994)=='MCMXCIV' and roman_to_int('MCMXCIV')==1994 and int_to_roman(roman_to_int('LVIII'))=='LVIII'"),
 ("Write a `BankAccount` class: deposit(amount), withdraw(amount), and a balance property. Withdrawing more than the balance raises ValueError; negative amounts raise ValueError. Output only a Python code block.","code",
  "a=BankAccount(); a.deposit(100); a.withdraw(30); assert a.balance==70\nfor bad in [lambda:a.withdraw(1000), lambda:a.deposit(-1)]:\n try:\n  bad(); assert False\n except ValueError: pass"),
 ("Implement `evaluate(expr)` for a basic integer calculator supporting + - * / and parentheses with correct precedence. Output only a Python code block.","code",
  "assert evaluate('2+3*4')==14 and evaluate('(2+3)*4')==20 and evaluate('10/2-1')==4"),
 ("Write an `LRUCache` class with __init__(capacity), get(key) returning -1 if absent, and put(key,value) evicting the least-recently-used entry when over capacity. Output only a Python code block.","code",
  "c=LRUCache(2); c.put(1,1); c.put(2,2); assert c.get(1)==1; c.put(3,3); assert c.get(2)==-1 and c.get(3)==3"),
 ("Write `group_by_parity(nums)` returning {'even':[...],'odd':[...]} preserving order, and `running_total(nums)` returning the list of cumulative sums. Output only a Python code block.","code",
  "assert group_by_parity([1,2,3,4])=={'even':[2,4],'odd':[1,3]} and running_total([1,2,3])==[1,3,6]"),
 ("Implement `is_valid_sudoku(board)` for a 9x9 board (list of 9 lists, '.' for empty) checking rows, columns, and 3x3 boxes have no duplicate digits. Output only a Python code block.","code",
  "b=[['5','3','.','.','7','.','.','.','.'],['6','.','.','1','9','5','.','.','.'],['.','9','8','.','.','.','.','6','.'],['8','.','.','.','6','.','.','.','3'],['4','.','.','8','.','3','.','.','1'],['7','.','.','.','2','.','.','.','6'],['.','6','.','.','.','.','2','8','.'],['.','.','.','4','1','9','.','.','5'],['.','.','.','.','8','.','.','7','9']]\nassert is_valid_sudoku(b)==True\nb2=[r[:] for r in b]; b2[0][1]='5'; assert is_valid_sudoku(b2)==False"),
],
}
def sh(c,**k): return subprocess.run(c,capture_output=True,text=True,**k)
def load_model(m):
    env=dict(os.environ,LLAMA_HF_REPO=m['repo'],LLAMA_HF_FILE=m['file'],LLAMA_CTX_SIZE=CTX,
             LLAMA_TEMP=m['temp'],LLAMA_TOP_P=m['top_p'],LLAMA_MIN_P=m['min_p'],LLAMA_EXTRA_ARGS=m['extra'])
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=DIR,env=env,capture_output=True)
    for _ in range(100):
        if '"status":"ok"' in sh(["docker","exec",CLA,"curl","-s","-m","3","http://llama:8080/health"]).stdout: return True
        time.sleep(3)
    return False
def ask(prompt,max_tokens=3072):
    body={"model":"local","max_tokens":max_tokens,"messages":[{"role":"user","content":prompt}]}
    t0=time.time()
    r=sh(["docker","exec","-i",CLA,"curl","-s","-m","300","http://llama:8080/v1/messages",
          "-H","Content-Type: application/json","-H","anthropic-version: 2023-06-01","-d","@-"],input=json.dumps(body))
    wall=time.time()-t0
    try:
        d=json.loads(r.stdout)
        text="\n".join(b.get("text","") for b in d.get("content",[]) if b.get("type")=="text")
        out=d.get("usage",{}).get("output_tokens") or 0
        return text,out,wall
    except Exception: return "",0,wall
def grade_code(text,test):
    m=re.search(r'```(?:python)?\s*(.*?)```',text,re.S); code=m.group(1) if m else text
    prog=code+"\n"+test+"\nprint('PASS')\n"
    try: return "PASS" in subprocess.run(["docker","run","--rm","--network","none","-i","python:3-alpine","python3","-c",prog],capture_output=True,text=True,timeout=30).stdout
    except Exception: return False
def run_model(m,limit=None):
    out={}
    for cat,tasks in TASKS.items():
        ts=tasks[:limit] if limit else tasks
        toks=[];walls=[];ok=0
        for prompt,kind,chk in ts:
            text,o,w=ask(prompt); toks.append(o); walls.append(w)
            good = grade_code(text,chk) if kind=="code" else bool(chk(text))
            ok+=good
        n=len(ts); acc=ok/n
        mt=sum(toks)/n; mw=sum(walls)/n
        sps=mw/acc if acc>0 else float('inf')
        out[cat]=(acc,mt,mw,sps,ok,n)
    return out
def fmt(res):
    parts=[]
    for cat in ["S","M","L"]:
        acc,mt,mw,sps,ok,n=res[cat]
        sps_s = "inf" if sps==float('inf') else f"{sps:.0f}"
        parts.append(f"{cat}: acc {ok}/{n} | {mt:.0f}tok | {mw:.1f}s wall | {sps_s}s/solved")
    return "  " + "\n  ".join(parts)
if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--smoke",type=int,default=0);ap.add_argument("--no-reload",action="store_true");ap.add_argument("--only",default="")
    a=ap.parse_args();limit=a.smoke or None
    models=[m for m in MODELS if (not a.only or m["name"] in a.only.split(","))]
    if a.no_reload:
        m=models[0];print(f"### {m['name']} (no reload) ###",flush=True);print(fmt(run_model(m,limit)),flush=True)
    else:
        for m in models:
            print(f"### {m['name']} ###",flush=True)
            if not load_model(m): print("  LOAD FAILED");continue
            print(fmt(run_model(m,limit)),flush=True)
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=DIR,capture_output=True)
    print("restored default",flush=True)
