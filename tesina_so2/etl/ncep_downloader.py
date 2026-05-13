"""
Módulo ETL para descarga de datos de viento desde ERA5 (CDS API)
Reemplaza NCEP Reanalysis para mayor precisión.

ERA5 ventajas sobre NCEP:
- Resolución espacial: 0.25° (vs 2.5° de NCEP)
- Resolución temporal: horaria (vs 6 horas de NCEP)
- Más preciso para comparar con resultados GDAS del profesor

Mantiene la misma interfaz que el downloader anterior para compatibilidad.
Basado en el método SO2FC del código MATLAB (AnalisisS02_CD_v4.m)
"""
import numpy as np
import xarray as xr
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import logging
import math
import os

from config.settings import (
    WIND_DIR, PRESSURE_TO_HEIGHT,
    DEFAULT_ALTITUDE_M
)
from database import get_session, DatosViento, Volcan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Alturas disponibles ordenadas
ALTURAS_DISPONIBLES = sorted(PRESSURE_TO_HEIGHT.values())

# Niveles de presión para ERA5 (los 19 del MATLAB del profesor)
ERA5_PRESSURE_LEVELS = [
    '1000', '975', '950', '925', '900', '850', '800', '750',
    '700', '650', '600', '550', '500', '450', '400', '350',
    '300', '250', '200'
]


