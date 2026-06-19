from __future__ import annotations

import mimetypes
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from mysql.connector.connection import MySQLConnection

# Resolver ruta base antes de cualquier import del proyecto
ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[1]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

from utilidades.puente_ui import (
    asegurar_puente,
    escribir_estado,
    leer_estado,
    leer_respuesta_una_vez,
)
from utilidades.configuracion import ConfigProyecto, cargar_config, cargar_env_desde_raiz
from bd.bootstrap_bd import main as ejecutar_inicializacion_bd
from bd.conexion import crear_conexion_mysql
from bd.publicar_y_materializar import publicar_y_materializar_documento

from pipeline.modulos.repositorio_documentos import RepositorioDocumentosMySQL
from pipeline.modulos.modulo_ingesta import EntradaIngesta, ingestar_documento
from pipeline.modulos.modulo_preprocesado import EntradaPreprocesado, ejecutar_preprocesado
from pipeline.modulos.repositorio_extraccion_qwen import RepositorioExtraccionesQwenMySQL
from pipeline.modulos.motor_qwen_transformers import ConfigMotorQwen, MotorQwenTransformers
from pipeline.modulos.modulo_extraccion_qwen import (
    ValidadorDocumentoGastoV2,
    extraer_documento,
)
from pipeline.modulos.modulo_normalizacion import normalizar_documento_gasto
from pipeline.modulos.validacion_contable import validar_documento_contable
from pipeline.modulos.modulo_clasificacion import ejecutar_clasificacion_documento
from pipeline.modulos.modulo_predicciones import ejecutar_predicciones_temporales




@dataclass
class ResultadoRellenoEventos:
    fecha_inicio: Optional[date]
    fecha_fin: Optional[date]
    dias_ejecutados: int
    ejecutado: bool
    motivo: Optional[str] = None


def lanzar_streamlit_ui() -> Optional[subprocess.Popen]:
    ruta_streamlit = ruta_raiz / "apps" / "streamlit_app.py"
    if not ruta_streamlit.exists():
        print(f"Streamlit no encontrado en: {ruta_streamlit}")
        return None

    cmd = [sys.executable, "-m", "streamlit", "run", str(ruta_streamlit)]
    creationflags = subprocess.CREATE_NEW_CONSOLE if sys.platform.startswith("win") else 0
    try:
        return subprocess.Popen(cmd, cwd=str(ruta_raiz), creationflags=creationflags)
    except Exception as exc:
        print(f"No se pudo iniciar Streamlit: {exc}")
        return None


