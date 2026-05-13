# Sistema de Monitoreo de Flujo de SO2 Volcánico

Sistema para el procesamiento y generación de series temporales de flujo de dióxido de azufre (SO2) en volcanes, utilizando imágenes del satélite TROPOMI (Sentinel-5P) y datos de viento de NCEP Reanalysis.

## 📋 Descripción

Este software permite:
- **Descargar** imágenes TROPOMI de SO2 desde Copernicus Data Space
- **Obtener** datos de viento desde NCEP Reanalysis (NOAA)
- **Calcular** el flujo de SO2 volcánico usando el método de 6 franjas horarias
- **Visualizar** series temporales y estadísticas de emisiones
- **Exportar** datos para análisis posterior

## 🏗️ Arquitectura
```
┌─────────────────────────────────────────────────────────────────────┐
│                         ORÍGENES DE DATOS                          │
│  ┌─────────────┐                          ┌─────────────┐          │
│  │  TROPOMI    │                          │    NCEP     │          │
│  │ (Copernicus)│                          │   (NOAA)    │          │
│  └──────┬──────┘                          └──────┬──────┘          │
└─────────┼────────────────────────────────────────┼──────────────────┘
          │                                        │
          ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                              ETL                                     │
│  ┌────────────────────┐              ┌────────────────────┐         │
│  │ tropomi_downloader │              │  ncep_downloader   │         │
│  └─────────┬──────────┘              └──────────┬─────────┘         │
│            │                                    │                    │
│            └──────────────┬─────────────────────┘                   │
│                           ▼                                          │
│                  ┌─────────────────┐                                │
│                  │ tropomi_processor│                               │
│                  └────────┬────────┘                                │
└───────────────────────────┼─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        MESSAGE BUS                                   │
│              ┌───────────────────────────┐                          │
│              │       RabbitMQ            │                          │
│              │  • CMD_CALCULOS_FINALES   │                          │
│              │  • CMD_NUEVA_EXTRACCION   │                          │
│              └───────────────────────────┘                          │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         CALCULADOR                                   │
│              ┌───────────────────────────┐                          │
│              │   Cálculo de Flujo SO2    │                          │
│              │  (6 Franjas Horarias)     │                          │
│              └─────────────┬─────────────┘                          │
└────────────────────────────┼────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      BASE DE DATOS                                   │
│              ┌───────────────────────────┐                          │
│              │       PostgreSQL          │                          │
│              │  • Volcanes               │                          │
│              │  • Imágenes TROPOMI       │                          │
│              │  • Datos de Viento        │                          │
│              │  • Resultados Flujo       │                          │
│              └───────────────────────────┘                          │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│       API REST          │     │        Jupyter          │
│      (FastAPI)          │     │       Notebooks         │
└───────────┬─────────────┘     └─────────────────────────┘
            │
            ▼
┌─────────────────────────┐
│       Frontend          │
│   (HTML/CSS/JS)         │
└─────────────────────────┘
```

## 🚀 Instalación

### Requisitos Previos

- Docker y Docker Compose
- Python 3.11+ (para desarrollo local)
- Cuenta en Copernicus Data Space: https://dataspace.copernicus.eu/

### Configuración

1. **Clonar o descargar el proyecto**
```bash
cd tesina_so2
```

2. **Configurar credenciales**
```bash
# Copiar archivo de ejemplo
cp .env.example .env

# Editar con tus credenciales de Copernicus
nano .env
```

### Iniciar con Docker
```bash
# Construir e iniciar todos los servicios
docker-compose up -d

# Ver logs
docker-compose logs -f

# Detener
docker-compose down
```

### Acceso a los servicios

| Servicio | URL | Descripción |
|----------|-----|-------------|
| Frontend | http://localhost | Interfaz web |
| API REST | http://localhost:8000/docs | Documentación Swagger |
| RabbitMQ | http://localhost:15672 | Panel de administración |
| Jupyter | http://localhost:8888 | Notebooks de análisis |

## 📖 Uso

### 1. Agregar un volcán

**Via API:**
```python
import requests

volcan = {
    "nombre": "Monte Etna",
    "latitud": 37.751,
    "longitud": 14.993,
    "pais": "Italia",
    "altitud_m": 3357
}

response = requests.post("http://localhost:8000/api/volcanes", json=volcan)
```