class ERA5Downloader:
    """
    Clase para descargar y procesar datos de viento de ERA5.
    Compatible con el método SO2FC del código MATLAB.
    """
    
    def __init__(self):
        """Inicializa el descargador de ERA5"""
        self.wind_dir = Path(WIND_DIR)
        self.wind_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ERA5Downloader inicializado")
    
    def _calcular_direccion_viento_matlab(self, u: float, v: float) -> Tuple[float, float]:
        """
        Calcula velocidad y dirección del viento EXACTAMENTE como en MATLAB.
        
        Del código MATLAB (líneas 443-446):
            wdir = atan2d(vi, ui);
            if wdir < 0 & wdir > -180
                wdir = wdir + 360;
            end
        
        Dirección MATEMÁTICA hacia donde va el viento:
        - 0° = Este, 90° = Norte, 180° = Oeste, 270° = Sur
        """
        velocidad = math.sqrt(u**2 + v**2)
        wdir = math.degrees(math.atan2(v, u))
        if wdir < 0 and wdir > -180:
            wdir = wdir + 360
        return velocidad, wdir
    
    def _convertir_azimut_a_matematico(self, azimut: float) -> float:
        """
        Convierte azimut geográfico a dirección matemática.
        EXACTO como en MATLAB (líneas 521-525):
        
            if PlumeAz > 0 & PlumeAz < 90
                PlumeAzMat = (PlumeAz - 90) * -1;
            else
                PlumeAzMat = (PlumeAz - 450) * -1;
            end
        """
        if azimut > 0 and azimut < 90:
            return (azimut - 90) * -1
        else:
            return (azimut - 450) * -1
    
    def _obtener_alturas_validas(self, altura_volcan: float) -> List[int]:
        """
        Obtiene las alturas disponibles válidas para plumas volcánicas.
        
        Del código MATLAB del profesor:
        - Busca en TODAS las alturas entre 1500m y 12000m
        - NO filtra por altura del volcán (la pluma puede estar
          a cualquier altura en la distancia de referencia)
        - La selección de altura se hace por coincidencia de 
          dirección del viento con el azimut de la pluma
        """
        ALTURA_MIN_PLUMA = 1500
        ALTURA_MAX_PLUMA = 12000
        
        alturas_validas = [
            h for h in ALTURAS_DISPONIBLES 
            if h >= ALTURA_MIN_PLUMA and h <= ALTURA_MAX_PLUMA
        ]
        
        return alturas_validas
    
    def _hora_mas_cercana(self, fecha: datetime) -> str:
        """Redondea a la hora entera más cercana para ERA5 (resolución horaria)"""
        hora = fecha.hour
        if fecha.minute >= 30:
            hora = hora + 1
            if hora >= 24:
                hora = 23
        return f"{hora:02d}:00"
    
    def descargar_viento_era5(
        self,
        lat: float,
        lon: float,
        fecha: datetime,
        altura_volcan: float = DEFAULT_ALTITUDE_M
    ) -> Optional[Dict]:
        """
        Descarga datos de viento de ERA5 para una ubicación y fecha.
        
        ERA5 tiene resolución 0.25° y horaria, mucho más preciso que NCEP (2.5°, 6h).
        """
        try:
            import cdsapi
            
            hora = self._hora_mas_cercana(fecha)
            
            logger.info(f"Descargando vientos ERA5 para {fecha.strftime('%Y-%m-%d')} {hora}")
            
            # Crear área pequeña alrededor del punto (0.5° margen)
            margen = 0.5
            area = [
                lat + margen,   # Norte
                lon - margen,   # Oeste
                lat - margen,   # Sur
                lon + margen    # Este
            ]
            
            # Archivo temporal con cache
            fname = self.wind_dir / f"era5_{fecha.strftime('%Y%m%d')}_{hora.replace(':','')}.nc"
            
            # Descargar si no existe (cache)
            if not fname.exists():
                c = cdsapi.Client()
                c.retrieve('reanalysis-era5-pressure-levels', {
                    'product_type': 'reanalysis',
                    'variable': ['u_component_of_wind', 'v_component_of_wind'],
                    'pressure_level': ERA5_PRESSURE_LEVELS,
                    'year': str(fecha.year),
                    'month': f"{fecha.month:02d}",
                    'day': f"{fecha.day:02d}",
                    'time': hora,
                    'area': area,
                    'format': 'netcdf'
                }, str(fname))
                logger.info(f"Archivo ERA5 descargado: {fname}")
            else:
                logger.info(f"Usando cache ERA5: {fname}")
            
            # Leer datos
            ds = xr.open_dataset(fname)
            
            # Seleccionar punto más cercano y aplanar dimensiones extra
            u_data = ds['u'].sel(latitude=lat, longitude=lon, method='nearest').squeeze()
            v_data = ds['v'].sel(latitude=lat, longitude=lon, method='nearest').squeeze()
            
            # Obtener niveles de presión
            levels_disponibles = ds.pressure_level.values
            logger.info(f"Niveles disponibles en ERA5: {list(levels_disponibles.astype(int))}")
            
            # Construir datos de viento para cada altura
            datos_viento = []
            alturas_validas = self._obtener_alturas_validas(altura_volcan)
            logger.info(f"Alturas válidas: {alturas_validas}")
            
            for level in levels_disponibles:
                level_int = int(level)
                altura = PRESSURE_TO_HEIGHT.get(level_int, None)
                
                if altura is None:
                    continue
                    
                if altura not in alturas_validas:
                    continue
                
                try:
                    u = float(u_data.sel(pressure_level=level).values.item())
                    v = float(v_data.sel(pressure_level=level).values.item())
                except Exception:
                    logger.warning(f"No se pudo obtener viento para nivel {level_int} hPa")
                    continue
                
                velocidad, dir_matematica = self._calcular_direccion_viento_matlab(u, v)
                
                datos_viento.append({
                    'altura_m': altura,
                    'nivel_presion_hpa': level_int,
                    'u_component': u,
                    'v_component': v,
                    'velocidad_ms': velocidad,
                    'direccion_matematica': dir_matematica,
                })
            
            ds.close()
            
            if not datos_viento:
                logger.warning("No se encontraron datos de viento en alturas válidas")
                return None
            
            # Ordenar por altura
            datos_viento.sort(key=lambda x: x['altura_m'])
            
            logger.info(f"Vientos ERA5 obtenidos para {len(datos_viento)} alturas:")
            for v in datos_viento:
                logger.info(f"  {v['altura_m']:>5}m ({v['nivel_presion_hpa']:>4}hPa): "
                           f"{v['velocidad_ms']:>5.1f} m/s, dir_mat={v['direccion_matematica']:>6.1f}°")
            
            return {
                'fecha': fecha,
                'lat': lat,
                'lon': lon,
                'altura_volcan': altura_volcan,
                'vientos_por_altura': datos_viento
            }
            
        except Exception as e:
            logger.error(f"Error descargando vientos ERA5: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def buscar_altura_por_azimut(
        self,
        datos_viento: Dict,
        azimut_pluma: float,
        tolerancia: float = 45
    ) -> Optional[Dict]:
        """
        Busca la altura donde la dirección del viento coincide con el azimut de la pluma.
        
        MÉTODO EXACTO DEL MATLAB (línea 533):
            [valW, idxW] = min(abs(temp - PlumeAzMat));
        """
        if not datos_viento or 'vientos_por_altura' not in datos_viento:
            return None
        
        vientos = datos_viento['vientos_por_altura']
        
        if not vientos:
            return None
        
        # Convertir azimut de pluma a dirección matemática (EXACTO como MATLAB)
        plume_az_mat = self._convertir_azimut_a_matematico(azimut_pluma)
        
        logger.info(f"Azimut pluma: {azimut_pluma:.1f}° geográfico = {plume_az_mat:.1f}° matemático")
        
        # Buscar la altura con menor diferencia
        mejor_match = None
        menor_diferencia = float('inf')
        
        for viento in vientos:
            dif = abs(viento['direccion_matematica'] - plume_az_mat)
            if dif > 180:
                dif = 360 - dif
            
            logger.info(f"  Altura {viento['altura_m']:>5}m: dir_viento={viento['direccion_matematica']:>6.1f}°, "
                       f"vel={viento['velocidad_ms']:.1f}m/s, dif={dif:.1f}°")
            
            if dif < menor_diferencia:
                menor_diferencia = dif
                mejor_match = viento
        
        if mejor_match:
            if menor_diferencia <= tolerancia:
                logger.info(f"✓ Altura seleccionada: {mejor_match['altura_m']}m (diferencia={menor_diferencia:.1f}°)")
            else:
                logger.warning(f"⚠ Mejor altura: {mejor_match['altura_m']}m pero diferencia={menor_diferencia:.1f}° > tolerancia={tolerancia}°")
            
            # Convertir dirección matemática a azimut geográfico
            dir_mat = mejor_match['direccion_matematica']
            azimut_viento = 90 - dir_mat
            if azimut_viento < 0:
                azimut_viento += 360
            if azimut_viento >= 360:
                azimut_viento -= 360
            
            mejor_match['azimut_grados'] = azimut_viento
            
            return mejor_match
        
        return vientos[0] if vientos else None
    
    def guardar_datos_viento(
        self,
        volcan_id: int,
        datos_viento: Dict,
        altura_seleccionada: Dict
    ) -> Optional[int]:
        """Guarda datos de viento en la base de datos"""
        session = get_session()
        
        try:
            registro = DatosViento(
                volcan_id=volcan_id,
                fecha_hora=datos_viento['fecha'],
                latitud=datos_viento['lat'],
                longitud=datos_viento['lon'],
                nivel_presion_hpa=altura_seleccionada.get('nivel_presion_hpa'),
                altitud_m=altura_seleccionada.get('altura_m'),
                u_component=altura_seleccionada.get('u_component'),
                v_component=altura_seleccionada.get('v_component'),
                velocidad_ms=altura_seleccionada.get('velocidad_ms'),
                direccion_grados=altura_seleccionada.get('azimut_grados'),
                fuente="ERA5"
            )
            
            session.add(registro)
            session.commit()
            
            registro_id = registro.id
            logger.info(f"Datos de viento ERA5 guardados con ID {registro_id}")
            
            return registro_id
            
        except Exception as e:
            logger.error(f"Error guardando datos de viento: {e}")
            session.rollback()
            return None
            
        finally:
            session.close()


