# main.py - Versión optimizada con detección de silencio

import os
import json
import base64
import audioop
import io
import wave
import asyncio
import time
import logging
from functools import partial
from typing import Optional
from fastapi import FastAPI, WebSocket, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from dotenv import load_dotenv
from agent import agent_reply
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from ibm_watson import SpeechToTextV1, TextToSpeechV1
from twiml import twiml_response

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Configuración
IBM_STT_APIKEY = os.getenv("IBM_STT_APIKEY")
IBM_STT_URL = os.getenv("IBM_STT_URL")
IBM_TTS_APIKEY = os.getenv("IBM_TTS_APIKEY")
IBM_TTS_URL = os.getenv("IBM_TTS_URL")

# Timeouts optimizados
STT_TIMEOUT = 8
AGENT_TIMEOUT = 12
TTS_TIMEOUT = 20
ACTIVITY_TIMEOUT = 30
DUPLICATE_RESPONSE_THRESHOLD = 3  # Reducido de 5 a 3 segundos
WEBSOCKET_PING_INTERVAL = 10

# ✅ OPTIMIZACIÓN: Parámetros de buffer y detección de silencio
MIN_BUFFER_SIZE = 16000  # 2 segundos mínimo (reducido de 5s)
MAX_BUFFER_SIZE = 64000  # 8 segundos máximo
SILENCE_THRESHOLD = 200  # RMS por debajo de esto = silencio
SILENCE_DURATION = 0.8   # segundos de silencio para procesar (reducido de implícito 5s)
SILENCE_CHUNKS = int((SILENCE_DURATION * 8000) / 160)  # ~40 chunks

app = FastAPI()

# IBM STT
stt_auth = IAMAuthenticator(IBM_STT_APIKEY)
stt = SpeechToTextV1(authenticator=stt_auth)
stt.set_service_url(IBM_STT_URL)

# IBM TTS
tts_auth = IAMAuthenticator(IBM_TTS_APIKEY)
tts = TextToSpeechV1(authenticator=tts_auth)
tts.set_service_url(IBM_TTS_URL)


def is_silence(audio_chunk: bytes) -> bool:
    """
    Detecta si un chunk de audio es silencio
    """
    try:
        # Decodificar μ-law a PCM para calcular RMS
        pcm = audioop.ulaw2lin(audio_chunk, 2)
        rms = audioop.rms(pcm, 2)
        return rms < SILENCE_THRESHOLD
    except:
        return False


def convert_mulaw_to_pcm_16k(mulaw_data):
    """
    Convierte audio μ-law 8kHz a PCM linear 16kHz para IBM Watson STT
    """
    try:
        logger.info(f"🔄 Convirtiendo {len(mulaw_data)} bytes de μ-law...")
        
        # Verificar que no sea todo silencio
        unique_bytes = len(set(mulaw_data))
        
        if unique_bytes < 5:
            logger.warning(f"   ⚠️  Audio parece ser silencio")
            raise ValueError("Audio es silencio")
        
        # Decodificar μ-law a PCM linear 16-bit
        pcm_data = audioop.ulaw2lin(mulaw_data, 2)
        
        # Calcular RMS antes del resampling
        rms_original = audioop.rms(pcm_data, 2)
        logger.info(f"   📊 Volumen RMS original: {rms_original}")
        
        # Resamplear de 8kHz a 16kHz
        pcm_16k, _ = audioop.ratecv(pcm_data, 2, 1, 8000, 16000, None)
        
        # Calcular RMS después del resampling
        rms = audioop.rms(pcm_16k, 2)
        logger.info(f"   📊 Volumen RMS final: {rms}")
        
        # Amplificar si es necesario
        if rms < 300:
            factor = min(3.0, 900 / max(rms, 1))
            logger.info(f"   📊 Amplificando audio {factor:.1f}x")
            pcm_16k = audioop.mul(pcm_16k, 2, factor)
            rms_final = audioop.rms(pcm_16k, 2)
            logger.info(f"   ✓ RMS después de amplificar: {rms_final}")
        
        return pcm_16k
    except Exception as e:
        logger.error(f"❌ Error en conversión de audio: {e}")
        raise


def convert_wav_to_mulaw_8k(wav_data):
    """
    Convierte WAV a μ-law 8kHz para Twilio
    """
    try:
        with wave.open(io.BytesIO(wav_data), 'rb') as wav_file:
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
            
            return mulaw_audio
    except Exception as e:
        logger.error(f"❌ Error convirtiendo WAV a μ-law: {e}")
        raise


