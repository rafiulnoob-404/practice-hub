import atexit, base64, json, os, re, threading, time, urllib.error, urllib.request, psutil
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
QDIR = os.environ.get("QUESTIONS_DIR", os.path.join(BASE, "questions"))
FDIR = os.environ.get("FLASHCARD_DIR", os.path.join(BASE, "flashcard"))
FC_FILE = "card.md"

# ---- GitHub storage (used only if GITHUB_TOKEN + DATA_REPO are set) ----
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GH_REPO = os.environ.get("DATA_REPO", "")
GH_BRANCH = os.environ.get("DATA_BRANCH", "data")
GH_PATH = os.environ.get("DATA_PATH", "questions").strip("/")
GH_FC = os.environ.get("DATA_FC_PATH", "flashcard").strip("/")
SYNC_SECS = int(os.environ.get("SYNC_SECS", "300"))  # batch commits every 5 min
GH = bool(GH_TOKEN and GH_REPO)

LOC = {"q": QDIR, "f": FDIR}
GHP = {"q": GH_PATH, "f": GH_FC}
cache = {}        # (kind, name) -> [text, sha]
dirty = set()     # keys changed but not yet committed to GitHub
names_cache = []
lock = threading.RLock()


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


def gh_fetch(kind, name):
    try:
        j = gh_req("GET", f"{GHP[kind]}/{name}", query=f"?ref={GH_BRANCH}")
        v = [base64.b64decode(j["content"]).decode("utf-8"), j["sha"]]
    except urllib.error.HTTPError as e:
        if e.code != 404 or kind != "f":
            raise
        v = ["", None]  # flashcard file doesn't exist yet
    cache[(kind, name)] = v
    return v


def gh_commit(key):
    kind, name = key
    path = f"{GHP[kind]}/{name}"
    for attempt in range(2):
        text, sha = cache[key]
        body = {"message": f"update {name}",
                "content": base64.b64encode(text.encode("utf-8")).decode(),
                "branch": GH_BRANCH}
        if sha:
            body["sha"] = sha
        try:
            res = gh_req("PUT", path, body)
            cache[key][1] = res["content"]["sha"]
            return
        except urllib.error.HTTPError as e:
            if e.code in (409, 422) and attempt == 0:
                try:
                    cache[key][1] = gh_req("GET", path, query=f"?ref={GH_BRANCH}")["sha"]
                except urllib.error.HTTPError as e2:
                    if e2.code != 404:
                        raise
                    cache[key][1] = None
                continue
            raise


def flush():
    """Commit all pending files to GitHub (one commit per changed file)."""
    with lock:
        for key in list(dirty):
            try:
                gh_commit(key)
                dirty.discard(key)
            except Exception as e:
                print(f"[sync] {key} failed: {e}", flush=True)
        return len(dirty)


def sync_loop():
    while True:
        time.sleep(SYNC_SECS)
        flush()


if GH:
    threading.Thread(target=sync_loop, daemon=True).start()
    atexit.register(flush)


