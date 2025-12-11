# recording_manager.py
"""
Sistema de grabación para Render gratis
Graba localmente y sube a almacenamiento externo
"""

import os
import wave
import base64
import asyncio
import logging
from datetime import datetime
from typing import Optional
import httpx  # pip install httpx

logger = logging.getLogger(__name__)

class CallRecorder:
    def __init__(self, call_sid: str, storage_type: str = "local"):
        """
        storage_type: "local", "cloudinary", "s3", "dropbox"
        """
        self.call_sid = call_sid
        self.storage_type = storage_type
        self.recording_file = None
        self.audio_buffer = []
        self.is_recording = False
        
        # Directorio temporal (se mantiene mientras el contenedor esté activo)
        self.recordings_dir = "/tmp/recordings"
        os.makedirs(self.recordings_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filename = f"{self.call_sid}_{timestamp}.wav"
        self.filepath = os.path.join(self.recordings_dir, self.filename)
        
    def start_recording(self):
        """Inicia la grabación"""
        try:
            self.is_recording = True
            # Configurar WAV: 8kHz, mono, 16-bit PCM
            self.recording_file = wave.open(self.filepath, 'wb')
            self.recording_file.setnchannels(1)  # Mono
            self.recording_file.setsampwidth(2)  # 16-bit
            self.recording_file.setframerate(8000)  # 8kHz
            logger.info(f"📼 Grabación iniciada: {self.filename}")
        except Exception as e:
            logger.error(f"❌ Error iniciando grabación: {e}")
            self.is_recording = False
    
    def add_audio_chunk(self, mulaw_chunk: bytes):
        """Añade un chunk de audio μ-law a la grabación"""
        if not self.is_recording or not self.recording_file:
            return
        
        try:
            # Convertir μ-law a PCM 16-bit
            import audioop
            pcm_chunk = audioop.ulaw2lin(mulaw_chunk, 2)
            self.recording_file.writeframes(pcm_chunk)
        except Exception as e:
            logger.error(f"❌ Error añadiendo chunk: {e}")
    
    def stop_recording(self) -> Optional[str]:
        """Detiene la grabación y retorna la ruta del archivo"""
        if not self.is_recording:
            return None
        
        try:
            self.is_recording = False
            if self.recording_file:
                self.recording_file.close()
                self.recording_file = None
            
            file_size = os.path.getsize(self.filepath) / (1024 * 1024)  # MB
            logger.info(f"✅ Grabación completada: {self.filename} ({file_size:.2f} MB)")
            return self.filepath
            
        except Exception as e:
            logger.error(f"❌ Error deteniendo grabación: {e}")
            return None
    
    async def upload_to_cloudinary(self, filepath: str) -> Optional[str]:
        """
        Sube la grabación a Cloudinary (gratis hasta 25GB)
        Requiere: pip install cloudinary
        """
        try:
            import cloudinary
            import cloudinary.uploader
            
            # Configurar con variables de entorno
            cloudinary.config(
                cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
                api_key=os.getenv("CLOUDINARY_API_KEY"),
                api_secret=os.getenv("CLOUDINARY_API_SECRET")
            )
            
            logger.info(f"☁️ Subiendo a Cloudinary: {filepath}")
            result = cloudinary.uploader.upload(
                filepath,
                resource_type="video",  # Para audio/video
                folder="call-recordings",
                public_id=self.filename.replace(".wav", "")
            )
            
            url = result.get("secure_url")
            logger.info(f"✅ Subido a Cloudinary: {url}")
            
            # Eliminar archivo local después de subir
            os.remove(filepath)
            
            return url
            
        except Exception as e:
            logger.error(f"❌ Error subiendo a Cloudinary: {e}")
            return None
    
    async def upload_to_dropbox(self, filepath: str) -> Optional[str]:
        """
        Sube a Dropbox (2GB gratis)
        Requiere: pip install dropbox
        """
        try:
            import dropbox
            
            dbx = dropbox.Dropbox(os.getenv("DROPBOX_ACCESS_TOKEN"))
            
            logger.info(f"📦 Subiendo a Dropbox: {filepath}")
            
            with open(filepath, 'rb') as f:
                dbx.files_upload(
                    f.read(),
                    f"/call-recordings/{self.filename}",
                    mode=dropbox.files.WriteMode.overwrite
                )
            
            # Crear link compartido
            shared_link = dbx.sharing_create_shared_link_with_settings(
                f"/call-recordings/{self.filename}"
            )
            
            url = shared_link.url.replace("?dl=0", "?dl=1")
            logger.info(f"✅ Subido a Dropbox: {url}")
            
            # Eliminar archivo local
            os.remove(filepath)
            
            return url
            
        except Exception as e:
            logger.error(f"❌ Error subiendo a Dropbox: {e}")
            return None
    
    async def upload_to_github_release(self, filepath: str) -> Optional[str]:
        """
        Sube como asset en un GitHub Release (gratis, ilimitado para públicos)
        Requiere: Personal Access Token con scope 'repo'
        """
        try:
            token = os.getenv("GITHUB_TOKEN")
            repo = os.getenv("GITHUB_REPO")  # formato: "usuario/repo"
            
            if not token or not repo:
                logger.error("❌ Faltan GITHUB_TOKEN o GITHUB_REPO")
                return None
            
            async with httpx.AsyncClient() as client:
                # Crear release si no existe
                release_tag = f"recordings-{datetime.now().strftime('%Y-%m')}"
                
                logger.info(f"🐙 Subiendo a GitHub Release: {repo}")
                
                # Verificar si el release existe
                response = await client.get(
                    f"https://api.github.com/repos/{repo}/releases/tags/{release_tag}",
                    headers={
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github.v3+json"
                    }
                )
                
                if response.status_code == 404:
                    # Crear nuevo release
                    response = await client.post(
                        f"https://api.github.com/repos/{repo}/releases",
                        headers={
                            "Authorization": f"token {token}",
                            "Accept": "application/vnd.github.v3+json"
                        },
                        json={
                            "tag_name": release_tag,
                            "name": f"Call Recordings {datetime.now().strftime('%B %Y')}",
                            "body": "Automated call recordings"
                        }
                    )
                
                release_data = response.json()
                upload_url = release_data["upload_url"].replace("{?name,label}", "")
                
                # Subir archivo
                with open(filepath, 'rb') as f:
                    file_data = f.read()
                
                response = await client.post(
                    f"{upload_url}?name={self.filename}",
                    headers={
                        "Authorization": f"token {token}",
                        "Content-Type": "audio/wav"
                    },
                    content=file_data
                )
                
                asset_data = response.json()
                url = asset_data.get("browser_download_url")
                
                logger.info(f"✅ Subido a GitHub: {url}")
                
                # Eliminar archivo local
                os.remove(filepath)
                
                return url
                
        except Exception as e:
            logger.error(f"❌ Error subiendo a GitHub: {e}")
            return None
    
    async def finalize(self):
        """Finaliza la grabación y sube según configuración"""
        filepath = self.stop_recording()
        
        if not filepath:
            return None
        
        if self.storage_type == "cloudinary":
            return await self.upload_to_cloudinary(filepath)
        elif self.storage_type == "dropbox":
            return await self.upload_to_dropbox(filepath)
        elif self.storage_type == "github":
            return await self.upload_to_github_release(filepath)
        else:
            # Local - solo retornar la ruta
            logger.info(f"📁 Grabación guardada localmente: {filepath}")
            return filepath


# Ejemplo de uso en main.py:
"""
# Al inicio del websocket
recorder = CallRecorder(call_sid="CS123", storage_type="cloudinary")
recorder.start_recording()

# En cada chunk de audio recibido:
if data["event"] == "media":
    audio_bytes = base64.b64decode(data["media"]["payload"])
    recorder.add_audio_chunk(audio_bytes)

# Al finalizar la llamada:
recording_url = await recorder.finalize()
logger.info(f"Grabación disponible en: {recording_url}")
"""
