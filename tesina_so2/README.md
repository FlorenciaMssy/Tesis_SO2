# Monitor de Flujo SO₂ Volcánico

Sistema automatizado de monitoreo y cuantificación de dióxido de azufre (SO₂) volcánico mediante sensado remoto satelital.

Tesina de grado — Licenciatura en Informática, Universidad Nacional de Hurlingham (UNAHUR).

**Autores:** Massey Florencia, Rodriguez Ayelén
**Director:** Prof. Federico Carballo

---

## Descripción

Sistema web que automatiza el flujo completo de estimación de emisiones de SO₂ volcánico:

1. **Descarga de imágenes TROPOMI** desde Google Earth Engine (producto L3 NRTI, 1km de resolución)
2. **Descarga de datos de viento** desde ERA5 (Copernicus CDS API, resolución 0.25°, horaria)
3. **Cálculo de flujo SO₂** mediante el método SO2 (cross-section method), replicando la metodología MATLAB del Prof. Carballo
4. **Visualización de resultados** en interfaz web con series temporales, tablas y previews de imágenes satelitales

## Caso de estudio

Volcán **Sabancaya** (Perú, -15.78°, -71.85°, 5976 m), julio 2019. Validado contra resultados de referencia del Prof. Carballo.

---

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Backend API | FastAPI (Python 3.11) |
| Base de datos | PostgreSQL 15 |
| Frontend | HTML5 + Bootstrap 5 + Chart.js + Leaflet |
| Proxy reverso | Nginx |
| Contenedores | Docker + Docker Compose |
| Datos satelitales | Google Earth Engine API |
| Datos de viento | ERA5 (CDS API - Copernicus) |
| Procesamiento | NumPy, Rasterio, xarray, Matplotlib |

## Arquitectura

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │────▶│    Nginx     │────▶│   FastAPI    │
│  (HTML/JS)   │     │  (proxy)     │     │   (API)      │
└─────────────┘     └──────────────┘     └──────┬───────┘
                                                 │
                         ┌───────────────────────┼───────────────────────┐
                         │                       │                       │
                    ┌────▼─────┐          ┌──────▼──────┐        ┌──────▼──────┐
                    │ Google   │          │    ERA5     │        │ PostgreSQL  │
                    │ Earth    │          │  (CDS API)  │        │    (DB)     │
                    │ Engine   │          │   Vientos   │        │             │
                    │ TROPOMI  │          └─────────────┘        └─────────────┘
                    └──────────┘
