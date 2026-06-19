from __future__ import annotations
import sys
from pathlib import Path

# Resolver ruta base antes de cualquier import del proyecto
ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[2]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

import argparse
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime

from utilidades.configuracion import ConfigProyecto, cargar_config
from bd.conexion import crear_conexion_mysql
from pipeline.modulos.repositorio_documentos import (
    DocumentoInsert,
    RepositorioDocumentosIngesta,
    RepositorioDocumentosMySQL,
)


@dataclass(frozen=True)
class EntradaIngesta:
    bytes_archivo: bytes
    nombre_original: str
    mime_type: str


@dataclass(frozen=True)
class SalidaIngesta:
    id_documento: int
    ruta_original_temporal: str
    nombre_original: str
    mime_type: str
    formato_original: str
    extension_original: str
    fecha_ingesta: datetime
    estado: str               # INGESTADO o FALLO_INGESTA
    avisos: list[str]


FORMATOS_MIME = {
    "image/jpeg": ("JPG", ".jpg"),
    "image/jpg": ("JPG", ".jpg"),
    "image/png": ("PNG", ".png"),
    "application/pdf": ("PDF", ".pdf"),
}

EXTENSIONES_FORMATO = {
    ".jpeg": ("JPG", ".jpg"),
    ".jpg": ("JPG", ".jpg"),
    ".png": ("PNG", ".png"),
    ".pdf": ("PDF", ".pdf"),
}


def limpiar_nombre_original(nombre_original: str) -> tuple[str, list[str]]:
    """Limpia el nombre del archivo"""
    avisos: list[str] = []

    base = Path(nombre_original).name
    if base != nombre_original:
        avisos.append("nombre_original_saneado")

    # Limpieza de caracteres raros
    base2 = re.sub(r"[\x00-\x1f]", "", base).strip()
    if base2 != base:
        avisos.append("nombre_original_limpiado")

    return base2, avisos


def formato_archivo(nombre_original: str, mime_type: str) -> tuple[str, str, list[str]]:
    """Averigua formato_original y extension_original"""
    avisos: list[str] = []
    mime = (mime_type or "").strip().lower()

    if mime in FORMATOS_MIME:
        formato, ext = FORMATOS_MIME[mime]
    else:
        formato, ext = "", ""
        avisos.append("mime_type_desconocido")

    # Validación por extensión del nombre
    ext_nombre = Path(nombre_original).suffix.lower()
    if ext_nombre in EXTENSIONES_FORMATO:
        formato_ext, ext_norm = EXTENSIONES_FORMATO[ext_nombre]
        if not formato:
            formato, ext = formato_ext, ext_norm
            avisos.append("usando_extension")
        else:
            # Si mime y extensión no coinciden, guardar aviso y utilizar mime
            if (formato_ext, ext_norm) != (formato, ext):
                avisos.append("extension_no_coincide_con_mime_type")
    else:
        if not formato:
            avisos.append("extension_desconocida")

    if not formato or not ext:
        raise ValueError(
            f"Formato no soportado: mime_type={mime_type}, nombre_original={nombre_original}"
        )

    return formato, ext, avisos


def construir_ruta_relativa(cfg: ConfigProyecto, id_documento: int, extension_original: str) -> Path:
    """Construye ruta relativa basada en id_documento y extension_original"""
    return cfg.rutas.originales_subidos.relative_to(cfg.rutas.base) / f"{id_documento}{extension_original}"


def guardar_bytes(cfg: ConfigProyecto, ruta_rel: Path, contenido: bytes) -> None:
    """Construye ruta absoluta desde la raíz del proyecto y guarda el archivo"""
    ruta_abs = (cfg.rutas.base / ruta_rel).resolve()
    ruta_abs.parent.mkdir(parents=True, exist_ok=True)
    ruta_abs.write_bytes(contenido)


