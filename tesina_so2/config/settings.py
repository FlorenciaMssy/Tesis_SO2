"""
Configuración global del sistema de monitoreo de SO2 volcánico
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Rutas base
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
WIND_DIR = DATA_DIR / "wind"
RESULTS_DIR = DATA_DIR / "results"

# Crear directorios si no existen
for dir_path in [DATA_DIR, IMAGES_DIR, WIND_DIR, RESULTS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# Copernicus Data Space (TROPOMI)
COPERNICUS_USERNAME = os.getenv("COPERNICUS_USERNAME", "")
COPERNICUS_PASSWORD = os.getenv("COPERNICUS_PASSWORD", "")
COPERNICUS_API_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
COPERNICUS_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

# ERA5 (CDS API) - Para datos de viento
CDS_API_URL = "https://cds.climate.copernicus.eu/api/v2"
CDS_API_KEY = os.getenv("CDS_API_KEY", "")

# Base de datos PostgreSQL
DATABASE_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "so2_monitoring"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

# RabbitMQ (Message Bus)
RABBITMQ_CONFIG = {
    "host": os.getenv("RABBITMQ_HOST", "localhost"),
    "port": int(os.getenv("RABBITMQ_PORT", 5672)),
    "user": os.getenv("RABBITMQ_USER", "guest"),
    "password": os.getenv("RABBITMQ_PASSWORD", "guest"),
    "virtual_host": os.getenv("RABBITMQ_VHOST", "/"),
}

# Comandos del Message Bus
CMD_CALCULOS_FINALES = "CMD_CALCULOS_FINALES"
CMD_NUEVA_EXTRACCION = "CMD_NUEVA_EXTRACCION"

# API REST
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))

# Configuración de volcanes predefinidos (lat, lon, nombre)
VOLCANES_PREDEFINIDOS = {
    "etna": {"lat": 37.751, "lon": 14.993, "nombre": "Monte Etna", "pais": "Italia"},
    "villarrica": {"lat": -39.42, "lon": -71.93, "nombre": "Villarrica", "pais": "Chile"},
    "copahue": {"lat": -37.85, "lon": -71.17, "nombre": "Copahue", "pais": "Argentina/Chile"},
    "stromboli": {"lat": 38.789, "lon": 15.213, "nombre": "Stromboli", "pais": "Italia"},
    "kilauea": {"lat": 19.421, "lon": -155.287, "nombre": "Kilauea", "pais": "USA"},
    "popocatepetl": {"lat": 19.023, "lon": -98.622, "nombre": "Popocatépetl", "pais": "México"},
}

# Parámetros de procesamiento
TROPOMI_PRODUCT = "L2__SO2___"  # Producto de SO2 nivel 2
BBOX_MARGIN_KM = 100  # Margen alrededor del volcán para la búsqueda
DEFAULT_ALTITUDE_M = 3000  # Altitud por defecto para extracción de viento (metros)

# Niveles de presión ERA5 (hPa) para extracción de viento
PRESSURE_LEVELS = [1000, 925, 850, 700, 500, 300, 200, 100]
