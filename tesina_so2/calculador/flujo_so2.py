"""
Módulo Calculador de Flujo de SO2
Implementa el algoritmo de cálculo de flujo basado en sección transversal
según la metodología de Merucci et al. (2011) y Theys et al. (2019)
"""
import numpy as np
from datetime import datetime
from typing import Optional, Dict, List, Tuple
import logging

from database import (
    get_session, ResultadoFlujoSO2, ImagenTROPOMI, 
    DatosViento, Volcan, LogProcesamiento
)
from etl.tropomi_processor import TROPOMIProcessor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CalculadorFlujoSO2:
    """
    Calculador de flujo de SO2 volcánico
    
    El flujo se calcula como:
        Φ = VCD_integral × v_wind
        
    donde:
        - VCD_integral: Integral de la columna vertical de SO2 a través de 
                       una sección transversal perpendicular al viento (mol/m)
        - v_wind: Velocidad del viento (m/s)
        
    El resultado Φ está en mol/s, que se convierte a kg/s y ton/día
    """
    
    # Constantes
    AVOGADRO = 6.02214076e23  # mol^-1
    MASA_MOLAR_SO2 = 64.066e-3  # kg/mol (64.066 g/mol)
    SEGUNDOS_POR_DIA = 86400
    KG_A_TON = 1e-3
    
    def __init__(self):
        self.session = get_session()
    
    def calcular_flujo(
        self,
        integral_so2_mol_m: float,
        velocidad_viento_ms: float,
        incertidumbre_vcd_pct: float = 30,
        incertidumbre_viento_pct: float = 20
    ) -> Dict:
        """
        Calcula el flujo de SO2 a partir de la integral de VCD y velocidad del viento
        
        Φ (mol/s) = VCD_integral (mol/m) × v_wind (m/s)
        
        Args:
            integral_so2_mol_m: Integral de SO2 a través de la sección (mol/m)
            velocidad_viento_ms: Velocidad del viento (m/s)
            incertidumbre_vcd_pct: Incertidumbre del VCD (%)
            incertidumbre_viento_pct: Incertidumbre del viento (%)
            
        Returns:
            Diccionario con flujo en diferentes unidades e incertidumbre
        """
        # Flujo en mol/s
        flujo_mol_s = integral_so2_mol_m * velocidad_viento_ms
        
        # Convertir a kg/s
        flujo_kg_s = flujo_mol_s * self.MASA_MOLAR_SO2
        
        # Convertir a toneladas/día
        flujo_ton_dia = flujo_kg_s * self.SEGUNDOS_POR_DIA * self.KG_A_TON
        
        # Calcular incertidumbre (propagación de errores)
        # δΦ/Φ = sqrt((δVCD/VCD)² + (δv/v)²)
        incertidumbre_relativa = np.sqrt(
            (incertidumbre_vcd_pct/100)**2 + 
            (incertidumbre_viento_pct/100)**2
        )
        incertidumbre_pct = incertidumbre_relativa * 100
        
        return {
            'flujo_mol_s': flujo_mol_s,
            'flujo_kg_s': flujo_kg_s,
            'flujo_ton_dia': flujo_ton_dia,
            'incertidumbre_pct': incertidumbre_pct,
            'incertidumbre_kg_s': flujo_kg_s * incertidumbre_relativa,
            'incertidumbre_ton_dia': flujo_ton_dia * incertidumbre_relativa
        }
    
    def calcular_flujo_desde_imagen(
        self,
        ruta_imagen: str,
        lat_volcan: float,
        lon_volcan: float,
        velocidad_viento_ms: float,
        direccion_viento_grados: float,
        distancias_km: List[float] = None,
        ancho_seccion_km: float = 50,
        qa_threshold: float = 0.5
    ) -> Dict:
        """
        Calcula el flujo de SO2 a partir de una imagen TROPOMI
        
        Args:
            ruta_imagen: Ruta al archivo NetCDF
            lat_volcan: Latitud del volcán
            lon_volcan: Longitud del volcán
            velocidad_viento_ms: Velocidad del viento en m/s
            direccion_viento_grados: Dirección del viento (de donde viene)
            distancias_km: Lista de distancias para calcular secciones
            ancho_seccion_km: Ancho de la sección transversal
            qa_threshold: Umbral de calidad
            
        Returns:
            Diccionario con resultados del cálculo
        """
        if distancias_km is None:
            distancias_km = [10, 20, 30, 40, 50]  # km desde el volcán
        
        # Procesar imagen
        processor = TROPOMIProcessor(ruta_imagen)
        
        try:
            # Filtrar por calidad
            so2_filtrado = processor.filtrar_por_calidad(qa_threshold)
            
            # Detectar pluma
            pluma = processor.detectar_pluma(lat_volcan, lon_volcan)
            
            if not pluma or not pluma.get('detectada'):
                logger.warning("No se detectó pluma de SO2")
                return {
                    'exito': False,
                    'mensaje': 'No se detectó pluma de SO2',
                    'pluma_detectada': False
                }
            
            # Calcular flujo en múltiples secciones
            flujos = []
            secciones = []
            
            for distancia in distancias_km:
                # Calcular sección transversal
                seccion = processor.calcular_seccion_transversal(
                    lat_volcan=lat_volcan,
                    lon_volcan=lon_volcan,
                    direccion_viento_grados=direccion_viento_grados,
                    distancia_km=distancia,
                    ancho_seccion_km=ancho_seccion_km
                )
                
                if not seccion or seccion.get('integral_so2_mol_m', 0) == 0:
                    continue
                
                # Calcular flujo para esta sección
                flujo = self.calcular_flujo(
                    integral_so2_mol_m=seccion['integral_so2_mol_m'],
                    velocidad_viento_ms=velocidad_viento_ms
                )
                
                flujo['distancia_km'] = distancia
                flujos.append(flujo)
                secciones.append(seccion)
            
            if not flujos:
                return {
                    'exito': False,
                    'mensaje': 'No se pudo calcular flujo en ninguna sección',
                    'pluma_detectada': True
                }
            
            # Calcular flujo promedio y estadísticas
            flujos_kg_s = [f['flujo_kg_s'] for f in flujos]
            flujos_ton_dia = [f['flujo_ton_dia'] for f in flujos]
            
            flujo_promedio_kg_s = np.mean(flujos_kg_s)
            flujo_promedio_ton_dia = np.mean(flujos_ton_dia)
            flujo_std_kg_s = np.std(flujos_kg_s)
            
            # Usar el flujo de la sección óptima (donde hay más señal)
            mejor_seccion_idx = np.argmax([s.get('so2_max', 0) for s in secciones])
            flujo_optimo = flujos[mejor_seccion_idx]
            seccion_optima = secciones[mejor_seccion_idx]
            
            return {
                'exito': True,
                'pluma_detectada': True,
                'pluma': {
                    'azimut': pluma.get('azimut_grados'),
                    'distancia_max_km': pluma.get('distancia_max_km'),
                    'so2_max': pluma.get('so2_max'),
                    'so2_total': pluma.get('so2_total')
                },
                'flujo_optimo': {
                    'flujo_kg_s': flujo_optimo['flujo_kg_s'],
                    'flujo_ton_dia': flujo_optimo['flujo_ton_dia'],
                    'incertidumbre_pct': flujo_optimo['incertidumbre_pct'],
                    'distancia_km': flujo_optimo['distancia_km']
                },
                'flujo_promedio': {
                    'flujo_kg_s': flujo_promedio_kg_s,
                    'flujo_ton_dia': flujo_promedio_ton_dia,
                    'std_kg_s': flujo_std_kg_s
                },
                'todos_flujos': flujos,
                'secciones': secciones,
                'viento': {
                    'velocidad_ms': velocidad_viento_ms,
                    'direccion_grados': direccion_viento_grados
                },
                'parametros': {
                    'distancias_km': distancias_km,
                    'ancho_seccion_km': ancho_seccion_km,
                    'qa_threshold': qa_threshold
                }
            }
            
        finally:
            processor.cerrar()
    
    def guardar_resultado(
        self,
        volcan_id: int,
        imagen_id: int,
        fecha_hora: datetime,
        resultado: Dict
    ) -> Optional[int]:
        """
        Guarda el resultado del cálculo de flujo en la base de datos
        
        Args:
            volcan_id: ID del volcán
            imagen_id: ID de la imagen TROPOMI
            fecha_hora: Fecha y hora de la medición
            resultado: Diccionario con resultados del cálculo
            
        Returns:
            ID del registro creado o None si falla
        """
        if not resultado.get('exito'):
            logger.warning("No se guardará resultado fallido")
            return None
        
        try:
            # Extraer datos del flujo óptimo
            flujo_optimo = resultado.get('flujo_optimo', {})
            pluma = resultado.get('pluma', {})
            viento = resultado.get('viento', {})
            
            # Determinar flag de calidad
            # 0 = bueno (incertidumbre < 50%)
            # 1 = aceptable (50-100%)
            # 2 = malo (> 100%)
            incertidumbre = flujo_optimo.get('incertidumbre_pct', 100)
            if incertidumbre < 50:
                qa_flag = 0
            elif incertidumbre < 100:
                qa_flag = 1
            else:
                qa_flag = 2
            
            # Crear registro
            registro = ResultadoFlujoSO2(
                volcan_id=volcan_id,
                imagen_id=imagen_id,
                fecha_hora=fecha_hora,
                flujo_so2_kg_s=flujo_optimo.get('flujo_kg_s'),
                flujo_so2_ton_dia=flujo_optimo.get('flujo_ton_dia'),
                columna_so2_max=pluma.get('so2_max'),
                columna_so2_total=pluma.get('so2_total'),
                velocidad_viento_ms=viento.get('velocidad_ms'),
                direccion_viento_grados=viento.get('direccion_grados'),
                ancho_pluma_km=resultado.get('parametros', {}).get('ancho_seccion_km'),
                distancia_volcan_km=flujo_optimo.get('distancia_km'),
                azimut_pluma_grados=pluma.get('azimut'),
                incertidumbre_pct=incertidumbre,
                qa_flag=qa_flag,
                metadatos_json=resultado
            )
            
            self.session.add(registro)
            self.session.commit()
            
            logger.info(f"Resultado guardado con ID {registro.id}")
            return registro.id
            
        except Exception as e:
            logger.error(f"Error guardando resultado: {e}")
            self.session.rollback()
            return None
    
    def obtener_serie_temporal(
        self,
        volcan_id: int,
        fecha_inicio: datetime,
        fecha_fin: datetime
    ) -> List[Dict]:
        """
        Obtiene la serie temporal de flujos de SO2 para un volcán
        
        Args:
            volcan_id: ID del volcán
            fecha_inicio: Fecha inicial
            fecha_fin: Fecha final
            
        Returns:
            Lista de diccionarios con datos de flujo
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
                'flujo_kg_s': r.flujo_so2_kg_s,
                'flujo_ton_dia': r.flujo_so2_ton_dia,
                'so2_max': r.columna_so2_max,
                'velocidad_viento_ms': r.velocidad_viento_ms,
                'direccion_viento_grados': r.direccion_viento_grados,
                'incertidumbre_pct': r.incertidumbre_pct,
                'qa_flag': r.qa_flag
            })
        
        return serie
    
    def cerrar(self):
        """Cierra la sesión de base de datos"""
        self.session.close()


def procesar_imagen_completa(
    imagen_id: int,
    altitud_viento_m: float = 3000
) -> Optional[Dict]:
    """
    Procesa una imagen TROPOMI completa: descarga viento, calcula flujo y guarda
    
    Args:
        imagen_id: ID de la imagen en la base de datos
        altitud_viento_m: Altitud para extraer datos de viento
        
    Returns:
        Diccionario con resultados o None si falla
    """
    from etl.era5_downloader import obtener_viento_para_imagen
    
    session = get_session()
    
    try:
        # Obtener imagen y volcán
        imagen = session.query(ImagenTROPOMI).get(imagen_id)
        if not imagen:
            logger.error(f"Imagen {imagen_id} no encontrada")
            return None
        
        if not imagen.descargado or not imagen.ruta_archivo:
            logger.error(f"Imagen {imagen_id} no está descargada")
            return None
        
        volcan = session.query(Volcan).get(imagen.volcan_id)
        
        # Obtener datos de viento
        viento = obtener_viento_para_imagen(
            volcan_id=volcan.id,
            lat=volcan.latitud,
            lon=volcan.longitud,
            fecha=imagen.fecha_adquisicion,
            altitud_m=altitud_viento_m
        )
        
        if not viento:
            logger.warning("No se pudieron obtener datos de viento")
            return None
        
        # Calcular flujo
        calculador = CalculadorFlujoSO2()
        
        resultado = calculador.calcular_flujo_desde_imagen(
            ruta_imagen=imagen.ruta_archivo,
            lat_volcan=volcan.latitud,
            lon_volcan=volcan.longitud,
            velocidad_viento_ms=viento['velocidad_ms'],
            direccion_viento_grados=viento['direccion_grados']
        )
        
        # Guardar resultado
        if resultado.get('exito'):
            resultado_id = calculador.guardar_resultado(
                volcan_id=volcan.id,
                imagen_id=imagen.id,
                fecha_hora=imagen.fecha_adquisicion,
                resultado=resultado
            )
            resultado['resultado_id'] = resultado_id
            
            # Marcar imagen como procesada
            imagen.procesado = True
            session.commit()
        
        calculador.cerrar()
        
        # Registrar log
        log = LogProcesamiento(
            nivel="INFO" if resultado.get('exito') else "WARNING",
            componente="CALCULADOR",
            mensaje=f"Procesamiento de imagen {imagen_id} completado",
            detalles_json={
                'imagen_id': imagen_id,
                'volcan': volcan.nombre,
                'exito': resultado.get('exito'),
                'flujo_ton_dia': resultado.get('flujo_optimo', {}).get('flujo_ton_dia')
            }
        )
        session.add(log)
        session.commit()
        
        return resultado
        
    except Exception as e:
        logger.error(f"Error procesando imagen {imagen_id}: {e}")
        return None
        
    finally:
        session.close()


if __name__ == "__main__":
    # Ejemplo de uso
    print("Módulo Calculador de Flujo SO2")
    print("=" * 50)
    
    calculador = CalculadorFlujoSO2()
    
    # Ejemplo de cálculo directo
    print("\nEjemplo de cálculo de flujo:")
    print("-" * 30)
    
    # Valores típicos para volcán activo
    integral_so2 = 0.5  # mol/m (ejemplo)
    velocidad_viento = 10  # m/s
    
    resultado = calculador.calcular_flujo(
        integral_so2_mol_m=integral_so2,
        velocidad_viento_ms=velocidad_viento
    )
    
    print(f"Integral SO2: {integral_so2} mol/m")
    print(f"Velocidad viento: {velocidad_viento} m/s")
    print(f"\nFlujo SO2:")
    print(f"  {resultado['flujo_kg_s']:.2f} kg/s")
    print(f"  {resultado['flujo_ton_dia']:.1f} ton/día")
    print(f"  Incertidumbre: ±{resultado['incertidumbre_pct']:.1f}%")
    
    calculador.cerrar()