```

---

## Requisitos previos

- **Docker** y **Docker Compose**
- **Cuenta Google Earth Engine** autenticada (`earthengine authenticate`)
- **Cuenta CDS (Copernicus)** con API key para ERA5

## Instalación

### 1. Clonar el repositorio

```bash
git clone <url-del-repositorio>
cd tesina_so2
```

### 2. Configurar credenciales

**Google Earth Engine:**
```bash
earthengine authenticate
```
Las credenciales se guardan en `~/.config/earthengine/` y se montan al container.

**ERA5 (CDS API):**
Crear archivo `.cdsapirc` en la raíz del proyecto:
```
url: https://cds.climate.copernicus.eu/api
key: TU_API_KEY_AQUI
```
Obtener la key en: https://cds.climate.copernicus.eu/

### 3. Levantar los servicios

```bash
docker-compose build
docker-compose up -d
```

### 4. Crear credenciales CDS dentro del container

```bash
docker exec so2_api bash -c "echo 'url: https://cds.climate.copernicus.eu/api
key: TU_API_KEY_AQUI' > /root/.cdsapirc"
```

### 5. Acceder al sistema

Abrir http://localhost en el navegador.

---

## Uso

### Agregar un volcán

1. Ir a la pestaña **Volcanes**
2. Seleccionar un volcán predefinido (Sabancaya, Copahue, Etna, Villarrica) o ingresar datos manualmente
3. Click en **Agregar**

### Descargar imágenes TROPOMI

1. Ir a la pestaña **Extracción**
2. Seleccionar el volcán y rango de fechas
3. Click en **Descargar desde GEE**
4. Las imágenes aparecen en la tabla con estado "Pend" (pendiente)

### Procesar imágenes

1. Con el volcán seleccionado, click en **Procesar Pendientes**
2. El sistema ejecuta para cada imagen:
   - Detección automática del azimut de pluma
   - Descarga de viento ERA5 (se cachea por fecha)
   - Cálculo de flujo SO₂ por franjas horarias
3. Las imágenes pasan a estado "OK" al completarse

### Ver resultados

1. Ir a la pestaña **Resultados**
2. Seleccionar volcán y rango de fechas → **Buscar**
3. Vista **Día**: muestra flujo promedio diario
4. Vista **Detalle**: muestra cada franja horaria con altitud, dirección y velocidad de viento
5. Click en un resultado para ver el detalle con la imagen TROPOMI

### Exportar datos

Click en **CSV** para descargar los resultados en formato CSV.

---

## Método SO2

El sistema implementa el método de corte transversal de pluma (cross-section method) para estimar el flujo de SO₂:

**Fórmula principal:**
```
Flujo (kg/s) = SO₂_max (kg/m²) × Distancia (m) × Velocidad_viento (m/s)
```

**Conversión de unidades:**
- mol/m² → g/m²: multiplicar por 64.1025 (1/0.0156)
- g/m² → kg/m²: multiplicar por 0.001
- kg/s → t/d: multiplicar por 86.4

**Franjas horarias:** 0.5, 1.0, 1.5, 2.0, 2.5, 3.0 horas

El resultado final es el promedio de los flujos de todas las franjas válidas.

**Detección de pluma:** Se busca el máximo de SO₂ a 60 km del cráter para determinar automáticamente la dirección de la pluma.

**Selección de altura de viento:** Se selecciona el nivel de presión cuya dirección de viento mejor coincide con el azimut de la pluma detectada, entre 1500 m y 12000 m de altitud.

---

## Estructura del proyecto

```
tesina_so2/
├── api/
│   ├── __init__.py
│   └── main.py              # API REST (FastAPI)
├── etl/
│   ├── __init__.py
│   ├── gee_tropomi_downloader.py  # Descarga TROPOMI desde GEE
│   ├── geotiff_processor.py       # Procesamiento SO2
│   └── ncep_downloader.py         # Descarga viento ERA5 (nombre legacy)
├── database/
│   ├── __init__.py
│   └── models.py             # Modelos SQLAlchemy
├── config/
│   └── settings.py           # Configuración y constantes
├── frontend/
│   └── index.html            # Interfaz web
├── data/
│   ├── images/               # Imágenes GeoTIFF descargadas
│   └── wind/                 # Datos de viento ERA5 (cache)
├── docker-compose.yml
├── Dockerfile.api
├── nginx.conf
├── requirements.txt
└── README.md
```

## Fuentes de datos

### Imágenes TROPOMI
- **Colección:** `COPERNICUS/S5P/NRTI/L3_SO2`
- **Variable:** `SO2_column_number_density` (VCD, mol/m²)
- **Resolución:** 1000 m
- **Plataforma:** Google Earth Engine

### Viento atmosférico
- **Dataset:** ERA5 Reanalysis (pressure levels)
- **Variables:** `u_component_of_wind`, `v_component_of_wind`
- **Resolución espacial:** 0.25°
- **Resolución temporal:** horaria
- **Niveles de presión:** 19 niveles (1000 a 200 hPa)
- **Plataforma:** Copernicus CDS API

---

## Notas importantes

- El archivo `.cdsapirc` debe recrearse dentro del container después de cada `docker-compose build` o recreación del container.
- La primera descarga de viento ERA5 para una fecha nueva tarda ~30 segundos (descarga del servidor CDS). Las siguientes consultas para la misma fecha usan caché local.
- La detección automática de pluma funciona mejor en días con emisiones claras y visibles. En días de baja actividad, puede no detectar pluma.

## 📄 Licencia

Este proyecto fue desarrollado como trabajo de tesina para la carrera de Licenciatura en Informática.