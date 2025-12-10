# main.py
# FastAPI server integrating Twilio Media Streams + IBM STT/TTS + Groq LLM

import os
import json
import base64
import audioop
import io
import wave
import asyncio
import time
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
    """
    try:
        print(f"🔄 Convirtiendo {len(mulaw_data)} bytes de μ-law...")
        
        # Verificar que no sea todo silencio
        unique_bytes = len(set(mulaw_data))
        print(f"   📊 Bytes únicos en μ-law: {unique_bytes} (debería ser > 10)")
        
        if unique_bytes < 5:
            print(f"   ⚠️  Audio parece ser silencio (muy pocos valores únicos)")
        
        # Decodificar μ-law a PCM linear 16-bit
        pcm_data = audioop.ulaw2lin(mulaw_data, 2)
        print(f"   ✓ Decodificado a PCM: {len(pcm_data)} bytes")
        
        # Calcular RMS antes del resampling
        rms_original = audioop.rms(pcm_data, 2)
        print(f"   📊 Volumen RMS original (8kHz): {rms_original}")
        
        # Resamplear de 8kHz a 16kHz
        pcm_16k, _ = audioop.ratecv(pcm_data, 2, 1, 8000, 16000, None)
        print(f"   ✓ Resampleado a 16kHz: {len(pcm_16k)} bytes")
        
        # Calcular RMS después del resampling
        rms = audioop.rms(pcm_16k, 2)
        print(f"   📊 Volumen RMS final (16kHz): {rms}")
        
        # Amplificar si es necesario
        if rms < 300:
            factor = min(3.0, 900 / max(rms, 1))  # Amplificar hasta factor 3x
            print(f"   🔊 Amplificando audio {factor:.1f}x (RMS bajo: {rms})")
            pcm_16k = audioop.mul(pcm_16k, 2, factor)
            rms_final = audioop.rms(pcm_16k, 2)
            print(f"   ✓ RMS después de amplificar: {rms_final}")
        else:
            print(f"   ✓ RMS suficiente, no se amplifica")
        
        return pcm_16k
    except Exception as e:
        print(f"❌ Error en conversión de audio: {e}")
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
        print(f"Error convirtiendo WAV a μ-law: {e}")
        raise

async def send_audio_to_twilio(ws, stream_sid, text, voice="es-LA_SofiaV3Voice"):
    """
    Convierte texto a audio y lo envía a Twilio
    """
    try:
        print(f"🔊 Generando audio para: '{text}'")
        
        # IBM TTS
        audio_reply = tts.synthesize(
            text=text,
            accept="audio/wav",
            voice=voice
        ).get_result().content

        # Convertir a μ-law para Twilio
        mulaw_audio = convert_wav_to_mulaw_8k(audio_reply)
        
        # Calcular duración aproximada del audio
        duration_seconds = len(mulaw_audio) / 8000  # 8000 bytes por segundo
        print(f"⏱️  Duración estimada del audio: {duration_seconds:.1f} segundos")
        
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
        
        print(f"✅ Audio enviado completamente ({chunks_sent} chunks)")
        
        # Esperar a que termine de reproducirse + 1 segundo extra de buffer
        await asyncio.sleep(duration_seconds + 1.0)
        print("🎧 Audio terminado de reproducir, listo para escuchar")
        
    except Exception as e:
        print(f"❌ Error enviando audio: {e}")

async def send_greeting(ws, stream_sid):
    """
    Envía saludo inicial
    """
    greeting = "Hola, ¿en qué puedo ayudarte?"  # Saludo más corto
    print("🤖 Enviando saludo inicial...")
    await send_audio_to_twilio(ws, stream_sid, greeting)
    print("👂 Saludo completado, ahora escuchando...")

@app.get("/")
async def root():
    return {"status": "server running"}

@app.get("/conversations")
async def list_conversations(limit: int = 10):
    """Lista las últimas conversaciones desde DB2"""
    from database import connect_to_db2
    import ibm_db
    
    conn = connect_to_db2()
    sql = """
        SELECT * FROM analisis_conversaciones 
        ORDER BY fecha_analisis DESC 
        FETCH FIRST ? ROWS ONLY
    """
    stmt = ibm_db.prepare(conn, sql)
    ibm_db.bind_param(stmt, 1, limit)
    ibm_db.execute(stmt)
    
    conversations = []
    row = ibm_db.fetch_assoc(stmt)
    while row:
        conversations.append(dict(row))
        row = ibm_db.fetch_assoc(stmt)
    
    ibm_db.close(conn)
    return {"conversations": conversations, "total": len(conversations)}

@app.get("/conversation/{call_sid}")
async def get_conversation(call_sid: str):
    """Obtiene una conversación específica desde DB2"""
    from database import connect_to_db2
    import ibm_db
    
    conn = connect_to_db2()
    sql = "SELECT * FROM analisis_conversaciones WHERE call_sid = ?"
    stmt = ibm_db.prepare(conn, sql)
    ibm_db.bind_param(stmt, 1, call_sid)
    ibm_db.execute(stmt)
    
    row = ibm_db.fetch_assoc(stmt)
    ibm_db.close(conn)
    
    if not row:
        return {"error": "Conversación no encontrada"}
    return dict(row)

@app.get("/conversation/{call_sid}/analyze")
async def analyze_conv(call_sid: str):
    """Obtiene análisis desde DB2"""
    from database import connect_to_db2
    import ibm_db
    
    conn = connect_to_db2()
    sql = "SELECT * FROM analisis_conversaciones WHERE call_sid = ?"
    stmt = ibm_db.prepare(conn, sql)
    ibm_db.bind_param(stmt, 1, call_sid)
    ibm_db.execute(stmt)
    
    row = ibm_db.fetch_assoc(stmt)
    ibm_db.close(conn)
    
    if not row:
        return {"error": "Análisis no encontrado"}
    return dict(row)

@app.get("/hot-leads")
async def get_hot_leads(limit: int = 10):
    """Obtiene leads calientes desde DB2"""
    from database import connect_to_db2
    import ibm_db
    
    conn = connect_to_db2()
    sql = """
        SELECT * FROM leads_calientes 
        FETCH FIRST ? ROWS ONLY
    """
    stmt = ibm_db.prepare(conn, sql)
    ibm_db.bind_param(stmt, 1, limit)
    ibm_db.execute(stmt)
    
    leads = []
    row = ibm_db.fetch_assoc(stmt)
    while row:
        leads.append(dict(row))
        row = ibm_db.fetch_assoc(stmt)
    
    ibm_db.close(conn)
    return {"leads": leads, "total": len(leads)}

@app.get("/conversation/{call_sid}/follow-up")
async def get_follow_up(call_sid: str):
    """Genera script de seguimiento basado en el análisis"""
    from database import connect_to_db2
    import ibm_db
    from groq import Groq
    
    # Obtener análisis
    conn = connect_to_db2()
    sql = "SELECT * FROM analisis_conversaciones WHERE call_sid = ?"
    stmt = ibm_db.prepare(conn, sql)
    ibm_db.bind_param(stmt, 1, call_sid)
    ibm_db.execute(stmt)
    
    row = ibm_db.fetch_assoc(stmt)
    ibm_db.close(conn)
    
    if not row:
        return {"error": "Análisis no encontrado"}
    
    # Generar script
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    prompt = f"""Genera un mensaje de seguimiento profesional basado en este análisis:

