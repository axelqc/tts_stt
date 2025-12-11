# twiml.py
def twiml_response(host):

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="es-MX">Conectando, por favor espera.</Say>
    <Record 
        recordingStatusCallback="https://{host}/recording-status"
        recordingStatusCallbackMethod="POST"
        maxLength="3600"
        playBeep="false"
        transcribe="false"
    />
    <Connect>
        <Stream url="wss://{host}/media-stream" track="inbound_track" />
    </Connect>
</Response>"""
