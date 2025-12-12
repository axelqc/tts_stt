# main.py - Versión FINAL CORREGIDA

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
from fastapi.responses import HTMLResponse, Response
from fastapi.websockets import WebSocketDisconnect
from dotenv import load_dotenv
from agent import agent_reply
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from ibm_watson import SpeechToTextV1, TextToSpeechV1
from recording_manager import CallRecorder

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
DUPLICATE_RESPONSE_THRESHOLD = 3
WEBSOCKET_PING_INTERVAL = 10

# Parámetros de buffer y detección de silencio
MIN_BUFFER_SIZE = 16000  # 2 segundos
MAX_BUFFER_SIZE = 64000  # 8 segundos
SILENCE_THRESHOLD = 200
SILENCE_DURATION = 0.8
SILENCE_CHUNKS = int((SILENCE_DURATION * 8000) / 160)

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
    """Detecta si un chunk de audio es silencio"""
    try:
        pcm = audioop.ulaw2lin(audio_chunk, 2)
        rms = audioop.rms(pcm, 2)
        return rms < SILENCE_THRESHOLD
    except:
        return False


def convert_mulaw_to_pcm_16k(mulaw_data):
    """Convierte audio μ-law 8kHz a PCM linear 16kHz para IBM Watson STT"""
    try:
        logger.info(f"🔄 Convirtiendo {len(mulaw_data)} bytes de μ-law...")
        unique_bytes = len(set(mulaw_data))
        
        if unique_bytes < 5:
            logger.warning(f"   ⚠️  Audio parece ser silencio")
            raise ValueError("Audio es silencio")
        
        pcm_data = audioop.ulaw2lin(mulaw_data, 2)
        rms_original = audioop.rms(pcm_data, 2)
        logger.info(f"   📊 Volumen RMS original: {rms_original}")
        
        pcm_16k, _ = audioop.ratecv(pcm_data, 2, 1, 8000, 16000, None)
        rms = audioop.rms(pcm_16k, 2)
        logger.info(f"   📊 Volumen RMS final: {rms}")
        
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
    """Convierte WAV a μ-law 8kHz para Twilio"""
    try:
        with wave.open(io.BytesIO(wav_data), 'rb') as wav_file:
            params = wav_file.getparams()
            frames = wav_file.readframes(params.nframes)
            
            if params.framerate != 8000:
                frames, _ = audioop.ratecv(
                    frames,
                    params.sampwidth,
                    params.nchannels,
                    params.framerate,
                    8000,
                    None
                )
            
            if params.nchannels == 2:
                frames = audioop.tomono(frames, params.sampwidth, 1, 1)
            
            mulaw_audio = audioop.lin2ulaw(frames, params.sampwidth)
            return mulaw_audio
    except Exception as e:
        logger.error(f"❌ Error convirtiendo WAV a μ-law: {e}")
        raise


async def send_audio_to_twilio(ws, stream_sid, text, voice="es-LA_SofiaV3Voice", mark_name=None):
    """
    Convierte texto a audio y lo envía a Twilio con mark events para sincronización.
    CORRECCIÓN CLAVE: Usa eventos 'mark' en lugar de sleep para evitar bloqueos.
    """
    try:
        logger.info(f"📊 Generando audio para: '{text[:50]}...'")
        
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

        mulaw_audio = convert_wav_to_mulaw_8k(audio_reply)
        duration_seconds = len(mulaw_audio) / 8000
        logger.info(f"⏱️  Duración del audio: {duration_seconds:.1f}s")
        
        # Generar un mark único si no se proporcionó
        if mark_name is None:
            mark_name = f"audio_{int(time.time() * 1000)}"
        
        chunk_size = 160
        chunks_sent = 0
        
        # Enviar todos los chunks de audio
        for i in range(0, len(mulaw_audio), chunk_size):
            chunk = mulaw_audio[i:i+chunk_size]
            chunk_b64 = base64.b64encode(chunk).decode()
            
            await ws.send_json({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": chunk_b64}
            })
            chunks_sent += 1
            
            if chunks_sent % 50 == 0:
                await asyncio.sleep(0.01)
        
        # CORRECCIÓN CRÍTICA: Enviar evento 'mark' al final del audio
        await ws.send_json({
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": mark_name}
        })
        
        logger.info(f"✅ Audio enviado ({chunks_sent} chunks) + mark '{mark_name}'")
        
        del mulaw_audio
        del audio_reply
        
        return mark_name
        
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Timeout generando audio TTS")
        raise
    except Exception as e:
        logger.error(f"❌ Error enviando audio: {e}")
        raise


