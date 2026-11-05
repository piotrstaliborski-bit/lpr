import base64, hashlib, hmac, json, os, re, sqlite3, time, uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from appstore import Denied, Store
from sanitize import clean_html

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "lpr.db"
STATIC_DIR = Path(__file__).parent / "static"
MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
TOKEN_TTL = 12 * 3600

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET = os.getenv("SECRET_KEY", "")
if not (ADMIN_EMAIL and ADMIN_PASSWORD and SECRET):
    raise SystemExit("Ustaw zmienne ADMIN_EMAIL, ADMIN_PASSWORD i SECRET_KEY.")

APP_ADMIN_LOGIN = os.getenv("APP_ADMIN_LOGIN", "p.cabak")
APP_ADMIN_PASSWORD = os.getenv("APP_ADMIN_PASSWORD", "")  # puste = dotychczasowe hasło konta głównego

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


with db() as con:
    con.execute(
        """CREATE TABLE IF NOT EXISTS dokumenty(
        id TEXT PRIMARY KEY, title TEXT, original_filename TEXT,
        size INTEGER, created_at TEXT)"""
    )
    con.execute("CREATE TABLE IF NOT EXISTS tresci(key TEXT PRIMARY KEY, html TEXT, updated_at TEXT)")
    con.execute(
        """CREATE TABLE IF NOT EXISTS galeria(
        id TEXT PRIMARY KEY, media_id TEXT, caption TEXT, created_at TEXT)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS posty(
        id TEXT PRIMARY KEY, title TEXT, body TEXT, media_id TEXT, created_at TEXT)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS pliki(
        id TEXT PRIMARY KEY, original_filename TEXT, ext TEXT, mime TEXT,
        size INTEGER, created_at TEXT)"""
    )

# ---------- dokumenty startowe (dodawane raz, potem można je usunąć w panelu) ----------
SEED_DIR = Path(__file__).parent / "seed"
SEED_DOCS = [
    ("oferta_lekarz.pdf", "Oferta pracy – Lekarz systemu (HEMS)"),
    ("oferta_ratownik_medyczny.pdf", "Oferta pracy – Ratownik medyczny (HEMS)"),
    ("oferta_pilot.pdf", "Oferta pracy – Pilot śmigłowca HEMS"),
    ("oswiadczenia.pdf", "Oświadczenia i kwestionariusz dla kandydatów"),
]


def seed_documents():
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS seed_done(name TEXT PRIMARY KEY)")
        base = int(time.time())
        for i, (fname, title) in enumerate(SEED_DOCS):
            src = SEED_DIR / fname
            if not src.is_file():
                continue
            if con.execute("SELECT 1 FROM seed_done WHERE name=?", (fname,)).fetchone():
                continue
            doc_id = uuid.uuid4().hex
            content = src.read_bytes()
            (UPLOAD_DIR / f"{doc_id}.pdf").write_bytes(content)
            # pierwszy na liście = najnowszy (lista sortowana malejąco po dacie)
            created = datetime.fromtimestamp(base - i, timezone.utc).isoformat()
            con.execute(
                "INSERT INTO dokumenty VALUES(?,?,?,?,?)",
                (doc_id, title, fname, len(content), created),
            )
            con.execute("INSERT INTO seed_done VALUES(?)", (fname,))


seed_documents()


store = Store(DATA_DIR / "app.db", SECRET, APP_ADMIN_LOGIN, APP_ADMIN_PASSWORD)

app = FastAPI(title="LPR Ratownik 6", docs_url=None, redoc_url=None, openapi_url=None)
bearer = HTTPBearer(auto_error=False)


# --- prosty podpisany token (HMAC), bez dodatkowych bibliotek ---
def make_token(email: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"e": email, "x": int(time.time()) + TOKEN_TTL}).encode()
    ).decode()
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token: str):
    try:
        payload, sig = token.rsplit(".", 1)
        good = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data["e"] if data["x"] > time.time() else None
    except Exception:
        return None


