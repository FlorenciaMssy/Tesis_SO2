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

# Franjas horarias para el cálculo de flujo (horas desde el volcán)
SO2FC_FRANJAS_HORAS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]

# Tolerancia para selección de píxeles en cada franja (±horas)
SO2FC_TOLERANCIA_HORA = 0.1

# Tolerancia para el azimut de la pluma (grados)
SO2FC_TOLERANCIA_AZIMUT = 30

# Distancia de referencia para detectar azimut de pluma (metros)
SO2FC_DISTANCIA_REFERENCIA = 60000  # 60 km

# Conversiones de unidades (del código MATLAB)
# mol/m² a g/m²: dividir por 0.0156 (masa molar / factor)
# g/m² a kg/m²: multiplicar por 0.001
MOLM2_TO_GM2_FACTOR = 1 / 0.0156  # ≈ 64.1
GM2_TO_KGM2_FACTOR = 0.001

# kg/s a ton/día: multiplicar por 86.4
KGS_TO_TD_FACTOR = 86.4  # 86400 s/día ÷ 1000 kg/ton

# Alturas por nivel de presión (metros) - Del código MATLAB
PRESSURE_TO_HEIGHT = {
    1000: 111,
    975: 323,
    950: 540,
    925: 762,
    900: 988,
    850: 1458,
    800: 1948,
    750: 2465,
    700: 3013,
    650: 3589,
    600: 4204,
    550: 4863,
    500: 5576,
    450: 6341,
    400: 7187,
    350: 8113,
    300: 9166,
    250: 10366,
    200: 11787
}

# URL NCEP Reanalysis (para vientos, sin autenticación)
NCEP_BASE_URL = "https://psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis"
