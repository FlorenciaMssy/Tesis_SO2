from .tropomi_downloader import (
    TROPOMIDownloader,
    buscar_y_descargar_tropomi
)
from .tropomi_processor import (
    TROPOMIProcessor,
    procesar_imagen_tropomi
)
# NCEP Reanalysis para vientos (reemplaza ERA5)
from .ncep_downloader import (
    NCEPDownloader,
    obtener_viento_para_imagen
)

# Mantener ERA5 por compatibilidad (deprecated)
try:
    from .era5_downloader import (
        ERA5Downloader,
        obtener_perfil_vertical_viento
    )
except ImportError:
    ERA5Downloader = None
    obtener_perfil_vertical_viento = None

__all__ = [
    'TROPOMIDownloader',
    'buscar_y_descargar_tropomi',
    'NCEPDownloader',
    'obtener_viento_para_imagen',
    'TROPOMIProcessor',
    'procesar_imagen_tropomi',
    # Deprecated
    'ERA5Downloader',
    'obtener_perfil_vertical_viento',
]
