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
    adapter_id: str
    base_model_path: str
    strict_local_only: bool
    dtype: str
    device_map: str
    load_in_4bit: bool
    max_new_tokens: int
    attn_implementation: str
    force_sdpa_math: bool
    target_height: int
    max_pixels: int
    debug: bool


@dataclass(frozen=True)
class ConfiguracionClasificador:
    model_id: str
    device: str


@dataclass(frozen=True)
class ConfiguracionForecasting:
    model: str
    horizon_months: int


@dataclass(frozen=True)
class ConfiguracionStreamlit:
    port: int


@dataclass(frozen=True)
class Rutas:
    base: Path
    bd_sql: Path
    datos: Path
    modelos: Path
    inputs: Path
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
    clasificador: ConfiguracionClasificador
    forecasting: ConfiguracionForecasting
    streamlit: ConfiguracionStreamlit


## Carga .env


def cargar_env_desde_raiz(ruta_base: Optional[Path] = None) -> None:
    """Carga variables de entorno desde un archivo .env ubicado en la raíz del proyecto.

    Mantiene las variables ya existentes en el entorno para permitir sobrescritura externa
    (por ejemplo, en CI/CD, Docker o despliegues).
    """
    ruta_raiz = Path(ruta_base).resolve() if ruta_base is not None else detectar_raiz_proyecto()
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
    """Devuelve la raíz del proyecto.

    Este archivo vive en utilidades/configuracion.py, por lo que parent.parent apunta
    a la raíz del repositorio.
    """
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
    """Resuelve rutas locales si existen; si no existen, conserva el id remoto HF."""
    valor = (valor_model_id or "").strip()
    if not valor:
        return valor

    ruta = Path(valor)
    if ruta.is_absolute():
        return str(ruta.resolve()) if ruta.exists() else valor

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
    """Carga configuración del proyecto desde .env y variables de entorno."""
    base = Path(ruta_base).resolve() if ruta_base is not None else detectar_raiz_proyecto()

    # Cargar .env
    cargar_env_desde_raiz(base)

    # Identidad: nombres nuevos + compatibilidad con variables antiguas
    nombre = leer_env_str("APP_NAME", leer_env_str("TFM_NOMBRE", "AI_Financial_Intelligence_Platform"))
    entorno = leer_env_str("APP_ENV", leer_env_str("TFM_ENTORNO", "development"))
    log_level = leer_env_str("LOG_LEVEL", "INFO")

    # Rutas
    ruta_inputs = ruta_desde_base(base, leer_env_str("INPUT_DIR", leer_env_str("IMAGENES_USUARIO", "inputs")))
    ruta_outputs = ruta_desde_base(base, leer_env_str("OUTPUT_DIR", "outputs"))
    ruta_jpg = (ruta_outputs / "jpg_normalizados").resolve()
    ruta_puente = ruta_desde_base(base, leer_env_str("PUENTE_UI_DIR", str(ruta_outputs / "puente_ui")))

    rutas = Rutas(
        base=base,
        bd_sql=(base / "bd" / "sql").resolve(),
        datos=(base / "datos").resolve(),
        modelos=(base / "modelos").resolve(),
        inputs=ruta_inputs,
        outputs=ruta_outputs,
        originales_subidos=ruta_inputs,
        jpg_normalizados=ruta_jpg,
        puente_ui=ruta_puente,
    )

    # Crear carpetas necesarias en tiempo de ejecución
    for carpeta in [rutas.inputs, rutas.outputs, rutas.jpg_normalizados, rutas.puente_ui]:
        carpeta.mkdir(parents=True, exist_ok=True)

    # MySQL
    mysql = ConfiguracionMySQL(
        host=leer_env_str("MYSQL_HOST", "localhost"),
        port=leer_env_int("MYSQL_PORT", 3306),
        database=leer_env_str("MYSQL_DATABASE", "financial_intelligence"),
        user=leer_env_str("MYSQL_USER", "root"),
        password=leer_env_str("MYSQL_PASSWORD", "root"),
    )

    # Hugging Face: si HF_TOKEN está definido, las librerías de HF/Transformers lo usarán
    # automáticamente para modelos privados o gated.
    hf_token = leer_env_str("HF_TOKEN", "")
    if hf_token and "HF_TOKEN" not in os.environ:
        os.environ["HF_TOKEN"] = hf_token

    # Qwen
    qwen = ConfiguracionQwen(
        model_id=resolver_model_id(base, leer_env_str("QWEN_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct")),
        adapter_id=resolver_model_id(base, leer_env_str("QWEN_ADAPTER_ID", "")),
        base_model_path=resolver_ruta_local(base, leer_env_str("QWEN_BASE_MODEL_PATH", "")),
        strict_local_only=leer_env_bool("QWEN_STRICT_LOCAL", False),
        dtype=leer_env_str("QWEN_DTYPE", "float16"),
        device_map=leer_env_str("QWEN_DEVICE_MAP", "cuda"),
        load_in_4bit=leer_env_bool("QWEN_LOAD_IN_4BIT", False),
        max_new_tokens=leer_env_int("QWEN_MAX_NEW_TOKENS", 768),
        attn_implementation=leer_env_str("QWEN_ATTN_IMPLEMENTATION", "sdpa"),
        force_sdpa_math=leer_env_bool("QWEN_FORCE_SDPA_MATH", True),
        target_height=leer_env_int("QWEN_TARGET_HEIGHT", 1024),
        max_pixels=leer_env_int("QWEN_MAX_PIXELS", 1024 * 1024),
        debug=leer_env_bool("QWEN_DEBUG", False),
    )

    # Clasificador
    clasificador = ConfiguracionClasificador(
        model_id=leer_env_str("CLASIFICADOR_MODEL_ID", "Showker87/xlmr-financial-classifier"),
        device=leer_env_str("CLASIFICADOR_DEVICE", "cuda"),
    )

    # Forecasting
    forecasting = ConfiguracionForecasting(
        model=leer_env_str("FORECAST_MODEL", "autoarima"),
        horizon_months=leer_env_int("FORECAST_HORIZON_MONTHS", 12),
    )

    # Streamlit
    streamlit = ConfiguracionStreamlit(
        port=leer_env_int("STREAMLIT_PORT", 8501),
    )

    return ConfigProyecto(
        nombre=nombre,
        entorno=entorno,
        log_level=log_level,
        rutas=rutas,
        mysql=mysql,
        qwen=qwen,
        clasificador=clasificador,
        forecasting=forecasting,
        streamlit=streamlit,
    )
