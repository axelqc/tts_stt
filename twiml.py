# twiml.py
def twiml_response(host: str):
    return f"""
<Response>
  <Connect>
    <Stream url="wss://{host}/media-stream" />
  </Connect>
</Response>
"""
