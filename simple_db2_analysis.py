# simple_db2_analysis.py
# Script simple para analizar conversaciones y subirlas a DB2

import os
import json
from groq import Groq
from database import connect_to_db2
import ibm_db
from conversation_logger import logger

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def analyze_and_save_to_db2(call_sid=None):
    """
    Analiza una conversación y la sube a DB2
    """
    # 1. Obtener conversación
    if call_sid:
        conv = logger.get_conversation(call_sid)
    else:
        # Última conversación
        convs = logger.list_conversations(1)
        conv = convs[0] if convs else None
    
    if not conv:
        print("❌ No se encontró conversación")
        return
    
    print(f"📞 Analizando conversación: {conv['call_sid']}")
    
    # 2. Preparar texto
    conversation_text = ""
    for msg in conv['messages']:
        role = "Usuario" if msg['role'] == 'user' else "Asistente"
        conversation_text += f"{role}: {msg['content']}\n"
    
    # 3. Analizar con Groq
    print("🤖 Analizando con IA...")
    
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

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Eres analista de ventas. Respondes solo JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        result = response.choices[0].message.content.strip()
        if result.startswith("```json"):
            result = result.replace("```json", "").replace("```", "").strip()
        
        analysis = json.loads(result)
        print(f"✅ Análisis completado:")
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
        
    except Exception as e:
        print(f"❌ Error analizando: {e}")
        return
    
    # 4. Guardar en DB2
    print("\n💾 Guardando en DB2...")
    
    conn = connect_to_db2()
    
    try:
        # Insertar análisis
        sql = """
            INSERT INTO analisis_conversaciones 
            (call_sid, resumen, sentimiento, interes_cliente, nivel_interes, 
             calificacion_lead, proximos_pasos, propiedades_mencionadas, fecha_analisis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        
        stmt = ibm_db.prepare(conn, sql)
        ibm_db.bind_param(stmt, 1, conv['call_sid'])
        ibm_db.bind_param(stmt, 2, analysis.get('resumen', ''))
        ibm_db.bind_param(stmt, 3, analysis.get('sentimiento', ''))
        ibm_db.bind_param(stmt, 4, analysis.get('interes_cliente', ''))
        ibm_db.bind_param(stmt, 5, analysis.get('nivel_interes', 5))
        ibm_db.bind_param(stmt, 6, analysis.get('calificacion_lead', 'tibio'))
        ibm_db.bind_param(stmt, 7, analysis.get('proximos_pasos', ''))
        ibm_db.bind_param(stmt, 8, analysis.get('propiedades_mencionadas', ''))
        
        ibm_db.execute(stmt)
        ibm_db.close(conn)
        
        print(f"✅ Guardado en DB2!")
        
        if analysis.get('calificacion_lead') == 'caliente':
            print("\n🔥🔥🔥 LEAD CALIENTE! 🔥🔥🔥")
        
    except Exception as e:
        print(f"❌ Error guardando en DB2: {e}")
        ibm_db.close(conn)


def list_recent_conversations():
    """
    Muestra las conversaciones recientes para analizar
    """
    convs = logger.list_conversations(10)
    
    print("\n📞 CONVERSACIONES DISPONIBLES:\n")
    for i, conv in enumerate(convs, 1):
        print(f"{i}. {conv['call_sid']}")
        print(f"   📅 {conv['start_time']}")
        print(f"   💬 {len(conv['messages'])} mensajes")
        print()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "list":
            list_recent_conversations()
        else:
            # Analizar conversación específica
            call_sid = sys.argv[1]
            analyze_and_save_to_db2(call_sid)
    else:
        # Analizar la última
        print("Analizando última conversación...\n")
        analyze_and_save_to_db2()
