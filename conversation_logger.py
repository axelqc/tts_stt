# conversation_logger.py
import json
import os
from datetime import datetime
from typing import List, Dict

class ConversationLogger:
    def __init__(self, storage_path="conversations"):
        self.storage_path = storage_path
        self.current_conversation = None
        
        # Crear directorio si no existe
        if not os.path.exists(storage_path):
            os.makedirs(storage_path)
    
    def start_conversation(self, call_sid: str, phone_number: str = None):
        """Inicia una nueva conversación"""
        self.current_conversation = {
            "call_sid": call_sid,
            "phone_number": phone_number,
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "messages": [],
            "metadata": {
                "total_user_messages": 0,
                "total_assistant_messages": 0,
                "duration_seconds": 0
            }
        }
        print(f"📝 Nueva conversación iniciada: {call_sid}")
    
    def add_message(self, role: str, content: str, confidence: float = None):
        """Agrega un mensaje a la conversación actual"""
        if not self.current_conversation:
            print("⚠️  No hay conversación activa")
            return
        
        message = {
            "role": role,  # 'user' o 'assistant'
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "confidence": confidence
        }
        
        self.current_conversation["messages"].append(message)
        
        if role == "user":
            self.current_conversation["metadata"]["total_user_messages"] += 1
        elif role == "assistant":
            self.current_conversation["metadata"]["total_assistant_messages"] += 1
        
        print(f"💬 Mensaje guardado: {role} - {content[:50]}...")
    
    def end_conversation(self):
        """Finaliza y guarda la conversación"""
        if not self.current_conversation:
            print("⚠️  No hay conversación activa para finalizar")
            return None
        
        self.current_conversation["end_time"] = datetime.now().isoformat()
        
        # Calcular duración
        start = datetime.fromisoformat(self.current_conversation["start_time"])
        end = datetime.fromisoformat(self.current_conversation["end_time"])
        duration = (end - start).total_seconds()
        self.current_conversation["metadata"]["duration_seconds"] = duration
        
        # Guardar en archivo
        filename = f"{self.storage_path}/{self.current_conversation['call_sid']}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(self.current_conversation, f, ensure_ascii=False, indent=2)
        
        print(f"💾 Conversación guardada: {filename}")
        print(f"   📊 {len(self.current_conversation['messages'])} mensajes, {duration:.1f}s")
        
        conversation_data = self.current_conversation
        self.current_conversation = None
        
        return conversation_data
    
    def get_conversation(self, call_sid: str) -> Dict:
        """Recupera una conversación guardada"""
        filename = f"{self.storage_path}/{call_sid}.json"
        
        if not os.path.exists(filename):
            return None
        
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def list_conversations(self, limit: int = 10) -> List[Dict]:
        """Lista las últimas conversaciones"""
        files = []
        for filename in os.listdir(self.storage_path):
            if filename.endswith('.json'):
                filepath = os.path.join(self.storage_path, filename)
                files.append((filepath, os.path.getmtime(filepath)))
        
        # Ordenar por fecha de modificación (más reciente primero)
        files.sort(key=lambda x: x[1], reverse=True)
        
        conversations = []
        for filepath, _ in files[:limit]:
            with open(filepath, 'r', encoding='utf-8') as f:
                conversations.append(json.load(f))
        
        return conversations
    
    def get_conversation_text(self, call_sid: str = None) -> str:
        """Obtiene el texto completo de la conversación para análisis"""
        if call_sid:
            conv = self.get_conversation(call_sid)
        else:
            conv = self.current_conversation
        
        if not conv:
            return ""
        
        text = f"Conversación del {conv['start_time']}\n\n"
        
        for msg in conv["messages"]:
            role = "Usuario" if msg["role"] == "user" else "Asistente"
            confidence = f" (confianza: {msg['confidence']:.2f})" if msg.get("confidence") else ""
            text += f"{role}: {msg['content']}{confidence}\n"
        
        return text


# Instancia global
logger = ConversationLogger()