# Mantener compatibilidad - misma función que antes
# El import en main.py es: from etl.ncep_downloader import obtener_viento_para_imagen
def obtener_viento_para_imagen(
    volcan_id: int,
    lat: float,
    lon: float,
    fecha: datetime,
    altitud_m: float = DEFAULT_ALTITUDE_M,
    azimut_pluma: float = None
) -> Optional[Dict]:
    """
    Función principal para obtener datos de viento para una imagen TROPOMI.
    Usa ERA5 como fuente de datos (reemplaza NCEP para mayor precisión).
    
    IMPORTANTE: El azimut_pluma debe ser en coordenadas GEOGRÁFICAS (0°=Norte, 90°=Este).
    """
    downloader = ERA5Downloader()
    
    # Descargar datos de viento
    datos = downloader.descargar_viento_era5(
        lat=lat,
        lon=lon,
        fecha=fecha,
        altura_volcan=altitud_m
    )
    
    if not datos:
        logger.warning("No se pudieron descargar datos de viento ERA5")
        return None
    
    # Seleccionar altura basada en azimut de la pluma
    if azimut_pluma is not None:
        viento_seleccionado = downloader.buscar_altura_por_azimut(
            datos, azimut_pluma
        )
    else:
        logger.warning("No se proporcionó azimut de pluma, usando primera altura")
        viento_seleccionado = datos['vientos_por_altura'][0] if datos['vientos_por_altura'] else None
    
    if viento_seleccionado:
        # Guardar en base de datos
        downloader.guardar_datos_viento(volcan_id, datos, viento_seleccionado)
        
        return {
            'velocidad_ms': viento_seleccionado['velocidad_ms'],
            'direccion_grados': viento_seleccionado.get('azimut_grados', viento_seleccionado['direccion_matematica']),
            'altura_m': viento_seleccionado['altura_m'],
            'nivel_presion_hpa': viento_seleccionado['nivel_presion_hpa'],
            'u_component': viento_seleccionado['u_component'],
            'v_component': viento_seleccionado['v_component']
        }
    
    return None