**Via Frontend:**
- Ir a la pestaña "Volcanes"
- Seleccionar de la lista predefinida o ingresar manualmente
- Clic en "Agregar"

### 2. Iniciar extracción de datos

**Via API:**
```python
extraccion = {
    "volcan_id": 1,
    "fecha_inicio": "2024-01-01",
    "fecha_fin": "2024-01-07"
}

response = requests.post("http://localhost:8000/api/extraccion/iniciar", json=extraccion)
```

### 3. Consultar resultados
```python
# Obtener resultados
resultados = requests.get("http://localhost:8000/api/resultados?volcan_id=1")
datos = resultados.json()
```

## 📁 Estructura del Proyecto
```
tesina_so2/
├── api/                    # API REST (FastAPI)
│   ├── __init__.py
│   └── main.py
├── calculador/             # Módulo de cálculo de flujo
│   ├── __init__.py
│   └── flujo_so2.py
├── config/                 # Configuración
│   ├── __init__.py
│   └── settings.py
├── database/               # Modelos de base de datos
│   ├── __init__.py
│   └── models.py
├── etl/                    # Extracción y procesamiento
│   ├── __init__.py
│   ├── tropomi_downloader.py
│   ├── tropomi_processor.py
│   └── ncep_downloader.py
├── frontend/               # Interfaz web
│   └── index.html
├── message_bus/            # Sistema de mensajería
│   ├── __init__.py
│   └── message_bus.py
├── notebooks/              # Jupyter notebooks
├── docker-compose.yml      # Orquestación Docker
├── Dockerfile.api          # Imagen Docker API
├── Dockerfile.worker       # Imagen Docker Workers
├── Dockerfile.jupyter      # Imagen Docker Jupyter
├── nginx.conf              # Configuración Nginx
├── requirements.txt        # Dependencias Python
├── .env.example            # Ejemplo de configuración
└── README.md               # Este archivo
```

## 🔬 Metodología de Cálculo

El flujo de SO2 se calcula usando el método de 6 franjas horarias:

### Franjas Horarias

El método analiza la pluma de SO2 en 6 franjas que representan el tiempo de viaje del gas desde el volcán:

| Franja | Representa |
|--------|------------|
| F 0.5h | SO2 emitido hace 30 minutos |
| F 1h | SO2 emitido hace 1 hora |
| F 1.5h | SO2 emitido hace 1.5 horas |
| F 2h | SO2 emitido hace 2 horas |
| F 2.5h | SO2 emitido hace 2.5 horas |
| F 3h | SO2 emitido hace 3 horas |

### Fórmula
```
Flujo (kg/s) = SO2_max (kg/m²) × distancia (m) × velocidad_viento (m/s)
Flujo (ton/día) = Flujo (kg/s) × 86.4
```

### Conversión de Unidades
```
SO2 (g/m²) = SO2 (mol/m²) / 0.0156
SO2 (kg/m²) = SO2 (g/m²) × 0.001
```

### Pasos del cálculo:

1. **Detección de pluma**: Buscar el máximo de SO2 a ~60km del volcán para determinar el azimut
2. **Selección de viento**: Buscar la altura (1500-10000m) donde la dirección del viento coincide con el azimut de la pluma
3. **Cálculo por franjas**: Para cada franja horaria, buscar el máximo de SO2 y calcular el flujo
4. **Promedio**: El flujo final es el promedio de las franjas válidas

## 📚 Referencias

- Theys, N., et al. (2017). Sulfur dioxide retrievals from TROPOMI. *Atmos. Meas. Tech.*
- Merucci, L., et al. (2011). Reconstruction of SO2 flux emission chronology. *J. Volcanol. Geotherm. Res.*
- Queißer, M., et al. (2019). TROPOMI enables high resolution SO2 flux observations. *Scientific Reports*

## 👥 Autores

- Ayelen Caterina Rodriguez Cardozo
- Patricia Florencia Massey
**Directores:**
- Federico Carballo
- Ricardo Cesar Jose Brea

**Institución:** Universidad Nacional de Hurlingham (UNAHUR)

## 📄 Licencia

Este proyecto fue desarrollado como trabajo de tesina para la carrera de Licenciatura en Informática.