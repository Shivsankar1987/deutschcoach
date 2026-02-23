import os
import base64
import uuid
import traceback
from typing import Dict, List

from fastapi import FastAPI, UploadFile, File, Form, Body, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from openai import OpenAI


# -------------------------
# LOGIN CONFIG (as requested)
# -------------------------
LOGIN_USERNAME = "Sanjit"
LOGIN_PASSWORD = "SanjitDeutchKurz2018"

# IMPORTANT: Set this in Render Environment as SESSION_SECRET (long random string)
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-insecure-change-me")


app = FastAPI()

# Signed cookie sessions
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="deutschcoach_session",
    same_site="lax",
    https_only=True,   # Render is HTTPS
)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

app.mount("/static", StaticFiles(directory="static"), name="static")


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("logged_in"))


def require_login(request: Request):
    if not is_logged_in(request):
        raise HTTPException(status_code=401, detail="Not logged in")


# -------------------------
# PAGES
# -------------------------
@app.get("/login")
async def login_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/", status_code=302)
    return FileResponse("static/login.html")


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form("")
):
    if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
        request.session["logged_in"] = True
        return RedirectResponse("/", status_code=302)

    return RedirectResponse("/login?error=1", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/")
async def root(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse("static/index.html")


# -------------------------
# COACH LOGIC
# -------------------------
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

🧠 Wenn das Kind unsicher wirkt:
- Gib ein kleines Beispiel.
- Stelle eine sehr einfache Frage.

🎭 Im Rollenspiel:
- Spiele eine Person (z.B. Verkäuferin, Lehrerin, Freund).
- Stelle genau eine Frage pro Runde.

🧩 Im Quiz-Modus:
- Stelle genau 3 sehr kurze Fragen.
- Warte auf die Antwort nach jeder Frage.

Bleibe immer freundlich, ruhig und unterstützend.
Antworte nur auf Deutsch.
"""

def mode_instruction(mode: str) -> str:
    mode = (mode or "chat").lower()
    if mode == "correct":
        return (
            "Mode: Korrigieren. Antworte sehr kurz: "
            "1) korrigierter Satz 2) eine Mini-Regel 3) Bitte wiederholen (eine Frage)."
        )
    if mode == "roleplay":
        return (
            "Mode: Rollenspiel in Österreich (Bäckerei, Schule, Spielplatz, Supermarkt). "
            "Du spielst die andere Person und stellst pro Runde genau eine Frage."
        )
    if mode == "quiz":
        return (
            "Mode: Mini-Quiz. Stelle genau 3 sehr kurze Fragen nacheinander. "
            "Warte jeweils auf die Antwort."
        )
    if mode == "dictation":
        return """
Mode: DIKTAT (Hören & Schreiben – Volksschule, 7 Jahre).

TOPIC-REGEL:
- Wenn das Kind einen Topic nennt (z.B. „Uhr“, „Tiere“, „Schule“),
  verwende genau diesen Topic.
- Wenn das Kind sagt „pick random topic“ oder „zufälliges Thema“,
  wähle selbst ein einfaches Thema für ein 7-jähriges Kind.
  Beispiele: Tiere, Schule, Familie, Uhr, Essen, Wetter, Körper,
  Farben, Zahlen, Bauernhof, Bäckerei, Spielplatz.

ABLAUF EINER RUNDE:

1) Sage: „Diktat Runde 1 – Thema: <Topic>“
2) Gib genau:
    - 4 einzelne Wörter
    - 2 kurze Sätze (A1 Niveau)
    Alle Wörter und Sätze müssen zum Topic passen.
3) Sage danach:
    „Schreib das bitte auf. Antworte nur mit deinem Text.“
4) WARTE. Zeige keine Lösung.

KORREKTUR:
Wenn das Kind Text schickt:

- Korrigiere ZEILE FÜR ZEILE.
- Vergleiche Wort für Wort.
- Zeige immer:

✅ Korrektur:

1) Kind: <Original>
    Richtig: <Korrekt>
    Warum: <kurze Erklärung, z.B. Großschreibung, Artikel, Plural>

- Gib danach genau EINE Mini-Regel.
- Stelle danach genau EINE Frage:
  „Willst du Runde 2?“
- Sei freundlich, motivierend und ruhig.

SPRACHE:
- Kurze Sätze.
- Sehr einfache Wörter.
- Österreichisches Deutsch (de-AT).
- Freundlicher Volksschul-Ton.
"""

    return "Mode: Chat über Alltag/Schule. Stelle pro Antwort genau eine Frage."


SESSIONS: Dict[str, List[dict]] = {}
MAX_TURNS = 6
MIN_AUDIO_BYTES = 1200


@app.post("/talk")
async def talk(
    request: Request,
    audio: UploadFile = File(...),
    mode: str = Form("chat"),
    session_id: str = Form("")
):
    require_login(request)

    try:
        audio_bytes = await audio.read()
        print("UPLOAD:", audio.filename, "bytes=", len(audio_bytes))

        if not audio_bytes or len(audio_bytes) < MIN_AUDIO_BYTES:
            raise HTTPException(status_code=400, detail="Audio zu kurz/leer. Bitte 2–3 Sekunden sprechen.")

        if not session_id:
            session_id = str(uuid.uuid4())

        history = SESSIONS.get(session_id, [])

        transcript = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=(audio.filename or "speech.webm", audio_bytes),
        )
        user_text = transcript.text.strip()

        messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + mode_instruction(mode)}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        chat = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.4,
        )
        reply_text = chat.choices[0].message.content.strip()

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply_text})
        history = history[-MAX_TURNS * 2:]
        SESSIONS[session_id] = history

        tts = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="marin",
            input=reply_text,
        )
        mp3_bytes = tts.read()

        return JSONResponse({
            "session_id": session_id,
            "transcript": user_text,
            "reply": reply_text,
            "audio_b64": base64.b64encode(mp3_bytes).decode("utf-8"),
        })

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset")
async def reset(request: Request, payload: dict = Body(...)):
    require_login(request)
    sid = (payload.get("session_id") or "").strip()
    if sid in SESSIONS:
        del SESSIONS[sid]
    return {"status": "reset"}