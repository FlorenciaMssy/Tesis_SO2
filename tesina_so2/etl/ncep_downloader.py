"""
Módulo ETL para descarga de datos de viento desde NCEP Reanalysis
Usa OPeNDAP (sin autenticación requerida)
"""
import numpy as np
import xarray as xr
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import logging
import math

from config.settings import (
    WIND_DIR, PRESSURE_TO_HEIGHT, NCEP_BASE_URL, 
    DEFAULT_ALTITUDE_M
)
from database import get_session, DatosViento, Volcan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Alturas disponibles ordenadas
ALTURAS_DISPONIBLES = sorted(PRESSURE_TO_HEIGHT.values())


class NCEPDownloader:
    """
    Clase para descargar y procesar datos de viento de NCEP Reanalysis
    Compatible con el formato usado por SO2FC en MATLAB
    """
    
    def __init__(self):
        """Inicializa el descargador de NCEP"""
        self.wind_dir = Path(WIND_DIR)
        self.wind_dir.mkdir(parents=True, exist_ok=True)
        logger.info("NCEPDownloader inicializado")
    
    def _calcular_velocidad_direccion(self, u: float, v: float) -> Tuple[float, float]:
        """
        Calcula velocidad y dirección del viento a partir de componentes U y V
        
        Según el código MATLAB:
        - wdir = atan2d(v, u) -> dirección matemática (hacia donde va el viento)
        
        Args:
            u: Componente este-oeste (m/s)
            v: Componente norte-sur (m/s)
            
        Returns:
            Tuple (velocidad en m/s, dirección en grados 0-360)
        """
        # Velocidad
        velocidad = math.sqrt(u**2 + v**2)
        
        # Dirección matemática (hacia donde va el viento)
        direccion = math.degrees(math.atan2(v, u))
        
        # Ajustar a 0-360
        if direccion < 0:
            direccion += 360
        
        return velocidad, direccion
    
    def _convertir_direccion_azimut(self, dir_mat: float) -> float:
        """
        Convierte dirección matemática a azimut geográfico
        
        Dirección matemática: 0° = Este, 90° = Norte
        Azimut geográfico: 0° = Norte, 90° = Este
        """
        azimut = 90 - dir_mat
        if azimut < 0:
            azimut += 360
        return azimut
    
    def _obtener_alturas_sobre_volcan(self, altura_volcan: float) -> List[int]:
        """
        Obtiene las alturas disponibles que están por encima del volcán
        (Igual que en el código MATLAB: auxil = alturas > hvolcan)
        """
        return [h for h in ALTURAS_DISPONIBLES if h > altura_volcan]
    
    def descargar_viento_ncep(
        self,
        lat: float,
        lon: float,
        fecha: datetime,
        altura_volcan: float = DEFAULT_ALTITUDE_M
    ) -> Optional[Dict]:
        """
        Descarga datos de viento de NCEP Reanalysis para una ubicación y fecha
        
        Args:
            lat: Latitud del volcán
            lon: Longitud del volcán
            fecha: Fecha de la imagen
            altura_volcan: Altura del volcán en metros
            
        Returns:
            Diccionario con datos de viento para múltiples alturas
        """
        try:
            año = fecha.year
            
            # URLs de NCEP Reanalysis
            url_uwnd = f"{NCEP_BASE_URL}/pressure/uwnd.{año}.nc"
            url_vwnd = f"{NCEP_BASE_URL}/pressure/vwnd.{año}.nc"
            
            logger.info(f"Descargando vientos NCEP para {fecha.strftime('%Y-%m-%d %H:%M')}")
            
            # Abrir datasets remotos con xarray via OPeNDAP
            ds_u = xr.open_dataset(url_uwnd)
            ds_v = xr.open_dataset(url_vwnd)
            
            # Ajustar longitud (NCEP usa 0-360, necesitamos -180 a 180)
            lon_ncep = lon if lon >= 0 else lon + 360
            
            # Seleccionar punto más cercano y tiempo más cercano
            fecha_np = np.datetime64(fecha)
            
            u_data = ds_u['uwnd'].sel(
                lat=lat, 
                lon=lon_ncep, 
                time=fecha_np,
                method='nearest'
            )
            
            v_data = ds_v['vwnd'].sel(
                lat=lat, 
                lon=lon_ncep, 
                time=fecha_np,
                method='nearest'
            )
            
            # Obtener niveles de presión
            levels = u_data.level.values
            
            # Construir datos de viento para cada altura
            datos_viento = []
            
            for level in levels:
                level_int = int(level)
                # Obtener altura aproximada para este nivel
                altura = PRESSURE_TO_HEIGHT.get(level_int, None)
                
                if altura is None or altura <= altura_volcan:
                    continue
                
                u = float(u_data.sel(level=level).values)
                v = float(v_data.sel(level=level).values)
                
                velocidad, direccion = self._calcular_velocidad_direccion(u, v)
                azimut = self._convertir_direccion_azimut(direccion)
                
                datos_viento.append({
                    'altura_m': altura,
                    'nivel_presion_hpa': level_int,
                    'u_component': u,
                    'v_component': v,
                    'velocidad_ms': velocidad,
                    'direccion_matematica': direccion,
                    'azimut_grados': azimut
                })
            
            ds_u.close()
            ds_v.close()
            
            if not datos_viento:
                logger.warning("No se encontraron datos de viento sobre la altura del volcán")
                return None
            
            logger.info(f"Vientos obtenidos para {len(datos_viento)} alturas")
            
            return {
                'fecha': fecha,
                'lat': lat,
                'lon': lon,
                'altura_volcan': altura_volcan,
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
        tolerancia: float = 30
    ) -> Optional[Dict]:
        """
        Busca la altura donde la dirección del viento coincide con el azimut de la pluma
        
        Este es el método usado en SO2FC:
        "Buscamos la dirección de viento más cercana al azimut de la pluma"
        
        Args:
            datos_viento: Diccionario con vientos por altura
            azimut_pluma: Azimut de la pluma en grados (0-360)
            tolerancia: Tolerancia en grados para la búsqueda
            
        Returns:
            Datos de viento de la altura seleccionada
        """
        if not datos_viento or 'vientos_por_altura' not in datos_viento:
            return None
        
        vientos = datos_viento['vientos_por_altura']
        
        mejor_match = None
        menor_diferencia = float('inf')
        
        for viento in vientos:
            # Calcular diferencia angular
            dif = abs(viento['azimut_grados'] - azimut_pluma)
            if dif > 180:
                dif = 360 - dif
            
            if dif < menor_diferencia:
                menor_diferencia = dif
                mejor_match = viento
        
        if mejor_match and menor_diferencia <= tolerancia:
            logger.info(f"Altura seleccionada: {mejor_match['altura_m']}m "
                       f"(azimut viento: {mejor_match['azimut_grados']:.1f}°, "
                       f"azimut pluma: {azimut_pluma:.1f}°, "
                       f"diferencia: {menor_diferencia:.1f}°)")
            return mejor_match
        else:
            # Si no hay coincidencia, usar la primera altura disponible
            logger.warning(f"No se encontró altura con azimut cercano a {azimut_pluma}°, "
                          f"usando primera altura disponible")
            return vientos[0] if vientos else None
    
    def guardar_datos_viento(
        self,
        volcan_id: int,
        datos_viento: Dict,
        altura_seleccionada: Dict
    ) -> Optional[int]:
        """
        Guarda datos de viento en la base de datos
        """
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
                fuente="NCEP_Reanalysis"
            )
            
            session.add(registro)
            session.commit()
            
            registro_id = registro.id
            logger.info(f"Datos de viento guardados con ID {registro_id}")
            
            return registro_id
            
        except Exception as e:
            logger.error(f"Error guardando datos de viento: {e}")
            session.rollback()
            return None
            
        finally:
            session.close()


