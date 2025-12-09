# main.py
# FastAPI server integrating Twilio Media Streams + IBM STT/TTS + Groq LLM

import os
import json
import base64
import audioop
import io
import wave
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

def convert_mulaw_to_pcm_16k(mulaw_data):
    """
    Convierte audio μ-law 8kHz a PCM linear 16kHz para IBM Watson STT
    Usa audioop (built-in) para evitar dependencia de ffmpeg
    """
    try:
        # Decodificar μ-law a PCM linear 16-bit
        pcm_data = audioop.ulaw2lin(mulaw_data, 2)
        
        # Resamplear de 8kHz a 16kHz
        pcm_16k, _ = audioop.ratecv(pcm_data, 2, 1, 8000, 16000, None)
        
        return pcm_16k
    except Exception as e:
        print(f"Error en conversión de audio: {e}")
        raise

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
    audio_buffer = b""  # Buffer para acumular audio

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
                
                # Acumular audio (Twilio envía chunks pequeños)
                audio_buffer += audio_bytes
                
                # Procesar cuando tengamos suficiente audio (ej: 0.5 segundos = 4000 bytes a 8kHz)
                if len(audio_buffer) < 4000:
                    continue
                
                try:
                    # Convertir de μ-law 8kHz a PCM 16kHz
                    pcm_audio = convert_mulaw_to_pcm_16k(audio_buffer)
                    
                    # IBM STT con formato correcto
                    result = stt.recognize(
                        audio=pcm_audio,
                        content_type="audio/l16; rate=16000; channels=1",
                        model="es-LA_BroadbandModel",  # Modelo en español
                        continuous=True,
                        interim_results=False
                    ).get_result()

                    text = ""
                    if result.get("results") and len(result["results"]) > 0:
                        text = result["results"][0]["alternatives"][0]["transcript"].strip()
                    
                    # Limpiar buffer después de procesar
                    audio_buffer = b""
                    
                    if not text:
                        continue
                        
                    print("💤 User:", text)

                    # Agent (Groq)
                    reply = agent_reply(text)
                    print("🤖 Agent:", reply)

                    # IBM TTS
                    audio_reply = tts.synthesize(
                        text=reply,
                        accept="audio/wav",
                        voice="es-LA_SofiaV3Voice"
                    ).get_result().content

                    # Convertir respuesta WAV a μ-law 8kHz para Twilio
                    # Leer WAV
                    with wave.open(io.BytesIO(audio_reply), 'rb') as wav_file:
                        params = wav_file.getparams()
                        frames = wav_file.readframes(params.nframes)
                        
                        # Resamplear a 8kHz si es necesario
                        if params.framerate != 8000:
                            frames, _ = audioop.ratecv(
                                frames,
                                params.sampwidth,
                                params.nchannels,
                                params.framerate,
                                8000,
                                None
                            )
                        
                        # Convertir a mono si es necesario
                        if params.nchannels == 2:
                            frames = audioop.tomono(frames, params.sampwidth, 1, 1)
                        
                        # Convertir a μ-law
                        mulaw_audio = audioop.lin2ulaw(frames, params.sampwidth)
                    
                    out_b64 = base64.b64encode(mulaw_audio).decode()

                    await ws.send_json({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": out_b64}
                    })
                    
                except Exception as e:
                    print(f"Error procesando audio: {e}")
                    audio_buffer = b""  # Limpiar buffer en caso de error
                    continue

            elif data["event"] == "stop":
                print("🔴 Stream stopped")
                break

    except WebSocketDisconnect:
        print("Client disconnected.")
    except Exception as e:
        print(f"Error en websocket: {e}")