async def send_greeting(ws, stream_sid):
    """Envía saludo inicial"""
    greeting = "Hola, ¿en qué puedo ayudarte?"
    logger.info("🤖 Enviando saludo inicial...")
    return await send_audio_to_twilio(ws, stream_sid, greeting, mark_name="greeting")


async def recognize_with_timeout(pcm_audio, timeout=STT_TIMEOUT) -> Optional[dict]:
    """Ejecuta IBM STT con timeout"""
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
                        content_type="audio/l16; rate=16000",
                        model=model,
                        smart_formatting=True,
                        max_alternatives=1,
                        inactivity_timeout=5,
                        background_audio_suppression=0.5,
                        speech_detector_sensitivity=0.5,
                    )
                ),
                timeout=timeout
            )
            
            result_dict = result.get_result()
            
            if result_dict and result_dict.get("results"):
                alternatives = result_dict["results"][0].get("alternatives", [])
                if alternatives and alternatives[0].get("transcript", "").strip():
                    logger.info(f"✅ Transcripción con {model}")
                    return result_dict
            
            logger.info(f"⚠️ Sin transcripción en {model}, probando siguiente...")
            
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Timeout en {model}")
            continue
        except Exception as e:
            logger.error(f"❌ Error en {model}: {e}")
            continue
    
    logger.warning("❌ Ningún modelo STT funcionó")
    return None


async def agent_reply_async(text: str, timeout=AGENT_TIMEOUT) -> str:
    """Wrapper asíncrono para agent_reply con timeout"""
    loop = asyncio.get_event_loop()
    try:
        reply = await asyncio.wait_for(
            loop.run_in_executor(None, agent_reply, text),
            timeout=timeout
        )
        return reply
    except asyncio.TimeoutError:
        logger.error("⏱️ Timeout en agent_reply")
        return "Lo siento, estoy teniendo problemas para procesar tu solicitud."
    except Exception as e:
        logger.error(f"❌ Error en agent_reply: {e}")
        return "Disculpa, ocurrió un error. ¿Puedes repetir?"


async def keep_alive(ws):
    """Mantiene la conexión activa con pings periódicos"""
    try:
        while True:
            await asyncio.sleep(WEBSOCKET_PING_INTERVAL)
            await ws.send_json({"event": "keepalive"})
            logger.debug("💓 Keepalive enviado")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"❌ Error en keepalive: {e}")


def generate_twiml(host: str) -> str:
    """
    Genera el TwiML response con la URL del WebSocket.
    NOTA: No incluir <Record> cuando usas <Stream> - son mutuamente exclusivos.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/media-stream" />
    </Connect>
</Response>"""


# ========================================
# HTTP ENDPOINTS
# ========================================

@app.get("/")
async def index():
    return HTMLResponse("""
        <h1>🤖 Twilio Voice Bot</h1>
        <p>WebSocket endpoint: <code>/media-stream</code></p>
        <p>Status: ✅ Running</p>
    """)


@app.post("/twiml")
async def handle_twiml(request: Request):
    """Endpoint principal para TwiML"""
    # Obtener el host del header o request
    host = request.headers.get("host") or str(request.base_url).replace("http://", "").replace("https://", "").rstrip("/")
    
    logger.info(f"📞 Llamada entrante desde host: {host}")
    
    twiml = generate_twiml(host)
    return Response(content=twiml, media_type="application/xml")


@app.post("/incoming-call")
async def incoming_call(request: Request):
    """Alias para /twiml"""
    return await handle_twiml(request)


@app.post("/voice")
async def voice(request: Request):
    """Otro alias común para /twiml"""
    return await handle_twiml(request)


@app.post("/recording-status")
async def recording_status(request: Request):
    """
    Endpoint para recibir callbacks de estado de grabación de Twilio.
    NOTA: Este endpoint ya no es necesario porque manejamos grabación internamente,
    pero lo mantenemos por compatibilidad.
    """
    form_data = await request.form()
    logger.info(f"📝 Recording status callback: {dict(form_data)}")
    return {"status": "received"}


# ========================================
# WEBSOCKET ENDPOINT
# ========================================

