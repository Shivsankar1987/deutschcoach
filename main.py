import os
import uuid
import base64
import random
from typing import Dict, List, Any, Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from itsdangerous import URLSafeSerializer, BadSignature
from openai import OpenAI


# -----------------------------
# Config
# -----------------------------
APP_USERNAME = os.environ.get("APP_USERNAME", "Sanjit")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "SanjitDeutchKurz2018")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET:
    # For local dev only; on Render ALWAYS set SESSION_SECRET in env vars
    SESSION_SECRET = "dev-only-change-me"

COOKIE_NAME = "dc_auth"

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

app = FastAPI()

# Serve static files (index.html + any assets)
app.mount("/static", StaticFiles(directory="static"), name="static")


# -----------------------------
# Conversation memory
# -----------------------------
SESSIONS: Dict[str, List[dict]] = {}  # session_id -> list of messages
MAX_TURNS = 6  # keep last 6 (user+assistant)


# -----------------------------
# Dictation state (hidden items)
# -----------------------------
DICTATION: Dict[str, Dict[str, Any]] = {}
# session_id -> {"topic": str, "items": List[str], "idx": int}


# -----------------------------
# Teacher prompt
# -----------------------------
SYSTEM_PROMPT = """
Du bist „DeutschCoach“, eine freundliche Deutschlehrerin aus Österreich (de-AT).
Du unterrichtest ein Volksschulkind (A1–A1+ Niveau).
Sprich langsam, klar und in kurzen Sätzen.

🌟 Allgemeine Regeln:
- Antworte mit maximal 1–3 kurzen Sätzen.
- Verwende einfache Wörter.
- Sprich wie eine geduldige Volksschullehrerin.
- Stelle pro Antwort genau EINE Frage.
- Motiviere freundlich („Super!“, „Sehr gut!“, „Toll gemacht!“).

🇦🇹 Verwende manchmal österreichische Wörter:
Jause, Semmel, Sackerl, Paradeiser, Erdäpfel, Marille,
heuer, Bim, Turnstunde, Hausübung.

✏️ Wenn das Kind einen Fehler macht:
1. Sage zuerst den richtigen Satz.
2. Erkläre genau EINE kleine Regel (ein Satz).
3. Bitte das Kind, den Satz noch einmal zu sagen (eine Frage).

📚 Wenn das Kind sagt:
„Lass uns über [Thema] sprechen“
oder
„Wir sprechen über [Thema]“

Dann:
1. Erkläre das Thema in 2 sehr einfachen Sätzen.
2. Gib genau EIN Beispiel.
3. Stelle genau EINE einfache Übungsfrage.

Bleibe immer freundlich, ruhig und unterstützend.
Antworte nur auf Deutsch.
""".strip()


def mode_instruction(mode: str) -> str:
    mode = (mode or "chat").lower().strip()

    if mode == "correct":
        return """
Mode: Correct my sentence.
- Korrigiere den Satz freundlich.
- Zeige den richtigen Satz.
- Erkläre 1 kurze Regel.
- Stelle 1 Frage.
""".strip()

    if mode == "roleplay":
        return """
Mode: Roleplay.
- Spiele eine Rolle (z.B. Verkäuferin, Lehrerin, Freund).
- Sehr einfache Sätze.
- Stelle genau 1 Frage pro Runde.
""".strip()

    if mode == "quiz":
        return """
Mode: Mini quiz.
- Stelle 1 sehr kurze Frage.
- Warte auf Antwort.
""".strip()

    if mode == "dictation":
        # Dictation is handled via /talk (topic) + /dictation/next (step-by-step).
        # If kid speaks/asks questions in dictation mode, keep short and friendly.
        return """
Mode: Dictation.
- Der Topic kommt vom Kind (z.B. „Uhr“). Oder Kind sagt „pick random topic“.
- Erkläre nichts lang. Wenn korrigiert wird: kurz, freundlich, 1 Frage.
""".strip()

    # default chat
    return """
Mode: Chat.
- Kurze Antworten.
- 1 Frage pro Antwort.
""".strip()