def cerrar_streamlit_ui(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def convertir_fecha_json_a_date(texto_fecha: str) -> Optional[date]:
    """Convierte 'YYYY-MM-DD' a date. Devuelve None si falla"""
    try:
        return datetime.strptime(texto_fecha.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def calcular_primer_dia_mes(fecha: date) -> date:
    return date(fecha.year, fecha.month, 1)


def obtener_rango_relleno(conexion: MySQLConnection) -> Tuple[Optional[date], Optional[date], Optional[str]]:
    """Devuelve (fecha_inicio, fecha_fin, motivo_si_no_hay)"""
    cursor = conexion.cursor()
    try:
        # 1) fecha_min desde normalizaciones (estado actual) con ok_normalizacion=1
        cursor.execute(
            """
            SELECT
              MIN(STR_TO_DATE(
                JSON_UNQUOTE(JSON_EXTRACT(documento_normalizado_json, '$.fecha')),
                '%Y-%m-%d'
              )) AS fecha_min
            FROM normalizaciones
            WHERE ok_normalizacion = 1
              AND JSON_EXTRACT(documento_normalizado_json, '$.fecha') IS NOT NULL
            """
        )
        fila = cursor.fetchone()
        fecha_min = fila[0] if fila else None
        if fecha_min is None:
            return None, None, "sin_normalizaciones_ok"

        fecha_min_date = fecha_min if isinstance(fecha_min, date) else fecha_min.date()
        fecha_min_redondeada = calcular_primer_dia_mes(fecha_min_date)

        # 2) fecha_max_ocurrencia en eventos_ocurrencias
        cursor.execute("SELECT MAX(fecha) AS fecha_max FROM eventos_ocurrencias")
        fila2 = cursor.fetchone()
        fecha_max = fila2[0] if fila2 else None

        if fecha_max is None:
            fecha_inicio = fecha_min_redondeada
        else:
            fecha_max_date = fecha_max if isinstance(fecha_max, date) else fecha_max.date()
            fecha_inicio = max(fecha_min_redondeada, fecha_max_date + timedelta(days=1))

        fecha_fin = date.today()

        if fecha_inicio > fecha_fin:
            return None, None, "ya_al_dia"

        return fecha_inicio, fecha_fin, None
    finally:
        cursor.close()


def ejecutar_relleno_eventos_incremental(conexion: MySQLConnection) -> ResultadoRellenoEventos:
    """Relleno incremental (mecanismo principal) llamando sp_imputar_eventos_en_fecha(fecha)"""
    fecha_inicio, fecha_fin, motivo = obtener_rango_relleno(conexion)
    if motivo is not None:
        return ResultadoRellenoEventos(
            fecha_inicio=None,
            fecha_fin=None,
            dias_ejecutados=0,
            ejecutado=False,
            motivo=motivo,
        )

    cursor = conexion.cursor()
    dias = 0
    try:
        fecha_actual = fecha_inicio
        while fecha_actual <= fecha_fin:
            cursor.execute("CALL sp_imputar_eventos_en_fecha(%s)", (fecha_actual,))
            dias += 1
            fecha_actual = fecha_actual + timedelta(days=1)
        conexion.commit()
        return ResultadoRellenoEventos(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            dias_ejecutados=dias,
            ejecutado=True,
            motivo=None,
        )
    except Exception:
        conexion.rollback()
        raise
    finally:
        cursor.close()


def leer_archivo_como_entrada_ingesta(ruta_entrada: Path) -> EntradaIngesta:
    bytes_archivo = ruta_entrada.read_bytes()
    mime, _ = mimetypes.guess_type(str(ruta_entrada))
    mime_type = mime or "application/octet-stream"
    return EntradaIngesta(
        bytes_archivo=bytes_archivo,
        nombre_original=ruta_entrada.name,
        mime_type=mime_type,
    )


def cargar_schema_validador(cfg: ConfigProyecto) -> ValidadorDocumentoGastoV2:
    ruta_schema = cfg.rutas.datos / "JSON schema v2.json"
    if not ruta_schema.exists():
        candidatos = list(cfg.rutas.datos.glob("*.json"))
        if candidatos:
            ruta_schema = candidatos[0]
    return ValidadorDocumentoGastoV2(str(ruta_schema))


def crear_motor_qwen(cfg: ConfigProyecto) -> MotorQwenTransformers:
    config_motor = ConfigMotorQwen(
        model_id=str(cfg.qwen.model_id),
        adapter_id=str(cfg.qwen.adapter_id),
        base_model_path=str(cfg.qwen.base_model_path),
        strict_local_only=bool(cfg.qwen.strict_local_only),
        dtype=str(cfg.qwen.dtype),
        device_map=str(cfg.qwen.device_map),
        load_in_4bit=bool(cfg.qwen.load_in_4bit),
        max_new_tokens=int(cfg.qwen.max_new_tokens),
        attn_implementation=str(cfg.qwen.attn_implementation),
        force_sdpa_math=bool(cfg.qwen.force_sdpa_math),
        target_height=int(cfg.qwen.target_height),
        max_pixels=int(cfg.qwen.max_pixels),
        debug=bool(cfg.qwen.debug),
    )
    return MotorQwenTransformers(config_motor)


def crear_borrador_documento_vacio() -> Dict[str, Any]:
    return {
        "empresa": "",
        "fecha": "",
        "items": [],
        "totales": {
            "descuento_total": None,
            "total": None,
        },
        "impuestos": {
            "iva_incluido_en_precios": False,
            "resumen_impuestos": [],
            "mapa_codigo_impuesto": {},
        },
    }


def guardar_borrador_normalizacion(
    *,
    conexion: MySQLConnection,
    repositorio_documentos: RepositorioDocumentosMySQL,
    id_documento: int,
    documento_json: Dict[str, Any],
    avisos: list[str],
    error_mensaje: Optional[str] = None,
    version_modulo: str = "W5_BORRADOR",
) -> None:
    doc_json = repositorio_documentos.json_dumps_normalizacion(documento_json)
    avisos_json = repositorio_documentos.json_dumps({"avisos": avisos})

    sql_hist = """
    INSERT INTO normalizaciones_hist (
        id_documento,
        ok_normalizacion,
        documento_normalizado_json,
        avisos_json,
        error_mensaje,
        version_modulo,
        origen
    )
    VALUES (%s, %s, %s, %s, %s, %s, 'W4')
    """

    cur = conexion.cursor()
    try:
        cur.execute(
            sql_hist,
            (
                int(id_documento),
                0,
                doc_json,
                avisos_json,
                error_mensaje,
                version_modulo,
            ),
        )
        conexion.commit()
    finally:
        cur.close()


def leer_documento_normalizado_actual(
    *,
    conexion: MySQLConnection,
    repositorio_documentos: RepositorioDocumentosMySQL,
    id_documento: int,
) -> Optional[Dict[str, Any]]:
    cur = conexion.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT documento_normalizado_json FROM normalizaciones WHERE id_documento=%s",
            (int(id_documento),),
        )
        fila = cur.fetchone()
        if not fila or not fila.get("documento_normalizado_json"):
            cur.execute(
                """
                SELECT documento_normalizado_json
                FROM normalizaciones_hist
                WHERE id_documento=%s
                ORDER BY id_normalizacion DESC
                LIMIT 1
                """,
                (int(id_documento),),
            )
            fila = cur.fetchone()

        if not fila or not fila.get("documento_normalizado_json"):
            return None

        doc = repositorio_documentos.json_loads_seguro(fila.get("documento_normalizado_json"))
        return doc if isinstance(doc, dict) else None
    finally:
        cur.close()


def ejecutar_flujo_completo(
    cfg: ConfigProyecto,
    conexion: MySQLConnection,
    ruta_entrada: Path,
    motor_qwen: MotorQwenTransformers,
    validador: ValidadorDocumentoGastoV2,
) -> int:
    """Ejecuta Ingesta-Preprocesado-Extracción-Normalización-Validación usurio-Validación contable (bucle hasta OK)-Clasificación-Materialización"""
    repositorio_documentos = RepositorioDocumentosMySQL(conexion, cfg)
    repositorio_extracciones = RepositorioExtraccionesQwenMySQL(conexion)


    ## Ingesta

    escribir_estado(fase="procesando", mensaje="Ingestando...", ruta_entrada=str(ruta_entrada), cfg=cfg)
    entrada_ingesta = leer_archivo_como_entrada_ingesta(ruta_entrada)
    salida_ingesta = ingestar_documento(entrada_ingesta, repositorio_documentos, cfg)
    id_documento = salida_ingesta.id_documento


    ## Preprocesado

    escribir_estado(
        fase="procesando",
        mensaje=f"Preprocesando (id_documento={id_documento})...",
        ruta_entrada=str(ruta_entrada),
        id_documento=id_documento,
        cfg=cfg,
    )
    salida_pre = ejecutar_preprocesado(
        EntradaPreprocesado(id_documento=id_documento),
        repositorio_documentos,
        cfg,
    )
    if salida_pre.estado != "OK":
        escribir_estado(
            fase="final",
            mensaje=f"Preprocesado KO (id_documento={id_documento}). Revisa logs/avisos.",
            id_documento=id_documento,
            cfg=cfg,
        )
        return id_documento


    ## Extracción

    escribir_estado(
        fase="procesando",
        mensaje=f"Extrayendo con Qwen (id_documento={id_documento})...",
        id_documento=id_documento,
        cfg=cfg,
    )
    resultado_extraccion = extraer_documento(
        id_documento=id_documento,
        documentos_repositorio=repositorio_documentos,
        extracciones_repositorio=repositorio_extracciones,
        motor_vlm=motor_qwen,
        validador=validador,
        modelo_nombre=motor_qwen.config.model_id,
    )

    ## Normalización y revisión (bifurcación si extracción falla)

    if not bool(getattr(resultado_extraccion, "ok", False)):
        # 1) Si falla extracción: crear borrador vacío, revisión usuario, luego normalizar
        borrador = crear_borrador_documento_vacio()
        guardar_borrador_normalizacion(
            conexion=conexion,
            repositorio_documentos=repositorio_documentos,
            id_documento=id_documento,
            documento_json=borrador,
            avisos=["EXTRACCION_NOK_BORRADOR"],
            error_mensaje="extraccion_ko",
            version_modulo="W5_BORRADOR",
        )

        mensaje_revision = "La extracción falló. Completa el formulario manualmente y pulsa Guardar."
        motivo_revision = "extraccion_ko"

        while True:
            escribir_estado(
                fase="revision",
                mensaje=mensaje_revision,
                id_documento=id_documento,
                motivo_repetir_revision=motivo_revision,
                cfg=cfg,
            )
            leer_respuesta_una_vez(cfg=cfg, id_documento_esperado=id_documento, tiempo_espera_segundos=None)

            documento_ui = leer_documento_normalizado_actual(
                conexion=conexion,
                repositorio_documentos=repositorio_documentos,
                id_documento=id_documento,
            )
            if not documento_ui:
                guardar_borrador_normalizacion(
                    conexion=conexion,
                    repositorio_documentos=repositorio_documentos,
                    id_documento=id_documento,
                    documento_json=borrador,
                    avisos=["BORRADOR_SIN_DATOS"],
                    error_mensaje="documento_no_encontrado",
                    version_modulo="W5_BORRADOR",
                )
                mensaje_revision = "No se pudo leer el borrador guardado. Revisa y vuelve a Guardar."
                motivo_revision = "documento_no_encontrado"
                continue

            escribir_estado(
                fase="procesando",
                mensaje=f"Normalizando (id_documento={id_documento})...",
                id_documento=id_documento,
                cfg=cfg,
            )
            salida_normalizacion = normalizar_documento_gasto(
                {
                    "id_documento": id_documento,
                    "ok_extraccion": True,
                    "json_extraido": documento_ui,
                }
            )
            ok_normalizacion = bool(salida_normalizacion.get("ok_normalizacion", False))
            documento_normalizado = salida_normalizacion.get("documento_normalizado")
            avisos_normalizacion = salida_normalizacion.get("avisos", [])

            if ok_normalizacion:
                repositorio_documentos.guardar_normalizacion(
                    id_documento=id_documento,
                    ok_normalizacion=ok_normalizacion,
                    documento_normalizado_json=documento_normalizado,
                    avisos_json={"avisos": avisos_normalizacion},
                    error_mensaje=None,
                    version_modulo="W4",
                )
                break

            guardar_borrador_normalizacion(
                conexion=conexion,
                repositorio_documentos=repositorio_documentos,
                id_documento=id_documento,
                documento_json=documento_ui,
                avisos=avisos_normalizacion,
                error_mensaje="normalizacion_ko",
                version_modulo="W5_BORRADOR",
            )
            motivo_revision = avisos_normalizacion[0] if avisos_normalizacion else "normalizacion_ko"
            mensaje_revision = "Normalización KO. Revisa campos obligatorios y vuelve a Guardar."
    else:
        # 2) Si extracción ok: normalización estándar y revisión usuario
        escribir_estado(
            fase="procesando",
            mensaje=f"Normalizando (id_documento={id_documento})...",
            id_documento=id_documento,
            cfg=cfg,
        )
        salida_normalizacion = normalizar_documento_gasto(
            {
                "id_documento": id_documento,
                "ok_extraccion": True,
                "json_extraido": resultado_extraccion.json_extraido,
            }
        )
        ok_normalizacion = bool(salida_normalizacion.get("ok_normalizacion", False))
        documento_normalizado = salida_normalizacion.get("documento_normalizado")
        avisos_normalizacion = salida_normalizacion.get("avisos", [])

        repositorio_documentos.guardar_normalizacion(
            id_documento=id_documento,
            ok_normalizacion=ok_normalizacion,
            documento_normalizado_json=documento_normalizado,
            avisos_json={"avisos": avisos_normalizacion},
            error_mensaje=None if ok_normalizacion else "normalizacion_ko",
            version_modulo="W4",
        )

        escribir_estado(
            fase="revision",
            mensaje="Revisa el formulario y pulsa Aceptar o Guardar cambios.",
            id_documento=id_documento,
            motivo_repetir_revision=None,
            cfg=cfg,
        )
        leer_respuesta_una_vez(cfg=cfg, id_documento_esperado=id_documento, tiempo_espera_segundos=None)


    ## Validación contable (bucle infinito hasta OK)

    while True:
        escribir_estado(
            fase="procesando",
            mensaje=f"Validando contablemente (id_documento={id_documento})...",
            id_documento=id_documento,
            cfg=cfg,
        )
        resultado_validacion = validar_documento_contable(id_documento, conn=conexion)

        ok_validacion = bool(getattr(resultado_validacion, "ok", False))
        if not ok_validacion and hasattr(resultado_validacion, "ok_validacion"):
            ok_validacion = bool(getattr(resultado_validacion, "ok_validacion", False))

        if ok_validacion:
            break

        motivo = None
        errores = getattr(resultado_validacion, "errores", None)
        if isinstance(errores, list) and errores:
            primero = errores[0]
            motivo = primero.get("codigo") if isinstance(primero, dict) else str(primero)
        if not motivo:
            motivo = "validacion_nok"

        escribir_estado(
            fase="revision",
            mensaje="Validación contable NOK. Corrige y vuelve a Aceptar/Guardar.",
            id_documento=id_documento,
            motivo_repetir_revision=motivo,
            cfg=cfg,
        )
        leer_respuesta_una_vez(cfg=cfg, id_documento_esperado=id_documento, tiempo_espera_segundos=None)


    ## Clasificación

    escribir_estado(
        fase="procesando",
        mensaje=f"Clasificando (id_documento={id_documento})...",
        id_documento=id_documento,
        cfg=cfg,
    )

    ejecutar_clasificacion_documento(conexion, id_documento=id_documento)


    ## Materialización a tablas de análisis (PowerBI)

    escribir_estado(
        fase="procesando",
        mensaje=f"Materializando tablas de consumo (id_documento={id_documento})...",
        id_documento=id_documento,
        cfg=cfg,
    )
    cursor_materializacion = conexion.cursor()
    try:
        publicar_y_materializar_documento(cursor_materializacion, id_documento)
        conexion.commit()
    finally:
        cursor_materializacion.close()


    ## Predicciones temporales y persistencia

    escribir_estado(
        fase="procesando",
        mensaje=f"Calculando predicciones temporales (id_documento={id_documento})...",
        id_documento=id_documento,
        cfg=cfg,
    )
    ejecutar_predicciones_temporales(generar_salidas=False)

    escribir_estado(
        fase="final",
        mensaje=f"✅ Proceso completado (id_documento={id_documento})",
        id_documento=id_documento,
        cfg=cfg,
    )
    return id_documento


def ejecutar_orquestador() -> None:
    cargar_env_desde_raiz()
    cfg = cargar_config()
    asegurar_puente(cfg)

    streamlit_proc = lanzar_streamlit_ui()

    conexion: Optional[MySQLConnection] = None


    try:
        # Asegurar BD/rutinas/eventos (idempotente)
        ejecutar_inicializacion_bd()

        # Conexión única del orquestador
        conexion = crear_conexion_mysql(cfg)
        # Relleno incremental (mecanismo principal)
        escribir_estado(
            fase="procesando",
            mensaje="Arranque: relleno incremental de eventos...",
            cfg=cfg,
        )
        resultado_relleno_eventos = ejecutar_relleno_eventos_incremental(conexion)
        if resultado_relleno_eventos.ejecutado:
            escribir_estado(
                fase="procesando",
                mensaje=f"Relleno eventos OK: {resultado_relleno_eventos.dias_ejecutados} días",
                cfg=cfg,
            )
        else:
            escribir_estado(
                fase="procesando",
                mensaje=f"Relleno eventos omitido: {resultado_relleno_eventos.motivo}",
                cfg=cfg,
            )

        # Cargar recursos pesados una sola vez
        escribir_estado(fase="procesando", mensaje="Cargando validador y motor Qwen...", cfg=cfg)
        validador = cargar_schema_validador(cfg)
        motor_qwen = crear_motor_qwen(cfg)

        # Estado inicial
        escribir_estado(fase="carga", mensaje="Sube un archivo en la UI para comenzar.", cfg=cfg)

        # Bucle principal: espera a que Streamlit marque fase=procesando con ruta_entrada
        ultimo_job_ui: Optional[Tuple[str, str]] = None

        while True:
            estado = leer_estado(cfg)
            if not estado:
                time.sleep(0.5)
                continue

            fase = (estado.get("fase") or "").strip().lower()
            ruta_entrada_txt = estado.get("ruta_entrada")

            if fase == "procesando" and ruta_entrada_txt:
                job = (fase, ruta_entrada_txt)
                if job == ultimo_job_ui:
                    time.sleep(0.5)
                    continue
                ultimo_job_ui = job

                ruta_entrada = Path(ruta_entrada_txt)

                escribir_estado(
                    fase="procesando",
                    mensaje="Orden recibida por el orquestador. Procesando...",
                    ruta_entrada=str(ruta_entrada),
                    cfg=cfg,
                )

                if not ruta_entrada.exists():
                    escribir_estado(
                        fase="final",
                        mensaje=f"Archivo no encontrado: {ruta_entrada_txt}",
                        cfg=cfg,
                    )
                    time.sleep(0.5)
                    continue

                try:
                    ejecutar_flujo_completo(
                        cfg=cfg,
                        conexion=conexion,
                        ruta_entrada=ruta_entrada,
                        motor_qwen=motor_qwen,
                        validador=validador,
                    )
                except Exception as error:
                    tb = traceback.format_exc()
                    print(tb)
                    escribir_estado(
                        fase="final",
                        mensaje=f"Error en orquestador: {type(error).__name__}: {error}\\n\\n{tb}",
                        cfg=cfg,
                    )


            time.sleep(0.5)
    finally:
        try:
            if conexion is not None:
                conexion.close()
        finally:
            cerrar_streamlit_ui(streamlit_proc)


if __name__ == "__main__":
    ejecutar_orquestador()