def admin(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    email = read_token(cred.credentials) if cred else None
    if not email:
        raise HTTPException(401, "Brak autoryzacji")
    return email


class Login(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def login(body: Login):
    ok_mail = hmac.compare_digest(body.email.strip().lower(), ADMIN_EMAIL)
    ok_pass = hmac.compare_digest(body.password, ADMIN_PASSWORD)
    if not (ok_mail and ok_pass):
        time.sleep(1)  # spowalnia zgadywanie hasła
        raise HTTPException(401, "Nieprawidłowy e-mail lub hasło")
    return {"token": make_token(ADMIN_EMAIL), "email": ADMIN_EMAIL}


@app.get("/api/auth/me")
def me(email: str = Depends(admin)):
    return {"email": email}


@app.get("/api/dokumenty")
def list_docs():
    with db() as con:
        rows = con.execute("SELECT * FROM dokumenty ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/dokumenty")
async def add_doc(
    file: UploadFile = File(...),
    title: str = Form(""),
    _: str = Depends(admin),
):
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, f"Plik jest za duży (max {MAX_BYTES // 1048576} MB)")
    if not content.startswith(b"%PDF"):
        raise HTTPException(400, "Dozwolone są tylko pliki PDF")
    doc_id = uuid.uuid4().hex
    (UPLOAD_DIR / f"{doc_id}.pdf").write_bytes(content)
    name = os.path.basename(file.filename or "dokument.pdf")[:200]
    doc = {
        "id": doc_id,
        "title": (title.strip() or name)[:200],
        "original_filename": name,
        "size": len(content),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with db() as con:
        con.execute("INSERT INTO dokumenty VALUES(:id,:title,:original_filename,:size,:created_at)", doc)
    return doc


@app.get("/api/dokumenty/{doc_id}/plik")
def get_file(doc_id: str):
    with db() as con:
        row = con.execute("SELECT * FROM dokumenty WHERE id=?", (doc_id,)).fetchone()
    path = UPLOAD_DIR / f"{doc_id}.pdf"
    if not row or not path.is_file():
        raise HTTPException(404, "Nie znaleziono dokumentu")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=row["original_filename"],
        content_disposition_type="inline",
    )


@app.delete("/api/dokumenty/{doc_id}")
def delete_doc(doc_id: str, _: str = Depends(admin)):
    with db() as con:
        cur = con.execute("DELETE FROM dokumenty WHERE id=?", (doc_id,))
    if not cur.rowcount:
        raise HTTPException(404, "Nie znaleziono dokumentu")
    (UPLOAD_DIR / f"{doc_id}.pdf").unlink(missing_ok=True)
    return {"ok": True}


# ---------- edytowalne treści strony ----------
KEY_RE = re.compile(r"^[a-z0-9-]{1,40}$")


class Tresc(BaseModel):
    html: str


@app.get("/api/tresci")
def get_tresci():
    with db() as con:
        rows = con.execute("SELECT key, html FROM tresci").fetchall()
    return {r["key"]: r["html"] for r in rows}


@app.put("/api/tresci/{key}")
def put_tresc(key: str, body: Tresc, _: str = Depends(admin)):
    if not KEY_RE.match(key):
        raise HTTPException(400, "Nieprawidłowy klucz")
    if len(body.html) > 300_000:
        raise HTTPException(413, "Treść jest za długa")
    cleaned = clean_html(body.html)
    with db() as con:
        con.execute(
            "INSERT INTO tresci(key, html, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET html=excluded.html, updated_at=excluded.updated_at",
            (key, cleaned, datetime.now(timezone.utc).isoformat()),
        )
    return {"key": key, "html": cleaned}


@app.delete("/api/tresci/{key}")
def delete_tresc(key: str, _: str = Depends(admin)):
    with db() as con:
        con.execute("DELETE FROM tresci WHERE key=?", (key,))
    return {"ok": True}


# ---------- pliki i obrazy wstawiane w treść ----------
ALLOWED_EXT = {
    ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain; charset=utf-8",
}
MAGIC = {
    ".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8",
    ".gif": b"GIF8", ".webp": b"RIFF", ".docx": b"PK", ".xlsx": b"PK", ".pptx": b"PK",
    ".doc": b"\xd0\xcf", ".xls": b"\xd0\xcf", ".ppt": b"\xd0\xcf",
}
INLINE_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp"}


@app.post("/api/pliki")
async def add_media(file: UploadFile = File(...), _: str = Depends(admin)):
    name = os.path.basename(file.filename or "plik")[:200]
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Niedozwolony typ pliku (PDF, obrazy, Word, Excel, PowerPoint, TXT)")
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, f"Plik jest za duży (max {MAX_BYTES // 1048576} MB)")
    if ext in MAGIC and not content.startswith(MAGIC[ext]):
        raise HTTPException(400, "Zawartość pliku nie zgadza się z jego rozszerzeniem")
    mid = uuid.uuid4().hex
    (MEDIA_DIR / f"{mid}{ext}").write_bytes(content)
    with db() as con:
        con.execute(
            "INSERT INTO pliki VALUES(?,?,?,?,?,?)",
            (mid, name, ext, ALLOWED_EXT[ext], len(content), datetime.now(timezone.utc).isoformat()),
        )
    return {"id": mid, "url": f"/api/pliki/{mid}", "name": name, "is_image": ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}}