# -----------------------------
# Auth helpers
# -----------------------------
def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(SESSION_SECRET, salt="dc_auth_v1")


def is_logged_in(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return False
    try:
        data = _serializer().loads(token)
        return data.get("ok") is True
    except BadSignature:
        return False


def require_login(request: Request):
    if not is_logged_in(request):
        raise HTTPException(status_code=401, detail="Not logged in")


def _set_auth_cookie(resp: RedirectResponse):
    token = _serializer().dumps({"ok": True})
    resp.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=True,       # Render is HTTPS
        samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )


# -----------------------------
# Login pages
# -----------------------------
LOGIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"/>
  <title>DeutschCoach Login</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#fff;margin:0;padding:18px;padding-bottom:calc(18px + env(safe-area-inset-bottom))}
    .container{max-width:420px;margin:0 auto}
    h1{font-size:26px;margin:10px 0 18px}
    .box{border:1px solid #ddd;border-radius:12px;padding:14px}
    label{display:block;font-weight:700;margin:10px 0 6px}
    input{width:100%;font-size:16px;padding:12px;border-radius:10px;border:1px solid #ddd}
    button{width:100%;margin-top:14px;font-size:18px;padding:12px;border-radius:12px;border:1px solid #ddd;background:#f4f4f4}
    .small{opacity:.7;font-size:12px;margin-top:10px}
    .err{color:#b00020;font-weight:600;margin-top:10px}
  </style>
</head>
<body>
  <div class="container">
    <h1>DeutschCoach (Österreich)</h1>
    <div class="box">
      <form method="post" action="/login">
        <label>Username</label>
        <input name="username" autocomplete="username" />
        <label>Password</label>
        <input name="password" type="password" autocomplete="current-password" />
        <button type="submit">Login</button>
      </form>
      <div class="small">Nur für Sanjit 🙂</div>
    </div>
  </div>
</body>
</html>
""".strip()


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if is_logged_in(request):
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(LOGIN_HTML)


@app.post("/login")
async def login_post(username: str = Form(...), password: str = Form(...)):
    if username == APP_USERNAME and password == APP_PASSWORD:
        resp = RedirectResponse(url="/", status_code=302)
        _set_auth_cookie(resp)
        return resp

    html = LOGIN_HTML.replace(
        "</form>",
        '</form><div class="err">Falscher Username oder Passwort.</div>'
    )
    return HTMLResponse(html, status_code=401)


@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# -----------------------------
# Index route
# -----------------------------
@app.get("/")
async def index(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/index.html")


# -----------------------------
# Utility: read bytes from OpenAI audio response
# -----------------------------
def _read_audio_bytes(tts_obj) -> bytes:
    if hasattr(tts_obj, "read"):
        return tts_obj.read()
    if isinstance(tts_obj, (bytes, bytearray)):
        return bytes(tts_obj)
    return bytes(tts_obj)


# -----------------------------
# Dictation helpers
# -----------------------------
KID_TOPICS = [
    "Uhr", "Tiere", "Schule", "Familie", "Essen", "Wetter", "Körper",
    "Farben", "Zahlen", "Bauernhof", "Bäckerei", "Spielplatz"
]


def normalize_topic(t: str) -> str:
    t = (t or "").strip()
    if not t:
        return random.choice(KID_TOPICS)

    low = t.lower()
    if "pick random" in low or "random" in low or "zufällig" in low:
        return random.choice(KID_TOPICS)

    return t


def build_dictation_items(topic: str) -> List[str]:
    prompt = f"""
Erstelle ein Diktat für ein 7-jähriges Kind (A1).
Thema: {topic}

Gib GENAU 6 Zeilen:
- Zeile 1-4: je EIN Wort
- Zeile 5-6: je EIN sehr kurzer Satz

WICHTIG:
- Keine Nummern, keine Aufzählungszeichen.
- Nur die 6 Zeilen.
""".strip()

    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Du bist eine Deutschlehrerin (de-AT). Antworte strikt nach Vorgabe."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )
    text = (r.choices[0].message.content or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 6:
        lines = (lines + ["Hallo"] * 6)[:6]
    return lines[:6]


# -----------------------------
# Talk endpoint (voice in -> reply or start dictation)
# -----------------------------
@app.post("/talk")
async def talk(
    request: Request,
    audio: UploadFile = File(...),
    mode: str = Form("chat"),
    session_id: Optional[str] = Form(None),
):
    require_login(request)

    if not session_id:
        session_id = str(uuid.uuid4())

    audio_bytes = await audio.read()
    if not audio_bytes or len(audio_bytes) < 800:
        raise HTTPException(status_code=400, detail="Audio zu kurz/leer. Bitte 2–3 Sekunden sprechen.")

    # transcription
    try:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=(audio.filename or "speech.webm", audio_bytes),
        )
        user_text = (transcript.text or "").strip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Transcription failed: {str(e)}")

    # Dictation mode: treat user speech as TOPIC
    if (mode or "").lower().strip() == "dictation":
        topic = normalize_topic(user_text)
        items = build_dictation_items(topic)
        DICTATION[session_id] = {"topic": topic, "items": items, "idx": 0}

        return JSONResponse({
            "session_id": session_id,
            "dictation_ready": True,
            "status": f"Diktat bereit. Thema: {topic}. Drück „Next“ für das erste Wort.",
            "transcript": user_text
        })

    # Normal conversation
    history = SESSIONS.get(session_id, [])

    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + mode_instruction(mode)}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    chat = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.4,
    )
    reply_text = (chat.choices[0].message.content or "").strip()

    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply_text})
    history = history[-MAX_TURNS * 2:]
    SESSIONS[session_id] = history

    audio_b64 = ""
    try:
        tts = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="marin",
            input=reply_text,
        )
        tts_bytes = _read_audio_bytes(tts)
        audio_b64 = base64.b64encode(tts_bytes).decode("utf-8")
    except Exception:
        audio_b64 = ""

    return JSONResponse({
        "session_id": session_id,
        "transcript": user_text,
        "reply": reply_text,
        "audio_b64": audio_b64,
    })


# -----------------------------
# Dictation next item endpoint
# -----------------------------
@app.post("/dictation/next")
async def dictation_next(request: Request, payload: dict = Body(...)):
    require_login(request)

    session_id = (payload.get("session_id") or "").strip()
    if not session_id or session_id not in DICTATION:
        raise HTTPException(status_code=400, detail="No dictation session. Start dictation first.")

    st = DICTATION[session_id]
    items: List[str] = st["items"]
    idx: int = st["idx"]

    if idx >= len(items):
        return JSONResponse({
            "session_id": session_id,
            "done": True,
            "status": "Fertig! Willst du noch Wörter oder Sätze?"
        })

    current = items[idx]
    st["idx"] = idx + 1

    tts = client.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice="marin",
        input=current,
    )
    tts_bytes = _read_audio_bytes(tts)
    audio_b64 = base64.b64encode(tts_bytes).decode("utf-8")

    done = st["idx"] >= len(items)
    status = f"Item {idx+1}/{len(items)}"
    if done:
        status += " — Fertig! Willst du noch Wörter oder Sätze?"

    return JSONResponse({
        "session_id": session_id,
        "done": done,
        "status": status,
        "audio_b64": audio_b64,
        "reveal_text": current,  # hidden unless Reveal pressed
    })


# -----------------------------
# Reset conversation + dictation state
# -----------------------------
@app.post("/reset")
async def reset(request: Request, payload: dict = Body(...)):
    require_login(request)

    session_id = (payload.get("session_id") or "").strip()
    if session_id:
        SESSIONS.pop(session_id, None)
        DICTATION.pop(session_id, None)

    return JSONResponse({"ok": True})