def obtener_viento_para_imagen(
    volcan_id: int,
    lat: float,
    lon: float,
    fecha: datetime,
    altitud_m: float = DEFAULT_ALTITUDE_M,
    azimut_pluma: float = None
) -> Optional[Dict]:
    """
    Función principal para obtener datos de viento para una imagen TROPOMI
    
    Si se proporciona azimut_pluma, busca la altura donde el viento coincide.
    Si no, devuelve el viento de la primera altura disponible.
    
    Args:
        volcan_id: ID del volcán
        lat: Latitud del volcán
        lon: Longitud del volcán
        fecha: Fecha/hora de la imagen
        altitud_m: Altura del volcán en metros
        azimut_pluma: Azimut de la pluma (opcional)
        
    Returns:
        Diccionario con datos de viento
    """
    downloader = NCEPDownloader()
    
    # Descargar datos de viento
    datos = downloader.descargar_viento_ncep(
        lat=lat,
        lon=lon,
        fecha=fecha,
        altura_volcan=altitud_m
    )
    
    if not datos:
        logger.warning("No se pudieron descargar datos de viento")
        return None
    
    # Si se proporciona azimut, buscar altura correspondiente
    if azimut_pluma is not None:
        viento_seleccionado = downloader.buscar_altura_por_azimut(
            datos, azimut_pluma
        )
    else:
        # Usar la primera altura disponible
        viento_seleccionado = datos['vientos_por_altura'][0] if datos['vientos_por_altura'] else None
    
    if viento_seleccionado:
        # Guardar en base de datos
        downloader.guardar_datos_viento(volcan_id, datos, viento_seleccionado)
        
        return {
            'velocidad_ms': viento_seleccionado['velocidad_ms'],
            'direccion_grados': viento_seleccionado['azimut_grados'],
            'altura_m': viento_seleccionado['altura_m'],
            'nivel_presion_hpa': viento_seleccionado['nivel_presion_hpa'],
            'u_component': viento_seleccionado['u_component'],
            'v_component': viento_seleccionado['v_component']
        }
    
    return None


if __name__ == "__main__":
    # Test
    print("Test de NCEPDownloader")
    print("=" * 50)
    
    downloader = NCEPDownloader()
    
    # Test con Monte Etna
    lat = 37.751
    lon = 14.993
    fecha = datetime(2024, 1, 15, 12, 0)
    altura_volcan = 3357
    
    print(f"\nDescargando vientos para Etna ({lat}, {lon})")
    print(f"Fecha: {fecha}")
    print(f"Altura volcán: {altura_volcan}m")
    
    datos = downloader.descargar_viento_ncep(lat, lon, fecha, altura_volcan)
    
    if datos:
        print(f"\nVientos obtenidos:")
        for v in datos['vientos_por_altura']:
            print(f"  {v['altura_m']}m: {v['velocidad_ms']:.1f} m/s, azimut {v['azimut_grados']:.1f}°")
