import os
import base64
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from openai import OpenAI
import traceback
import uuid
from typing import Dict, List
from fastapi import Form
from fastapi import Body
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware



# ---- Session memory ----
SESSIONS: Dict[str, List[dict]] = {}  # session_id -> list of messages
MAX_TURNS = 6  # keep last 6 messages (user+assistant)
# Minimum number of bytes for an audio upload to be considered valid.
# Lowered a bit to be more forgiving for short mobile/iOS recordings.
MIN_AUDIO_BYTES = 500
# ------------------------

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # you can restrict later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/reset")
async def reset(payload: dict = Body(...)):
    session_id = (payload.get("session_id") or "").strip()

    if session_id and session_id in SESSIONS:
        del SESSIONS[session_id]

    return JSONResponse({"status": "reset"})

app.mount("/static", StaticFiles(directory="static"), name="static")  # :contentReference[oaicite:3]{index=3}

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

SYSTEM_PROMPT = """Du bist 'DeutschCoach', eine freundliche Deutschlehrerin / ein freundlicher Deutschlehrer für ein Volksschulkind (Anfänger, nicht-muttersprachlich).

Sprich Deutsch in österreichischer Variante (de-AT):
- verwende 'du'
- kurze, klare Sätze (1–3 Sätze)
- warm, geduldig, wie in der Volksschule
- verwende regelmäßig (aber nicht übertrieben) österreichische Wörter und Ausdrücke

Österreich-Wortschatz (verwende passend im Kontext):
Jänner, heuer, leiwand, Jause, Sackerl, Paradeiser, Marille, Erdäpfel, Topfen, Obers,
Sessel (nicht Stuhl), Mistkübel, Rauchfangkehrer, Bim, Semmel, Palatschinken

Schulkontext Österreich:
Volksschule, große Pause, Turnstunde, Hausübung, Schultasche, Jausenbox, Jause

Korrigieren (wenn das Kind Fehler macht):
1) Sag den korrekten Satz.
2) Erkläre genau EINE Mini-Regel (kindgerecht, 1 Satz).
3) Lass das Kind den Satz noch einmal sagen (eine Frage).
Immer genau EINE Rückfrage stellen.

Sicherheit: keine erwachsenen/angstigen Themen, keine persönlichen Daten erfragen (Adresse, Schulname).
Wenn das Kind Englisch spricht: antworte auf Deutsch, gib höchstens EINEN kurzen englischen Hinweis.
"""


@app.get("/")
def home():
    return FileResponse("static/index.html")

@app.post("/talk")
async def talk(audio: UploadFile = File(...),
               mode: str = Form("chat"),
               session_id: str = Form("")):
    try:
        # 1) Speech-to-text (transcription)
        audio_bytes = await audio.read()
          print("UPLOAD:", audio.filename, "content_type=", getattr(audio, "content_type", None), "bytes=", len(audio_bytes))

          if not audio_bytes or len(audio_bytes) < MIN_AUDIO_BYTES:
              raise HTTPException(status_code=400, detail="Audio too short/empty. Bitte etwas länger sprechen und noch einmal probieren.")

        
        if not session_id:
            session_id = str(uuid.uuid4())

        history = SESSIONS.get(session_id, [])
        # OpenAI transcription endpoint (speech-to-text)
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=(audio.filename or "speech.webm", audio_bytes),
        )
        user_text = transcript.text.strip()

        # 2) Teacher response (LLM)
        messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + mode_instruction(mode)}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        chat = client.chat.completions.create(
               model="gpt-4o-mini",
               messages=messages,
               temperature=0.4,
            )
        reply_text = chat.choices[0].message.content.strip()
        #After you compute reply_text, update history and store it
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply_text})

        # keep only last MAX_TURNS*2 messages (user+assistant pairs)
        history = history[-MAX_TURNS*2:]
        SESSIONS[session_id] = history
        # 3) Text-to-speech (TTS)
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
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def mode_instruction(mode: str) -> str:
    if mode == "correct":
        return (
            "Mode: Correct my sentence. Keep it short. "
            "First: corrected sentence. Second: one tiny rule. Third: ask the child to repeat."
        )
    if mode == "roleplay":
        return (
            "Mode: Rollenspiel in Österreich (Bäckerei, Supermarkt, Volksschule, Spielplatz, Bim). "
            "Verwende österreichische Wörter (Jause, Semmel, Sackerl). "
            "Stell pro Runde genau eine Frage."
       )

    if mode == "quiz":
        return (
            "Mode: Mini quiz. Ask exactly 3 short questions one by one. "
            "Wait for the child's answer each time. Keep A1 level."
        )
    return "Mode: Chat naturally about daily life and school."
