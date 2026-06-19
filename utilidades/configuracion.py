from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional



## Modelos de configuración


@dataclass(frozen=True)
class ConfiguracionMySQL:
    host: str
    port: int
    database: str
    user: str
    password: str


@dataclass(frozen=True)
class ConfiguracionQwen:
    model_id: str
    base_model_path: str
    strict_local_only: bool
    dtype: str
    load_in_4bit: bool
    max_new_tokens: int


@dataclass(frozen=True)
class Rutas:
    base: Path
    bd_sql: Path
    datos: Path
    modelos: Path
    outputs: Path
    originales_subidos: Path
    jpg_normalizados: Path
    puente_ui: Path


@dataclass(frozen=True)
class ConfigProyecto:
    nombre: str
    entorno: str
    log_level: str
    rutas: Rutas
    mysql: ConfiguracionMySQL
    qwen: ConfiguracionQwen


## Carga .env


def cargar_env_desde_raiz(ruta_base: Optional[Path] = None) -> None:
    """Carga variables de entorno desde el archivo .env en la raíz fija de producción"""
    ruta_raiz = Path(__file__).resolve().parent.parent  # apunta a 'codigo/produccion'
    archivo_env = ruta_raiz / ".env"
    if not archivo_env.exists():
        return

    for linea_bruta in archivo_env.read_text(encoding="utf-8").splitlines():
        linea = linea_bruta.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if clave and (clave not in os.environ):
            os.environ[clave] = valor



def detectar_raiz_proyecto() -> Path:
    """Devuelve la raíz fija de producción ('codigo/produccion')"""
    return Path(__file__).resolve().parent.parent


def leer_env_str(clave: str, default: str) -> str:
    valor = os.environ.get(clave)
    return valor.strip() if valor and valor.strip() else default


def leer_env_int(clave: str, default: int) -> int:
    valor = os.environ.get(clave)
    if valor is None or not str(valor).strip():
        return default
    try:
        return int(str(valor).strip())
    except ValueError:
        return default


def leer_env_bool(clave: str, default: bool) -> bool:
    valor = os.environ.get(clave)
    if valor is None or not str(valor).strip():
        return default
    valor_norm = str(valor).strip().lower()
    if valor_norm in {"1", "true", "t", "yes", "y", "si", "sí"}:
        return True
    if valor_norm in {"0", "false", "f", "no", "n"}:
        return False
    return default


def ruta_desde_base(base: Path, ruta_relativa: str) -> Path:
    ruta = Path(ruta_relativa)
    return (ruta if ruta.is_absolute() else (base / ruta)).resolve()


def resolver_model_id(base: Path, valor_model_id: str) -> str:
    valor = (valor_model_id or "").strip()
    if not valor:
        return valor

    ruta = Path(valor)
    if ruta.is_absolute():
        return str(ruta.resolve())

    ruta_relativa = (base / ruta).resolve()
    if ruta_relativa.exists():
        return str(ruta_relativa)

    return valor


def resolver_ruta_local(base: Path, valor_ruta: str) -> str:
    valor = (valor_ruta or "").strip()
    if not valor:
        return valor
    ruta = Path(valor)
    if ruta.is_absolute():
        return str(ruta.resolve())
    return str((base / ruta).resolve())


## Carga de configuración unificada


def cargar_config(ruta_base: Optional[Path] = None) -> ConfigProyecto:
    """Carga configuración del proyecto usando variables de entorno y .env de producción"""
    base = Path(__file__).resolve().parent.parent  # 'codigo/produccion'

    # Cargar .env
    cargar_env_desde_raiz(base)

    # Identidad
    nombre = leer_env_str("TFM_NOMBRE", "contabilidad_familiar")
    entorno = leer_env_str("TFM_ENTORNO", "produccion")
    log_level = leer_env_str("LOG_LEVEL", "PRODUCCION")

    # Rutas
    ruta_outputs = (base / "outputs").resolve()
    ruta_originales = ruta_desde_base(base, leer_env_str("IMAGENES_USUARIO", r"outputs\originales_subidos"))
    ruta_jpg = (ruta_outputs / "jpg_normalizados").resolve()
    ruta_puente = (ruta_outputs / "puente_ui").resolve()

    rutas = Rutas(
        base=base,
        bd_sql=(base / "bd" / "sql").resolve(),
        datos=(base / "datos").resolve(),
        modelos=(base / "modelos").resolve(),
        outputs=ruta_outputs,
        originales_subidos=ruta_originales,
        jpg_normalizados=ruta_jpg,
        puente_ui=ruta_puente,
    )

    # Crear carpetas necesarias
    for carpeta in [rutas.outputs, rutas.originales_subidos, rutas.jpg_normalizados, rutas.puente_ui]:
        carpeta.mkdir(parents=True, exist_ok=True)

    # MySQL
    mysql = ConfiguracionMySQL(
        host=leer_env_str("MYSQL_HOST", "localhost"),
        port=leer_env_int("MYSQL_PORT", 3306),
        database=leer_env_str("MYSQL_DATABASE", "contabilidad_familiar"),
        user=leer_env_str("MYSQL_USER", "root"),
        password=leer_env_str("MYSQL_PASSWORD", "root"),
    )

    # Qwen
    qwen = ConfiguracionQwen(
        model_id=resolver_model_id(base, leer_env_str("QWEN_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct")),
        base_model_path=resolver_ruta_local(base, leer_env_str("QWEN_BASE_MODEL_PATH", r"modelos\extracción\modelo_base")),
        strict_local_only=leer_env_bool("QWEN_STRICT_LOCAL", True),
        dtype=leer_env_str("QWEN_DTYPE", "float16"),
        load_in_4bit=leer_env_bool("QWEN_LOAD_IN_4BIT", False),
        max_new_tokens=leer_env_int("QWEN_MAX_NEW_TOKENS", 768),
    )

    return ConfigProyecto(
        nombre=nombre,
        entorno=entorno,
        log_level=log_level,
        rutas=rutas,
        mysql=mysql,
        qwen=qwen,
    )