async def send_audio_to_twilio(ws, stream_sid, text, voice="es-LA_SofiaV3Voice"):
    """
    Convierte texto a audio y lo envía a Twilio
    """
    try:
        logger.info(f"📊 Generando audio para: '{text[:50]}...'")
        
        # IBM TTS con timeout
        loop = asyncio.get_event_loop()
        audio_reply = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: tts.synthesize(
                    text=text,
                    accept="audio/wav",
                    voice=voice
                ).get_result().content
            ),
            timeout=TTS_TIMEOUT
        )

        # Convertir a μ-law para Twilio
        mulaw_audio = convert_wav_to_mulaw_8k(audio_reply)
        
        # Calcular duración aproximada del audio
        duration_seconds = len(mulaw_audio) / 8000
        logger.info(f"⏱️  Duración del audio: {duration_seconds:.1f}s")
        
        # Enviar en chunks de 20ms (160 bytes a 8kHz)
        chunk_size = 160
        chunks_sent = 0
        
        for i in range(0, len(mulaw_audio), chunk_size):
            chunk = mulaw_audio[i:i+chunk_size]
            chunk_b64 = base64.b64encode(chunk).decode()
            
            await ws.send_json({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": chunk_b64}
            })
            chunks_sent += 1
            
            # Pequeña pausa cada 50 chunks
            if chunks_sent % 50 == 0:
                await asyncio.sleep(0.01)
        
        logger.info(f"✅ Audio enviado ({chunks_sent} chunks)")
        
        # ✅ OPTIMIZACIÓN: Reducir espera después del audio
        # Esperar solo el tiempo del audio + pequeño buffer
        await asyncio.sleep(duration_seconds + 0.3)  # Reducido de 0.5 a 0.3
        logger.info("🎧 Listo para escuchar")
        
        # Liberación de memoria
        del mulaw_audio
        del audio_reply
        
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Timeout generando audio TTS")
        raise
    except Exception as e:
        logger.error(f"❌ Error enviando audio: {e}")
        raise


async def send_greeting(ws, stream_sid):
    """
    Envía saludo inicial
    """
    greeting = "Hola, ¿en qué puedo ayudarte?"
    logger.info("🤖 Enviando saludo inicial...")
    await send_audio_to_twilio(ws, stream_sid, greeting)


async def recognize_with_timeout(pcm_audio, timeout=STT_TIMEOUT) -> Optional[dict]:
    """
    Ejecuta IBM STT con timeout para evitar cuelgues
    """
    loop = asyncio.get_event_loop()
    
    spanish_models = [
        "es-MX_BroadbandModel",
        "es-ES_BroadbandModel", 
        "es-LA_BroadbandModel"
    ]
    
    for model in spanish_models:
        try:
            logger.info(f"🎯 STT con modelo: {model}")
            
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    partial(
                        stt.recognize,
                        audio=pcm_audio,
                        content_type="audio/l16;rate=16000",
                        model=model,
                        max_alternatives=1,
                        word_confidence=True
                    )
                ),
                timeout=timeout
            )
            
            result_dict = result.get_result()
            logger.info(f"✅ STT exitoso")
            return result_dict
            
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Timeout con {model}")
            continue
        except Exception as e:
            logger.warning(f"❌ Error con {model}: {e}")
            continue
    
    logger.error("❌ Todos los modelos STT fallaron")
    return None


async def agent_reply_async(text: str, timeout: int = AGENT_TIMEOUT) -> str:
    """
    Llama a agent_reply de forma asíncrona con timeout
    """
    loop = asyncio.get_event_loop()
    try:
        reply = await asyncio.wait_for(
            loop.run_in_executor(None, agent_reply, text),
            timeout=timeout
        )
        return reply
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Timeout del agente ({timeout}s)")
        return "Disculpa, estoy teniendo problemas para procesar tu solicitud. ¿Podrías repetirlo?"
    except Exception as e:
        logger.error(f"❌ Error en agent_reply: {e}")
        return "Lo siento, ha ocurrido un error. ¿Podrías intentarlo de nuevo?"


@app.post("/voice")
async def voice_webhook(request: Request):
    """
    Webhook para iniciar llamada con Twilio
    """
    return twiml_response()


