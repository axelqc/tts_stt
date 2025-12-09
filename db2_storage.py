# db2_storage.py
import json
from datetime import datetime
from typing import Dict, List, Optional
from database import connect_to_db2
import ibm_db


class DB2ConversationStorage:
    """
    Maneja el almacenamiento de conversaciones y análisis en DB2
    """
    
    def __init__(self):
        self.conn = None
    
    def _get_connection(self):
        """Obtiene o reutiliza la conexión a DB2"""
        if not self.conn:
            self.conn = connect_to_db2()
        return self.conn
    
    def save_conversation(self, conversation_data: Dict) -> int:
        """
        Guarda una conversación completa en DB2
        Retorna el conversation_id generado
        """
        conn = self._get_connection()
        
        try:
            # 1. Insertar conversación principal
            sql_conversation = """
                INSERT INTO conversaciones 
                (call_sid, phone_number, start_time, end_time, duration_seconds, 
                 total_user_messages, total_assistant_messages)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            
            start_time = datetime.fromisoformat(conversation_data['start_time'])
            end_time = datetime.fromisoformat(conversation_data['end_time']) if conversation_data.get('end_time') else None
            
            stmt = ibm_db.prepare(conn, sql_conversation)
            ibm_db.bind_param(stmt, 1, conversation_data['call_sid'])
            ibm_db.bind_param(stmt, 2, conversation_data.get('phone_number', 'unknown'))
            ibm_db.bind_param(stmt, 3, start_time)
            ibm_db.bind_param(stmt, 4, end_time)
            ibm_db.bind_param(stmt, 5, conversation_data['metadata']['duration_seconds'])
            ibm_db.bind_param(stmt, 6, conversation_data['metadata']['total_user_messages'])
            ibm_db.bind_param(stmt, 7, conversation_data['metadata']['total_assistant_messages'])
            
            ibm_db.execute(stmt)
            
            # 2. Obtener el ID generado
            sql_get_id = "SELECT id FROM conversaciones WHERE call_sid = ?"
            stmt_id = ibm_db.prepare(conn, sql_get_id)
            ibm_db.bind_param(stmt_id, 1, conversation_data['call_sid'])
            ibm_db.execute(stmt_id)
            
            row = ibm_db.fetch_assoc(stmt_id)
            conversation_id = row['ID']
            
            # 3. Insertar mensajes
            sql_message = """
                INSERT INTO mensajes 
                (conversation_id, role, content, confidence, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """
            
            for msg in conversation_data['messages']:
                stmt_msg = ibm_db.prepare(conn, sql_message)
                msg_timestamp = datetime.fromisoformat(msg['timestamp'])
                
                ibm_db.bind_param(stmt_msg, 1, conversation_id)
                ibm_db.bind_param(stmt_msg, 2, msg['role'])
                ibm_db.bind_param(stmt_msg, 3, msg['content'])
                ibm_db.bind_param(stmt_msg, 4, msg.get('confidence'))
                ibm_db.bind_param(stmt_msg, 5, msg_timestamp)
                
                ibm_db.execute(stmt_msg)
            
            print(f"✅ Conversación guardada en DB2: ID={conversation_id}")
            return conversation_id
            
        except Exception as e:
            print(f"❌ Error guardando conversación en DB2: {e}")
            raise
    
    def save_analysis(self, conversation_id: int, analysis: Dict) -> bool:
        """
        Guarda el análisis de una conversación
        """
        conn = self._get_connection()
        
        try:
            sql_analysis = """
                INSERT INTO analisis_conversaciones 
                (conversation_id, resumen, sentimiento, sentimiento_detalle, 
                 interes_cliente, nivel_interes, calificacion_lead, 
                 proximos_pasos, propiedades_mencionadas, puntos_clave)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            
            # Convertir listas/objetos a JSON strings
            puntos_clave = json.dumps(analysis.get('puntos_clave', []), ensure_ascii=False)
            proximos_pasos = json.dumps(analysis.get('proximos_pasos', []), ensure_ascii=False)
            propiedades = json.dumps(analysis.get('propiedades_mencionadas', []), ensure_ascii=False)
            
            stmt = ibm_db.prepare(conn, sql_analysis)
            ibm_db.bind_param(stmt, 1, conversation_id)
            ibm_db.bind_param(stmt, 2, analysis.get('resumen', ''))
            ibm_db.bind_param(stmt, 3, analysis.get('sentimiento', '').split('-')[0].strip() if '-' in str(analysis.get('sentimiento', '')) else analysis.get('sentimiento', ''))
            ibm_db.bind_param(stmt, 4, analysis.get('sentimiento', ''))
            ibm_db.bind_param(stmt, 5, analysis.get('interes_cliente', ''))
            ibm_db.bind_param(stmt, 6, analysis.get('nivel_interes', 0))
            ibm_db.bind_param(stmt, 7, analysis.get('calificacion_lead', 'tibio'))
            ibm_db.bind_param(stmt, 8, proximos_pasos)
            ibm_db.bind_param(stmt, 9, propiedades)
            ibm_db.bind_param(stmt, 10, puntos_clave)
            
            ibm_db.execute(stmt)
            
            print(f"✅ Análisis guardado en DB2 para conversation_id={conversation_id}")
            return True
            
        except Exception as e:
            print(f"❌ Error guardando análisis en DB2: {e}")
            raise
    
    def save_follow_up_script(self, conversation_id: int, script: str) -> bool:
        """
        Guarda el script de seguimiento generado
        """
        conn = self._get_connection()
        
        try:
            sql_script = """
                INSERT INTO scripts_seguimiento 
                (conversation_id, script_content)
                VALUES (?, ?)
            """
            
            stmt = ibm_db.prepare(conn, sql_script)
            ibm_db.bind_param(stmt, 1, conversation_id)
            ibm_db.bind_param(stmt, 2, script)
            
            ibm_db.execute(stmt)
            
            print(f"✅ Script de seguimiento guardado para conversation_id={conversation_id}")
            return True
            
        except Exception as e:
            print(f"❌ Error guardando script en DB2: {e}")
            raise
    
    def get_hot_leads(self, limit: int = 10) -> List[Dict]:
        """
        Obtiene los leads calientes más recientes
        """
        conn = self._get_connection()
        
        try:
            sql = "SELECT * FROM leads_calientes FETCH FIRST ? ROWS ONLY"
            stmt = ibm_db.prepare(conn, sql)
            ibm_db.bind_param(stmt, 1, limit)
            ibm_db.execute(stmt)
            
            leads = []
            row = ibm_db.fetch_assoc(stmt)
            while row:
                leads.append(dict(row))
                row = ibm_db.fetch_assoc(stmt)
            
            return leads
            
        except Exception as e:
            print(f"❌ Error obteniendo leads calientes: {e}")
            return []
    
    def get_statistics(self, days: int = 7) -> List[Dict]:
        """
        Obtiene estadísticas de conversaciones
        """
        conn = self._get_connection()
        
        try:
            sql = """
                SELECT * FROM estadisticas_conversaciones 
                WHERE fecha >= CURRENT_DATE - ? DAYS
                ORDER BY fecha DESC
            """
            stmt = ibm_db.prepare(conn, sql)
            ibm_db.bind_param(stmt, 1, days)
            ibm_db.execute(stmt)
            
            stats = []
            row = ibm_db.fetch_assoc(stmt)
            while row:
                stats.append(dict(row))
                row = ibm_db.fetch_assoc(stmt)
            
            return stats
            
        except Exception as e:
            print(f"❌ Error obteniendo estadísticas: {e}")
            return []
    
    def get_conversation_by_call_sid(self, call_sid: str) -> Optional[Dict]:
        """
        Recupera una conversación completa por call_sid
        """
        conn = self._get_connection()
        
        try:
            # Obtener conversación
            sql_conv = "SELECT * FROM conversaciones WHERE call_sid = ?"
            stmt_conv = ibm_db.prepare(conn, sql_conv)
            ibm_db.bind_param(stmt_conv, 1, call_sid)
            ibm_db.execute(stmt_conv)
            
            conv_row = ibm_db.fetch_assoc(stmt_conv)
            if not conv_row:
                return None
            
            conversation = dict(conv_row)
            conv_id = conversation['ID']
            
            # Obtener mensajes
            sql_msgs = "SELECT * FROM mensajes WHERE conversation_id = ? ORDER BY timestamp"
            stmt_msgs = ibm_db.prepare(conn, sql_msgs)
            ibm_db.bind_param(stmt_msgs, 1, conv_id)
            ibm_db.execute(stmt_msgs)
            
            messages = []
            msg_row = ibm_db.fetch_assoc(stmt_msgs)
            while msg_row:
                messages.append(dict(msg_row))
                msg_row = ibm_db.fetch_assoc(stmt_msgs)
            
            conversation['messages'] = messages
            
            # Obtener análisis si existe
            sql_analysis = "SELECT * FROM analisis_conversaciones WHERE conversation_id = ?"
            stmt_analysis = ibm_db.prepare(conn, sql_analysis)
            ibm_db.bind_param(stmt_analysis, 1, conv_id)
            ibm_db.execute(stmt_analysis)
            
            analysis_row = ibm_db.fetch_assoc(stmt_analysis)
            if analysis_row:
                conversation['analysis'] = dict(analysis_row)
            
            return conversation
            
        except Exception as e:
            print(f"❌ Error recuperando conversación: {e}")
            return None
    
    def close(self):
        """Cierra la conexión"""
        if self.conn:
            ibm_db.close(self.conn)
            self.conn = None


# Instancia global
db2_storage = DB2ConversationStorage()
