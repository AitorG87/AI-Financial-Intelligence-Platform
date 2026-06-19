from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageOps
from pdf2image import convert_from_path, pdfinfo_from_path

from bd.conexion import crear_conexion_mysql
from pipeline.modulos.repositorio_documentos import (
    RepositorioDocumentosMySQL,
    RepositorioDocumentosPreprocesado,
)
from utilidades.configuracion import ConfigProyecto, cargar_config, cargar_env_desde_raiz



# Raíz proyecto


ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[2]  # pipeline/modulos -> TFM
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))



# Entrada/salida


@dataclass(frozen=True)
class EntradaPreprocesado:
    id_documento: int


@dataclass(frozen=True)
class SalidaPreprocesado:
    id_documento: int
    ruta_jpg: str
    estado: str  # "OK" | "KO"
    avisos: List[str]



# Utilidades


def ruta_absoluta(ruta_base: Path, ruta_relativa_o_absoluta: str) -> Path:
    ruta = Path(ruta_relativa_o_absoluta)
    return ruta if ruta.is_absolute() else (ruta_base / ruta).resolve()


def normalizar_avisos_json(avisos_json: Any) -> Dict[str, Any]:
    """Normaliza avisos_json a dict"""
    if avisos_json is None:
        return {}

    if isinstance(avisos_json, str):
        texto = avisos_json.strip()
        if not texto:
            return {}
        try:
            return json.loads(texto)
        except Exception:
            return {"avisos_json_original_corrupto": texto}

    if isinstance(avisos_json, list):
        return {"avisos": avisos_json}

    if isinstance(avisos_json, dict):
        return avisos_json

    return {
        "avisos_json_tipo_no_soportado": str(type(avisos_json)),
        "valor": str(avisos_json),
    }


def asegurar_dict_avisos(avisos: Any) -> Dict[str, Any]:
    """Garantiza que avisos sea un dict para poder colgar claves como 'preprocesado'
    Compatibilidad con histórico:
      - dict -> se devuelve tal cual
      - list -> {'avisos': [...]}
      - str JSON -> se intenta parsear
      - None/otros -> dict mínimo
    """
    if avisos is None:
        return {}
    if isinstance(avisos, dict):
        return avisos
    # Reutiliza normalizar_avisos_json para string/list/otros
    try:
        return normalizar_avisos_json(avisos)
    except Exception:
        return {"avisos": [str(avisos)]}


def obtener_lista_avisos(avisos_dict: Any) -> List[str]:
    # Acepta dict o lista (compatibilidad con avisos_json='[]')
    if avisos_dict is None:
        return []
    if isinstance(avisos_dict, list):
        return [str(x) for x in avisos_dict]
    if not isinstance(avisos_dict, dict):
        return [str(avisos_dict)]
    avisos = avisos_dict.get("avisos")
    if avisos is None:
        avisos_dict["avisos"] = []
        return avisos_dict["avisos"]
    if isinstance(avisos, list):
        return avisos
    avisos_dict["avisos"] = [str(avisos)]
    return avisos_dict["avisos"]



def aplicar_orientacion_exif(imagen: Image.Image) -> Tuple[Image.Image, bool, Optional[int]]:
    orientacion = None
    exif_aplicado = False
    try:
        exif = imagen.getexif()
        if exif:
            orientacion = exif.get(274)
            if orientacion and orientacion != 1:
                exif_aplicado = True
    except Exception:
        orientacion = None
        exif_aplicado = False

    imagen_corregida = ImageOps.exif_transpose(imagen)
    return imagen_corregida, exif_aplicado, orientacion


def convertir_a_rgb(imagen: Image.Image) -> Image.Image:
    return imagen if imagen.mode == "RGB" else imagen.convert("RGB")


def redimensionar_lado_largo(imagen: Image.Image, lado_largo_px: int) -> Image.Image:
    """Reduce la imagen manteniendo proporción"""
    ancho, alto = imagen.size
    lado_largo_actual = max(ancho, alto)
    if lado_largo_actual <= lado_largo_px:
        return imagen

    escala = lado_largo_px / float(lado_largo_actual)
    nuevo_ancho = max(1, int(round(ancho * escala)))
    nuevo_alto = max(1, int(round(alto * escala)))
    return imagen.resize((nuevo_ancho, nuevo_alto), resample=Image.Resampling.LANCZOS)


def renderizar_pdf_primera_pagina(ruta_pdf: Path, dpi: int) -> Tuple[Image.Image, int]:
    """Renderiza la página 1 del PDF"""
    info = pdfinfo_from_path(str(ruta_pdf))
    paginas_totales = int(info.get("Pages", 1))

    imagenes = convert_from_path(
        str(ruta_pdf),
        dpi=dpi,
        first_page=1,
        last_page=1,
    )
    if not imagenes:
        raise RuntimeError("No se pudo renderizar ninguna página del PDF (lista vacía)")

    return imagenes[0], paginas_totales



# PREPROCESADO


