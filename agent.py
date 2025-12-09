# agent.py
# Simple wrapper to call Groq LLM

from groq_client import ask_groq

def agent_reply(text: str) -> str:
    if not text:
        return "¿Podrías repetirlo por favor?"
    return ask_groq(text)