def load(kind, name):
    if GH:
        if (kind, name) not in cache:
            gh_fetch(kind, name)
        return cache[(kind, name)][0]
    try:
        with open(os.path.join(LOC[kind], name), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def save(kind, name, text):
    if GH:
        if (kind, name) not in cache:
            gh_fetch(kind, name)
        cache[(kind, name)][0] = text
        dirty.add((kind, name))   # committed later by sync_loop
        return
    os.makedirs(LOC[kind], exist_ok=True)
    p = os.path.join(LOC[kind], name)
    with open(p + ".tmp", "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(p + ".tmp", p)


# ---------------- questions ----------------
FN = re.compile(r"^(GK|[BCPEWMT])(\d+)\.md$", re.I)
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


def list_names():
    global names_cache
    if GH:
        if not names_cache:
            items = gh_req("GET", GH_PATH, query=f"?ref={GH_BRANCH}")
            names_cache = [i["name"] for i in items if i["type"] == "file" and FN.match(i["name"])]
        return names_cache
    os.makedirs(QDIR, exist_ok=True)
    return [n for n in os.listdir(QDIR) if FN.match(n)]


def read(name):
    if not FN.match(name or ""):
        raise ValueError("bad file name")
    return parse(load("q", name))


def write(name, head, qs):
    if not FN.match(name or ""):
        raise ValueError("bad file name")
    save("q", name, ser(head, qs))


def update(name, n, **fields):
    with lock:
        head, qs = read(name)
        q = next((x for x in qs if x["n"] == n), None)
        if not q:
            raise ValueError("question not found")
        q.update(fields)
        write(name, head, qs)
        return q


# ---------------- flashcards (flashcard/card.md) ----------------
CARD_HEAD = re.compile(r"^### Card (\d+)[ \t]*\n(?:<!-- color: (#[0-9a-fA-F]{6}) -->[ \t]*\n)?", re.M)
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def parse_cards(t):
    ms = list(CARD_HEAD.finditer(t))
    out = []
    for i, m in enumerate(ms):
        body = t[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(t)].strip()
        out.append({"id": int(m.group(1)), "color": m.group(2) or "#fff3a3", "body": body})
    return out


def ser_cards(cs):
    return "# Flashcards\n\n" + "\n".join(
        f"### Card {c['id']}\n<!-- color: {c['color']} -->\n\n{c['body']}\n" for c in cs)


def clean_body(b):
    b = (b or "").strip()
    return re.sub(r"^(### Card )", r"\\\1", b, flags=re.M)  # keep body from faking a card header


def cards_read():
    return parse_cards(load("f", FC_FILE))


def cards_write(cs):
    save("f", FC_FILE, ser_cards(cs))


@app.get("/api/cards")
def cards_list():
    with lock:
        return jsonify(cards_read())


@app.post("/api/cards")
def cards_add():
    b = request.get_json() or {}
    body = clean_body(b.get("body"))
    if not body:
        return jsonify(error="empty card"), 400
    color = b.get("color") if COLOR.match(b.get("color") or "") else "#fff3a3"
    with lock:
        cs = cards_read()
        c = {"id": max([x["id"] for x in cs], default=0) + 1, "color": color, "body": body}
        cs.append(c)
        cards_write(cs)
    return jsonify(c)


@app.put("/api/cards/<int:cid>")
def cards_edit(cid):
    b = request.get_json() or {}
    body = clean_body(b.get("body"))
    if not body:
        return jsonify(error="empty card"), 400
    with lock:
        cs = cards_read()
        c = next((x for x in cs if x["id"] == cid), None)
        if not c:
            return jsonify(error="not found"), 404
        c["body"] = body
        if COLOR.match(b.get("color") or ""):
            c["color"] = b["color"]
        cards_write(cs)
    return jsonify(c)


@app.delete("/api/cards/<int:cid>")
def cards_del(cid):
    with lock:
        cs = [x for x in cards_read() if x["id"] != cid]
        cards_write(cs)
    return jsonify(ok=True)


# ---------------- routes ----------------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/data")
def data():
    out = []
    with lock:
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
    # re-pull from GitHub; pending (uncommitted) files are pushed first and never dropped
    flush()
    with lock:
        for k in list(cache):
            if k not in dirty:
                del cache[k]
        names_cache.clear()
    return jsonify(ok=True)


@app.post("/api/sync")
def sync_now():
    return jsonify(ok=True, pending=flush() if GH else 0)


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


@app.get("/api/health")
def health():
    return jsonify(github=GH, repo=GH_REPO, branch=GH_BRANCH, path=GH_PATH,
                   token_set=bool(GH_TOKEN), pending=len(dirty), sync_secs=SYNC_SECS)


@app.get("/system/health")
def health_system():
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    cpu_usage = psutil.cpu_percent(interval=None)
    ram_usage = ram.percent
    disk_free = round(disk.free / (1024**2), 2)
    is_healthy = ram_usage < 90 and disk.percent < 90
    return jsonify(
        status="healthy" if is_healthy else "warning",
        cpu_usage_pct=cpu_usage,
        ram_used_pct=ram_usage,
        disk_free_mb=disk_free
    ), 200 if is_healthy else 500


if __name__ == "__main__":
    if not GH:
        os.makedirs(QDIR, exist_ok=True)
        os.makedirs(FDIR, exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0" if os.environ.get("PORT") else "127.0.0.1", port=port, debug=False)