@app.websocket("/media-stream")
async def handle_media_stream(ws: WebSocket):
    """
    WebSocket endpoint principal para Twilio Media Streams.
    CORRECCIONES:
    1. Usa eventos 'mark' para sincronización (no sleep)
    2. Endpoint correcto: /media-stream
    3. Manejo de grabación interno (no usa <Record> de Twilio)
    """
    await ws.accept()
    logger.info("✅ WebSocket conectado en /media-stream")
    
    # Estado de la conversación
    stream_sid = None
    call_sid = None
    has_greeted = False
    is_speaking = False
    audio_buffer = b""
    chunks_received = 0
    consecutive_silence_chunks = 0
    has_speech = False
    last_response_time = 0
    recorder = None
    
    # Rastrear marks pendientes (CORRECCIÓN CLAVE)
    pending_marks = set()
    current_mark = None
    
    keep_alive_task = asyncio.create_task(keep_alive(ws))
    
    try:
        async for message in ws.iter_text():
            data = json.loads(message)
            
            if data["event"] == "start":
                stream_sid = data["start"]["streamSid"]
                call_sid = data["start"].get("callSid", "unknown")
                logger.info(f"📞 Stream iniciado: {stream_sid}")
                logger.info(f"📞 Call SID: {call_sid}")
                
                media_format = data["start"].get("mediaFormat", {})
                logger.info(f"📋 Format: {json.dumps(media_format, indent=2)}")
                
                # 🎬 Iniciar grabación interna
                try:
                    storage_type = os.getenv("RECORDING_STORAGE", "local")
                    recorder = CallRecorder(call_sid, storage_type=storage_type)
                    recorder.start_recording()
                    logger.info(f"🎬 Grabación iniciada (storage: {storage_type})")
                except Exception as e:
                    logger.error(f"❌ Error iniciando grabación: {e}")
                    recorder = None
                
                if not has_greeted:
                    has_greeted = True
                    is_speaking = True
                    
                    try:
                        mark_name = await send_greeting(ws, stream_sid)
                        pending_marks.add(mark_name)
                        current_mark = mark_name
                        logger.info(f"🎯 Esperando mark: {mark_name}")
                    except Exception as e:
                        logger.error(f"❌ Error saludo: {e}")
                        is_speaking = False
                    
                    # Resetear buffers
                    audio_buffer = b""
                    chunks_received = 0
                    consecutive_silence_chunks = 0
                    has_speech = False

            # CORRECCIÓN CLAVE: Manejar evento 'mark' de Twilio
            elif data["event"] == "mark":
                mark_name = data["mark"]["name"]
                logger.info(f"✅ Mark recibido: {mark_name}")
                
                if mark_name in pending_marks:
                    pending_marks.remove(mark_name)
                
                # Si este era el mark actual y no hay más marks pendientes
                if mark_name == current_mark and len(pending_marks) == 0:
                    is_speaking = False
                    current_mark = None
                    logger.info("👂 Listo para escuchar (mark confirmado)")

            elif data["event"] == "media":
                # Solo ignorar audio si hay marks pendientes
                if is_speaking and len(pending_marks) > 0:
                    continue
                
                audio_b64 = data["media"]["payload"]
                audio_bytes = base64.b64decode(audio_b64)
                
                # 🎬 Grabar cada chunk
                if recorder and recorder.is_recording:
                    try:
                        recorder.add_audio_chunk(audio_bytes)
                    except Exception as e:
                        logger.error(f"❌ Error grabando chunk: {e}")
                
                chunks_received += 1
                
                # Detectar silencio puro
                if audio_bytes == b'\xff' * len(audio_bytes) or audio_bytes == b'\x00' * len(audio_bytes):
                    consecutive_silence_chunks += 1
                    continue
                
                # Protección contra buffer overflow
                if len(audio_buffer) > MAX_BUFFER_SIZE:
                    logger.warning(f"⚠️ Buffer excedió {MAX_BUFFER_SIZE} bytes, reseteando")
                    audio_buffer = b""
                    chunks_received = 0
                    consecutive_silence_chunks = 0
                    has_speech = False
                    continue
                
                # Detección de silencio
                if is_silence(audio_bytes):
                    consecutive_silence_chunks += 1
                else:
                    if consecutive_silence_chunks > 0:
                        logger.debug(f"🔊 Habla detectada después de {consecutive_silence_chunks} chunks silencio")
                    consecutive_silence_chunks = 0
                    has_speech = True
                
                audio_buffer += audio_bytes
                
                if chunks_received % 100 == 0:
                    seconds_recorded = len(audio_buffer) / 8000
                    logger.info(f"📦 Buffer: {seconds_recorded:.1f}s")
                
                should_process = False
                
                # Condición 1: Buffer mínimo + habla + pausa detectada
                if len(audio_buffer) >= MIN_BUFFER_SIZE and has_speech:
                    if consecutive_silence_chunks >= SILENCE_CHUNKS:
                        logger.info(f"✅ Pausa detectada ({consecutive_silence_chunks} chunks silencio)")
                        should_process = True
                
                # Condición 2: Buffer máximo alcanzado
                elif len(audio_buffer) >= MAX_BUFFER_SIZE:
                    logger.info(f"✅ Buffer máximo alcanzado")
                    should_process = True
                
                if not should_process:
                    continue
                
                # PROCESAR AUDIO
                buffer_seconds = len(audio_buffer) / 8000
                logger.info(f"🎤 Procesando {buffer_seconds:.1f}s de audio")
                
                is_speaking = True
                current_buffer = audio_buffer
                audio_buffer = b""
                chunks_received = 0
                consecutive_silence_chunks = 0
                has_speech = False
                
                try:
                    # Validar que no sea silencio
                    unique_bytes = len(set(current_buffer))
                    if unique_bytes < 10:
                        logger.warning(f"⚠️ Solo {unique_bytes} valores únicos (silencio)")
                        is_speaking = False
                        continue
                    
                    # Convertir audio
                    try:
                        pcm_audio = convert_mulaw_to_pcm_16k(current_buffer)
                    except ValueError as e:
                        logger.warning(f"⚠️ Audio inválido: {e}")
                        is_speaking = False
                        continue
                    except Exception as e:
                        logger.error(f"❌ Error conversión: {e}")
                        is_speaking = False
                        continue
                    
                    logger.info(f"📊 PCM: {len(pcm_audio)} bytes")
                    
                    # Speech-to-Text
                    result = await recognize_with_timeout(pcm_audio, timeout=STT_TIMEOUT)
                    
                    del current_buffer
                    del pcm_audio
                    
                    if not result:
                        logger.warning("⚠️ STT sin resultado")
                        is_speaking = False
                        continue
                    
                    # Extraer texto y confianza
                    text = ""
                    confidence = 0
                    if result.get("results") and len(result["results"]) > 0:
                        alternatives = result["results"][0].get("alternatives", [])
                        if alternatives and len(alternatives) > 0:
                            text = alternatives[0].get("transcript", "").strip()
                            confidence = alternatives[0].get("confidence", 0)
                            logger.info(f"📝 '{text}' (conf: {confidence:.2f})")
                    
                    # Validar texto
                    if not text or len(text) < 3 or confidence < 0.5:
                        logger.warning(f"⚠️ Rechazado: '{text}' (conf: {confidence:.2f})")
                        is_speaking = False
                        continue
                    
                    logger.info(f"💬 User: {text}")
                    
                    # Prevenir respuestas duplicadas
                    current_time = time.time()
                    if last_response_time > 0 and current_time - last_response_time < DUPLICATE_RESPONSE_THRESHOLD:
                        logger.info("⏭️ Ignorado (respuesta reciente)")
                        is_speaking = False
                        continue
                    
                    # Obtener respuesta del agente
                    reply = await agent_reply_async(text, timeout=AGENT_TIMEOUT)
                    logger.info(f"🤖 Agent: {reply[:100]}...")
                    
                    try:
                        # Enviar audio con mark (CORRECCIÓN CLAVE)
                        mark_name = await asyncio.wait_for(
                            send_audio_to_twilio(ws, stream_sid, reply),
                            timeout=TTS_TIMEOUT + 5
                        )
                        
                        # Agregar mark a pendientes
                        pending_marks.add(mark_name)
                        current_mark = mark_name
                        logger.info(f"🎯 Esperando mark: {mark_name}")
                        
                        last_response_time = time.time()
                        
                    except asyncio.TimeoutError:
                        logger.error("⏱️ Timeout TTS")
                        is_speaking = False
                    except Exception as e:
                        logger.error(f"❌ Error TTS: {e}")
                        is_speaking = False
                    
                except Exception as e:
                    logger.error(f"❌ Error procesamiento: {e}")
                    import traceback
                    traceback.print_exc()
                    is_speaking = False

            elif data["event"] == "stop":
                logger.info("🔴 Stream stopped")
                
                # 🎬 Finalizar y subir grabación
                if recorder and recorder.is_recording:
                    logger.info("💾 Finalizando grabación...")
                    try:
                        recording_url = await recorder.finalize()
                        if recording_url:
                            logger.info(f"🎬 ✅ Grabación disponible: {recording_url}")
                        else:
                            logger.warning("⚠️ No se pudo guardar la grabación")
                    except Exception as e:
                        logger.error(f"❌ Error finalizando grabación: {e}")
                
                break

    except WebSocketDisconnect:
        logger.info("❌ Client disconnected")
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 🎬 Backup: Guardar grabación en caso de cierre inesperado
        if recorder and recorder.is_recording:
            logger.info("💾 Guardando grabación por cierre inesperado...")
            try:
                recording_url = await recorder.finalize()
                if recording_url:
                    logger.info(f"🎬 ✅ Grabación guardada: {recording_url}")
            except Exception as e:
                logger.error(f"❌ Error guardando grabación: {e}")
        
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
