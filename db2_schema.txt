-- create_tables.sql
-- Tablas para almacenar conversaciones y análisis en DB2

-- Tabla principal de conversaciones
CREATE TABLE conversaciones (
    id INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1),
    call_sid VARCHAR(100) NOT NULL UNIQUE,
    phone_number VARCHAR(20),
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    duration_seconds DECIMAL(10,2),
    total_user_messages INTEGER DEFAULT 0,
    total_assistant_messages INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);

-- Tabla de mensajes individuales
CREATE TABLE mensajes (
    id INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1),
    conversation_id INTEGER NOT NULL,
    role VARCHAR(20) NOT NULL, -- 'user' o 'assistant'
    content CLOB NOT NULL,
    confidence DECIMAL(5,4),
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    FOREIGN KEY (conversation_id) REFERENCES conversaciones(id) ON DELETE CASCADE
);

-- Tabla de análisis de conversaciones
CREATE TABLE analisis_conversaciones (
    id INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1),
    conversation_id INTEGER NOT NULL,
    resumen CLOB,
    sentimiento VARCHAR(50),
    sentimiento_detalle VARCHAR(500),
    interes_cliente CLOB,
    nivel_interes INTEGER, -- 1-10
    calificacion_lead VARCHAR(20), -- 'caliente', 'tibio', 'frio'
    proximos_pasos CLOB,
    propiedades_mencionadas VARCHAR(500),
    puntos_clave CLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    FOREIGN KEY (conversation_id) REFERENCES conversaciones(id) ON DELETE CASCADE
);

-- Tabla de scripts de seguimiento generados
CREATE TABLE scripts_seguimiento (
    id INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1),
    conversation_id INTEGER NOT NULL,
    script_content CLOB NOT NULL,
    enviado SMALLINT DEFAULT 0, -- 0 = no enviado, 1 = enviado
    fecha_envio TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    FOREIGN KEY (conversation_id) REFERENCES conversaciones(id) ON DELETE CASCADE
);

-- Índices para mejorar performance
CREATE INDEX idx_call_sid ON conversaciones(call_sid);
CREATE INDEX idx_conversation_messages ON mensajes(conversation_id);
CREATE INDEX idx_conversation_analysis ON analisis_conversaciones(conversation_id);
CREATE INDEX idx_calificacion_lead ON analisis_conversaciones(calificacion_lead);
CREATE INDEX idx_nivel_interes ON analisis_conversaciones(nivel_interes);
CREATE INDEX idx_start_time ON conversaciones(start_time);

-- Vista para consultas rápidas de leads calientes
CREATE VIEW leads_calientes AS
SELECT 
    c.call_sid,
    c.phone_number,
    c.start_time,
    c.duration_seconds,
    a.resumen,
    a.sentimiento,
    a.nivel_interes,
    a.calificacion_lead,
    a.interes_cliente,
    a.proximos_pasos
FROM conversaciones c
INNER JOIN analisis_conversaciones a ON c.id = a.conversation_id
WHERE a.calificacion_lead = 'caliente'
ORDER BY c.start_time DESC;

-- Vista para dashboard de estadísticas
CREATE VIEW estadisticas_conversaciones AS
SELECT 
    DATE(c.start_time) as fecha,
    COUNT(*) as total_conversaciones,
    AVG(c.duration_seconds) as duracion_promedio,
    SUM(c.total_user_messages + c.total_assistant_messages) as total_mensajes,
    COUNT(CASE WHEN a.calificacion_lead = 'caliente' THEN 1 END) as leads_calientes,
    COUNT(CASE WHEN a.calificacion_lead = 'tibio' THEN 1 END) as leads_tibios,
    COUNT(CASE WHEN a.calificacion_lead = 'frio' THEN 1 END) as leads_frios,
    AVG(a.nivel_interes) as interes_promedio
FROM conversaciones c
LEFT JOIN analisis_conversaciones a ON c.id = a.conversation_id
GROUP BY DATE(c.start_time)
ORDER BY fecha DESC;
