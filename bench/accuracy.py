#!/usr/bin/env python3
"""Local accuracy benchmark — AS-CONFIGURED per .env block, fixed 131072 context
for every model. Five auto-graded categories (~68 items/model): logic/reasoning,
computed math, unit-tested code, adversarial tool-calling, and needle retrieval
in an ~88k-token haystack. Math golds are COMPUTED in-process (can't be wrong by
hand); code golds are unit tests; long-context needles are planted. Coarse — read
results as tiers, not a leaderboard. See BENCHMARKS.md (repo root).

Run:  python3 bench/accuracy.py                 # all 6 models, full set
      python3 bench/accuracy.py --smoke 3       # 3 items/category (quick check)
      python3 bench/accuracy.py --only gpt-oss-20b --cats tool,longctx
"""
import json, os, re, subprocess, time, argparse
from math import gcd, isqrt, factorial
from functools import reduce

CLA="lair-claude"
COMPOSE_DIR=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (parent of bench/)
CTX="131072"
MAXTOK=2048   # raised in --thinking mode (reasoning needs room not to truncate)
# Thinking-ON variants for the toggleable models (params only; reasoning routes to
# reasoning_content so Claude Code still sees clean text). --thinking applies these.
THINK_OVERRIDES={
 "GLM-4.7-Flash": dict(temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --chat-template-kwargs {"enable_thinking":true}'),
 "Qwen3.6-35B-A3B": dict(temp="1.0",top_p="0.95",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --presence-penalty 1.5 --chat-template-kwargs {"enable_thinking":true}'),
 "Nemotron-3-Nano": dict(temp="1.0",top_p="1.0",min_p="0.0",extra='--fit on --jinja --kv-unified --chat-template-kwargs {"enable_thinking":true}'),
}
MODELS=[
 dict(name="GLM-4.7-Flash",repo="unsloth/GLM-4.7-Flash-GGUF",file="GLM-4.7-Flash-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3.6-35B-A3B",repo="unsloth/Qwen3.6-35B-A3B-GGUF",file="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",temp="0.7",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --presence-penalty 1.5 --chat-template-kwargs {"enable_thinking":false}'),
 dict(name="Qwen3-Coder-30B",repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",file="Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",temp="0.2",top_p="0.8",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 20 --repeat-penalty 1.05'),
 dict(name="gpt-oss-20b",repo="unsloth/gpt-oss-20b-GGUF",file="gpt-oss-20b-F16.gguf",temp="1.0",top_p="1.0",min_p="0.0",extra='--fit on --jinja --kv-unified --top-k 100'),
 dict(name="Gemma-4-26B",repo="unsloth/gemma-4-26B-A4B-it-GGUF",file="gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf",temp="1.0",top_p="0.95",min_p="0.01",extra='--fit on --jinja --kv-unified --top-k 64'),
 dict(name="Nemotron-3-Nano",repo="unsloth/Nemotron-3-Nano-30B-A3B-GGUF",file="Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf",temp="0.6",top_p="0.95",min_p="0.0",extra='--fit on --jinja --kv-unified'),
]

def _after(t):
    m=list(re.finditer(r'answer\s*[:\-]', t, re.I)); return t[m[-1].end():] if m else t
def last_int(text):
    n=re.findall(r'-?\d[\d,]*', text.replace(' ','')); return n[-1].replace(',','') if n else None
def gi(val): return lambda t: last_int(_after(t))==str(val)
def gword(*subs): return lambda t: all(s.lower() in _after(t).lower() for s in subs)
def gtoken(tok): return lambda t: re.search(r'\b'+re.escape(tok)+r'\b', _after(t), re.I) is not None

# ---- MATH (gold computed now) --------------------------------------------
def _fib20():
    f=[1,1]
    while len(f)<20: f.append(f[-1]+f[-2])
    return sum(f)
def _coins(): return sum(1 for q in range(2) for d in range(3) for n in range(6) for p in range(26) if 25*q+10*d+5*n+p==25)
def _prime100():
    ps=[]
    n=2
    while len(ps)<100:
        if all(n%p for p in ps if p*p<=n): ps.append(n)
        n+=1
    return ps[-1]
def _isprime(x): return x>1 and all(x%i for i in range(2,isqrt(x)+1))
MATH=[
 ("Sum of all 3-digit integers (100 to 999 inclusive) divisible by 7?", sum(n for n in range(100,1000) if n%7==0)),
 ("How many integers from 1 to 1000 inclusive are divisible by none of 2, 3, or 5?", sum(1 for n in range(1,1001) if n%2 and n%3 and n%5)),
 ("What is 17^3 mod 100?", pow(17,3,100)),
 ("How many trailing zeros does 100! (100 factorial) have?", sum(100//5**k for k in range(1,4))),
 ("How many distinct arrangements are there of the letters in the word MISSISSIPPI?", factorial(11)//(factorial(4)*factorial(4)*factorial(2))),
 ("What is the sum of the first 20 Fibonacci numbers, where the sequence starts 1, 1, 2, 3, 5, ...?", _fib20()),
 ("What is the greatest common divisor of 1071 and 462?", gcd(1071,462)),
 ("How many ways can you make 25 cents using pennies, nickels, dimes, and quarters?", _coins()),
 ("What is the 100th prime number?", _prime100()),
 ("How many diagonals does a convex 12-sided polygon have?", 12*9//2),
 ("What is the sum of all positive divisors of 28?", sum(d for d in range(1,29) if 28%d==0)),
 ("What is the least common multiple of 12, 15, and 20?", reduce(lambda a,b:a*b//gcd(a,b),[12,15,20])),
 ("In how many ways can 8 distinct people be seated around a round table (rotations considered identical)?", factorial(7)),
 ("Define a(1)=2 and a(n)=2*a(n-1)+1. What is a(5)?", 47),
 ("What is the value of floor(sqrt(2026))?", isqrt(2026)),
 ("How many ordered pairs (a, b) of positive integers satisfy a + b = 999 where neither a nor b contains the digit 0?", sum(1 for a in range(1,999) if '0' not in str(a) and '0' not in str(999-a))),
 ("What is the remainder when 2^2026 is divided by 1000?", pow(2,2026,1000)),
 ("How many positive integers less than 100 are coprime to 100?", sum(1 for n in range(1,100) if gcd(n,100)==1)),
 ("What is the largest prime factor of 13195?", max(p for p in range(2,13196) if 13195%p==0 and _isprime(p))),
 ("A and B are positive integers with A+B=15 and A*B=56. What is the larger of the two?", 8),
]
# ---- REASON (verifiable logic) -------------------------------------------
REASON=[
 ("A man must ferry a wolf, a goat, and a cabbage across a river; his boat holds him plus one item. Alone, the wolf eats the goat and the goat eats the cabbage. Minimum one-way crossings? End with 'Answer: <number>'.", gi(7)),
 ("A snail at the bottom of a 10 m well climbs 3 m each day and slips 2 m each night. On which day does it first reach the top? End with 'Answer: <number>'.", gi(8)),
 ("In a family, each daughter has equal numbers of brothers and sisters, and each son has twice as many sisters as brothers. How many sons are there? End with 'Answer: <number>'.", gi(3)),
 ("What is the next term in the sequence 1, 11, 21, 1211, 111221, ... ? End with 'Answer: <number>'.", gi(312211)),
 ("Three guests pay $10 each for a $30 room; the clerk refunds $5 via a bellboy who keeps $2 and returns $1 to each guest. The '$27 + $2 = $29' argument claims a dollar is missing. Is a dollar actually missing? End with 'Answer: <yes/no>'.", gtoken("no")),
 ("A 3-digit number has digits summing to 13; its hundreds digit is twice its units digit, and its tens digit is one more than its units digit. What is the number? End with 'Answer: <number>'.", gi(643)),
 ("How many squares of any size are there on a standard 8x8 chessboard? End with 'Answer: <number>'.", gi(204)),
 ("100 bulbs in a row start off. On pass k (k=1..100) you toggle every k-th bulb. After 100 passes, how many bulbs are on? End with 'Answer: <number>'.", gi(10)),
 ("If today is Wednesday, what day of the week will it be 100 days from now? End with 'Answer: <day>'.", gtoken("friday")),
 ("You overtake the runner in second place in a race. What place are you in now? End with 'Answer: <number or word>'.", lambda t: gtoken("second")(t) or gi(2)(t)),
 ("A is taller than B; C is shorter than B; D is taller than A. Who is the tallest? Reply with the single letter. End with 'Answer: <letter>'.", gtoken("D")),
 ("What is the minimum number of weighings on a balance scale needed to guarantee finding the single heavier coin among 9 otherwise-identical coins? End with 'Answer: <number>'.", gi(2)),
]
# ---- CODE (harder; unit tests are the gold) ------------------------------
CODE=[
 ("Write a Python function `longest_palindrome(s)` returning a longest palindromic substring of s. Output only a Python code block.",
  "assert longest_palindrome('babad') in ('bab','aba'); assert longest_palindrome('cbbd')=='bb'"),
 ("Write `merge_intervals(intervals)` merging overlapping [start,end] intervals, returned sorted by start. Output only a Python code block.",
  "assert merge_intervals([[1,3],[2,6],[8,10],[15,18]])==[[1,6],[8,10],[15,18]]; assert merge_intervals([[1,4],[4,5]])==[[1,5]]"),
 ("Write `is_balanced(s)` returning True iff the brackets ()[]{} in s are balanced and correctly nested. Output only a Python code block.",
  "assert is_balanced('([{}])')==True; assert is_balanced('(]')==False; assert is_balanced('(')==False"),
 ("Write `roman_to_int(s)` converting a Roman numeral to its integer value. Output only a Python code block.",
  "assert roman_to_int('MCMXCIV')==1994; assert roman_to_int('LVIII')==58; assert roman_to_int('IV')==4"),
 ("Write `max_subarray_sum(nums)` returning the maximum sum of any contiguous non-empty subarray. Output only a Python code block.",
  "assert max_subarray_sum([-2,1,-3,4,-1,2,1,-5,4])==6; assert max_subarray_sum([-1,-2,-3])==-1"),
 ("Write `edit_distance(a, b)` returning the Levenshtein edit distance between two strings. Output only a Python code block.",
  "assert edit_distance('kitten','sitting')==3; assert edit_distance('','abc')==3; assert edit_distance('abc','abc')==0"),
 ("Write `coin_change(coins, amount)` returning the fewest coins to make amount, or -1 if impossible. Output only a Python code block.",
  "assert coin_change([1,2,5],11)==3; assert coin_change([2],3)==-1; assert coin_change([1],0)==0"),
 ("Write `num_islands(grid)` counting connected groups of '1' (4-directionally) in a list-of-strings grid. Output only a Python code block.",
  "assert num_islands(['11000','11000','00100','00011'])==3; assert num_islands(['111','010','111'])==1"),
 ("Write `length_of_longest_substring(s)` returning the length of the longest substring without repeating characters. Output only a Python code block.",
  "assert length_of_longest_substring('abcabcbb')==3; assert length_of_longest_substring('bbbbb')==1; assert length_of_longest_substring('')==0"),
 ("Write `is_prime(n)` returning True iff n is a prime number. Output only a Python code block.",
  "assert is_prime(2)==True; assert is_prime(1)==False; assert is_prime(97)==True; assert is_prime(100)==False"),
 ("Write `rotate_right(lst, k)` returning the list rotated right by k positions (k may exceed len). Output only a Python code block.",
  "assert rotate_right([1,2,3,4,5],2)==[4,5,1,2,3]; assert rotate_right([1,2,3],4)==[3,1,2]; assert rotate_right([],3)==[]"),
 ("Write `group_anagrams(strs)` returning a list of groups of anagrams; inner groups and outer list may be in any order. Output only a Python code block.",
  "r=group_anagrams(['eat','tea','tan','ate','nat','bat']); s=sorted(sorted(g) for g in r); assert s==sorted([sorted(x) for x in [['eat','tea','ate'],['tan','nat'],['bat']]])"),
]
# ---- TOOLS (adversarial; et may be str, tuple, or None) ------------------
TOOLS=[
 {"name":"get_weather","description":"Get weather for a location","input_schema":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}},
 {"name":"calculator","description":"Evaluate a math expression","input_schema":{"type":"object","properties":{"expression":{"type":"string"}},"required":["expression"]}},
 {"name":"web_search","description":"Search the web","input_schema":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}},
 {"name":"send_email","description":"Send an email","input_schema":{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["to","subject","body"]}},
 {"name":"create_calendar_event","description":"Create a calendar event","input_schema":{"type":"object","properties":{"title":{"type":"string"},"datetime":{"type":"string"}},"required":["title","datetime"]}},
 {"name":"currency_convert","description":"Convert between currencies","input_schema":{"type":"object","properties":{"amount":{"type":"number"},"from":{"type":"string"},"to":{"type":"string"}},"required":["amount","from","to"]}},
 {"name":"get_stock_price","description":"Get current stock price for a ticker/company","input_schema":{"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}},
 {"name":"set_reminder","description":"Set a personal reminder","input_schema":{"type":"object","properties":{"text":{"type":"string"},"time":{"type":"string"}},"required":["text","time"]}},
]
TOOLCALL=[
 ("What's Apple's current stock price?","get_stock_price","apple"),
 ("Convert 250 euros to US dollars.","currency_convert","250"),
 ("Schedule a dentist appointment titled 'Dentist' for next Monday at 9am.","create_calendar_event","dentist"),
 ("Send an email to john@corp.com with subject 'Q3 Report' and body 'See attached.'","send_email","john@corp.com"),
 ("What's the weather forecast for Berlin this weekend?","get_weather","berlin"),
 ("Search the web for the latest research on CRISPR gene editing.","web_search","crispr"),
 ("Remind me to call mom at 6pm today.","set_reminder","mom"),
 ("Convert 1000 Japanese yen to euros.","currency_convert","1000"),
 ("Use the calculator to compute 15% of 240.","calculator","240"),
 ("Find me a highly rated pasta recipe.","web_search","pasta"),
 ("What is the capital of Japan?",None,None),
 ("Thanks, that's everything I needed!",None,None),
 ("What is 2 + 2?",None,None),
 ("I need the weather in Oslo and also Tesla's stock price.",("get_weather","get_stock_price"),None),
]
# ---- LONG CONTEXT (deeper haystack, 10 needles incl. multi-hop) ----------
NEEDLES=[
 ("The secret access code for the Vega project is 74815.","What is the secret access code for the Vega project?","74815"),
 ("Dr. Eleanor Finch was appointed chief archivist in the year 1987.","In what year was Dr. Eleanor Finch appointed chief archivist?","1987"),
 ("The rare mineral discovered in the Kessler caves is called luminite.","What is the name of the rare mineral discovered in the Kessler caves?","luminite"),
 ("The maximum capacity of the Hollowbrook reservoir is 38500 cubic meters.","What is the maximum capacity of the Hollowbrook reservoir, in cubic meters?","38500"),
 ("Captain Mira Solanke commanded the vessel Aurora during the 2031 expedition.","Who commanded the vessel Aurora during the 2031 expedition?","solanke"),
 ("The annual membership fee for the Lyceum society is 240 credits.","What is the annual membership fee for the Lyceum society, in credits?","240"),
 ("The Orion satellite was launched on the 14th of March.","On what date was the Orion satellite launched?","14"),
 ("The head chef at the Bluebell restaurant is named Marco Reyes.","Who is the head chef at the Bluebell restaurant?","reyes"),
 ("The annual rainfall in Greendale averages 812 millimeters.","What is the average annual rainfall in Greendale, in millimeters?","812"),
]
# multi-hop: two facts above must be combined
MULTIHOP=[("Combining the facts in the document: multiply the Vega project access code by the number of cubic meters of the Hollowbrook reservoir is NOT needed; instead, what is the Vega access code plus the Lyceum membership fee?", str(74815+240)),]
# ---- GENERAL (non-code everyday knowledge: is a model too code-specialized?) --
def gany(*subs): return lambda t: any(s.lower() in t.lower() for s in subs)
GENERAL=[
 ("What is the capital city of Canada? Answer with just the city name.", gany("ottawa")),
 ("Who painted the Mona Lisa? Give the artist's name.", gany("leonardo","vinci")),
 ("In what year did the Berlin Wall fall? Just the year.", gtoken("1989")),
 ("What is the largest planet in our solar system? One word.", gany("jupiter")),
 ("How many continents are there on Earth? Just the number.", gtoken("7")),
 ("What gas do plants absorb from the air during photosynthesis? Name the gas.", gany("carbon dioxide","co2","co₂")),
 ("Who wrote the novel '1984'? Give the author's surname.", gany("orwell")),
 ("What is the official currency of Japan? One word.", gany("yen")),
 ("What is the tallest mountain on Earth above sea level? Name it.", gany("everest")),
 ("Which is the largest ocean on Earth? One word.", gany("pacific")),
 ("At what temperature in degrees Fahrenheit does water freeze? Just the number.", gtoken("32")),
 ("Who was the first President of the United States? Give the surname.", gany("washington")),
 ("What is the primary language spoken in Brazil? One word.", gany("portuguese")),
 ("Which planet is known as the Red Planet? One word.", gany("mars")),
 ("Translate the English phrase 'thank you' into French.", gany("merci")),
]

def build_haystack(depth_blocks=120):
    block=("The quarterly logistics review noted that routine shipments proceeded on schedule "
           "and that no exceptional events were recorded for the period under consideration. ")*depth_blocks
    parts=["Read the following document carefully; you will be asked a question about it.\n\n"]
    for needle,_,_ in NEEDLES:
        parts.append(block); parts.append(needle+" "); parts.append(block)
    return "".join(parts)

def sh(c,**k): return subprocess.run(c,capture_output=True,text=True,**k)
def load_model(m):
    env=dict(os.environ,LLAMA_HF_REPO=m['repo'],LLAMA_HF_FILE=m['file'],LLAMA_CTX_SIZE=CTX,
             LLAMA_TEMP=m['temp'],LLAMA_TOP_P=m['top_p'],LLAMA_MIN_P=m['min_p'],LLAMA_EXTRA_ARGS=m['extra'])
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=COMPOSE_DIR,env=env,capture_output=True)
    for _ in range(100):
        if '"status":"ok"' in sh(["docker","exec",CLA,"curl","-s","-m","3","http://llama:8080/health"]).stdout: return True
        time.sleep(3)
    return False
def ask(prompt,tools=None,max_tokens=2048):
    body={"model":"local","max_tokens":max_tokens,"messages":[{"role":"user","content":prompt}]}
    if tools: body["tools"]=tools
    r=sh(["docker","exec","-i",CLA,"curl","-s","-m","300","http://llama:8080/v1/messages",
          "-H","Content-Type: application/json","-H","anthropic-version: 2023-06-01","-d","@-"],input=json.dumps(body))
    try: return json.loads(r.stdout)
    except Exception: return {"content":[]}
def atext(r): return "\n".join(b.get("text","") for b in r.get("content",[]) if b.get("type")=="text")
def tuses(r): return [b for b in r.get("content",[]) if b.get("type")=="tool_use"]
def grade_tool(r,et,ea):
    tus=tuses(r)
    if et is None: return len(tus)==0
    if not tus: return False
    names=et if isinstance(et,(tuple,list)) else (et,)
    if tus[0].get("name") not in names: return False
    return ea is None or ea.lower() in json.dumps(tus[0].get("input",{})).lower()
def grade_code(text,test):
    m=re.search(r'```(?:python)?\s*(.*?)```',text,re.S); code=m.group(1) if m else text
    prog=code+"\n"+test+"\nprint('PASS')\n"
    try: return "PASS" in subprocess.run(["docker","run","--rm","--network","none","-i","python:3-alpine","python3","-c",prog],capture_output=True,text=True,timeout=30).stdout
    except Exception: return False

def run_model(m,limit=None,cats=None):
    res={}
    def lim(xs): return xs[:limit] if limit else xs
    if not cats or "reason" in cats:
        c=0;it=lim(REASON)
        for p,chk in it:
            try: c+=bool(chk(atext(ask(p,max_tokens=MAXTOK))))
            except Exception: pass
        res["reason"]=(c,len(it))
    if not cats or "math" in cats:
        c=0;it=lim(MATH)
        for q,g in it: c+= last_int(_after(atext(ask(f"Solve step by step, then end with 'Answer: <number>'.\n\n{q}",max_tokens=MAXTOK))))==str(g)
        res["math"]=(c,len(it))
    if not cats or "code" in cats:
        c=0;it=lim(CODE)
        for p,test in it: c+=grade_code(atext(ask(p,max_tokens=MAXTOK)),test)
        res["code"]=(c,len(it))
    if not cats or "tool" in cats:
        c=0;it=lim(TOOLCALL)
        for p,et,ea in it: c+=grade_tool(ask(p,tools=TOOLS,max_tokens=1024),et,ea)
        res["tool"]=(c,len(it))
    if not cats or "general" in cats:
        c=0;it=lim(GENERAL)
        for p,chk in it:
            try: c+=bool(chk(atext(ask(p,max_tokens=MAXTOK))))
            except Exception: pass
        res["general"]=(c,len(it))
    if not cats or "longctx" in cats:
        hay=build_haystack(); items=lim(NEEDLES); c=0
        for _,q,g in items:
            t=atext(ask(hay+"\n\nQuestion: "+q+"\nAnswer concisely.",max_tokens=200))
            c+= g.lower().replace(',','') in t.lower().replace(',','')
        # multi-hop
        mh=0
        if not limit:
            for q,g in MULTIHOP:
                t=atext(ask(hay+"\n\nQuestion: "+q+"\nAnswer concisely.",max_tokens=400)); mh+= g in t.replace(',','')
            c+=mh; n=len(items)+len(MULTIHOP)
        else: n=len(items)
        res["longctx"]=(c,n)
    return res
ORDER=["reason","math","code","tool","general","longctx"]
def fmt(res):
    parts=[];tot=0;den=0;pcts=[]
    for k in ORDER:
        if k not in res: continue
        c,n=res[k];pct=100*c/n if n else 0;pcts.append(pct);parts.append(f"{k} {c}/{n} ({pct:.0f}%)");tot+=c;den+=n
    return f"  {' | '.join(parts)}  || composite {sum(pcts)/len(pcts):.1f}%  (raw {tot}/{den})"
if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--smoke",type=int,default=0);ap.add_argument("--no-reload",action="store_true");ap.add_argument("--only",default="");ap.add_argument("--cats",default="");ap.add_argument("--thinking",action="store_true")
    a=ap.parse_args();limit=a.smoke or None;cats=a.cats.split(",") if a.cats else None
    models=[m for m in MODELS if (not a.only or m["name"] in a.only.split(","))]
    if a.thinking:
        MAXTOK=4096
        models=[{**m,**THINK_OVERRIDES[m["name"]]} for m in models if m["name"] in THINK_OVERRIDES]
        print(f"# THINKING-ON mode: {[m['name'] for m in models]} (MAXTOK={MAXTOK})",flush=True)
    if a.no_reload:
        m=models[0];print(f"### {m['name']} (no reload) ###",flush=True);print(fmt(run_model(m,limit,cats)),flush=True)
    else:
        for m in models:
            print(f"### {m['name']} ###",flush=True)
            if not load_model(m): print("  LOAD FAILED");continue
            print(fmt(run_model(m,limit,cats)),flush=True)
    subprocess.run(["docker","compose","up","-d","--force-recreate","--no-deps","llama"],cwd=COMPOSE_DIR,capture_output=True)
    print("restored default (Qwen3.6)",flush=True)
