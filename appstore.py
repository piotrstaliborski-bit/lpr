"""Konta użytkowników aplikacji i prosta baza dokumentów (SQLite).

Zastępuje to, co strona dostawała w środowisku Claude jako claude.use("db"):
po wdrożeniu na własny hosting wszystko zapisuje się na serwerze, w DATA_DIR.
Moduł nie zależy od FastAPI, dzięki czemu można go testować osobno.
"""
import base64, hashlib, hmac, json, re, secrets, sqlite3, time
from contextlib import contextmanager
from datetime import datetime, timezone

ROLES = ["Administrator", "Dyspozytor", "Pilot", "Lekarz", "CZaH"]
COLLS = {"users", "events", "dyzury", "teams", "karty", "zlecenia", "usterki"}
LOGIN_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
ID_RE = re.compile(r"^[A-Za-z0-9._@:-]{1,80}$")
MAX_DOC_BYTES = 2_000_000
APP_TOKEN_TTL = 30 * 24 * 3600


class Denied(Exception):
    """Błąd z kodem HTTP i komunikatem po polsku."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def ukey(s):
    return str(s or "").strip().lower()


def sha(salt, pw):
    return hashlib.sha256(f"{salt}:{pw}".encode()).hexdigest()


class Store:
    def __init__(self, path, secret, admin_login, admin_password, admin_name="P. Cabak",
                 legacy_salt="9c4e1a7b2d6f",
                 legacy_hash="3a4de0258482862a3142834749ca7d0b5ca2b0826a77111da7c1aeef19108473"):
        self.path, self.secret = str(path), secret.encode()
        self.admin_login = ukey(admin_login)
        self.admin_name = admin_name
        if admin_password:  # hasło z konfiguracji serwera
            salt = secrets.token_hex(6)
            self._admin = {"salt": salt, "hash": sha(salt, admin_password)}
        else:  # zachowuje dotychczasowe hasło konta głównego
            self._admin = {"salt": legacy_salt, "hash": legacy_hash}
        self.fails = {}
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(
                "CREATE TABLE IF NOT EXISTS docs(coll TEXT NOT NULL, id TEXT NOT NULL, "
                "data TEXT NOT NULL, updated_at TEXT, PRIMARY KEY(coll,id))"
            )

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=15)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---------- użytkownicy ----------
    def _raw_user(self, login):
        login = ukey(login)
        if login == self.admin_login:
            return {"login": login, "name": self.admin_name, "role": "Administrator",
                    "mustChange": False, "builtin": True, **self._admin}
        with self.conn() as c:
            r = c.execute("SELECT data FROM docs WHERE coll='users' AND id=?", (login,)).fetchone()
        return json.loads(r[0]) if r else None

    @staticmethod
    def public(u):
        return {k: v for k, v in u.items() if k not in ("salt", "hash", "password")}

    def _token(self, u):
        payload = base64.urlsafe_b64encode(json.dumps(
            {"u": u["login"], "h": u["hash"][:12], "x": int(time.time()) + APP_TOKEN_TTL}).encode()).decode()
        sig = hmac.new(self.secret, b"app." + payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def auth(self, token):
        """Zwraca (publiczny użytkownik) albo rzuca Denied(401)."""
        try:
            payload, sig = token.rsplit(".", 1)
            good = hmac.new(self.secret, b"app." + payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, good):
                raise ValueError
            d = json.loads(base64.urlsafe_b64decode(payload))
            u = self._raw_user(d["u"])
            if not u or d["x"] < time.time() or u["hash"][:12] != d["h"]:
                raise ValueError
        except Exception:
            raise Denied(401, "Sesja wygasła, zaloguj się ponownie")
        return self.public(u)

    def _throttle(self, key):
        n, until = self.fails.get(key, (0, 0))
        if until > time.time():
            raise Denied(429, f"Zbyt wiele prób. Odczekaj {int(until - time.time()) + 1} s.")

    def _fail(self, key):
        n, _ = self.fails.get(key, (0, 0))
        n += 1
        self.fails[key] = (0, time.time() + 30) if n >= 5 else (n, 0)
        time.sleep(0.4)
        raise Denied(401, "Nieprawidłowy login lub hasło.")

    def _check(self, login, password, ip=""):
        k = (ukey(login), ip)
        self._throttle(k)
        u = self._raw_user(login)
        ok = bool(u) and hmac.compare_digest(sha(u["salt"], password or ""), u["hash"])
        if not ok:
            self._fail(k)
        self.fails.pop(k, None)
        return u

    def login(self, login, password, ip=""):
        u = self._check(login, password, ip)
        if u.get("mustChange"):
            return {"mustChange": True, "login": u["login"]}
        return {"token": self._token(u), "user": self.public(u)}

    def change_password(self, login, password, new_password, ip=""):
        u = self._check(login, password, ip)
        if u.get("builtin"):
            raise Denied(400, "Hasło konta głównego ustawia się w konfiguracji serwera.")
        if len(new_password or "") < 8:
            raise Denied(400, "Hasło: minimum 8 znaków.")
        if new_password == password:
            raise Denied(400, "Nowe hasło musi być inne niż tymczasowe.")
        salt = secrets.token_hex(8)
        u.update(salt=salt, hash=sha(salt, new_password), mustChange=False,
                 pwChanged=int(time.time() * 1000))
        self._put("users", u["login"], u)
        return {"token": self._token(u), "user": self.public(u)}

    # ---------- baza dokumentów ----------
    def _put(self, coll, id_, data):
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw.encode()) > MAX_DOC_BYTES:
            raise Denied(413, "Dokument jest za duży")
        with self.conn() as c:
            c.execute(
                "INSERT INTO docs(coll,id,data,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(coll,id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (coll, id_, raw, datetime.now(timezone.utc).isoformat()),
            )

    def _get(self, coll, id_):
        with self.conn() as c:
            r = c.execute("SELECT data FROM docs WHERE coll=? AND id=?", (coll, id_)).fetchone()
        return json.loads(r[0]) if r else None

    @staticmethod
    def _keys(user):
        return {k for k in (ukey(user.get("name")), ukey(user.get("login"))) if k}

    def _visible(self, coll, data, user):
        """Czy użytkownik może zobaczyć dokument (karty i zlecenia)."""
        role = user["role"]
        if role == "Administrator":
            return True
        if coll == "zlecenia" and role == "Dyspozytor":
            return True
        mine = self._keys(user)
        if data.get("team") == user["login"] and coll == "zlecenia":
            return True
        return bool(mine & {ukey(k) for k in (data.get("crewKeys") or [])})

    def _check_coll(self, coll):
        if coll not in COLLS:
            raise Denied(404, "Nieznana kolekcja")

    def _check_id(self, id_):
        if not ID_RE.match(id_ or ""):
            raise Denied(400, "Nieprawidłowy identyfikator")

    def _own_or_admin(self, id_, user):
        if user["role"] != "Administrator" and id_ != user["login"]:
            raise Denied(403, "Brak uprawnień")

    def list(self, coll, user, where=None):
        self._check_coll(coll)
        if coll in ("events", "dyzury") and user["role"] != "Administrator":
            raise Denied(403, "Brak uprawnień")
        if coll == "usterki" and user["role"] not in ("Administrator", "Pilot"):
            raise Denied(403, "Brak uprawnień")
        with self.conn() as c:
            rows = c.execute("SELECT id,data FROM docs WHERE coll=? ORDER BY id", (coll,)).fetchall()
        out = []
        for id_, raw in rows:
            d = json.loads(raw)
            if coll == "users":
                d = self.public(d)
            elif coll in ("karty", "zlecenia") and not self._visible(coll, d, user):
                continue
            if where:
                f, op, v = where
                cur = d.get(f)
                if op == "==" and cur != v:
                    continue
                if op == "array-contains" and not (isinstance(cur, list) and v in cur):
                    continue
            out.append({"id": id_, "data": d})
        return {"docs": out}

    def get(self, coll, id_, user):
        self._check_coll(coll)
        self._check_id(id_)
        if coll in ("events", "dyzury"):
            self._own_or_admin(id_, user)
        if coll == "usterki" and user["role"] not in ("Administrator", "Pilot"):
            raise Denied(403, "Brak uprawnień")
        if coll == "users" and id_ == self.admin_login:
            return self.public(self._raw_user(id_))
        d = self._get(coll, id_)
        if d is None:
            raise Denied(404, "Nie znaleziono")
        if coll == "users":
            return self.public(d)
        if coll in ("karty", "zlecenia") and not self._visible(coll, d, user):
            raise Denied(403, "Brak uprawnień")
        return d

    def _guard_write(self, coll, id_, user, existing, creating):
        role = user["role"]
        if coll == "users":
            if role != "Administrator":
                raise Denied(403, "Brak uprawnień")
            if id_ == self.admin_login:
                raise Denied(400, "Konta głównego nie można zmieniać")
        elif coll == "teams":
            if role != "Administrator" and id_ != user["login"]:
                crew = (existing or {}).get("crew") or {}
                ck = {ukey(crew.get(f)) for f in ("pilot", "rat", "lek")} - {""}
                if not (existing is not None and ck & self._keys(user)):
                    raise Denied(403, "Brak uprawnień")
        elif coll in ("events", "dyzury"):
            self._own_or_admin(id_, user)
        elif coll == "usterki":
            if role not in ("Administrator", "Pilot"):
                raise Denied(403, "Brak uprawnień")
        elif coll == "zlecenia":
            if creating and role not in ("Dyspozytor", "Administrator"):
                raise Denied(403, "Brak uprawnień")
            if existing is not None and not self._visible(coll, existing, user):
                raise Denied(403, "Brak uprawnień")
        elif coll == "karty":
            if role == "Dyspozytor":
                raise Denied(403, "Brak uprawnień")
            if existing is not None and not self._visible(coll, existing, user):
                raise Denied(403, "Brak uprawnień")

    def _prep_user(self, id_, body, existing):
        if not LOGIN_RE.match(id_):
            raise Denied(400, "Login: 3–32 znaków (a–z, 0–9, kropka, myślnik, podkreślnik)")
        if body.get("role") not in ROLES:
            raise Denied(400, "Nieprawidłowa rola")
        if not str(body.get("name") or "").strip():
            raise Denied(400, "Wpisz imię i nazwisko")
        new = {k: v for k, v in body.items() if k not in ("salt", "hash", "password", "builtin")}
        new["login"] = id_
        pw = body.get("password")
        if pw is not None:
            if len(pw) < 8:
                raise Denied(400, "Hasło: minimum 8 znaków")
            salt = secrets.token_hex(8)
            new.update(salt=salt, hash=sha(salt, pw))
        elif existing:
            new.update(salt=existing["salt"], hash=existing["hash"])
        else:
            raise Denied(400, "Brak hasła")
        return new

    def _one_duty(self, coll, id_):
        """Tylko jeden dyżur (wpis zespołu) naraz."""
        if coll != "teams":
            return
        with self.conn() as c:
            r = c.execute("SELECT 1 FROM docs WHERE coll='teams' AND id!=? LIMIT 1", (id_,)).fetchone()
        if r:
            raise Denied(409, "Dyżur już trwa – nie można utworzyć drugiego")

    @staticmethod
    def _stamp_reject(coll, body, user):
        """Odrzucenie zlecenia wymaga powodu; podpis i czas ustawia serwer (z konta osoby)."""
        if coll != "zlecenia" or body.get("status") != "Odrzucone":
            return body
        reason = str(body.get("rejReason") or "").strip()
        if len(reason) < 3:
            raise Denied(400, "Podaj powód odrzucenia zlecenia")
        return {**body, "rejReason": reason[:500], "rejBy": user["name"],
                "rejLogin": user["login"], "rejT": int(time.time() * 1000)}

    def set(self, coll, id_, body, user):
        self._check_coll(coll)
        self._check_id(id_)
        if not isinstance(body, dict):
            raise Denied(400, "Nieprawidłowe dane")
        existing = self._get(coll, id_)
        self._guard_write(coll, id_, user, existing, existing is None)
        body = self._stamp_reject(coll, body, user)
        self._one_duty(coll, id_)
        if coll == "users":
            body = self._prep_user(id_, body, existing)
        self._put(coll, id_, body)
        return {"ok": True}

    def update(self, coll, id_, body, user):
        self._check_coll(coll)
        self._check_id(id_)
        existing = self._get(coll, id_)
        if existing is None:
            raise Denied(404, "Nie znaleziono")
        self._guard_write(coll, id_, user, existing, False)
        body = self._stamp_reject(coll, body, user)
        self._one_duty(coll, id_)
        merged = {**existing, **body}
        if coll == "users":
            merged = self._prep_user(id_, merged, existing)
        self._put(coll, id_, merged)
        return {"ok": True}

    def delete(self, coll, id_, user):
        self._check_coll(coll)
        self._check_id(id_)
        existing = self._get(coll, id_)
        if existing is None:
            return {"ok": True}
        if coll == "zlecenia" and user["role"] not in ("Dyspozytor", "Administrator"):
            raise Denied(403, "Brak uprawnień")
        if coll == "usterki" and user["role"] != "Administrator":
            raise Denied(403, "Brak uprawnień")
        self._guard_write(coll, id_, user, existing, False)
        with self.conn() as c:
            c.execute("DELETE FROM docs WHERE coll=? AND id=?", (coll, id_))
        return {"ok": True}