@app.get("/api/pliki/{mid}")
def get_media(mid: str):
    with db() as con:
        row = con.execute("SELECT * FROM pliki WHERE id=?", (mid,)).fetchone()
    path = MEDIA_DIR / f"{mid}{row['ext']}" if row else None
    if not row or not path.is_file():
        raise HTTPException(404, "Nie znaleziono pliku")
    return FileResponse(
        path,
        media_type=row["mime"],
        filename=row["original_filename"],
        content_disposition_type="inline" if row["ext"] in INLINE_EXT else "attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


# ---------- galeria i aktualności (dodaje administrator) ----------
def site_admin(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    """Administrator strony (panel w Ofercie pracy) albo konto Administrator aplikacji."""
    if cred:
        email = read_token(cred.credentials)
        if email:
            return email
        try:
            u = store.auth(cred.credentials)
            if u.get("role") == "Administrator":
                return u.get("login", "admin")
        except Denied:
            pass
    raise HTTPException(401, "Brak autoryzacji")


IMG_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


async def save_image(file: UploadFile) -> str:
    name = os.path.basename(file.filename or "zdjecie.jpg")[:200]
    ext = os.path.splitext(name)[1].lower()
    if ext not in IMG_EXT:
        raise HTTPException(400, "Dozwolone są tylko zdjęcia (JPG, PNG, WebP, GIF)")
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, f"Plik jest za duży (max {MAX_BYTES // 1048576} MB)")
    if not content.startswith(MAGIC[ext]):
        raise HTTPException(400, "Zawartość pliku nie zgadza się z jego rozszerzeniem")
    mid = uuid.uuid4().hex
    (MEDIA_DIR / f"{mid}{ext}").write_bytes(content)
    with db() as con:
        con.execute(
            "INSERT INTO pliki VALUES(?,?,?,?,?,?)",
            (mid, name, ext, IMG_EXT[ext], len(content), datetime.now(timezone.utc).isoformat()),
        )
    return mid


def drop_media(mid):
    if not mid:
        return
    with db() as con:
        row = con.execute("SELECT ext FROM pliki WHERE id=?", (mid,)).fetchone()
        con.execute("DELETE FROM pliki WHERE id=?", (mid,))
    if row:
        (MEDIA_DIR / f"{mid}{row['ext']}").unlink(missing_ok=True)


@app.get("/api/galeria")
def list_gallery():
    with db() as con:
        rows = con.execute("SELECT * FROM galeria ORDER BY created_at ASC").fetchall()
    return [{"id": r["id"], "url": f"/api/pliki/{r['media_id']}", "caption": r["caption"], "created_at": r["created_at"]} for r in rows]


@app.post("/api/galeria")
async def add_gallery(file: UploadFile = File(...), caption: str = Form(""), _: str = Depends(site_admin)):
    mid = await save_image(file)
    gid = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    cap = caption.strip()[:160]
    with db() as con:
        con.execute("INSERT INTO galeria VALUES(?,?,?,?)", (gid, mid, cap, created))
    return {"id": gid, "url": f"/api/pliki/{mid}", "caption": cap, "created_at": created}


@app.delete("/api/galeria/{gid}")
def delete_gallery(gid: str, _: str = Depends(site_admin)):
    with db() as con:
        row = con.execute("SELECT media_id FROM galeria WHERE id=?", (gid,)).fetchone()
        if not row:
            raise HTTPException(404, "Nie znaleziono zdjęcia")
        con.execute("DELETE FROM galeria WHERE id=?", (gid,))
    drop_media(row["media_id"])
    return {"ok": True}


def post_json(r):
    return {"id": r["id"], "title": r["title"], "body": r["body"],
            "url": f"/api/pliki/{r['media_id']}" if r["media_id"] else None, "created_at": r["created_at"]}


@app.get("/api/posty")
def list_posts():
    with db() as con:
        rows = con.execute("SELECT * FROM posty ORDER BY created_at DESC").fetchall()
    return [post_json(r) for r in rows]


@app.post("/api/posty")
async def add_post(
    title: str = Form(...),
    body: str = Form(""),
    file: UploadFile | None = File(None),
    _: str = Depends(site_admin),
):
    title, body = title.strip()[:160], body.strip()[:8000]
    if len(title) < 3:
        raise HTTPException(400, "Wpisz tytuł posta (min. 3 znaki)")
    mid = await save_image(file) if file is not None and file.filename else None
    pid = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("INSERT INTO posty VALUES(?,?,?,?,?)", (pid, title, body, mid, created))
        row = con.execute("SELECT * FROM posty WHERE id=?", (pid,)).fetchone()
    return post_json(row)


@app.delete("/api/posty/{pid}")
def delete_post(pid: str, _: str = Depends(site_admin)):
    with db() as con:
        row = con.execute("SELECT media_id FROM posty WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Nie znaleziono posta")
        con.execute("DELETE FROM posty WHERE id=?", (pid,))
    drop_media(row["media_id"])
    return {"ok": True}


# ---------- konta użytkowników aplikacji i dane (dyżury, zlecenia, karty, zdarzenia) ----------
def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() or (request.client.host if request.client else ""))


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Denied as e:
        raise HTTPException(e.code, e.msg)


