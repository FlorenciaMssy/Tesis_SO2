"""
Módulo ETL para descarga de datos de viento desde NCEP/NCAR Reanalysis 1
via OPeNDAP (NOAA PSL) — la MISMA fuente que usa el profesor en su MATLAB.

NCEP/NCAR Reanalysis 1:
- Resolución espacial: 2.5° x 2.5°
- Resolución temporal: 6 horas (00, 06, 12, 18 UTC)
- 17 niveles de presión
- Fuente: https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.pressure.html
- Acceso OPeNDAP: https://psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis/

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

# Niveles de presión de NCEP Reanalysis 1 (17 niveles)
# El profesor usa estos mismos (línea 68 del MATLAB):
# level: 17 Pressure levels (mb): 1000,925,850,700,600,500,400,300,250,200,150,100,70,50,30,20,10
NCEP_PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 70, 50, 30, 20, 10]

# Mapeo presión→altura del MATLAB del profesor (línea 53 para .nc)
# Solo las alturas que el profesor usa
NCEP_PRESSURE_TO_HEIGHT = {
    1000: 111,
    925: 762,
    850: 1458,
    700: 3013,
    600: 4204,
    500: 5576,
    400: 7187,
    300: 9166,
    250: 10366,
    200: 11787,
    150: 13503
}

# URLs OPeNDAP de NCEP Reanalysis 1
NCEP_OPENDAP_BASE = "https://psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis"


class NCEPDownloader:
    """
    Clase para descargar y procesar datos de viento de NCEP/NCAR Reanalysis 1.
    Usa la misma fuente de datos que el MATLAB del profesor.
    """
    
    def __init__(self):
        """Inicializa el descargador de NCEP"""
        self.wind_dir = Path(WIND_DIR)
        self.wind_dir.mkdir(parents=True, exist_ok=True)
        logger.info("NCEPDownloader inicializado")
    
    def _calcular_direccion_viento_matlab(self, u: float, v: float) -> Tuple[float, float]:
        """
        Calcula velocidad y dirección del viento EXACTAMENTE como en MATLAB.
        
        Del código MATLAB (líneas 443-446, 565-568):
            wdir = atan2d(vi, ui);
            if wdir < 0 & wdir > -180
                wdir = wdir + 360;
            end
        
        Dirección MATEMÁTICA "hacia donde sopla" el viento:
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
        Obtiene las alturas disponibles para buscar viento.
        Se usan TODAS las alturas del mapeo NCEP del profesor.
        La selección de la altura correcta se hace por coincidencia
        del azimut de la pluma con la dirección del viento.
        """
        alturas_profesor = sorted(NCEP_PRESSURE_TO_HEIGHT.values())
        return alturas_profesor
    
    def _hora_ncep_mas_cercana(self, fecha: datetime) -> int:
        """
        NCEP tiene datos cada 6 horas: 00, 06, 12, 18 UTC.
        Retorna el índice temporal más cercano.
        
        Del MATLAB (línea 411):
            [val,idx]=min(abs(datesWind-dateSO2));
        """
        hora_decimal = fecha.hour + fecha.minute / 60
        horas_ncep = [0, 6, 12, 18]
        idx = min(range(len(horas_ncep)), key=lambda i: abs(horas_ncep[i] - hora_decimal))
        return horas_ncep[idx]
    
    def descargar_viento_ncep(
        self,
        lat: float,
        lon: float,
        fecha: datetime,
        altura_volcan: float = DEFAULT_ALTITUDE_M
    ) -> Optional[Dict]:
        """
        Descarga datos de viento de NCEP/NCAR Reanalysis 1 via OPeNDAP.
        
        MISMA FUENTE que el MATLAB del profesor.
        Resolución: 2.5° x 2.5°, 6 horas.
        """
        import concurrent.futures
        
        try:
            hora_cercana = self._hora_ncep_mas_cercana(fecha)
            year = fecha.year
            
            logger.info(f"Descargando vientos NCEP para {fecha.strftime('%Y-%m-%d')} {hora_cercana:02d}:00 UTC")
            
            # Archivo cache local
            fname = self.wind_dir / f"ncep_{fecha.strftime('%Y%m%d')}_{hora_cercana:02d}00.nc"
            
            if not fname.exists():
                def _descargar():
                    # URLs OPeNDAP para uwnd y vwnd
                    uwnd_url = f"{NCEP_OPENDAP_BASE}/pressure/uwnd.{year}.nc"
                    vwnd_url = f"{NCEP_OPENDAP_BASE}/pressure/vwnd.{year}.nc"
                    
                    logger.info(f"Conectando a NCEP OPeNDAP: uwnd.{year}.nc")
                    
                    # Abrir datasets remotos
                    ds_u = xr.open_dataset(uwnd_url, engine='netcdf4')
                    ds_v = xr.open_dataset(vwnd_url, engine='netcdf4')
                    
                    # Encontrar el punto más cercano en la grilla NCEP
                    # NCEP usa longitudes 0-360, convertir si es negativa
                    lon_ncep = lon if lon >= 0 else lon + 360
                    
                    # Buscar la fecha/hora más cercana
                    target_time = datetime(fecha.year, fecha.month, fecha.day, hora_cercana)
                    
                    # Seleccionar datos: punto más cercano + tiempo más cercano
                    u_data = ds_u['uwnd'].sel(
                        lat=lat, lon=lon_ncep, time=target_time,
                        method='nearest'
                    )
                    v_data = ds_v['vwnd'].sel(
                        lat=lat, lon=lon_ncep, time=target_time,
                        method='nearest'
                    )
                    
                    # Descargar a memoria (esto es lo que tarda)
                    u_values = u_data.values
                    v_values = v_data.values
                    levels = ds_u['level'].values
                    
                    ds_u.close()
                    ds_v.close()
                    
                    # Guardar cache local
                    ds_local = xr.Dataset({
                        'uwnd': (['level'], u_values),
                        'vwnd': (['level'], v_values),
                    }, coords={'level': levels})
                    ds_local.to_netcdf(str(fname))
                    ds_local.close()
                    
                    logger.info(f"Datos NCEP guardados en cache: {fname}")
                
                # Ejecutar con timeout de 120 segundos
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(_descargar)
                    try:
                        future.result(timeout=120)
                    except concurrent.futures.TimeoutError:
                        logger.error("Timeout (120s) descargando NCEP — servidor NOAA no respondió")
                        if fname.exists():
                            fname.unlink()
                        return None
            else:
                logger.info(f"Usando cache NCEP: {fname}")
            
            # Leer datos del cache
            ds = xr.open_dataset(str(fname))
            u_values = ds['uwnd'].values
            v_values = ds['vwnd'].values
            levels = ds['level'].values
            ds.close()
            
            logger.info(f"Niveles de presión NCEP: {list(levels.astype(int))}")
            
            # Construir datos de viento para cada altura
            datos_viento = []
            alturas_validas = self._obtener_alturas_validas(altura_volcan)
            logger.info(f"Alturas disponibles: {alturas_validas}")
            
            for i, level in enumerate(levels):
                level_int = int(level)
                altura = NCEP_PRESSURE_TO_HEIGHT.get(level_int, None)
                
                if altura is None:
                    continue
                
                if altura not in alturas_validas:
                    continue
                
                try:
                    u = float(u_values[i])
                    v = float(v_values[i])
                except (IndexError, ValueError):
                    logger.warning(f"No se pudo obtener viento para nivel {level_int} hPa")
                    continue
                
                velocidad, dir_matematica = self._calcular_direccion_viento_matlab(u, v)
                
                datos_viento.append({
                    'nivel_presion_hpa': level_int,
                    'altura_m': altura,
                    'u': u,
                    'v': v,
                    'velocidad_ms': velocidad,
                    'direccion_matematica': dir_matematica,
                })
            
            if datos_viento:
                logger.info(f"Vientos NCEP obtenidos para {len(datos_viento)} alturas:")
                for v in datos_viento:
                    logger.info(f"  {v['altura_m']:>6}m ({v['nivel_presion_hpa']:>4}hPa): "
                               f"{v['velocidad_ms']:>5.1f} m/s, dir_mat={v['direccion_matematica']:>6.1f}°")
            
            return {
                'fecha': fecha,
                'lat': lat,
                'lon': lon,
                'fuente': 'NCEP_Reanalysis_1',
                'hora_utc': hora_cercana,
                'vientos_por_altura': datos_viento
            }
            
        except Exception as e:
            logger.error(f"Error descargando vientos NCEP: {e}")
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
        
        temp = dirección del viento (HACIA donde sopla) en coordenadas matemáticas
        PlumeAzMat = azimut de la pluma en coordenadas matemáticas
        """
        if not datos_viento or 'vientos_por_altura' not in datos_viento:
            return None
        
        vientos = datos_viento['vientos_por_altura']
        
        if not vientos:
            return None
        
        # Convertir azimut de pluma a dirección matemática (EXACTO como MATLAB)
        plume_az_mat = self._convertir_azimut_a_matematico(azimut_pluma)
        
        logger.info(f"Azimut pluma: {azimut_pluma:.1f}° geográfico = {plume_az_mat:.1f}° matemático")
        
        # MATLAB línea 533: [valW,idxW]=min(abs(temp-PlumeAzMat));
        # Compara directamente — sin inversión.
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
                u_component=altura_seleccionada.get('u'),
                v_component=altura_seleccionada.get('v'),
                velocidad_ms=altura_seleccionada.get('velocidad_ms'),
                direccion_grados=altura_seleccionada.get('azimut_grados', 
                                                          altura_seleccionada.get('direccion_matematica')),
                fuente=datos_viento.get('fuente', 'NCEP_Reanalysis_1')
            )
            
            session.add(registro)
            session.commit()
            
            registro_id = registro.id
            logger.info(f"Datos de viento NCEP guardados con ID {registro_id}")
            
            return registro_id
            
        except Exception as e:
            logger.error(f"Error guardando datos de viento: {e}")
            session.rollback()
            return None
            
        finally:
            session.close()