@app.post("/recording-status")
async def recording_status(
    RecordingSid: str = Form(...),
    RecordingUrl: str = Form(...),
    RecordingStatus: str = Form(...),
    CallSid: str = Form(...)
):
    """
    Callback cuando Twilio termina una grabación
    """
    logger.info(f"📼 Recording status: {RecordingStatus}")
    
    if RecordingStatus == "completed":
        logger.info(f"✅ Grabación completada: {RecordingUrl}")
        
    elif RecordingStatus == "absent":
        logger.warning(f"⚠️ Grabación ausente para call {CallSid}")
        
    elif RecordingStatus == "failed":
        logger.error(f"❌ Grabación falló para call {CallSid}")
    
    return {
        "status": "received",
        "recording_sid": RecordingSid,
        "call_sid": CallSid
    }


async def keep_alive(ws: WebSocket, interval: int = WEBSOCKET_PING_INTERVAL):
    """
    Mantiene la conexión WebSocket activa
    """
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await ws.send_json({"event": "ping"})
                logger.debug("💓 Keep-alive")
            except Exception as e:
                logger.error(f"❌ Error en keep-alive: {e}")
                break
    except asyncio.CancelledError:
        logger.info("🛑 Keep-alive cancelado")


@app.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    logger.info("✅ Client connected.")

    # Estado de la sesión
    stream_sid = None
    audio_buffer = b""
    is_speaking = False
    chunks_received = 0
    has_greeted = False
    last_response_time = 0
    last_activity = time.time()
    
    # ✅ NUEVO: Variables para detección de silencio
    consecutive_silence_chunks = 0
    has_speech = False  # Indica si hemos detectado habla en el buffer actual
    
    keep_alive_task = asyncio.create_task(keep_alive(ws))

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
                last_activity = time.time()
                
            except asyncio.TimeoutError:
                elapsed = time.time() - last_activity
                if elapsed > ACTIVITY_TIMEOUT:
                    logger.warning(f"⏱️ Timeout de inactividad ({elapsed:.0f}s)")
                    audio_buffer = b""
                    is_speaking = False
                    chunks_received = 0
                    consecutive_silence_chunks = 0
                    has_speech = False
                    last_activity = time.time()
                continue
            
            data = json.loads(msg)
            
            # Logging selectivo
            if data["event"] != "media" or chunks_received % 100 == 0:
                logger.debug(f"📨 {data['event']}")

            if data["event"] == "connected":
                logger.info("🔗 WebSocket conectado")
            
            elif data["event"] == "start":
                stream_sid = data["start"]["streamSid"]
                logger.info(f"🔵 Stream: {stream_sid}")
                
                media_format = data["start"].get("mediaFormat", {})
                logger.info(f"📋 Format: {json.dumps(media_format, indent=2)}")
                
                if not has_greeted:
                    has_greeted = True
                    is_speaking = True
                    
                    try:
                        await send_greeting(ws, stream_sid)
                    except Exception as e:
                        logger.error(f"❌ Error saludo: {e}")
                    finally:
                        is_speaking = False
                        audio_buffer = b""
                        chunks_received = 0
                        consecutive_silence_chunks = 0
                        has_speech = False
                        logger.info("👂 Listo")

            elif data["event"] == "media":
                if is_speaking:
                    continue
                
                audio_b64 = data["media"]["payload"]
                audio_bytes = base64.b64decode(audio_b64)
                
                chunks_received += 1
                
                # Verificar silencio total
                if audio_bytes == b'\xff' * len(audio_bytes) or audio_bytes == b'\x00' * len(audio_bytes):
                    consecutive_silence_chunks += 1
                    continue
                
                # Verificar límite de memoria
                if len(audio_buffer) > MAX_BUFFER_SIZE:
                    logger.warning(f"⚠️ Buffer excedió {MAX_BUFFER_SIZE} bytes, reseteando")
                    audio_buffer = b""
                    chunks_received = 0
                    consecutive_silence_chunks = 0
                    has_speech = False
                    continue
                
                # ✅ DETECCIÓN DE SILENCIO
                if is_silence(audio_bytes):
                    consecutive_silence_chunks += 1
                else:
                    # Hay habla, resetear contador de silencio
                    if consecutive_silence_chunks > 0:
                        logger.debug(f"🔊 Habla detectada después de {consecutive_silence_chunks} chunks silencio")
                    consecutive_silence_chunks = 0
                    has_speech = True
                
                # Acumular audio
                audio_buffer += audio_bytes
                
                # Log progreso cada 100 chunks
                if chunks_received % 100 == 0:
                    seconds_recorded = len(audio_buffer) / 8000
                    logger.info(f"📦 Buffer: {seconds_recorded:.1f}s")
                
                # ✅ OPTIMIZACIÓN: Decidir cuándo procesar
                should_process = False
                
                # Opción 1: Buffer mínimo + pausa detectada
                if len(audio_buffer) >= MIN_BUFFER_SIZE and has_speech:
                    if consecutive_silence_chunks >= SILENCE_CHUNKS:
                        logger.info(f"✅ Pausa detectada ({consecutive_silence_chunks} chunks silencio)")
                        should_process = True
                
                # Opción 2: Buffer llegó al máximo
                elif len(audio_buffer) >= MAX_BUFFER_SIZE:
                    logger.info(f"✅ Buffer máximo alcanzado")
                    should_process = True
                
                if not should_process:
                    continue
                
                # Procesar audio
                buffer_seconds = len(audio_buffer) / 8000
                logger.info(f"🎤 Procesando {buffer_seconds:.1f}s de audio")
                
                # Bloquear procesamiento
                is_speaking = True
                current_buffer = audio_buffer
                audio_buffer = b""
                chunks_received = 0
                consecutive_silence_chunks = 0
                has_speech = False
                
                try:
                    # Verificar que no sea todo silencio
                    unique_bytes = len(set(current_buffer))
                    if unique_bytes < 10:
                        logger.warning(f"⚠️ Solo {unique_bytes} valores únicos (silencio)")
                        continue
                    
                    # Convertir audio
                    try:
                        pcm_audio = convert_mulaw_to_pcm_16k(current_buffer)
                    except ValueError as e:
                        logger.warning(f"⚠️ Audio inválido: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"❌ Error conversión: {e}")
                        continue
                    
                    logger.info(f"📊 PCM: {len(pcm_audio)} bytes")
                    
                    # IBM STT
                    result = await recognize_with_timeout(pcm_audio, timeout=STT_TIMEOUT)
                    
                    # Liberación de memoria
                    del current_buffer
                    del pcm_audio
                    
                    if not result:
                        logger.warning("⚠️ STT sin resultado")
                        continue
                    
                    # Extraer texto
                    text = ""
                    confidence = 0
                    if result.get("results") and len(result["results"]) > 0:
                        alternatives = result["results"][0].get("alternatives", [])
                        if alternatives and len(alternatives) > 0:
                            text = alternatives[0].get("transcript", "").strip()
                            confidence = alternatives[0].get("confidence", 0)
                            logger.info(f"📝 '{text}' (conf: {confidence:.2f})")
                    
                    # Validar transcripción
                    if not text or len(text) < 3 or confidence < 0.5:  # Reducido de 0.6 a 0.5
                        logger.warning(f"⚠️ Rechazado: '{text}' (conf: {confidence:.2f})")
                        continue
                    
                    logger.info(f"💬 User: {text}")
                    
                    # Evitar respuestas duplicadas
                    current_time = time.time()
                    if last_response_time > 0 and current_time - last_response_time < DUPLICATE_RESPONSE_THRESHOLD:
                        logger.info("⏭️ Ignorado (respuesta reciente)")
                        continue
                    
                    # Obtener respuesta del agente
                    reply = await agent_reply_async(text, timeout=AGENT_TIMEOUT)
                    logger.info(f"🤖 Agent: {reply[:100]}...")
                    
                    # Enviar audio
                    try:
                        await asyncio.wait_for(
                            send_audio_to_twilio(ws, stream_sid, reply),
                            timeout=TTS_TIMEOUT + 5
                        )
                        last_response_time = time.time()
                        
                    except asyncio.TimeoutError:
                        logger.error("⏱️ Timeout TTS")
                    except Exception as e:
                        logger.error(f"❌ Error TTS: {e}")
                    
                except Exception as e:
                    logger.error(f"❌ Error procesamiento: {e}")
                    import traceback
                    traceback.print_exc()
                
                finally:
                    is_speaking = False
                    logger.info("👂 Listo para escuchar")

            elif data["event"] == "stop":
                logger.info("🔴 Stream stopped")
                break

    except WebSocketDisconnect:
        logger.info("❌ Client disconnected")
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
    finally:
        keep_alive_task.cancel()
        try:
            await keep_alive_task
        except asyncio.CancelledError:
            pass
        
        is_speaking = False
        audio_buffer = b""
        logger.info("🧹 Limpieza completa")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
