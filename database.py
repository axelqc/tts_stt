import os
from dotenv import load_dotenv
import ibm_db
import ibm_db_dbi
from typing import Generator

# Cargar las variables de entorno desde el archivo .env
load_dotenv()

# Variables de conexión
HOST = os.getenv("DB2_HOST")
PORT = os.getenv("DB2_PORT")
DATABASE = os.getenv("DB2_DATABASE")
USER = os.getenv("DB2_USER")
PASSWORD = os.getenv("DB2_PASSWORD")


CONNECTION_STRING_TUPLE = (
    f"DATABASE={DATABASE};"
    f"HOSTNAME={HOST};"
    f"PORT={PORT};"
    f"PROTOCOL=TCPIP;"
    f"UID={USER};"
    f"PWD={PASSWORD};"
    f"Security=SSL;"
)

def connect_to_db2() -> ibm_db.IBM_DBConnection:
    """
    Intenta establecer la conexión ibm_db.
    """
    try:
        full_connection_string = "".join(CONNECTION_STRING_TUPLE)
        
        # pconnect es una conexión persistente. Los campos de UID y PWD se pasan
        # como el segundo y tercer argumento de pconnect, o pueden ir en la cadena.
        # En este caso, ya están en la cadena, por lo que pasamos cadenas vacías
        conn = ibm_db.pconnect(full_connection_string, "", "") 
        return conn

    except Exception as e:
        print(f"Error al conectar con Db2: {e}")
        # Lanza una excepción para que FastAPI pueda atraparla
        raise RuntimeError("No se pudo establecer la conexión a la base de datos Db2.") from e


def get_db_connection() -> Generator[ibm_db_dbi.Connection, None, None]:
    """
    Función de dependencia para FastAPI que maneja el ciclo de vida de la conexión.
    """
    conn_ibm_db = None
    conn_dbi = None
    try:
        # 1. Establece la conexión de bajo nivel ibm_db
        # Nota: La función connect_to_db2 ya no toma argumentos
        conn_ibm_db = connect_to_db2()
        
        # 2. Crea el wrapper ibm_db_dbi necesario para pandas.read_sql
        conn_dbi = ibm_db_dbi.Connection(conn_ibm_db)
        
        # 3. Cede la conexión al endpoint de FastAPI
        yield conn_dbi
        
    finally:
        # 4. Asegura que la conexión se cierre después de la solicitud
        if conn_dbi:
            # Cierra la conexión de alto nivel
            conn_dbi.close()
        elif conn_ibm_db:
            # Cierra la conexión de bajo nivel si la conversión falló
            ibm_db.close(conn_ibm_db)