def ejecutar_preprocesado(
    entrada: EntradaPreprocesado,
    repositorio: RepositorioDocumentosPreprocesado,
    cfg: ConfigProyecto,
) -> SalidaPreprocesado:
    """Ejecuta preprocesado y guarda un JPG normalizado"""
    ruta_base = cfg.rutas.base
    carpeta_jpg = cfg.rutas.jpg_normalizados

    # Parámetros
    dpi = 300
    lado_largo_px = 1536
    calidad_jpg = 95

    ruta_jpg_relativa: str = ""
    avisos_salida: List[str] = []

    try:
        documento = repositorio.obtener_documento(entrada.id_documento)

        formato = str(documento.get("formato_original", "")).upper().strip()
        ruta_original = documento.get("ruta_original_temporal")
        if not ruta_original:
            raise ValueError("Documento sin ruta_original_temporal en MySQL")

        avisos_dict = asegurar_dict_avisos(normalizar_avisos_json(documento.get("avisos_json")))
        avisos = obtener_lista_avisos(avisos_dict)

        ruta_original_abs = ruta_absoluta(ruta_base, str(ruta_original))
        if not ruta_original_abs.exists():
            raise FileNotFoundError(f"No existe el archivo original: {ruta_original_abs}")

        metadatos = {
            "dpi_objetivo": dpi,
            "lado_largo_px_objetivo": lado_largo_px,
            "calidad_jpg": calidad_jpg,
            "formato_original": formato,
        }

        if formato in ("JPG", "JPEG", "PNG"):
            with Image.open(ruta_original_abs) as imagen:
                imagen.load()
                imagen2, exif_aplicado, orientacion = aplicar_orientacion_exif(imagen)
                if exif_aplicado:
                    avisos.append("EXIF: orientación aplicada (ImageOps.exif_transpose)")

                metadatos["exif_orientacion_aplicada"] = bool(exif_aplicado)
                metadatos["exif_orientacion_original"] = orientacion

                imagen_final = convertir_a_rgb(imagen2)

        elif formato == "PDF":
            imagen_pdf, paginas_totales = renderizar_pdf_primera_pagina(ruta_original_abs, dpi=dpi)

            metadatos["pdf_paginas_total"] = paginas_totales
            metadatos["pdf_pagina_usada"] = 1
            metadatos["pdf_multipagina"] = bool(paginas_totales > 1)

            if paginas_totales > 1:
                avisos.append(f"PDF multipágina: se usó página 1 de {paginas_totales}")

            imagen_final = convertir_a_rgb(imagen_pdf)

        else:
            raise ValueError(f"Formato no soportado: '{formato}'")

        # Redimensionar
        ancho_antes, alto_antes = imagen_final.size
        imagen_final = redimensionar_lado_largo(imagen_final, lado_largo_px)
        ancho_despues, alto_despues = imagen_final.size

        metadatos["tamano_original"] = {"ancho": ancho_antes, "alto": alto_antes}
        metadatos["tamano_final"] = {"ancho": ancho_despues, "alto": alto_despues}
        metadatos["dpi_final"] = dpi

        # Guardar JPG
        carpeta_jpg.mkdir(parents=True, exist_ok=True)
        ruta_jpg_abs = (carpeta_jpg / f"{entrada.id_documento}.jpg").resolve()

        imagen_final.save(
            str(ruta_jpg_abs),
            format="JPEG",
            quality=calidad_jpg,
            dpi=(dpi, dpi),
            optimize=True,
        )

        # Guardar ruta relativa en BD
        try:
            ruta_jpg_relativa = str(
                ruta_jpg_abs.relative_to(ruta_base)
            ).replace("\\", "/")
        except Exception:
            ruta_jpg_relativa = str(ruta_jpg_abs)

        avisos_dict["preprocesado"] = metadatos

        repositorio.actualizar_preprocesado_ok(
            id_documento=entrada.id_documento,
            ruta_jpg=ruta_jpg_relativa,
            avisos_json=avisos_dict,
        )

        avisos_salida = list(obtener_lista_avisos(avisos_dict))

        return SalidaPreprocesado(
            id_documento=entrada.id_documento,
            ruta_jpg=ruta_jpg_relativa,
            estado="OK",
            avisos=avisos_salida,
        )

    except Exception as error:
        mensaje_error = f"Fallo en preprocesado: {type(error).__name__}: {error}"

        avisos_dict: Dict[str, Any] = {}
        try:
            documento = repositorio.obtener_documento(entrada.id_documento)
            avisos_dict = asegurar_dict_avisos(normalizar_avisos_json(documento.get("avisos_json")))
        except Exception:
            avisos_dict = {}

        avisos = obtener_lista_avisos(avisos_dict)
        avisos.append(mensaje_error)

        avisos_dict["preprocesado_error"] = {
            "mensaje": mensaje_error,
            "trazabilidad": traceback.format_exc(limit=10),
        }

        try:
            repositorio.actualizar_preprocesado_error(
                id_documento=entrada.id_documento,
                error_mensaje=mensaje_error,
                avisos_json=avisos_dict,
            )
        except Exception:
            pass

        return SalidaPreprocesado(
            id_documento=entrada.id_documento,
            ruta_jpg=ruta_jpg_relativa,
            estado="KO",
            avisos=avisos,
        )



# CLI


def ejecutar_cli() -> int:
    cargar_env_desde_raiz()
    cfg = cargar_config()

    parser = argparse.ArgumentParser(description="Preprocesado / Estandarización")
    parser.add_argument("--id-documento", type=int, required=True, help="id_documento a preprocesar")
    args = parser.parse_args()

    conexion = crear_conexion_mysql(cfg)
    try:
        repositorio = RepositorioDocumentosMySQL(conexion, cfg)

        salida = ejecutar_preprocesado(
            EntradaPreprocesado(id_documento=args.id_documento),
            repositorio,
            cfg,
        )

        print(json.dumps({
            "id_documento": salida.id_documento,
            "estado": salida.estado,
            "ruta_jpg": salida.ruta_jpg,
            "avisos": salida.avisos,
        }, ensure_ascii=False, indent=2))
        return 0 if salida.estado == "OK" else 2
    finally:
        conexion.close()


if __name__ == "__main__":
    raise SystemExit(ejecutar_cli())