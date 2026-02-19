import os
import base64
import uuid
import traceback
from typing import Dict, List

from fastapi import FastAPI, UploadFile, File, Form, Body, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from openai import OpenAI

app = FastAPI()

# CORS (helps with stricter clients/browsers)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # can restrict later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


# ---- Teacher prompt (Austrian-flavoured, kid-friendly) ----
SYSTEM_PROMPT = """Du bist 'DeutschCoach', eine freundliche Deutschlehrerin / ein freundlicher Deutschlehrer
für ein Volksschulkind (A1/A1+). Sprich Deutsch in österreichischer Variante (de-AT).

Regeln:
- Kurze, klare Sätze (1–3 Sätze).
- Warm, geduldig, wie in der Volksschule.
- Wenn das Kind Fehler macht:
  1) Sag den korrekten Satz.
  2) Erkläre genau EINE Mini-Regel (1 Satz).
  3) Stelle genau EINE Rückfrage, damit das Kind wiederholt.

Österreich-Wortschatz (passend, nicht übertreiben):
Jause, Semmel, Sackerl, Paradeiser, Erdäpfel, Marille, heuer, Bim, Turnstunde, Hausübung.
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
    return "Mode: Chat über Alltag/Schule. Stelle pro Antwort genau eine Frage."


# ---- Session memory ----
SESSIONS: Dict[str, List[dict]] = {}
MAX_TURNS = 6
MIN_AUDIO_BYTES = 1200


@app.post("/talk")
async def talk(
    audio: UploadFile = File(...),
    mode: str = Form("chat"),
    session_id: str = Form("")
):
    try:
        audio_bytes = await audio.read()
        print("UPLOAD:", audio.filename, "bytes=", len(audio_bytes))

        if not audio_bytes or len(audio_bytes) < MIN_AUDIO_BYTES:
            raise HTTPException(status_code=400, detail="Audio zu kurz/leer. Bitte 2–3 Sekunden sprechen.")

        if not session_id:
            session_id = str(uuid.uuid4())

        history = SESSIONS.get(session_id, [])

        # Speech-to-text
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=(audio.filename or "speech.webm", audio_bytes),
        )
        user_text = transcript.text.strip()

        # Chat with memory
        messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + mode_instruction(mode)}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        chat = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.4,
        )
        reply_text = chat.choices[0].message.content.strip()

        # Update memory
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply_text})
        history = history[-MAX_TURNS * 2:]
        SESSIONS[session_id] = history

        # Text-to-speech
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
async def reset(payload: dict = Body(...)):
    sid = (payload.get("session_id") or "").strip()
    if sid in SESSIONS:
        del SESSIONS[sid]
    return {"status": "reset"}