def app_user(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    if not cred:
        raise HTTPException(401, "Brak autoryzacji")
    return _run(store.auth, cred.credentials)


class AppLogin(BaseModel):
    login: str
    password: str


class AppChange(BaseModel):
    login: str
    password: str
    new_password: str


@app.post("/api/app/login")
def app_login(body: AppLogin, request: Request):
    return _run(store.login, body.login, body.password, _ip(request))


@app.post("/api/app/change")
def app_change(body: AppChange, request: Request):
    return _run(store.change_password, body.login, body.password, body.new_password, _ip(request))


@app.get("/api/app/me")
def app_me(user: dict = Depends(app_user)):
    return user


@app.get("/api/db/{coll}")
def db_list(coll: str, f: str = "", op: str = "==", v: str = "", user: dict = Depends(app_user)):
    where = (f, op, v) if f and op in ("==", "array-contains") else None
    return _run(store.list, coll, user, where)


@app.get("/api/db/{coll}/{doc_id}")
def db_get(coll: str, doc_id: str, user: dict = Depends(app_user)):
    return {"id": doc_id, "data": _run(store.get, coll, doc_id, user)}


@app.put("/api/db/{coll}/{doc_id}")
def db_set(coll: str, doc_id: str, body: dict = Body(...), user: dict = Depends(app_user)):
    return _run(store.set, coll, doc_id, body, user)


@app.patch("/api/db/{coll}/{doc_id}")
def db_update(coll: str, doc_id: str, body: dict = Body(...), user: dict = Depends(app_user)):
    return _run(store.update, coll, doc_id, body, user)


@app.delete("/api/db/{coll}/{doc_id}")
def db_delete(coll: str, doc_id: str, user: dict = Depends(app_user)):
    return _run(store.delete, coll, doc_id, user)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
