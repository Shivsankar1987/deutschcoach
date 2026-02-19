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

# Enable CORS (important for iOS Safari)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


SYSTEM_PROMPT = """
Du bist 'DeutschCoach', eine freundliche Deutschlehrerin für ein Volksschulkind.
Sprich Deutsch in österreichischer Variante (de-AT).
Kurze, klare Sätze.
Wenn das Kind Fehler macht:
1) Sag den richtigen Satz.
2) Erkläre eine Mini-Regel.
3) Stelle genau eine Frage.
"""


SESSIONS: Dict[str, List[dict]] = {}
MAX_TURNS = 6
MIN_AUDIO_BYTES = 1000


def mode_instruction(mode: str) -> str:
    if mode == "chat":
        return "Mode: lockeres Gespräch."
    elif mode == "roleplay":
        return "Mode: Rollenspiel in Österreich (Bäckerei, Schule, Spielplatz)."
    elif mode == "grammar":
        return "Mode: Fokus auf Grammatik."
    return ""


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
            raise HTTPException(status_code=400, detail="Audio zu kurz.")

        if not session_id:
            session_id = str(uuid.uuid4())

        history = SESSIONS.get(session_id, [])

        transcript = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=(audio.filename or "speech.webm", audio_bytes),
        )
        user_text = transcript.text.strip()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + mode_instruction(mode)}
        ]
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
async def reset(payload: dict = Body(...)):
    session_id = (payload.get("session_id") or "").strip()
    if session_id in SESSIONS:
        del SESSIONS[session_id]
    return {"status": "reset"}