# Función principal compatible con main.py
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
    Usa NCEP/NCAR Reanalysis 1 (misma fuente que el MATLAB del profesor).
    
    IMPORTANTE: El azimut_pluma debe ser en coordenadas GEOGRÁFICAS (0°=Norte, 90°=Este).
    """
    downloader = NCEPDownloader()
    
    # Descargar datos de viento de NCEP
    datos = downloader.descargar_viento_ncep(
        lat=lat,
        lon=lon,
        fecha=fecha,
        altura_volcan=altitud_m
    )
    
    if not datos:
        logger.warning("No se pudieron descargar datos de viento NCEP")
        return None
    
    # Seleccionar altura basada en azimut de la pluma
    if azimut_pluma is not None:
        viento_seleccionado = downloader.buscar_altura_por_azimut(
            datos, azimut_pluma
        )
    else:
        logger.warning("No se proporcionó azimut de pluma, usando primera altura")
        viento_seleccionado = datos['vientos_por_altura'][0] if datos['vientos_por_altura'] else None
    
    if not viento_seleccionado:
        logger.warning("No se pudo seleccionar viento por azimut")
        return None
    
    # Guardar en base de datos
    downloader.guardar_datos_viento(
        volcan_id=volcan_id,
        datos_viento=datos,
        altura_seleccionada=viento_seleccionado
    )
    
    return {
        'velocidad_ms': viento_seleccionado['velocidad_ms'],
        'direccion_grados': viento_seleccionado.get('azimut_grados', viento_seleccionado['direccion_matematica']),
        'altura_m': viento_seleccionado['altura_m'],
        'nivel_presion_hpa': viento_seleccionado.get('nivel_presion_hpa'),
        'fuente': 'NCEP_Reanalysis_1'
    }