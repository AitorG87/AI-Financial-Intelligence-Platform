from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from utilidades.configuracion import ConfigProyecto, cargar_config


NOMBRE_ARCHIVO_ESTADO = "estado_ui.json"
NOMBRE_ARCHIVO_RESPUESTA = "respuesta_ui.json"
NOMBRE_CARPETA_ARCHIVADO = "archivado"

# Permite cambiar la ruta del puente sin tocar código.
# Ejemplo para el repo limpio:
#   PUENTE_UI_DIR=outputs/puente_ui
ENV_PUENTE_UI_DIR = "PUENTE_UI_DIR"


## Utilidades internas

def ahora_iso() -> str:
    """Devuelve timestamp sin microsegundos."""
    return datetime.now().replace(microsecond=0).isoformat()


def asegurar_directorio(ruta: Path) -> None:
    """Crea un directorio si no existe."""
    ruta.mkdir(parents=True, exist_ok=True)


def escribir_json_atomico(ruta: Path, datos: Dict[str, Any]) -> None:
    """Escribe un JSON de forma atómica usando un temporal y os.replace.

    Esto evita que Streamlit lea un JSON a medio escribir mientras el pipeline
    está actualizando el estado.
    """
    asegurar_directorio(ruta.parent)
    contenido = json.dumps(datos, ensure_ascii=False, indent=2)

    ruta_tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    ruta_tmp.write_text(contenido, encoding="utf-8")

    for _ in range(5):
        try:
            os.replace(ruta_tmp, ruta)
            return
        except PermissionError:
            time.sleep(0.05)

    # Fallback para Windows si el archivo está bloqueado puntualmente.
    try:
        ruta.write_text(contenido, encoding="utf-8")
    except PermissionError:
        pass


def leer_json(ruta: Path) -> Optional[Dict[str, Any]]:
    """Lee un JSON. Devuelve None si no existe o está temporalmente inválido."""
    if not ruta.exists():
        return None
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return datos if isinstance(datos, dict) else None


def estado_inicial() -> Dict[str, Any]:
    """Estado inicial del puente UI.

    No debe versionarse como archivo real en GitHub: se genera automáticamente
    al arrancar el pipeline o la UI.
    """
    return {
        "fase": "carga",
        "mensaje": "Sube un archivo para comenzar",
        "ruta_entrada": None,
        "id_documento": None,
        "motivo_repetir_revision": None,
        "timestamp": ahora_iso(),
    }


def _resolver_ruta_desde_base(base: Path, valor: str) -> Path:
    ruta = Path(valor).expanduser()
    if ruta.is_absolute():
        return ruta.resolve()
    return (base / ruta).resolve()


def obtener_carpeta_puente(cfg: Optional[ConfigProyecto] = None) -> Path:
    """Devuelve la carpeta del puente UI.

    Prioridad:
    1. Variable de entorno PUENTE_UI_DIR.
       Recomendado para el repo público: outputs/puente_ui
    2. cfg.rutas.puente_ui, si se pasa configuración.
    3. cargar_config().rutas.puente_ui, por compatibilidad con el proyecto original.
    """
    cfg_local = cfg or cargar_config()

    valor_env = os.environ.get(ENV_PUENTE_UI_DIR)
    if valor_env and valor_env.strip():
        return _resolver_ruta_desde_base(cfg_local.rutas.base, valor_env.strip())

    return cfg_local.rutas.puente_ui


def obtener_ruta_estado(cfg: Optional[ConfigProyecto] = None) -> Path:
    return obtener_carpeta_puente(cfg) / NOMBRE_ARCHIVO_ESTADO


def obtener_ruta_respuesta(cfg: Optional[ConfigProyecto] = None) -> Path:
    return obtener_carpeta_puente(cfg) / NOMBRE_ARCHIVO_RESPUESTA


def obtener_carpeta_archivado(cfg: Optional[ConfigProyecto] = None) -> Path:
    return obtener_carpeta_puente(cfg) / NOMBRE_CARPETA_ARCHIVADO


## API: Orquestador -> Streamlit (estado)