Resumen: {row['RESUMEN']}
Interés del cliente: {row['INTERES_CLIENTE']}
Calificación: {row['CALIFICACION_LEAD']}
Próximos pasos: {row['PROXIMOS_PASOS']}

Genera un mensaje corto (3-4 líneas) en español para WhatsApp o email."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "Eres experto en seguimiento de ventas inmobiliarias."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        max_tokens=200
    )
    
    script = response.choices[0].message.content.strip()
    return {"script": script}

@app.get("/stats")
async def get_stats(days: int = 7):
    """Obtiene estadísticas desde DB2"""
    from database import connect_to_db2
    import ibm_db
    
    conn = connect_to_db2()
    sql = """
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN calificacion_lead = 'caliente' THEN 1 END) as calientes,
            COUNT(CASE WHEN calificacion_lead = 'tibio' THEN 1 END) as tibios,
            COUNT(CASE WHEN calificacion_lead = 'frio' THEN 1 END) as frios,
            AVG(nivel_interes) as interes_promedio
        FROM analisis_conversaciones
        WHERE fecha_analisis >= CURRENT_DATE - ? DAYS
    """
    stmt = ibm_db.prepare(conn, sql)
    ibm_db.bind_param(stmt, 1, days)
    ibm_db.execute(stmt)
    
    row = ibm_db.fetch_assoc(stmt)
    ibm_db.close(conn)
    
    if row:
        return dict(row)
    return {"error": "No hay datos"}

