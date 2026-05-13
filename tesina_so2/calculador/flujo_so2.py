"""
Calculador de Flujo SO2

CORRECCIÓN IMPORTANTE:
El flujo se calcula iterando por alturas de viento disponibles y 
seleccionando aquella donde se detecta una pluma coherente.

El flujo se calcula como:
    Flujo (kg/s) = SO2_max (kg/m²) × distancia (m) × velocidad_viento (m/s)

Para 6 franjas horarias (0.5h, 1h, 1.5h, 2h, 2.5h, 3h) desde el punto de emisión.
El flujo diario es el promedio de las franjas válidas, convertido a ton/día.
"""
import numpy as np
from datetime import datetime, date
from typing import Optional, Dict, List
import logging

from database import (
    get_session, ResultadoFlujoSO2, ImagenTROPOMI,
    Volcan, LogProcesamiento
)
from etl.tropomi_processor import TROPOMIProcessor
from etl.ncep_downloader import NCEPDownloader
from config.settings import SO2FC_FRANJAS_HORAS, KGS_TO_TD_FACTOR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def to_float(value) -> Optional[float]:
    """Convierte a float nativo de Python"""
    if value is None:
        return None
    try:
        result = float(value)
        if np.isnan(result) or np.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def to_int(value) -> Optional[int]:
    """Convierte a int nativo de Python"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def convertir_a_serializable(obj):
    """Convierte recursivamente a tipos serializables JSON"""
    if obj is None:
        return None
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        val = float(obj)
        return None if (np.isnan(val) or np.isinf(val)) else val
    elif isinstance(obj, np.ndarray):
        return [convertir_a_serializable(x) for x in obj.tolist()]
    elif isinstance(obj, dict):
        return {k: convertir_a_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convertir_a_serializable(item) for item in obj]
    elif isinstance(obj, (str, int, bool)):
        return obj
    elif isinstance(obj, float):
        return None if (np.isnan(obj) or np.isinf(obj)) else obj
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    else:
        try:
            return str(obj)
        except:
            return None


class CalculadorFlujoSO2:
    """
    Calculador de flujo SO2 usando método SO2FC
    
    Franjas horarias: 0.5, 1.0, 1.5, 2.0, 2.5, 3.0 horas desde el volcán
    """
    
    def __init__(self):
        self.session = get_session()
    
    def procesar_imagen(
        self,
        imagen_id: int
    ) -> Optional[Dict]:
        """
        Procesa una imagen TROPOMI completa usando método SO2FC
        
        MÉTODO CORREGIDO:
        1. Obtener datos de la imagen y volcán
        2. Descargar vientos para TODAS las alturas disponibles
        3. Para cada altura, verificar si hay pluma en la dirección del viento
        4. Usar la altura donde se encuentra la mejor coincidencia
        5. Calcular flujo para cada franja horaria
        6. Guardar resultados
        """
        try:
            # Obtener imagen y volcán
            imagen = self.session.query(ImagenTROPOMI).get(imagen_id)
            if not imagen:
                logger.error(f"Imagen {imagen_id} no encontrada")
                return {'exito': False, 'mensaje': 'Imagen no encontrada', 'imagen_id': imagen_id}
            
            if not imagen.descargado or not imagen.ruta_archivo:
                logger.error(f"Imagen {imagen_id} no está descargada")
                return {'exito': False, 'mensaje': 'Imagen no descargada', 'imagen_id': imagen_id}
            
            volcan = self.session.query(Volcan).get(imagen.volcan_id)
            if not volcan:
                logger.error(f"Volcán {imagen.volcan_id} no encontrado")
                return {'exito': False, 'mensaje': 'Volcán no encontrado', 'imagen_id': imagen_id}
            
            logger.info(f"Procesando imagen {imagen_id} para {volcan.nombre}")
            logger.info(f"Fecha: {imagen.fecha_adquisicion}")
            logger.info(f"Volcán: ({volcan.latitud}, {volcan.longitud}), altura: {volcan.altitud_m}m")
            
            # Crear procesador TROPOMI
            processor = TROPOMIProcessor(imagen.ruta_archivo)
            
            try:
                # Paso 1: Descargar vientos para TODAS las alturas
                altura_volcan = volcan.altitud_m or 3000
                ncep = NCEPDownloader()
                
                datos_viento = ncep.descargar_viento_ncep(
                    lat=volcan.latitud,
                    lon=volcan.longitud,
                    fecha=imagen.fecha_adquisicion,
                    altura_volcan=altura_volcan
                )
                
                if not datos_viento or not datos_viento.get('vientos_por_altura'):
                    logger.warning("No se pudieron obtener datos de viento")
                    return {
                        'exito': False,
                        'mensaje': 'No se pudieron obtener datos de viento',
                        'imagen_id': imagen_id
                    }
                
                vientos = datos_viento['vientos_por_altura']
                logger.info(f"Vientos disponibles para {len(vientos)} alturas")
                
                # Paso 2: PRIMERO detectar la pluma SIN restricción de dirección
                logger.info("Detectando azimut de pluma...")
                azimut_pluma = processor.detectar_azimut_pluma(
                    volcan.latitud, volcan.longitud
                )
                
                if azimut_pluma is None:
                    return {
                        'exito': False,
                        'mensaje': 'No se detectó pluma de SO2',
                        'imagen_id': imagen_id
                    }
                
                logger.info(f"Azimut de pluma detectado: {azimut_pluma:.1f}°")
                
                # Paso 3: Buscar la altura de viento que coincide con el azimut de la pluma
                logger.info(f"Buscando altura de viento que coincida con azimut {azimut_pluma:.1f}°...")
                
                mejor_viento = None
                menor_diferencia = float('inf')
                
                for viento in vientos:
                    # Convertir dirección matemática a azimut geográfico
                    dir_mat = viento['direccion_matematica']
                    azimut_viento = 90 - dir_mat
                    if azimut_viento < 0:
                        azimut_viento += 360
                    if azimut_viento >= 360:
                        azimut_viento -= 360
                    
                    viento['azimut_grados'] = azimut_viento
                    
                    # Calcular diferencia angular
                    dif = abs(azimut_viento - azimut_pluma)
                    if dif > 180:
                        dif = 360 - dif
                    
                    logger.info(f"  Altura {viento['altura_m']}m: viento hacia {azimut_viento:.1f}°, "
                               f"vel={viento['velocidad_ms']:.1f} m/s, dif={dif:.1f}°")
                    
                    if dif < menor_diferencia:
                        menor_diferencia = dif
                        mejor_viento = viento
                
                if mejor_viento is None:
                    mejor_viento = vientos[0]
                    mejor_viento['azimut_grados'] = 90 - mejor_viento['direccion_matematica']
                
                logger.info(f"✓ Altura seleccionada: {mejor_viento['altura_m']}m "
                           f"(dif={menor_diferencia:.1f}°, vel={mejor_viento['velocidad_ms']:.1f} m/s)")
                
                # Paso 4: Calcular flujo con el viento seleccionado
                mejor_resultado = processor.calcular_so2_por_franja_horaria(
                    lat_volcan=volcan.latitud,
                    lon_volcan=volcan.longitud,
                    velocidad_viento_ms=mejor_viento['velocidad_ms'],
                    azimut_pluma=azimut_pluma,
                    horas=SO2FC_FRANJAS_HORAS
                )
                
                if not mejor_resultado or not mejor_resultado.get('exito'):
                    return {
                        'exito': False,
                        'mensaje': 'No se pudo calcular flujo de SO2',
                        'imagen_id': imagen_id
                    }
                
                # Agregar info adicional
                mejor_resultado['imagen_id'] = imagen_id
                mejor_resultado['volcan_id'] = volcan.id
                mejor_resultado['volcan_nombre'] = volcan.nombre
                mejor_resultado['fecha_hora'] = imagen.fecha_adquisicion
                mejor_resultado['altura_viento_m'] = mejor_viento.get('altura_m')
                mejor_resultado['direccion_viento_grados'] = mejor_viento.get('azimut_grados')
                
                logger.info(f"\n✓ Flujo calculado: {mejor_resultado['flujo_promedio_td']:.2f} ton/día")
                logger.info(f"  Altura viento seleccionada: {mejor_viento.get('altura_m')}m")
                logger.info(f"  Velocidad viento: {mejor_viento.get('velocidad_ms'):.1f} m/s")
                logger.info(f"  Franjas válidas: {mejor_resultado.get('n_franjas_validas')}/6")
                
                # Guardar datos de viento
                ncep.guardar_datos_viento(volcan.id, datos_viento, mejor_viento)
                
                # Guardar resultado
                resultado_id = self.guardar_resultado(
                    volcan_id=volcan.id,
                    imagen_id=imagen_id,
                    fecha_hora=imagen.fecha_adquisicion,
                    resultado=mejor_resultado
                )
                
                mejor_resultado['resultado_id'] = resultado_id
                
                # Marcar imagen como procesada
                imagen.procesado = True
                self.session.commit()
                
                return mejor_resultado
                
            finally:
                processor.cerrar()
                
        except Exception as e:
            logger.error(f"Error procesando imagen {imagen_id}: {e}")
            import traceback
            traceback.print_exc()
            return {
                'exito': False,
                'mensaje': str(e),
                'imagen_id': imagen_id
            }
    
    def guardar_resultado(
        self,
        volcan_id: int,
        imagen_id: int,
        fecha_hora: datetime,
        resultado: Dict
    ) -> Optional[int]:
        """
        Guarda el resultado del cálculo en la base de datos
        """
        if not resultado.get('exito'):
            return None
        
        try:
            # Determinar calidad
            n_franjas = resultado.get('n_franjas_validas', 0)
            if n_franjas >= 5:
                qa_flag = 0  # Bueno
            elif n_franjas >= 3:
                qa_flag = 1  # Aceptable
            else:
                qa_flag = 2  # Malo
            
            # Calcular incertidumbre aproximada
            flujos = [f['flujo_kgs'] for f in resultado.get('franjas', []) 
                     if f.get('flujo_kgs') is not None]
            if len(flujos) > 1:
                incertidumbre_pct = (np.std(flujos) / np.mean(flujos)) * 100
            else:
                incertidumbre_pct = 50  # Default
            
            registro = ResultadoFlujoSO2(
                volcan_id=to_int(volcan_id),
                imagen_id=to_int(imagen_id),
                fecha_hora=fecha_hora,
                flujo_so2_kg_s=to_float(resultado.get('flujo_promedio_kgs')),
                flujo_so2_ton_dia=to_float(resultado.get('flujo_promedio_td')),
                columna_so2_max=to_float(resultado.get('so2_total_molm2')),
                columna_so2_total=to_float(resultado.get('so2_total_molm2')),
                velocidad_viento_ms=to_float(resultado.get('velocidad_viento_ms')),
                direccion_viento_grados=to_float(resultado.get('direccion_viento_grados')),
                altitud_viento_m=to_float(resultado.get('altura_viento_m')),
                azimut_pluma_grados=to_float(resultado.get('azimut_pluma')),
                distancia_volcan_km=None,
                ancho_pluma_km=None,
                incertidumbre_pct=to_float(incertidumbre_pct),
                qa_flag=to_int(qa_flag),
                metadatos_json=convertir_a_serializable(resultado)
            )
            
            self.session.add(registro)
            self.session.commit()
            
            logger.info(f"Resultado guardado con ID {registro.id}")
            return registro.id
            
        except Exception as e:
            logger.error(f"Error guardando resultado: {e}")
            import traceback
            traceback.print_exc()
            self.session.rollback()
            return None
    
    def obtener_resultados_diarios(
        self,
        volcan_id: int,
        fecha_inicio: datetime,
        fecha_fin: datetime
    ) -> List[Dict]:
        """
        Obtiene resultados agrupados por día (como en MATLAB)
        """
        resultados = self.session.query(ResultadoFlujoSO2).filter(
            ResultadoFlujoSO2.volcan_id == volcan_id,
            ResultadoFlujoSO2.fecha_hora >= fecha_inicio,
            ResultadoFlujoSO2.fecha_hora <= fecha_fin
        ).order_by(ResultadoFlujoSO2.fecha_hora).all()
        
        # Agrupar por día
        por_dia = {}
        for r in resultados:
            fecha = r.fecha_hora.date()
            if fecha not in por_dia:
                por_dia[fecha] = []
            por_dia[fecha].append(r)
        
        # Calcular promedio por día
        resultados_diarios = []
        for fecha, registros in sorted(por_dia.items()):
            flujos_td = [r.flujo_so2_ton_dia for r in registros 
                        if r.flujo_so2_ton_dia is not None]
            
            if flujos_td:
                flujo_promedio = np.mean(flujos_td)
                flujo_std = np.std(flujos_td) if len(flujos_td) > 1 else 0
                
                resultados_diarios.append({
                    'fecha': fecha.isoformat(),
                    'flujo_td': to_float(flujo_promedio),
                    'flujo_std': to_float(flujo_std),
                    'n_mediciones': len(flujos_td),
                    'mediciones': [
                        {
                            'hora': r.fecha_hora.strftime('%H:%M'),
                            'flujo_td': to_float(r.flujo_so2_ton_dia),
                            'qa_flag': r.qa_flag
                        }
                        for r in registros
                    ]
                })
        
        return resultados_diarios
    
    def obtener_serie_temporal(
        self,
        volcan_id: int,
        fecha_inicio: datetime,
        fecha_fin: datetime
    ) -> List[Dict]:
        """
        Obtiene la serie temporal de flujos de SO2 para un volcán
        """
        resultados = self.session.query(ResultadoFlujoSO2).filter(
            ResultadoFlujoSO2.volcan_id == volcan_id,
            ResultadoFlujoSO2.fecha_hora >= fecha_inicio,
            ResultadoFlujoSO2.fecha_hora <= fecha_fin
        ).order_by(ResultadoFlujoSO2.fecha_hora).all()
        
        serie = []
        for r in resultados:
            serie.append({
                'id': r.id,
                'fecha_hora': r.fecha_hora.isoformat(),
                'flujo_kg_s': to_float(r.flujo_so2_kg_s),
                'flujo_ton_dia': to_float(r.flujo_so2_ton_dia),
                'so2_max': to_float(r.columna_so2_max),
                'velocidad_viento_ms': to_float(r.velocidad_viento_ms),
                'direccion_viento_grados': to_float(r.direccion_viento_grados),
                'incertidumbre_pct': to_float(r.incertidumbre_pct),
                'qa_flag': to_int(r.qa_flag)
            })
        
        return serie
    
    def cerrar(self):
        """Cierra la sesión"""
        self.session.close()


def procesar_imagen_completa(imagen_id: int, altitud_viento_m: float = None) -> Optional[Dict]:
    """
    Función de conveniencia para procesar una imagen
    """
    calculador = CalculadorFlujoSO2()
    try:
        return calculador.procesar_imagen(imagen_id)
    finally:
        calculador.cerrar()


def procesar_multiples_imagenes(imagen_ids: List[int]) -> List[Dict]:
    """
    Procesa múltiples imágenes
    """
    calculador = CalculadorFlujoSO2()
    resultados = []
    
    try:
        for imagen_id in imagen_ids:
            logger.info(f"\n{'='*50}")
            logger.info(f"Procesando imagen {imagen_id}")
            resultado = calculador.procesar_imagen(imagen_id)
            resultados.append(resultado)
    finally:
        calculador.cerrar()
    
    return resultados


if __name__ == "__main__":
    print("Calculador de Flujo SO2 - Método SO2FC")
    print("=" * 50)
    print("\nMétodo basado en Carbajal et al. (SEGEMAR/OAVV)")
    print(f"\nFranjas horarias: {SO2FC_FRANJAS_HORAS}")
    print(f"\nFórmula: Flujo (kg/s) = SO2_max × distancia × velocidad_viento")
    print(f"Conversión: ton/día = kg/s × {KGS_TO_TD_FACTOR}")