def escribir_estado(
    *,
    fase: str,
    mensaje: str,
    ruta_entrada: Optional[str] = None,
    id_documento: Optional[int] = None,
    motivo_repetir_revision: Optional[str] = None,
    cfg: Optional[ConfigProyecto] = None,
) -> None:
    """Escribe el estado que consumirá Streamlit.

    Fases esperadas: "carga" | "procesando" | "revision" | "final".
    """
    datos: Dict[str, Any] = {
        "fase": fase,
        "mensaje": mensaje,
        "ruta_entrada": ruta_entrada,
        "id_documento": id_documento,
        "motivo_repetir_revision": motivo_repetir_revision,
        "timestamp": ahora_iso(),
    }
    escribir_json_atomico(obtener_ruta_estado(cfg), datos)


def leer_estado(cfg: Optional[ConfigProyecto] = None) -> Dict[str, Any]:
    """Lee el estado actual.

    Si no existe estado_ui.json, lo crea automáticamente con el estado inicial.
    Esto permite no versionar outputs/puente_ui/estado_ui.json en GitHub.
    """
    asegurar_puente(cfg)
    datos = leer_json(obtener_ruta_estado(cfg))
    if datos is None:
        datos = estado_inicial()
        escribir_json_atomico(obtener_ruta_estado(cfg), datos)
    return datos


## API: Streamlit -> Orquestador (respuesta)

def escribir_respuesta(
    *,
    accion: str,
    id_documento: int,
    cfg: Optional[ConfigProyecto] = None,
) -> None:
    """Escribe la respuesta del usuario para el orquestador.

    Respuestas esperadas: "aceptar" | "guardar".
    """
    if accion not in {"aceptar", "guardar"}:
        raise ValueError("accion debe ser 'aceptar' o 'guardar'")

    datos: Dict[str, Any] = {
        "accion": accion,
        "id_documento": int(id_documento),
        "timestamp": ahora_iso(),
    }
    escribir_json_atomico(obtener_ruta_respuesta(cfg), datos)


def leer_respuesta_una_vez(
    *,
    cfg: Optional[ConfigProyecto] = None,
    id_documento_esperado: Optional[int] = None,
    tiempo_espera_segundos: Optional[float] = None,
    intervalo_poleo_segundos: float = 0.5,
) -> Optional[Dict[str, Any]]:
    """Lee y consume una única vez la respuesta del usuario.

    - Si se pasa id_documento_esperado, ignora respuestas de otros documentos.
    - Si tiempo_espera_segundos es None, espera indefinidamente.
    - Al consumir, mueve el archivo a puente_ui/archivado/.
    """
    inicio = time.time()
    cfg_local = cfg or cargar_config()

    asegurar_puente(cfg_local)
    ruta_respuesta = obtener_ruta_respuesta(cfg_local)
    carpeta_archivado = obtener_carpeta_archivado(cfg_local)
    asegurar_directorio(carpeta_archivado)

    while True:
        datos = leer_json(ruta_respuesta)
        if datos is not None:
            accion = datos.get("accion")
            id_doc = datos.get("id_documento")

            if accion in ("aceptar", "guardar") and isinstance(id_doc, int):
                if id_documento_esperado is None or id_doc == id_documento_esperado:
                    marca_tiempo = datos.get("timestamp") or ahora_iso()
                    marca_segura = (
                        str(marca_tiempo)
                        .replace(":", "")
                        .replace("-", "")
                        .replace("T", "_")
                    )
                    destino = carpeta_archivado / f"respuesta_ui_{id_doc}_{marca_segura}.json"
                    try:
                        os.replace(ruta_respuesta, destino)
                    except FileNotFoundError:
                        pass
                    return datos

        if (
            tiempo_espera_segundos is not None
            and (time.time() - inicio) >= tiempo_espera_segundos
        ):
            return None

        time.sleep(intervalo_poleo_segundos)


## Inicialización

def asegurar_puente(cfg: Optional[ConfigProyecto] = None) -> None:
    """Asegura carpetas del puente y un estado inicial.

    Crea dinámicamente:
    - outputs/puente_ui/
    - outputs/puente_ui/archivado/
    - outputs/puente_ui/estado_ui.json si no existe

    Estos archivos son runtime artifacts y deben quedar fuera de GitHub.
    """
    cfg_local = cfg or cargar_config()
    carpeta_puente = obtener_carpeta_puente(cfg_local)
    carpeta_archivado = obtener_carpeta_archivado(cfg_local)

    asegurar_directorio(carpeta_puente)
    asegurar_directorio(carpeta_archivado)

    ruta_estado = obtener_ruta_estado(cfg_local)
    if not ruta_estado.exists():
        escribir_json_atomico(ruta_estado, estado_inicial())
