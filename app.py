import base64, json, os, re, threading, urllib.error, urllib.request
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
QDIR = os.environ.get("QUESTIONS_DIR", os.path.join(BASE, "questions"))
CFG = os.path.join(BASE, "config.json")

# ---- GitHub storage (used only if GITHUB_TOKEN + DATA_REPO are set) ----
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GH_REPO = os.environ.get("DATA_REPO", "")
GH_BRANCH = os.environ.get("DATA_BRANCH", "data")
GH_PATH = os.environ.get("DATA_PATH", "questions").strip("/")
GH = bool(GH_TOKEN and GH_REPO)
cache = {}        # name -> [text, sha]
names_cache = []  # list of file names


def gh_req(method, path, body=None, query=""):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{path}{query}"
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {GH_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "practice-hub",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def gh_fetch(name):
    j = gh_req("GET", f"{GH_PATH}/{name}", query=f"?ref={GH_BRANCH}")
    text = base64.b64decode(j["content"]).decode("utf-8")
    cache[name] = [text, j["sha"]]
    return cache[name]


def gh_put(name, text):
    if name not in cache:
        gh_fetch(name)
    for attempt in range(2):
        body = {"message": f"update {name}",
                "content": base64.b64encode(text.encode("utf-8")).decode(),
                "sha": cache[name][1], "branch": GH_BRANCH}
        try:
            res = gh_req("PUT", f"{GH_PATH}/{name}", body)
            cache[name] = [text, res["content"]["sha"]]
            return
        except urllib.error.HTTPError as e:
            if e.code in (409, 422) and attempt == 0:
                cache[name][1] = gh_fetch(name)[1]  # stale sha: refresh and retry
                cache[name][0] = text
                continue
            raise


def cfg():
    d = {"gemini_key": os.environ.get("GEMINI_API_KEY", ""),
         "model": os.environ.get("GEMINI_MODEL", "gemini-3.6-flash-lite")}
    try:
        with open(CFG, encoding="utf-8") as f:
            d.update({k: v for k, v in json.load(f).items() if v})
    except Exception:
        pass
    return d


lock = threading.Lock()

FN = re.compile(r"^(GK|[BCPEWM])(\d+)\.md$", re.I)
HEAD = re.compile(r"^[ \t]*###\s*Q\s*(\d+)[^\n]*\n", re.I | re.M)
SEC = re.compile(r"^[ \t]*##\s*(answer|explanation|status)[ \t]*:?[ \t]*([^\n]*)\n?", re.I | re.M)
KEYMAP = {"answer": "ans", "explanation": "exp", "status": "st"}


def parse(t):
    ms = list(HEAD.finditer(t))
    head = t[: ms[0].start()] if ms else t
    qs = []
    for i, m in enumerate(ms):
        blk = t[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(t)]
        ps = SEC.split(blk)
        q = {"n": int(m.group(1)), "stem": [], "opts": [], "ans": "", "exp": "", "st": ""}
        for l in ps[0].split("\n"):
            o = re.match(r"^\s*[*\-]\s+(.*)", l)
            if o:
                q["opts"].append(o.group(1).strip())
            elif l.strip():
                q["stem"].append(l.strip())
        for k in range(1, len(ps), 3):
            q[KEYMAP[ps[k].lower()]] = ((ps[k + 1] or "") + "\n" + (ps[k + 2] or "")).strip()
        v = q["st"].lower()
        q["st"] = ("incorrect" if re.search("incorrect|wrong", v) else
                   "correct" if "correct" in v else "skipped" if "skip" in v else "")
        qs.append(q)
    return head, qs


def ser(head, qs):
    out = []
    for q in qs:
        stem = "\n".join(q["stem"]) + "\n\n" if q["stem"] else ""
        opts = "\n".join("* " + o for o in q["opts"])
        out.append(f"### Q {q['n']}\n\n{stem}{opts}\n\n## answer\n{q['ans']}\n\n## explanation\n{q['exp']}\n\n## status\n{q['st']}\n")
    return head + "\n".join(out)


def ans_index(q):
    a = re.sub(r"[*`]", "", q["ans"]).strip()
    m = re.match(r"^\(?([A-Da-d1-4ক-ঘ১-৪])\)?(?:[\s.):]|$)", a)
    if m:
        return "abcd1234কখগঘ১২৩৪".index(m.group(1).lower()) % 4
    for i, o in enumerate(q["opts"]):
        if o.lower() == a.lower():
            return i
    return -1


def path_of(name):
    if not FN.match(name or ""):
        raise ValueError("bad file name")
    return os.path.join(QDIR, name)


def list_names():
    global names_cache
    if GH:
        if not names_cache:
            items = gh_req("GET", GH_PATH, query=f"?ref={GH_BRANCH}")
            names_cache = [i["name"] for i in items if i["type"] == "file" and FN.match(i["name"])]
        return names_cache
    return [n for n in os.listdir(QDIR) if FN.match(n)]


def read(name):
    if not FN.match(name or ""):
        raise ValueError("bad file name")
    if GH:
        if name not in cache:
            gh_fetch(name)
        return parse(cache[name][0])
    with open(path_of(name), encoding="utf-8") as f:
        return parse(f.read())


def write(name, head, qs):
    if GH:
        if not FN.match(name or ""):
            raise ValueError("bad file name")
        gh_put(name, ser(head, qs))
        return
    p = path_of(name)
    with open(p + ".tmp", "w", encoding="utf-8", newline="\n") as f:
        f.write(ser(head, qs))
    os.replace(p + ".tmp", p)


def update(name, n, **fields):
    with lock:
        head, qs = read(name)
        q = next((x for x in qs if x["n"] == n), None)
        if not q:
            raise ValueError("question not found")
        q.update(fields)
        write(name, head, qs)
        return q


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/data")
def data():
    out = []
    for n in sorted(list_names()):
        m = FN.match(n)
        if not m:
            continue
        _, qs = read(n)
        for q in qs:
            out.append({"file": n, "k": m.group(1).upper(), "fn": int(m.group(2)), "n": q["n"],
                        "stem": "\n".join(q["stem"]), "opts": q["opts"], "ans": ans_index(q),
                        "exp": q["exp"], "st": q["st"]})
    out.sort(key=lambda x: (x["k"], x["fn"], x["n"]))
    return jsonify(out)


@app.post("/api/reload")
def reload_cache():
    # re-pull everything from GitHub (use after editing question files on GitHub)
    with lock:
        cache.clear()
        names_cache.clear()
    return jsonify(ok=True)


@app.post("/api/status")
def status():
    b = request.get_json()
    if b.get("status") not in ("correct", "incorrect", "skipped"):
        return jsonify(error="bad status"), 400
    try:
        update(b["file"], int(b["n"]), st=b["status"])
    except Exception as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


@app.post("/api/explain")
def explain():
    b = request.get_json()
    c = cfg()
    if not c["gemini_key"]:
        return jsonify(error="Add your Gemini API key in Settings"), 400
    try:
        _, qs = read(b["file"])
        q = next(x for x in qs if x["n"] == int(b["n"]))
    except Exception as e:
        return jsonify(error=str(e)), 400
    a = ans_index(q)
    prompt = (
        "Question:\n" + "\n".join(q["stem"]) + "\nOptions:\n"
        + "\n".join(f"{'ABCD'[i]}. {o}" for i, o in enumerate(q["opts"]))
        + f"\nCorrect answer: {'ABCD'[a] if a >= 0 else q['ans']}\n\n"
        "Explain why this answer is correct. Format: one short paragraph, or a small markdown table "
        "if a comparison helps. Write in the same language as the question. Use LaTeX with $...$ for "
        "formulas and chemical equations. No headings, no preamble."
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{c['model']}:generateContent"
    req = urllib.request.Request(
        url, data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": c["gemini_key"]})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.load(r)
        text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
    except urllib.error.HTTPError as e:
        return jsonify(error=f"Gemini {e.code}: {e.read().decode()[:300]}"), 502
    except Exception as e:
        return jsonify(error=str(e)), 502
    text = re.sub(r"^#+\s*", "", text, flags=re.M).strip()
    try:
        update(b["file"], q["n"], exp=text)
    except Exception as e:
        return jsonify(error=f"Save failed: {e}"), 502
    return jsonify(exp=text)


@app.get("/api/settings")
def get_settings():
    c = cfg()
    return jsonify(model=c["model"], has_key=bool(c["gemini_key"]), hint=c["gemini_key"][-4:])


@app.post("/api/settings")
def set_settings():
    b = request.get_json() or {}
    d = {}
    try:
        with open(CFG, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        pass
    if (b.get("gemini_key") or "").strip():
        d["gemini_key"] = b["gemini_key"].strip()
    if (b.get("model") or "").strip():
        d["model"] = b["model"].strip()
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(d, f)
    return jsonify(ok=True)


@app.get("/api/health")
def health():
    return jsonify(github=GH, repo=GH_REPO, branch=GH_BRANCH, path=GH_PATH,
                   token_set=bool(GH_TOKEN))


if __name__ == "__main__":
    if not GH:
        os.makedirs(QDIR, exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0" if os.environ.get("PORT") else "127.0.0.1", port=port, debug=False)
