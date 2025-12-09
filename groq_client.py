# groq_client.py
import os
from groq import Groq

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def ask_groq(prompt: str) -> str:
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",  # ✅ Modelo actualizado
        messages=[
            {"role": "system", "content": "Eres un agente telefónico profesional y conciso."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=150  # Respuestas cortas para teléfono
    )
    return response.choices[0].message.content  # ✅ Cambiado de ["content"] a .content
