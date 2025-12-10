# groq_client.py
import os
from groq import Groq
from properties import PROPERTIES, get_property_description, search_properties, get_all_properties_summary

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Historial de conversación
conversation_history = []
last_mentioned_property = None  # Rastrear última propiedad mencionada

def ask_groq(prompt: str) -> str:
    global last_mentioned_property
    
    # Buscar si el usuario menciona alguna propiedad
    matching_properties = search_properties(prompt)
    
    # Construir contexto con información de propiedades
    context = """Eres un agente inmobiliario profesional y amigable que atiende llamadas telefónicas.

Propiedades disponibles:
"""
    
    for prop in PROPERTIES:
        context += f"\n- {prop['nombre']} en {prop['ubicacion']}: {prop['descripcion']} "
        context += f"Precio: ${prop['precio']:,.0f} MXN, {prop['cuartos']} recámaras, {prop['banos']} baños, {prop['area']} m²"
    
    context += """

INSTRUCCIONES:
- Responde de manera concisa y clara (máximo 2-3 oraciones)
- Si te preguntan por propiedades, menciona las que tenemos disponibles
- Si detectas interés en alguna propiedad específica, pregunta si tienen dudas sobre ella
- Ofrece agendar una visita o proporcionar más información
- Sé natural y conversacional
"""
    
    # Agregar contexto si se encontraron propiedades relacionadas
    if matching_properties:
        last_mentioned_property = matching_properties[0]
        context += f"\nNOTA: El usuario parece interesado en {matching_properties[0]['nombre']}."
    
    # Agregar al historial
    conversation_history.append({
        "role": "user",
        "content": prompt
    })
    
    # Crear mensajes
    messages = [
        {"role": "system", "content": context},
        *conversation_history[-6:]  # Últimos 3 intercambios
    ]
    
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        temperature=0.7,
        max_tokens=150
    )
    
    reply = response.choices[0].message.content
    
    # Agregar respuesta al historial
    conversation_history.append({
        "role": "assistant",
        "content": reply
    })
    
    return reply
