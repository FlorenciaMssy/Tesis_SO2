"""
Procesador de imágenes TROPOMI GeoTIFF - Método SO2FC
Replica EXACTAMENTE el procesamiento del código MATLAB del profesor Carbajal.

Este módulo procesa los archivos GeoTIFF exportados desde GEE,
que es el formato que usa el profesor en su rutina MATLAB.

Basado en: AnalisisS02_CD_v4.m (líneas 201-220)
    [SO2_Molm2, R] = geotiffread([path '\' listIm(i).name]);
    SO2_gm2 = SO2_Molm2 / 0.0156;
    SO2_kgm2 = SO2_gm2 * 0.001;
    SO2_Selec(SO2_Molm2<=0) = NaN;
"""
import numpy as np
import rasterio
from rasterio.transform import xy
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import logging
import math

from config.settings import (
    SO2FC_FRANJAS_HORAS, SO2FC_TOLERANCIA_HORA, SO2FC_TOLERANCIA_AZIMUT,
    SO2FC_DISTANCIA_REFERENCIA, KGS_TO_TD_FACTOR, SO2FC_RADIO_MAXIMO_KM
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Constantes de conversión (EXACTAS del MATLAB líneas 213-215)
# SO2_gm2 = SO2_Molm2 / 0.0156
# SO2_kgm2 = SO2_gm2 * 0.001
MOLM2_TO_GM2_FACTOR = 1 / 0.0156  # = 64.1025...
GM2_TO_KGM2_FACTOR = 0.001


class GeoTIFFProcessor:
    """
    Procesador de imágenes TROPOMI en formato GeoTIFF.
    
    Lee archivos GeoTIFF exportados desde GEE y los procesa
    EXACTAMENTE igual que el código MATLAB del profesor.
    
    Ventajas sobre NetCDF:
    - Grilla regular (ya reproyectada por GEE)
    - Resolución fija (1000m)
    - Coordenadas en EPSG:4326
    - Más simple de procesar
    """
    
    EARTH_RADIUS_M = 6371000
    
    def __init__(self, ruta_archivo: str):
        """
        Inicializa el procesador con un archivo GeoTIFF.
        
        Args:
            ruta_archivo: Ruta al archivo .tif
        """
        self.ruta_archivo = Path(ruta_archivo)
        self.dataset = None
        self.so2_molm2 = None
        self.so2_gm2 = None
        self.so2_kgm2 = None
        self.lat = None
        self.lon = None
        self.transform = None
        self.crs = None
        self.fecha = None
        
        self._cargar_datos()
    
    def _cargar_datos(self):
        """
        Carga los datos del GeoTIFF.
        
        Replica las líneas 201-220 del MATLAB:
            [SO2_Molm2, R] = geotiffread([path '\' listIm(i).name]);
            SO2_Molm2 = double(SO2_Molm2);
            SO2_gm2 = SO2_Molm2 / 0.0156;
            SO2_kgm2 = SO2_gm2 * 0.001;
            SO2_Selec(SO2_Molm2<=0) = NaN;
        """
        try:
            # Abrir con rasterio (equivalente a geotiffread)
            self.dataset = rasterio.open(self.ruta_archivo)
            
            # Leer banda 1 (SO2 en mol/m²)
            self.so2_molm2 = self.dataset.read(1).astype(np.float64)
            
            logger.info(f"GeoTIFF abierto: {self.ruta_archivo.name}")
            logger.info(f"Shape: {self.so2_molm2.shape}")
            logger.info(f"CRS: {self.dataset.crs}")
            
            # Guardar transformación y CRS
            self.transform = self.dataset.transform
            self.crs = self.dataset.crs
            
            # Convertir a g/m² y kg/m² (EXACTO como MATLAB)
            self.so2_gm2 = self.so2_molm2 * MOLM2_TO_GM2_FACTOR
            self.so2_kgm2 = self.so2_gm2 * GM2_TO_KGM2_FACTOR
            
            # Valores <= 0 = NaN (EXACTO como MATLAB: SO2_Selec(SO2_Molm2<=0)=NaN)
            self.so2_molm2 = np.where(self.so2_molm2 > 0, self.so2_molm2, np.nan)
            self.so2_gm2 = np.where(self.so2_gm2 > 0, self.so2_gm2, np.nan)
            self.so2_kgm2 = np.where(self.so2_kgm2 > 0, self.so2_kgm2, np.nan)
            
            # Crear matrices de coordenadas (como MATLAB líneas 301-307)
            # [LonSO2, LatSO2] = meshgrid(Longitudes, Latitudes);
            self._crear_grillas_coordenadas()
            
            # Extraer fecha del nombre de archivo
            self._extraer_fecha()
            
            # Log estadísticas
            valid = self.so2_kgm2[~np.isnan(self.so2_kgm2)]
            if len(valid) > 0:
                logger.info(f"SO2 kg/m²: min={np.min(valid):.2e}, max={np.max(valid):.2e}, mean={np.mean(valid):.2e}")
            
        except Exception as e:
            logger.error(f"Error cargando GeoTIFF: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def _crear_grillas_coordenadas(self):
        """
        Crea las grillas de latitud y longitud.
        
        Replica las líneas 301-307 del MATLAB:
            Latitudes = R.LatitudeLimits(2): -R.CellExtentInLatitude: R.LatitudeLimits(1);
            Longitudes = R.LongitudeLimits(1): R.CellExtentInLongitude: R.LongitudeLimits(2);
            [LonSO2, LatSO2] = meshgrid(Longitudes, Latitudes);
        """
        rows, cols = self.so2_molm2.shape
        
        # Crear arrays de coordenadas usando la transformación
        col_indices = np.arange(cols)
        row_indices = np.arange(rows)
        
        # Obtener coordenadas del centro de cada píxel
        # La transformación de rasterio va de píxel a coordenada
        lons = np.zeros(cols)
        lats = np.zeros(rows)
        
        for c in range(cols):
            x, _ = xy(self.transform, 0, c)
            lons[c] = x
        
        for r in range(rows):
            _, y = xy(self.transform, r, 0)
            lats[r] = y
        
        # Crear meshgrid (igual que MATLAB)
        self.lon, self.lat = np.meshgrid(lons, lats)
        
        logger.info(f"Grilla creada: lat [{self.lat.min():.3f}, {self.lat.max():.3f}], "
                   f"lon [{self.lon.min():.3f}, {self.lon.max():.3f}]")
    
    def _extraer_fecha(self):
        """
        Extrae la fecha del nombre de archivo.
        
        Formato esperado: Volcan_YYYYMMDD-HHMM_VCDofSO2_TROPOMI.tif
        Replica línea 204-206 del MATLAB:
            date = strrep(listIm(i).name,'_VCDofSO2_TROPOMI.tif','');
            dateSO2 = datenum(date, 'yyyymmdd-HHMM');
        """
        try:
            nombre = self.ruta_archivo.stem
            # Formato: Sabancaya_20190701-1746_VCDofSO2_TROPOMI
            partes = nombre.split('_')
            if len(partes) >= 2:
                fecha_str = partes[1]  # 20190701-1746
                self.fecha = datetime.strptime(fecha_str, '%Y%m%d-%H%M')
                logger.info(f"Fecha extraída: {self.fecha}")
        except Exception as e:
            logger.warning(f"No se pudo extraer fecha del nombre: {e}")
            self.fecha = None
    
    def calcular_distancia_metros(
        self,
        lat_centro: float,
        lon_centro: float
    ) -> np.ndarray:
        """
        Calcula la distancia en metros de cada píxel al volcán.
        
        Usa fórmula de Haversine (igual que MATLAB con distance()).
        """
        lat_rad = np.radians(self.lat)
        lon_rad = np.radians(self.lon)
        lat_centro_rad = np.radians(lat_centro)
        lon_centro_rad = np.radians(lon_centro)
        
        dlat = lat_rad - lat_centro_rad
        dlon = lon_rad - lon_centro_rad
        
        a = np.sin(dlat/2)**2 + np.cos(lat_centro_rad) * np.cos(lat_rad) * np.sin(dlon/2)**2
        c = 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        
        distancia_m = self.EARTH_RADIUS_M * c
        
        return distancia_m
    
    def calcular_azimut(
        self,
        lat_centro: float,
        lon_centro: float
    ) -> np.ndarray:
        """
        Calcula el azimut geográfico desde el volcán a cada píxel.
        
        Replica la función azimuth() de MATLAB (línea 343):
            azGrilla = azimuth(pe(1), pe(2), LatSO2, LonSO2);
        
        Azimut geográfico: 0° = Norte, 90° = Este, 180° = Sur, 270° = Oeste
        """
        dlat = self.lat - lat_centro
        dlon = self.lon - lon_centro
        
        # Azimut = atan2(dlon, dlat) normalizado a 0-360
        azimut = np.degrees(np.arctan2(dlon, dlat))
        azimut = np.where(azimut < 0, azimut + 360, azimut)
        
        return azimut
    
    def detectar_azimut_pluma(
        self,
        lat_volcan: float,
        lon_volcan: float,
        distancia_referencia_m: float = None
    ) -> Optional[float]:
        """
        Detecta el azimut de la pluma buscando el máximo de SO2.
        
        Similar al método del profesor donde busca visualmente
        la dirección de la pluma.
        
        Args:
            lat_volcan: Latitud del volcán
            lon_volcan: Longitud del volcán
            distancia_referencia_m: Distancia de referencia (default 60km)
            
        Returns:
            Azimut de la pluma en grados (0-360)
        """
        if distancia_referencia_m is None:
            distancia_referencia_m = SO2FC_DISTANCIA_REFERENCIA
        
        distancias = self.calcular_distancia_metros(lat_volcan, lon_volcan)
        azimuts = self.calcular_azimut(lat_volcan, lon_volcan)
        
        logger.info(f"Distancias: min={np.nanmin(distancias)/1000:.1f}km, max={np.nanmax(distancias)/1000:.1f}km")
        
        # Buscar máximo a ~60km (±5km)
        tolerancia_dist = 5000  # metros
        
        for dist_ref in [distancia_referencia_m, 40000, 30000, 20000, 10000]:
            mascara = np.abs(distancias - dist_ref) < tolerancia_dist
            so2_selec = np.where(mascara, self.so2_molm2, np.nan)
            
            max_so2 = np.nanmax(so2_selec)
            
            if max_so2 > 0 and not np.isnan(max_so2):
                logger.info(f"Pluma detectada a {dist_ref/1000:.0f}km, SO2_max={max_so2:.2e} mol/m²")
                break
        else:
            logger.warning("No se detectó pluma")
            return None
        
        # Encontrar ubicación del máximo
        idx_max = np.unravel_index(np.nanargmax(so2_selec), so2_selec.shape)
        azimut_pluma = azimuts[idx_max]
        
        logger.info(f"Azimut de pluma detectado: {azimut_pluma:.1f}°")
        
        return float(azimut_pluma)
    
    def calcular_so2_por_franja_horaria(
        self,
        lat_volcan: float,
        lon_volcan: float,
        velocidad_viento_ms: float,
        azimut_pluma: float,
        horas: List[float] = None,
        tolerancia_azimut: float = None,
        radio_maximo_km: float = None
    ) -> Dict:
        """
        Calcula el SO2 para cada franja horaria (método SO2FC).
        
        Este es el MISMO método que usamos para NetCDF, pero aplicado
        a GeoTIFF. La lógica es idéntica.
        
        Fórmula: Flujo (kg/s) = SO2_max (kg/m²) × distancia (m) × velocidad (m/s)
        """
        if horas is None:
            horas = SO2FC_FRANJAS_HORAS
        if tolerancia_azimut is None:
            tolerancia_azimut = SO2FC_TOLERANCIA_AZIMUT
        if radio_maximo_km is None:
            radio_maximo_km = SO2FC_RADIO_MAXIMO_KM
        
        # Calcular distancias y azimuts
        distancia_m = self.calcular_distancia_metros(lat_volcan, lon_volcan)
        azimuts = self.calcular_azimut(lat_volcan, lon_volcan)
        
        # Aplicar radio máximo (AOI)
        radio_maximo_m = radio_maximo_km * 1000
        mascara_aoi = distancia_m <= radio_maximo_m
        
        pixeles_en_aoi = np.sum(mascara_aoi)
        logger.info(f"AOI: {pixeles_en_aoi} píxeles dentro de {radio_maximo_km}km")
        
        if pixeles_en_aoi == 0:
            return {'exito': False, 'mensaje': f'No hay datos dentro de {radio_maximo_km}km'}
        
        # Aplicar máscara AOI
        so2_molm2 = np.where(mascara_aoi, self.so2_molm2, np.nan)
        so2_kgm2 = np.where(mascara_aoi, self.so2_kgm2, np.nan)
        
        # Calcular distancia temporal
        velocidad_mh = velocidad_viento_ms * 3600
        distancia_h = distancia_m / velocidad_mh
        
        # Máscara de píxeles en dirección de la pluma
        az_min = azimut_pluma - tolerancia_azimut
        az_max = azimut_pluma + tolerancia_azimut
        
        if az_min < 0:
            mascara_pluma = (azimuts >= az_min + 360) | (azimuts <= az_max)
        elif az_max > 360:
            mascara_pluma = (azimuts >= az_min) | (azimuts <= az_max - 360)
        else:
            mascara_pluma = (azimuts >= az_min) & (azimuts <= az_max)
        
        mascara_combinada = mascara_pluma & mascara_aoi
        distancia_h_pluma = np.where(mascara_combinada, distancia_h, np.nan)
        
        resultados_franjas = []
        
        for hora in horas:
            tolerancia_h = SO2FC_TOLERANCIA_HORA
            mascara_hora = (distancia_h_pluma >= hora - tolerancia_h) & \
                          (distancia_h_pluma <= hora + tolerancia_h)
            
            so2_molm2_franja = np.where(mascara_hora, so2_molm2, np.nan)
            so2_kgm2_franja = np.where(mascara_hora, so2_kgm2, np.nan)
            
            max_so2_molm2 = np.nanmax(so2_molm2_franja)
            max_so2_kgm2 = np.nanmax(so2_kgm2_franja)
            
            if max_so2_kgm2 > 0 and not np.isnan(max_so2_kgm2):
                idx_max = np.unravel_index(np.nanargmax(so2_kgm2_franja), so2_kgm2_franja.shape)
                dist_m_max = distancia_m[idx_max]
                
                flujo_kgs = max_so2_kgm2 * dist_m_max * velocidad_viento_ms
                
                resultados_franjas.append({
                    'hora': hora,
                    'so2_molm2': float(max_so2_molm2),
                    'so2_kgm2': float(max_so2_kgm2),
                    'distancia_m': float(dist_m_max),
                    'flujo_kgs': float(flujo_kgs),
                    'pixeles_encontrados': int(np.sum(mascara_hora))
                })
                
                logger.info(f"Franja {hora}h: SO2={max_so2_kgm2:.2e} kg/m², "
                           f"dist={dist_m_max/1000:.1f}km, flujo={flujo_kgs:.4f} kg/s")
            else:
                resultados_franjas.append({
                    'hora': hora,
                    'so2_molm2': None,
                    'so2_kgm2': None,
                    'distancia_m': None,
                    'flujo_kgs': None,
                    'pixeles_encontrados': 0
                })
        
        # Calcular promedios
        flujos_validos = [r['flujo_kgs'] for r in resultados_franjas if r['flujo_kgs'] is not None]
        
        if flujos_validos:
            flujo_promedio_kgs = np.mean(flujos_validos)
            flujo_promedio_td = flujo_promedio_kgs * KGS_TO_TD_FACTOR
            
            return {
                'exito': True,
                'franjas': resultados_franjas,
                'flujo_promedio_kgs': float(flujo_promedio_kgs),
                'flujo_promedio_td': float(flujo_promedio_td),
                'azimut_pluma': azimut_pluma,
                'velocidad_viento_ms': velocidad_viento_ms,
                'n_franjas_validas': len(flujos_validos)
            }
        else:
            return {
                'exito': False,
                'mensaje': 'No se encontró SO2 en ninguna franja',
                'franjas': resultados_franjas
            }
    
    def cerrar(self):
        """Cierra el dataset"""
        if self.dataset is not None:
            self.dataset.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Procesador GeoTIFF TROPOMI - Método SO2FC")
    print("Replica el procesamiento MATLAB del profesor Carbajal")
    print("=" * 60)
    
    print("\nEste módulo procesa archivos GeoTIFF exportados desde GEE")
    print("Formato esperado: Volcan_YYYYMMDD-HHMM_VCDofSO2_TROPOMI.tif")
    
    print(f"\nConstantes de conversión (igual que MATLAB):")
    print(f"  mol/m² → g/m²: × {MOLM2_TO_GM2_FACTOR:.4f} (= 1/0.0156)")
    print(f"  g/m² → kg/m²: × {GM2_TO_KGM2_FACTOR}")
    
    print(f"\nFranjas horarias: {SO2FC_FRANJAS_HORAS}")
    print(f"Tolerancia hora: ±{SO2FC_TOLERANCIA_HORA}h")
    print(f"Tolerancia azimut: ±{SO2FC_TOLERANCIA_AZIMUT}°")