"""
Sistema de Mensajería (Message Bus) usando RabbitMQ
Permite comunicación asíncrona entre componentes del sistema
"""
import json
import pika
from datetime import datetime
from typing import Callable, Dict, Optional
import logging
import threading
import time

from config.settings import (
    RABBITMQ_CONFIG, CMD_CALCULOS_FINALES, CMD_NUEVA_EXTRACCION
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MessageBus:
    """
    Cliente del Message Bus para publicar y consumir mensajes
    """
    
    QUEUE_CALCULOS = "cola_calculos"
    QUEUE_EXTRACCION = "cola_extraccion"
    
    def __init__(self):
        """Inicializa la conexión con RabbitMQ"""
        self.connection = None
        self.channel = None
        self._conectar()
    
    def _conectar(self):
        """Establece conexión con RabbitMQ"""
        try:
            credentials = pika.PlainCredentials(
                RABBITMQ_CONFIG['user'],
                RABBITMQ_CONFIG['password']
            )
            
            parameters = pika.ConnectionParameters(
                host=RABBITMQ_CONFIG['host'],
                port=RABBITMQ_CONFIG['port'],
                virtual_host=RABBITMQ_CONFIG['virtual_host'],
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300
            )
            
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            # Declarar colas
            self.channel.queue_declare(queue=self.QUEUE_CALCULOS, durable=True)
            self.channel.queue_declare(queue=self.QUEUE_EXTRACCION, durable=True)
            
            logger.info("Conexión con RabbitMQ establecida")
            
        except Exception as e:
            logger.error(f"Error conectando a RabbitMQ: {e}")
            raise
    
    def _asegurar_conexion(self):
        """Verifica y restablece la conexión si es necesario"""
        if self.connection is None or self.connection.is_closed:
            self._conectar()
        if self.channel is None or self.channel.is_closed:
            self.channel = self.connection.channel()
    
    def publicar_comando_calculos(self, imagen_id: int, parametros: Dict = None) -> bool:
        """
        Publica un comando para calcular flujo de SO2
        
        Args:
            imagen_id: ID de la imagen a procesar
            parametros: Parámetros adicionales del cálculo
            
        Returns:
            True si se publicó correctamente
        """
        mensaje = {
            'comando': CMD_CALCULOS_FINALES,
            'imagen_id': imagen_id,
            'parametros': parametros or {},
            'timestamp': datetime.utcnow().isoformat()
        }
        
        return self._publicar(self.QUEUE_CALCULOS, mensaje)
    
    def publicar_comando_extraccion(
        self,
        volcan_id: int,
        fecha_inicio: str,
        fecha_fin: str,
        parametros: Dict = None
    ) -> bool:
        """
        Publica un comando para iniciar una extracción de datos
        
        Args:
            volcan_id: ID del volcán
            fecha_inicio: Fecha inicial (ISO format)
            fecha_fin: Fecha final (ISO format)
            parametros: Parámetros adicionales
            
        Returns:
            True si se publicó correctamente
        """
        mensaje = {
            'comando': CMD_NUEVA_EXTRACCION,
            'volcan_id': volcan_id,
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'parametros': parametros or {},
            'timestamp': datetime.utcnow().isoformat()
        }
        
        return self._publicar(self.QUEUE_EXTRACCION, mensaje)
    
    def _publicar(self, queue: str, mensaje: Dict) -> bool:
        """
        Publica un mensaje en una cola
        
        Args:
            queue: Nombre de la cola
            mensaje: Diccionario con el mensaje
            
        Returns:
            True si se publicó correctamente
        """
        try:
            self._asegurar_conexion()
            
            self.channel.basic_publish(
                exchange='',
                routing_key=queue,
                body=json.dumps(mensaje),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Mensaje persistente
                    content_type='application/json'
                )
            )
            
            logger.info(f"Mensaje publicado en {queue}: {mensaje.get('comando')}")
            return True
            
        except Exception as e:
            logger.error(f"Error publicando mensaje: {e}")
            return False
    
    def consumir_calculos(self, callback: Callable[[Dict], None]):
        """
        Inicia el consumo de la cola de cálculos
        
        Args:
            callback: Función a ejecutar por cada mensaje
        """
        self._consumir(self.QUEUE_CALCULOS, callback)
    
    def consumir_extraccion(self, callback: Callable[[Dict], None]):
        """
        Inicia el consumo de la cola de extracción
        
        Args:
            callback: Función a ejecutar por cada mensaje
        """
        self._consumir(self.QUEUE_EXTRACCION, callback)
    
    def _consumir(self, queue: str, callback: Callable[[Dict], None]):
        """
        Inicia el consumo de una cola
        
        Args:
            queue: Nombre de la cola
            callback: Función a ejecutar por cada mensaje
        """
        self._asegurar_conexion()
        
        def wrapper(ch, method, properties, body):
            try:
                mensaje = json.loads(body)
                logger.info(f"Mensaje recibido de {queue}: {mensaje.get('comando')}")
                
                callback(mensaje)
                
                ch.basic_ack(delivery_tag=method.delivery_tag)
                
            except Exception as e:
                logger.error(f"Error procesando mensaje: {e}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        
        self.channel.basic_qos(prefetch_count=1)
        self.channel.basic_consume(queue=queue, on_message_callback=wrapper)
        
        logger.info(f"Esperando mensajes en {queue}...")
        self.channel.start_consuming()
    
    def cerrar(self):
        """Cierra la conexión con RabbitMQ"""
        if self.channel and self.channel.is_open:
            self.channel.close()
        if self.connection and self.connection.is_open:
            self.connection.close()
        logger.info("Conexión con RabbitMQ cerrada")


class WorkerCalculos:
    """
    Worker que procesa comandos de cálculo de flujo
    """
    
    def __init__(self):
        self.message_bus = None
        self.running = False
    
    def _procesar_mensaje(self, mensaje: Dict):
        """Procesa un mensaje de cálculo"""
        from calculador.flujo_so2 import procesar_imagen_completa
        
        imagen_id = mensaje.get('imagen_id')
        parametros = mensaje.get('parametros', {})
        
        logger.info(f"Procesando imagen {imagen_id}")
        
        try:
            resultado = procesar_imagen_completa(
                imagen_id=imagen_id,
                altitud_viento_m=parametros.get('altitud_viento_m', 3000)
            )
            
            if resultado and resultado.get('exito'):
                logger.info(f"Imagen {imagen_id} procesada: {resultado.get('flujo_optimo', {}).get('flujo_ton_dia', 0):.1f} ton/día")
            else:
                logger.warning(f"Procesamiento de imagen {imagen_id} sin éxito")
                
        except Exception as e:
            logger.error(f"Error procesando imagen {imagen_id}: {e}")
    
    def iniciar(self):
        """Inicia el worker"""
        self.running = True
        self.message_bus = MessageBus()
        
        logger.info("Worker de cálculos iniciado")
        
        try:
            self.message_bus.consumir_calculos(self._procesar_mensaje)
        except KeyboardInterrupt:
            self.detener()
    
    def detener(self):
        """Detiene el worker"""
        self.running = False
        if self.message_bus:
            self.message_bus.cerrar()
        logger.info("Worker de cálculos detenido")


class WorkerExtraccion:
    """
    Worker que procesa comandos de extracción de datos
    """
    
    def __init__(self):
        self.message_bus = None
        self.running = False
    
    def _procesar_mensaje(self, mensaje: Dict):
        """Procesa un mensaje de extracción"""
        from etl.tropomi_downloader import TROPOMIDownloader, buscar_y_descargar_tropomi
        from database import get_session, Volcan, ImagenTROPOMI
        
        volcan_id = mensaje.get('volcan_id')
        fecha_inicio = datetime.fromisoformat(mensaje.get('fecha_inicio'))
        fecha_fin = datetime.fromisoformat(mensaje.get('fecha_fin'))
        descargar = mensaje.get('parametros', {}).get('descargar', True)
        
        logger.info(f"Iniciando extracción para volcán {volcan_id}")
        
        try:
            # Obtener información del volcán
            session = get_session()
            volcan = session.query(Volcan).get(volcan_id)
            
            if not volcan:
                logger.error(f"Volcán {volcan_id} no encontrado")
                session.close()
                return
            
            # Ejecutar búsqueda y registro de metadatos
            resultado = buscar_y_descargar_tropomi(
                volcan_nombre=volcan.nombre,
                lat=volcan.latitud,
                lon=volcan.longitud,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                descargar=False  # Solo registrar metadatos primero
            )
            
            logger.info(f"Extracción completada: {resultado.get('productos_encontrados')} productos encontrados")
            
            # Ahora descargar los archivos pendientes si se solicitó
            if descargar:
                downloader = TROPOMIDownloader()
                
                # Obtener TODAS las imágenes pendientes de este volcán
                pendientes = session.query(ImagenTROPOMI).filter(
                    ImagenTROPOMI.volcan_id == volcan_id,
                    ImagenTROPOMI.descargado == False
                ).all()
                
                logger.info(f"Descargando {len(pendientes)} imágenes pendientes...")
                
                imagenes_descargadas = []
                for img in pendientes:
                    try:
                        logger.info(f"Descargando producto {img.producto_id}...")
                        nombre_archivo = f"{img.producto_id}.nc"
                        ruta = downloader.descargar_producto(img.producto_id, nombre_archivo)
                        
                        if ruta:
                            img.ruta_archivo = ruta
                            img.descargado = True
                            img.fecha_descarga = datetime.utcnow()
                            session.commit()
                            imagenes_descargadas.append(img.id)
                            logger.info(f"  Descargado: {ruta}")
                        else:
                            logger.warning(f"  No se pudo descargar {img.producto_id}")
                            
                    except Exception as e:
                        logger.error(f"  Error descargando {img.producto_id}: {e}")
                        continue
                
                logger.info(f"Descarga completada: {len(imagenes_descargadas)} archivos")
                
                # Publicar comandos de cálculo para las imágenes descargadas
                if imagenes_descargadas:
                    mb = MessageBus()
                    for imagen_id in imagenes_descargadas:
                        mb.publicar_comando_calculos(imagen_id)
                    mb.cerrar()
            
            session.close()
                
        except Exception as e:
            logger.error(f"Error en extracción: {e}")
    
    def iniciar(self):
        """Inicia el worker"""
        self.running = True
        self.message_bus = MessageBus()
        
        logger.info("Worker de extracción iniciado")
        
        try:
            self.message_bus.consumir_extraccion(self._procesar_mensaje)
        except KeyboardInterrupt:
            self.detener()
    
    def detener(self):
        """Detiene el worker"""
        self.running = False
        if self.message_bus:
            self.message_bus.cerrar()
        logger.info("Worker de extracción detenido")


def iniciar_worker_calculos():
    """Función para iniciar el worker de cálculos"""
    worker = WorkerCalculos()
    worker.iniciar()


def iniciar_worker_extraccion():
    """Función para iniciar el worker de extracción"""
    worker = WorkerExtraccion()
    worker.iniciar()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Uso: python message_bus.py [calculos|extraccion|test]")
        sys.exit(1)
    
    modo = sys.argv[1]
    
    if modo == "calculos":
        iniciar_worker_calculos()
    elif modo == "extraccion":
        iniciar_worker_extraccion()
    elif modo == "test":
        # Prueba de publicación
        mb = MessageBus()
        
        # Publicar comando de extracción de prueba
        mb.publicar_comando_extraccion(
            volcan_id=1,
            fecha_inicio="2024-01-01T00:00:00",
            fecha_fin="2024-01-07T23:59:59"
        )
        
        # Publicar comando de cálculo de prueba
        mb.publicar_comando_calculos(imagen_id=1)
        
        mb.cerrar()
        print("Comandos de prueba publicados")
    else:
        print(f"Modo desconocido: {modo}")
