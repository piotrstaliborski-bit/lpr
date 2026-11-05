# LPR Ratownik 6 – strona + backend

Jeden serwis (FastAPI) serwuje stronę (`static/index.html`) oraz API pod `/api`:
logowanie administratora, lista / podgląd / wgrywanie / usuwanie dokumentów PDF.

## Zmienne środowiskowe (wszystkie wymagane)
| Zmienna | Opis |
|---|---|
| `ADMIN_EMAIL` | lpr.krakow@intmail.pl |
| `ADMIN_PASSWORD` | Babciafrania123 |
| `SECRET_KEY` | losowy ciąg do podpisywania sesji, np. `openssl rand -hex 32` |
| `DATA_DIR` | (opcjonalnie) gdzie trzymać bazę i PDF-y, domyślnie `./data` |
| `MAX_UPLOAD_MB` | (opcjonalnie) limit pliku, domyślnie 20 |

## Uruchomienie lokalnie
```
pip install -r requirements.txt
export ADMIN_EMAIL=ty@example.pl ADMIN_PASSWORD='mocne-haslo' SECRET_KEY=$(openssl rand -hex 32)
uvicorn main:app --port 8000
```
Strona: http://localhost:8000

## Wdrożenie
**VPS (Hetzner / OVH / cyber_Folks) – najprostsze i najtańsze:**
```
docker build -t lpr .
docker run -d --restart=always -p 80:8000 -v lpr-data:/data \
  -e ADMIN_EMAIL=... -e ADMIN_PASSWORD=... -e SECRET_KEY=... lpr
```
HTTPS najłatwiej dodać przez Caddy (automatyczny certyfikat Let's Encrypt).

**Render / Railway / Fly.io:** wskaż repozytorium z tymi plikami (wykryją Dockerfile),
ustaw zmienne i **dołącz trwały dysk (volume) zamontowany w `/data`** – bez tego
wgrane PDF-y znikną przy każdym restarcie.

## Edycja strony z poziomu przeglądarki
1. Wejdź na **Oferta pracy → Panel administratora** i zaloguj się.
2. W prawym dolnym rogu pojawi się przycisk **✎ Edytuj stronę** (na każdej podstronie z edytowalnymi tekstami).
3. Klikasz w tekst i piszesz. Pasek na dole: pogrubienie, kursywa, nagłówek, listy, link, **Obraz**, **Plik** (PDF, Word, Excel, PowerPoint, TXT, do 20 MB) oraz **Zapisz / Anuluj / Domyślne**.

Edytowalne miejsca: tytuł i opis na stronie głównej, zakładki **Aktualności**, **O nas**, **Kontakt**,
opis i klauzula informacyjna w **Ofercie pracy**. Lista „Dokumenty do pobrania” w Ofercie pracy działa jak dotychczas.
Treść jest czyszczona po stronie serwera (zostają tylko bezpieczne znaczniki), a „Domyślne” przywraca oryginalny tekst.

## Dokumenty startowe
Pliki z folderu `seed/` (oferty pracy i oświadczenia) są dodawane do listy „Dokumenty do pobrania” przy pierwszym uruchomieniu. Można je potem usuwać i dodawać własne w panelu administratora; usunięte nie wracają.

## Uwaga
Reszta aplikacji (dyżury, zlecenia, użytkownicy) korzysta z Firebase/Firestore
wbudowanego w plik HTML – ten backend obsługuje tylko moduł dokumentów.

## Konta i dane aplikacji (dyżury, zlecenia, karty, dziennik)

Strona nie korzysta już z `claude.use("db")`. Konta użytkowników i wszystkie dane zapisują się
na serwerze, w bazie SQLite `DATA_DIR/app.db` (obok plików i treści strony).

- **Dysk trwały jest konieczny.** `DATA_DIR` (domyślnie `./data`, w Dockerze `/data`) musi leżeć na
  woluminie, który przetrwa restart i ponowne wdrożenie. Bez tego konta znikną po każdym deployu.
- Weryfikacja haseł i sesji odbywa się na serwerze (`/api/app/login`, `/api/app/change`).
  Hasła nie są już haszowane w przeglądarce, a lista kont nie zdradza hashy.
- Konto główne (`p.cabak`): ustaw `APP_ADMIN_PASSWORD`, aby zmienić jego hasło. Gdy zmiennej brak,
  obowiązuje dotychczasowe hasło. `APP_ADMIN_LOGIN` zmienia login konta głównego.
- Konta z poprzedniej wersji (zapisane w przeglądarce) nie przenoszą się. Załóż je od nowa w
  zakładce „Użytkownicy” (hasło tymczasowe, użytkownik ustawia własne przy pierwszym logowaniu).
- Zmiany z innych urządzeń pojawiają się w ciągu kilku sekund (odpytywanie co 3 s).
- Sesja trwa 30 dni; reset hasła lub usunięcie konta unieważnia ją od razu.
