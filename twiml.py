# twiml.py
def twiml_response(host):
    """
    Genera el TwiML response para Twilio con configuración correcta
    IMPORTANTE: Especificamos track="inbound_track" para capturar la voz del usuario
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="es-MX">Conectando, por favor espera.</Say>
    <Connect>
        <Stream url="wss://{host}/media-stream" track="inbound_track" />
    </Connect>
</Response>"""
