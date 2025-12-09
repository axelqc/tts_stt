# twiml.py
def twiml_response(host):
    """
    Genera el TwiML response para Twilio con configuración correcta
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="es-MX">Hola, conectando con el asistente. Por favor espera un momento.</Say>
    <Connect>
        <Stream url="wss://{host}/media-stream" />
    </Connect>
</Response>"""
