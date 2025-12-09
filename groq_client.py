# groq_client.py
import os
from groq import Groq

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def ask_groq(prompt: str) -> str:
    response = client.chat.completions.create(
        model="mixtral-8x7b-32768",
        messages=[
            {"role": "system", "content": "Eres un agente telefónico profesional y conciso."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
    )
    return response.choices[0].message["content"]
