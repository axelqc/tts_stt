# main.py
# FastAPI server integrating Twilio Media Streams + IBM STT/TTS + Groq LLM
# VERSIÓN REFACTORIZADA - Anti-cuelgues con timeouts y manejo robusto de errores

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
from fastapi import FastAPI, WebSocket, Request
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

# Timeouts configurables
STT_TIMEOUT = 10  # segundos
AGENT_TIMEOUT = 15  # segundos
TTS_TIMEOUT = 30  # segundos
ACTIVITY_TIMEOUT = 60  # segundos sin actividad = reset
DUPLICATE_RESPONSE_THRESHOLD = 5  # segundos entre respuestas

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
    """
    try:
        logger.info(f"🔄 Convirtiendo {len(mulaw_data)} bytes de μ-law...")
        
        # Verificar que no sea todo silencio
        unique_bytes = len(set(mulaw_data))
        logger.info(f"   📊 Bytes únicos en μ-law: {unique_bytes}")
        
        if unique_bytes < 5:
            logger.warning(f"   ⚠️  Audio parece ser silencio (muy pocos valores únicos)")
            raise ValueError("Audio es silencio")
        
        # Decodificar μ-law a PCM linear 16-bit
        pcm_data = audioop.ulaw2lin(mulaw_data, 2)
        logger.info(f"   ✓ Decodificado a PCM: {len(pcm_data)} bytes")
        
        # Calcular RMS antes del resampling
        rms_original = audioop.rms(pcm_data, 2)
        logger.info(f"   📊 Volumen RMS original (8kHz): {rms_original}")
        
        # Resamplear de 8kHz a 16kHz
        pcm_16k, _ = audioop.ratecv(pcm_data, 2, 1, 8000, 16000, None)
        logger.info(f"   ✓ Resampleado a 16kHz: {len(pcm_16k)} bytes")
        
        # Calcular RMS después del resampling
        rms = audioop.rms(pcm_16k, 2)
        logger.info(f"   📊 Volumen RMS final (16kHz): {rms}")
        
        # Amplificar si es necesario
        if rms < 300:
            factor = min(3.0, 900 / max(rms, 1))  # Amplificar hasta factor 3x
            logger.info(f"   📊 Amplificando audio {factor:.1f}x (RMS bajo: {rms})")
            pcm_16k = audioop.mul(pcm_16k, 2, factor)
            rms_final = audioop.rms(pcm_16k, 2)
            logger.info(f"   ✓ RMS después de amplificar: {rms_final}")
        else:
            logger.info(f"   ✓ RMS suficiente, no se amplifica")
        
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
        logger.info(f"📊 Generando audio para: '{text}'")
        
        # IBM TTS con timeout implícito (operación síncrona rápida)
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
        duration_seconds = len(mulaw_audio) / 8000  # 8000 bytes por segundo
        logger.info(f"⏱️  Duración estimada del audio: {duration_seconds:.1f} segundos")
        
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
        
        logger.info(f"✅ Audio enviado completamente ({chunks_sent} chunks)")
        
        # Esperar a que termine de reproducirse + 1 segundo extra de buffer
        await asyncio.sleep(duration_seconds + 1.0)
        logger.info("🎧 Audio terminado de reproducir, listo para escuchar")
        
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
    logger.info("👂 Saludo completado, ahora escuchando...")


async def recognize_with_timeout(pcm_audio, timeout=STT_TIMEOUT) -> Optional[dict]:
    """
    Ejecuta IBM STT con timeout para evitar cuelgues
    """
    loop = asyncio.get_event_loop()
    
    # Modelos de español en orden de preferencia
    spanish_models = [
        "es-MX_BroadbandModel",  # Español México (mejor para Latinoamérica)
        "es-ES_BroadbandModel",   # Español España
        "es-LA_BroadbandModel",   # Español Latinoamérica
    ]
    
    for model in spanish_models:
        try:
            logger.info(f"🔄 Intentando STT con modelo {model}...")
            
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: stt.recognize(
                        audio=pcm_audio,
                        content_type="audio/l16; rate=16000",
                        model=model
                    ).get_result()
                ),
                timeout=timeout
            )
            
            logger.info(f"✅ STT exitoso con modelo {model}")
            return result
            
        except asyncio.TimeoutError:
            logger.error(f"⏱️ Timeout en STT con modelo {model}")
            continue
        except Exception as e:
            logger.warning(f"⚠️ Modelo {model} falló: {e}")
            continue
    
    # Si todos los modelos fallan, intentar con default
    try:
        logger.info("🔄 Intentando STT con modelo default...")
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: stt.recognize(
                    audio=pcm_audio,
                    content_type="audio/l16; rate=16000"
                ).get_result()
            ),
            timeout=timeout
        )
        logger.info("✅ STT exitoso con modelo default")
        return result
    except Exception as e:
        logger.error(f"❌ STT falló completamente: {e}")
        return None


async def agent_reply_async(text: str, timeout=AGENT_TIMEOUT) -> str:
    """
    Wrapper asíncrono para agent_reply con timeout
    """
    loop = asyncio.get_event_loop()
    
    try:
        reply = await asyncio.wait_for(
            loop.run_in_executor(None, agent_reply, text),
            timeout=timeout
        )
        return reply
    except asyncio.TimeoutError:
        logger.error("⏱️ Timeout en agent_reply")
        return "Disculpa, ¿puedes repetir? No procesé bien tu mensaje."
    except Exception as e:
        logger.error(f"❌ Error en agent_reply: {e}")
        return "Lo siento, tuve un problema técnico. ¿Podrías repetir?"


@app.get("/")
async def root():
    return {
        "status": "server running",
        "timestamp": time.time()
    }


@app.get("/health")
async def health():
    """Healthcheck para Render"""
    return {
        "status": "ok",
        "service": "twilio-voice-bot",
        "timestamp": time.time()
    }


@app.post("/incoming-call")
async def incoming_call(request: Request):
    host = request.url.hostname
    xml = twiml_response(host)
    return HTMLResponse(content=xml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    logger.info("✅ Client connected.")

    # Estado de la sesión
    stream_sid = None
    audio_buffer = b""
    BUFFER_SIZE = 40000  # 5 segundos a 8kHz μ-law
    is_speaking = False
    chunks_received = 0
    has_greeted = False
    last_response_time = 0
    last_activity = time.time()

    try:
        while True:
            try:
                # Recibir mensaje con timeout para detectar inactividad
                msg = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
                last_activity = time.time()
                
            except asyncio.TimeoutError:
                # Verificar inactividad prolongada
                if time.time() - last_activity > ACTIVITY_TIMEOUT:
                    logger.warning("⏱️ Timeout de inactividad, limpiando estado")
                    audio_buffer = b""
                    is_speaking = False
                    chunks_received = 0
                    last_activity = time.time()
                continue
            
            data = json.loads(msg)
            
            # Logging selectivo (no spam)
            if data["event"] != "media" or chunks_received % 100 == 0:
                logger.debug(f"📨 Evento: {data['event']}")

            if data["event"] == "connected":
                logger.info("🔗 WebSocket conectado con Twilio")
            
            elif data["event"] == "start":
                stream_sid = data["start"]["streamSid"]
                logger.info(f"🔵 Stream started: {stream_sid}")
                
                # Verificar configuración del stream
                media_format = data["start"].get("mediaFormat", {})
                logger.info(f"📋 Media format: {json.dumps(media_format, indent=2)}")
                
                # Enviar saludo inicial solo una vez
                if not has_greeted:
                    has_greeted = True
                    is_speaking = True
                    
                    try:
                        await send_greeting(ws, stream_sid)
                    except Exception as e:
                        logger.error(f"❌ Error enviando saludo: {e}")
                    finally:
                        is_speaking = False
                        audio_buffer = b""
                        chunks_received = 0
                        logger.info("👂 Sistema listo para escuchar")

            elif data["event"] == "media":
                # CRÍTICO: No procesar audio mientras el bot está hablando
                if is_speaking:
                    continue
                
                audio_b64 = data["media"]["payload"]
                audio_bytes = base64.b64decode(audio_b64)
                
                chunks_received += 1
                
                # Verificar que no sea silencio total
                if audio_bytes == b'\xff' * len(audio_bytes) or audio_bytes == b'\x00' * len(audio_bytes):
                    continue
                
                # Acumular audio
                audio_buffer += audio_bytes
                
                # Log progreso cada 100 chunks
                if chunks_received % 100 == 0:
                    percentage = (len(audio_buffer) / BUFFER_SIZE) * 100
                    seconds_recorded = len(audio_buffer) / 8000
                    logger.info(f"📦 Acumulando... {percentage:.0f}% ({seconds_recorded:.1f}s)")
                
                # Esperar a tener suficiente audio
                if len(audio_buffer) < BUFFER_SIZE:
                    continue
                
                logger.info(f"🎤 Procesando {len(audio_buffer)} bytes ({chunks_received} chunks)...")
                
                # 🔒 BLOQUEAR procesamiento
                is_speaking = True
                processing_succeeded = False
                current_buffer = audio_buffer  # Guardar referencia
                audio_buffer = b""  # Limpiar inmediatamente para siguiente captura
                chunks_received = 0
                
                try:
                    # Verificar que no sea todo silencio
                    unique_bytes = len(set(current_buffer))
                    if unique_bytes < 10:
                        logger.warning(f"⚠️ Buffer rechazado: solo {unique_bytes} bytes únicos (silencio)")
                        continue
                    
                    # Convertir de μ-law 8kHz a PCM 16kHz
                    try:
                        pcm_audio = convert_mulaw_to_pcm_16k(current_buffer)
                    except ValueError as e:
                        logger.warning(f"⚠️ Audio inválido: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"❌ Error convirtiendo audio: {e}")
                        continue
                    
                    logger.info(f"📊 Audio convertido: {len(pcm_audio)} bytes PCM")
                    
                    # IBM STT con timeout
                    result = await recognize_with_timeout(pcm_audio, timeout=STT_TIMEOUT)
                    
                    if not result:
                        logger.warning("⚠️ STT no retornó resultado")
                        continue
                    
                    logger.debug(f"🔍 Resultado STT: {json.dumps(result, indent=2)}")
                    
                    # Extraer texto
                    text = ""
                    confidence = 0
                    if result.get("results") and len(result["results"]) > 0:
                        alternatives = result["results"][0].get("alternatives", [])
                        if alternatives and len(alternatives) > 0:
                            text = alternatives[0].get("transcript", "").strip()
                            confidence = alternatives[0].get("confidence", 0)
                            logger.info(f"📝 Transcripción: '{text}' (confianza: {confidence:.2f})")
                    
                    # Validar transcripción
                    if not text or len(text) < 3 or confidence < 0.6:
                        logger.warning(f"⚠️ Transcripción rechazada: '{text}' (conf: {confidence:.2f})")
                        continue
                    
                    logger.info(f"💬 User: {text}")
                    
                    # Evitar respuestas duplicadas
                    current_time = time.time()
                    if last_response_time > 0 and current_time - last_response_time < DUPLICATE_RESPONSE_THRESHOLD:
                        logger.info("⏭️ Ignorando (acabamos de responder)")
                        continue
                    
                    # Obtener respuesta del agente con timeout
                    reply = await agent_reply_async(text, timeout=AGENT_TIMEOUT)
                    logger.info(f"🤖 Agent: {reply}")
                    
                    # Enviar audio con timeout
                    try:
                        await asyncio.wait_for(
                            send_audio_to_twilio(ws, stream_sid, reply),
                            timeout=TTS_TIMEOUT
                        )
                        processing_succeeded = True
                        last_response_time = time.time()
                        
                    except asyncio.TimeoutError:
                        logger.error("⏱️ Timeout enviando audio a Twilio")
                    except Exception as e:
                        logger.error(f"❌ Error enviando audio: {e}")
                    
                except Exception as e:
                    logger.error(f"❌ Error general en procesamiento: {e}")
                    import traceback
                    traceback.print_exc()
                
                finally:
                    # ✅ SIEMPRE liberar el lock y limpiar estado
                    is_speaking = False
                    logger.info("👂 Listo para escuchar de nuevo")

            elif data["event"] == "stop":
                logger.info("🔴 Stream stopped")
                break

    except WebSocketDisconnect:
        logger.info("❌ Client disconnected")
    except Exception as e:
        logger.error(f"❌ Error fatal en websocket: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Limpiar estado final
        is_speaking = False
        audio_buffer = b""
        logger.info("🧹 Estado limpiado, conexión cerrada")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

