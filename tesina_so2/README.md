# Sistema de Monitoreo de Flujo de SO2 Volcánico

Sistema para el procesamiento y generación de series temporales de flujo de dióxido de azufre (SO2) en volcanes, utilizando imágenes del satélite TROPOMI (Sentinel-5P) y datos de viento de ERA5.

## 📋 Descripción

Este software permite:
- **Descargar** imágenes TROPOMI de SO2 desde Copernicus Data Space
- **Obtener** datos de viento desde ERA5 (Climate Data Store)
- **Calcular** el flujo de SO2 volcánico usando el método de sección transversal
- **Visualizar** series temporales y estadísticas de emisiones
- **Exportar** datos para análisis posterior

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ORÍGENES DE DATOS                          │
│  ┌─────────────┐                          ┌─────────────┐          │
│  │  TROPOMI    │                          │    ERA5     │          │
│  │ (Copernicus)│                          │   (CDS)     │          │
│  └──────┬──────┘                          └──────┬──────┘          │
└─────────┼────────────────────────────────────────┼──────────────────┘
          │                                        │
          ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                              ETL                                     │
│  ┌────────────────────┐              ┌────────────────────┐         │
│  │ tropomi_downloader │              │  era5_downloader   │         │
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
│              │  (Sección Transversal)    │                          │
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
- Cuenta en CDS (Climate Data Store): https://cds.climate.copernicus.eu/

### Configuración

1. **Clonar o descargar el proyecto**

```bash
cd tesina_so2
```

2. **Configurar credenciales**

```bash
# Copiar archivo de ejemplo
cp .env.example .env

# Editar con tus credenciales
nano .env
```

3. **Configurar CDS API**

Crear archivo `~/.cdsapirc`:
```
url: https://cds.climate.copernicus.eu/api/v2
key: TU_UID:TU_API_KEY
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
    "pais": "Italia"
}

response = requests.post("http://localhost:8000/api/volcanes", json=volcan)
```

**Via Frontend:**
- Ir a la pestaña "Volcanes"
- Seleccionar de la lista predefinida o ingresar manualmente
- Clic en "Guardar Volcán"

### 2. Iniciar extracción de datos

**Via API:**
```python
extraccion = {
    "volcan_id": 1,
    "fecha_inicio": "2024-01-01T00:00:00",
    "fecha_fin": "2024-01-07T23:59:59",
    "descargar": True
}

response = requests.post("http://localhost:8000/api/extraccion/iniciar", json=extraccion)
```

### 3. Consultar resultados

```python
# Obtener serie temporal
serie = requests.post("http://localhost:8000/api/resultados/serie-temporal", json={
    "volcan_id": 1,
    "fecha_inicio": "2024-01-01T00:00:00",
    "fecha_fin": "2024-12-31T23:59:59"
})

datos = serie.json()
```

### 4. Análisis en Jupyter

Acceder a http://localhost:8888 y abrir el notebook `analisis_flujo_so2.ipynb`

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
│   └── era5_downloader.py
├── frontend/               # Interfaz web
│   └── index.html
├── message_bus/            # Sistema de mensajería
│   ├── __init__.py
│   └── message_bus.py
├── notebooks/              # Jupyter notebooks
│   └── analisis_flujo_so2.ipynb
├── tests/                  # Tests unitarios
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

El flujo de SO2 se calcula usando el método de sección transversal:

```
Φ = VCD_integral × v_wind
```

Donde:
- **Φ**: Flujo de SO2 (mol/s, convertido a kg/s y ton/día)
- **VCD_integral**: Integral de la columna vertical de SO2 a través de una sección transversal perpendicular al viento (mol/m)
- **v_wind**: Velocidad del viento a la altitud de la pluma (m/s)

### Pasos del cálculo:

1. **Detección de pluma**: Identificación de píxeles con SO2 > umbral
2. **Sección transversal**: Línea perpendicular a la dirección del viento
3. **Integración**: Suma de SO2 a través de la sección
4. **Cálculo de flujo**: Multiplicación por velocidad del viento

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