def ingestar_documento(
    entrada: EntradaIngesta,
    repositorio: RepositorioDocumentosIngesta,
    cfg: ConfigProyecto,
) -> SalidaIngesta:
    """
    Función pipeline:
    1- Limpia el nombre original
    2- Detecta formato del archivo
    3- Inserta registro
    4- Guarda el archivo
    5- Actualiza estado a INGESTADO
    """
    fecha = datetime.now()
    avisos: list[str] = []

    nombre_limpio, avisos_nombre = limpiar_nombre_original(entrada.nombre_original)
    avisos.extend(avisos_nombre)

    try:
        formato, extension, avisos_formato = formato_archivo(nombre_limpio, entrada.mime_type)
        avisos.extend(avisos_formato)
    except Exception as e:
        return SalidaIngesta(
            id_documento=-1,
            ruta_original_temporal="",
            nombre_original=nombre_limpio,
            mime_type=entrada.mime_type,
            formato_original="",
            extension_original="",
            fecha_ingesta=fecha,
            estado="FALLO_INGESTA",
            avisos=avisos + [f"error: {type(e).__name__}"],
        )

    tamano = len(entrada.bytes_archivo)

    # 1) Inserta registro en BD
    try:
        id_doc = repositorio.crear_documento_inicial(
            DocumentoInsert(
                nombre_original=nombre_limpio,
                mime_type=entrada.mime_type,
                formato_original=formato,
                extension_original=extension,
                tamano_bytes=tamano,
            )
        )
    except Exception as e:
        return SalidaIngesta(
            id_documento=-1,
            ruta_original_temporal="",
            nombre_original=nombre_limpio,
            mime_type=entrada.mime_type,
            formato_original=formato,
            extension_original=extension,
            fecha_ingesta=fecha,
            estado="FALLO_INGESTA",
            avisos=avisos + [f"error_db_insert: {type(e).__name__}"],
        )

    # 2) Guardar archivo original
    ruta_rel = construir_ruta_relativa(cfg, id_doc, extension)
    try:
        guardar_bytes(cfg, ruta_rel, entrada.bytes_archivo)
    except Exception as e:
        try:
            repositorio.marcar_fallo(id_doc, f"Error guardando el archivo: {e}", avisos)
        finally:
            return SalidaIngesta(
                id_documento=id_doc,
                ruta_original_temporal="",
                nombre_original=nombre_limpio,
                mime_type=entrada.mime_type,
                formato_original=formato,
                extension_original=extension,
                fecha_ingesta=fecha,
                estado="FALLO_INGESTA",
                avisos=avisos + [f"error_guardado: {type(e).__name__}"],
            )

    # 3) Marcar OK en base de datos
    ruta_rel_str = ruta_rel.as_posix()
    try:
        repositorio.marcar_ingestado(id_doc, ruta_rel_str, avisos)
    except Exception as e:
        try:
            repositorio.marcar_fallo(id_doc, f"Error actualizando estado a INGESTADO: {e}", avisos)
        finally:
            return SalidaIngesta(
                id_documento=id_doc,
                ruta_original_temporal=ruta_rel_str,
                nombre_original=nombre_limpio,
                mime_type=entrada.mime_type,
                formato_original=formato,
                extension_original=extension,
                fecha_ingesta=fecha,
                estado="FALLO_INGESTA",
                avisos=avisos + [f"error_db_update: {type(e).__name__}"],
            )

    return SalidaIngesta(
        id_documento=id_doc,
        ruta_original_temporal=ruta_rel_str,
        nombre_original=nombre_limpio,
        mime_type=entrada.mime_type,
        formato_original=formato,
        extension_original=extension,
        fecha_ingesta=fecha,
        estado="INGESTADO",
        avisos=avisos,
    )



## CLI

def _mime_por_extension(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingesta: registra un documento en MySQL y guarda el archivo original"
    )
    parser.add_argument(
        "--ruta",
        required=True,
        help="Ruta al archivo local (jpg/png/pdf) a ingestar",
    )
    parser.add_argument(
        "--nombre",
        default=None,
        help="Nombre original a registrar (nombre archivo)",
    )
    parser.add_argument(
        "--mime",
        default=None,
        help="mime_type",
    )
    args = parser.parse_args(argv)

    ruta = Path(args.ruta)
    if not ruta.exists() or not ruta.is_file():
        print(f"ERROR: no existe el archivo: {ruta}", file=sys.stderr)
        return 2

    nombre_original = args.nombre or ruta.name
    mime_type = args.mime or _mime_por_extension(ruta)

    # configuración + repositorio MySQL
    cfg = cargar_config()
    conn = crear_conexion_mysql(cfg)
    repo = RepositorioDocumentosMySQL(conn)

    entrada = EntradaIngesta(
        bytes_archivo=ruta.read_bytes(),
        nombre_original=nombre_original,
        mime_type=mime_type,
    )

    salida = ingestar_documento(entrada=entrada, repositorio=repo, cfg=cfg)

    print(
        f"estado={salida.estado} id_documento={salida.id_documento} "
        f"ruta_original_temporal={salida.ruta_original_temporal} avisos={salida.avisos}"
    )

    return 0 if salida.estado == "INGESTADO" else 1


if __name__ == "__main__":
    raise SystemExit(main())