@app.post("/incoming-call")
async def incoming_call(request: Request):
    host = request.url.hostname
    xml = twiml_response(host)
    return HTMLResponse(content=xml, media_type="application/xml")

async def process_and_save_conversation(conversation):
    """
    Función auxiliar para procesar y guardar la conversación
    """
    if not conversation["messages"]:
        print("⚠️  No hay mensajes para analizar")
        return
    
    print(f"\n✅ Conversación capturada: {len(conversation['messages'])} mensajes")
    
    # Debug: mostrar mensajes
    print("\n💬 MENSAJES CAPTURADOS:")
    for i, msg in enumerate(conversation["messages"], 1):
        print(f"   {i}. {msg['role']}: {msg['content'][:50]}...")
    
    try:
        print("\n🤖 Iniciando análisis con Groq...")
        
        # Preparar texto
        conversation_text = ""
        for msg in conversation["messages"]:
            role = "Usuario" if msg['role'] == 'user' else "Asistente"
            conversation_text += f"{role}: {msg['content']}\n"
        
        print(f"📝 Texto preparado ({len(conversation_text)} caracteres)")
        
        # Analizar con Groq
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        
        print("🔄 Llamando a Groq API...")
        
        prompt = f"""Analiza esta conversación inmobiliaria y responde SOLO en JSON:

{conversation_text}

Formato JSON:
{{
  "resumen": "resumen breve en 1-2 oraciones",
  "sentimiento": "positivo/neutral/negativo",
  "interes_cliente": "qué busca el cliente",
  "nivel_interes": 5,
  "calificacion_lead": "caliente/tibio/frio",
  "proximos_pasos": "acciones recomendadas",
  "propiedades_mencionadas": "propiedades discutidas"
}}"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Eres analista de ventas. Respondes solo JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        print("✅ Respuesta de Groq recibida")
        
        result = response.choices[0].message.content.strip()
        print(f"📄 Resultado raw: {result[:200]}...")
        
        if result.startswith("```json"):
            result = result.replace("```json", "").replace("```", "").strip()
        
        analysis = json.loads(result)
        print(f"✅ JSON parseado correctamente")
        print(f"📊 Análisis: {analysis.get('calificacion_lead', 'N/A')} - Nivel {analysis.get('nivel_interes', 0)}/10")
        
        # Guardar en DB2
        print("\n💾 Conectando a DB2...")
        from database import connect_to_db2
        import ibm_db
        
        conn = connect_to_db2()
        print("✅ Conexión a DB2 establecida")
        
        sql = """
            INSERT INTO analisis_conversaciones 
            (call_sid, resumen, sentimiento, interes_cliente, nivel_interes, 
             calificacion_lead, proximos_pasos, propiedades_mencionadas, fecha_analisis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        
        print("🔄 Preparando statement SQL...")
        stmt = ibm_db.prepare(conn, sql)
        
        print("🔄 Binding parameters...")
        ibm_db.bind_param(stmt, 1, conversation["call_sid"])
        ibm_db.bind_param(stmt, 2, analysis.get('resumen', ''))
        ibm_db.bind_param(stmt, 3, analysis.get('sentimiento', ''))
        ibm_db.bind_param(stmt, 4, analysis.get('interes_cliente', ''))
        ibm_db.bind_param(stmt, 5, analysis.get('nivel_interes', 5))
        ibm_db.bind_param(stmt, 6, analysis.get('calificacion_lead', 'tibio'))
        ibm_db.bind_param(stmt, 7, analysis.get('proximos_pasos', ''))
        ibm_db.bind_param(stmt, 8, analysis.get('propiedades_mencionadas', ''))
        
        print("🔄 Ejecutando INSERT...")
        ibm_db.execute(stmt)
        
        print("🔄 Cerrando conexión...")
        ibm_db.close(conn)
        
        print(f"✅ ¡Guardado exitosamente en DB2!")
        
        if analysis.get('calificacion_lead') == 'caliente':
            print("\n🔥🔥🔥 LEAD CALIENTE! 🔥🔥🔥")
            print(f"   📞 Call SID: {conversation['call_sid']}")
            print(f"   💡 Interés: {analysis.get('interes_cliente', 'N/A')}")
            
    except json.JSONDecodeError as e:
        print(f"❌ Error parseando JSON: {e}")
        print(f"   Contenido recibido: {result}")
    except Exception as e:
        print(f"❌ Error en análisis: {e}")
        print(f"   Tipo de error: {type(e).__name__}")
        import traceback
        print("   Traceback completo:")
        traceback.print_exc()


@app.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    print("✅ Client connected.")

    stream_sid = None
    audio_buffer = b""
    BUFFER_SIZE = 40000  # 5 segundos a 8kHz μ-law - más tiempo para hablar
    is_speaking = False
    chunks_received = 0
    has_greeted = False
    last_response_time = 0
    
    # Almacenar conversación en memoria
    conversation = {
        "call_sid": None,
        "messages": []
    }  # Para evitar respuestas repetidas

    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            
            #print(f"📨 Evento recibido: {data['event']}")

            if data["event"] == "connected":
                print("🔗 WebSocket conectado con Twilio")
            
            elif data["event"] == "start":
                stream_sid = data["start"]["streamSid"]
                conversation["call_sid"] = stream_sid
                print(f"🔵 Stream started: {stream_sid}")
                
                # Verificar configuración del stream
                media_format = data["start"].get("mediaFormat", {})
                print(f"📋 Media format: {json.dumps(media_format, indent=2)}")
                print(f"📋 Tracks: {data['start'].get('tracks', 'N/A')}")
                
                # Enviar saludo inicial solo una vez
                if not has_greeted:
                    has_greeted = True
                    is_speaking = True  # Bloquear escucha durante saludo
                    await send_greeting(ws, stream_sid)
                    is_speaking = False  # Ahora sí, listo para escuchar
                    audio_buffer = b""  # Limpiar buffer
                    chunks_received = 0

            elif data["event"] == "media":
                # CRÍTICO: No procesar audio mientras el bot está hablando
                if is_speaking:
                    # Descartar este chunk completamente
                    continue
                    
                audio_b64 = data["media"]["payload"]
                audio_bytes = base64.b64decode(audio_b64)
                
                chunks_received += 1
                
                # Debug: mostrar primeros bytes
                if chunks_received == 1:
                    print(f"🎵 Primer chunk recibido: {len(audio_bytes)} bytes")
                    print(f"   Primeros 10 bytes (hex): {audio_bytes[:10].hex()}")
                
                # Verificar que no sea silencio total (todos ceros)
                if audio_bytes == b'\xff' * len(audio_bytes) or audio_bytes == b'\x00' * len(audio_bytes):
                    continue
                
                # Acumular audio SOLO si no está hablando el bot
                audio_buffer += audio_bytes
                
                if chunks_received % 50 == 0:  # Log cada 50 chunks
                    print(f"📦 Acumulados {chunks_received} chunks, buffer: {len(audio_buffer)} bytes")
                
                # Procesar cuando tengamos suficiente audio (2 segundos)
                if len(audio_buffer) < BUFFER_SIZE:
                    if chunks_received % 100 == 0 and chunks_received > 0:
                        percentage = (len(audio_buffer) / BUFFER_SIZE) * 100
                        seconds_recorded = len(audio_buffer) / 8000
                        print(f"📦 Acumulando... {percentage:.0f}% ({seconds_recorded:.1f}s de 5s)")
                    continue
                
                print(f"🎤 Procesando {len(audio_buffer)} bytes de audio ({chunks_received} chunks)...")
                chunks_received = 0  # Reset contador
                
                # Verificar que no sea todo silencio ANTES de convertir
                unique_bytes = len(set(audio_buffer))
                if unique_bytes < 10:
                    print(f"⚠️  Buffer rechazado: solo {unique_bytes} bytes únicos (es silencio)")
                    audio_buffer = b""
                    is_speaking = False
                    continue
                
                # Marcar que el bot va a hablar
                is_speaking = True
                
                try:
                    # Convertir de μ-law 8kHz a PCM 16kHz
                    pcm_audio = convert_mulaw_to_pcm_16k(audio_buffer)
                    
                    print(f"📊 Audio convertido: {len(pcm_audio)} bytes PCM")
                    
                    # IBM STT - Probar modelos en español
                    spanish_models = [
                        "es-MX_BroadbandModel",  # Español México (mejor para Latinoamérica)
                        "es-ES_BroadbandModel",   # Español España
                        "es-LA_BroadbandModel",   # Español Latinoamérica
                    ]
                    
                    result = None
                    for model in spanish_models:
                        try:
                            print(f"🔄 Intentando con modelo {model}...")
                            result = stt.recognize(
                                audio=pcm_audio,
                                content_type="audio/l16; rate=16000",
                                model=model
                            ).get_result()
                            print(f"✅ Modelo {model} funcionó!")
                            break
                        except Exception as model_error:
                            print(f"⚠️  Modelo {model} no disponible: {model_error}")
                            continue
                    
                    if not result:
                        print("❌ Ningún modelo en español disponible, usando default")
                        result = stt.recognize(
                            audio=pcm_audio,
                            content_type="audio/l16; rate=16000"
                        ).get_result()
                    
                    print(f"🔍 Resultado STT completo: {json.dumps(result, indent=2)}")

                    text = ""
                    confidence = 0
                    if result.get("results") and len(result["results"]) > 0:
                        alternatives = result["results"][0].get("alternatives", [])
                        if alternatives and len(alternatives) > 0:
                            text = alternatives[0].get("transcript", "").strip()
                            confidence = alternatives[0].get("confidence", 0)
                            print(f"📝 Transcripción: '{text}' (confianza: {confidence:.2f})")
                    
                    # Limpiar buffer después de procesar
                    audio_buffer = b""
                    
                    # Filtrar resultados con baja confianza o muy cortos
                    if not text or len(text) < 3 or confidence < 0.6:
                        print(f"⚠️  Transcripción rechazada (muy corta o baja confianza)")
                        is_speaking = False
                        continue
                        
                    print(f"💬 User: {text}")
                    
                    # Guardar mensaje del usuario en memoria
                    conversation["messages"].append({
                        "role": "user",
                        "content": text,
                        "confidence": confidence
                    })
                    
                    # Evitar procesar lo mismo dos veces
                    current_time = time.time()
                    if last_response_time > 0 and current_time - last_response_time < 5:
                        print("⏭️  Ignorando (acabamos de responder hace menos de 5 seg)")
                        is_speaking = False
                        continue

                    # Marcar que vamos a responder
                    is_speaking = True
                    
                    # IMPORTANTE: Limpiar cualquier audio acumulado durante el procesamiento
                    audio_buffer = b""
                    chunks_received = 0

                    # Agent (Groq)
                    reply = agent_reply(text)
                    print(f"🤖 Agent: {reply}")
                    
                    # Guardar respuesta del asistente en memoria
                    conversation["messages"].append({
                        "role": "assistant",
                        "content": reply
                    })

                    # Enviar respuesta de audio (esto ya incluye el delay)
                    await send_audio_to_twilio(ws, stream_sid, reply)
                    
                    # Actualizar timestamp de última respuesta
                    last_response_time = time.time()
                    
                    # Ahora sí, permitir escuchar de nuevo
                    is_speaking = False
                    print("👂 Ahora escuchando al usuario...")
                    
                except Exception as e:
                    print(f"❌ Error procesando audio: {e}")
                    audio_buffer = b""
                    is_speaking = False  # Permitir seguir escuchando
                    continue

            elif data["event"] == "stop":
                print("🔴 Stream stopped (evento STOP recibido)")
                print(f"📊 Mensajes capturados: {len(conversation['messages'])}")
                print(f"📞 Call SID: {conversation['call_sid']}")
                
                # Procesar conversación
                await process_and_save_conversation(conversation)
                break

    except WebSocketDisconnect:
        print("❌ Client disconnected (WebSocket cerrado)")
        print(f"📊 Mensajes al desconectar: {len(conversation['messages'])}")
        
        # Procesar conversación aunque no se reciba el evento "stop"
        if conversation["messages"]:
            print("💾 Procesando conversación antes de cerrar...")
            await process_and_save_conversation(conversation)
        else:
            print("⚠️  No hay mensajes para guardar")
            
    except Exception as e:
        print(f"❌ Error en websocket: {e}")
        print(f"📊 Mensajes al momento del error: {len(conversation['messages'])}")
        
        # Intentar guardar lo que se tenga
        if conversation["messages"]:
            print("💾 Intentando guardar conversación parcial...")
            try:
                await process_and_save_conversation(conversation)
            except Exception as e2:
                print(f"❌ No se pudo guardar: {e2}")
