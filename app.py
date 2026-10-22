import json, os, re, threading, urllib.error, urllib.request
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
QDIR = os.environ.get("QUESTIONS_DIR", os.path.join(BASE, "questions"))
CFG = os.path.join(BASE, "config.json")


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


def read(name):
    with open(path_of(name), encoding="utf-8") as f:
        return parse(f.read())


def write(name, head, qs):
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
    for n in sorted(os.listdir(QDIR)):
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
    update(b["file"], q["n"], exp=text)
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


if __name__ == "__main__":
    os.makedirs(QDIR, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=False)
