# twiml.py
def twiml_response(host):
    """
    Genera el TwiML response para Twilio con configuración correcta
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/media-stream">
            <Parameter name="track" value="inbound_track" />
        </Stream>
    </Connect>
    <Say language="es-MX">Conectando con el asistente</Say>
</Response>"""
