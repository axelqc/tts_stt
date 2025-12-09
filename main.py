# main.py
# FastAPI server integrating Twilio Media Streams + IBM STT/TTS + Groq LLM

import os
import json
import base64
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from dotenv import load_dotenv
from agent import agent_reply
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from ibm_watson import SpeechToTextV1, TextToSpeechV1
from twiml import twiml_response

load_dotenv()

IBM_STT_APIKEY = os.getenv("IBM_STT_APIKEY")
IBM_STT_URL = os.getenv("IBM_STT_URL")
IBM_TTS_APIKEY = os.getenv("IBM_TTS_APIKEY")
IBM_TTS_URL = os.getenv("IBM_TTS_URL")

app = FastAPI()

# IBM STT
stt_auth = IAMAuthenticator(IBM_STT_APIKEY)
stt = SpeechToTextV1(authenticator=stt_auth)
stt.set_service_url(IBM_STT_URL)

# IBM TTS
tts_auth = IAMAuthenticator(IBM_TTS_APIKEY)
tts = TextToSpeechV1(authenticator=tts_auth)
tts.set_service_url(IBM_TTS_URL)

@app.get("/")
async def root():
    return {"status": "server running"}

@app.post("/incoming-call")
async def incoming_call(request: Request):
    host = request.url.hostname
    xml = twiml_response(host)
    return HTMLResponse(content=xml, media_type="application/xml")

@app.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    print("Client connected.")

    stream_sid = None

    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)

            if data["event"] == "start":
                stream_sid = data["start"]["streamSid"]
                print("🔵 Stream started", stream_sid)

            elif data["event"] == "media":
                audio_b64 = data["media"]["payload"]
                audio_bytes = base64.b64decode(audio_b64)

                # IBM STT
                result = stt.recognize(
                    audio=audio_bytes,
                    content_type="audio/l16; rate=8000"
                ).get_result()

                text = ""
                try:
                    text = result["results"][0]["alternatives"][0]["transcript"]
                    print("👤 User:", text)
                except:
                    continue

                # Agent (Groq)
                reply = agent_reply(text)
                print("🤖 Agent:", reply)

                # IBM TTS
                audio_reply = tts.synthesize(
                    text=reply,
                    accept="audio/wav",
                    voice="es-LA_SofiaV3Voice"
                ).get_result().content

                out_b64 = base64.b64encode(audio_reply).decode()

                await ws.send_json({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": out_b64}
                })

            elif data["event"] == "stop":
                print("🔴 Stream stopped")
                break

    except WebSocketDisconnect:
        print("Client disconnected.")
