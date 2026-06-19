from __future__ import annotations

import sys
from pathlib import Path

# Resolver ruta base antes de cualquier import
ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[2]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

import os
import json
import re
import logging
import unicodedata
import ast
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd
from mysql.connector.connection import MySQLConnection

from utilidades.configuracion import cargar_config, ConfigProyecto
from bd.conexion import crear_conexion_mysql
from utilidades.puente_ui import escribir_respuesta


registro = logging.getLogger(__name__)



# UTILIDADES DE CONVERSIÓN Y PARSEO


def convertir_a_diccionario(valor):
    """Convierte JSON string o bytes a diccionario"""
    if valor is None:
        return None

    if isinstance(valor, (dict, list)):
        return desempaquetar_valor_si_aplica(valor)

    if isinstance(valor, (bytes, bytearray)):
        valor = valor.decode("utf-8", errors="ignore")

    if isinstance(valor, str):
        texto = valor.strip()
        if texto == "":
            return None

        try:
            objeto = json.loads(texto)
            return desempaquetar_valor_si_aplica(objeto)
        except Exception:
            return interpretar_texto_tipo_python(texto)

    return valor


def desempaquetar_valor_si_aplica(objeto):
    """Desempaqueta {"valor": "x"} a su contenido"""
    if (
        isinstance(objeto, dict)
        and set(objeto.keys()) == {"valor"}
        and isinstance(objeto.get("valor"), str)
    ):
        texto_interno = objeto.get("valor").strip()
        return interpretar_texto_tipo_python(texto_interno)
    return objeto


def interpretar_texto_tipo_python(texto):
    """Interpreta texto (dict, list, etc)"""
    if texto is None:
        return None

    texto = str(texto).strip()
    if texto == "":
        return None

    # Eliminar Decimal
    texto = re.sub(
        r"Decimal\(\s*['\"]([+-]?[0-9]+(?:\.[0-9]+)?)['\"]s*\)",
        r"\1",
        texto,
    )

    try:
        return ast.literal_eval(texto)
    except Exception:
        return {"valor": texto}


def normalizar_texto_empresa(texto):
    """Normaliza nombre de empresa"""
    if texto is None:
        return None
    texto = str(texto).strip()
    if not texto:
        return None
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join([c for c in texto if not unicodedata.combining(c)])
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = texto.title()
    return texto


def parsear_fecha_a_sql(valor):
    """Parsea fecha a formato SQL (YYYY-MM-DD)"""
    if valor is None:
        return None, False
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor.isoformat(), False
    
    texto = str(valor).strip()
    if not texto:
        return None, False

    patrones = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"]
    for patron in patrones:
        try:
            fecha = datetime.strptime(texto, patron).date()
            return fecha.isoformat(), patron != "%Y-%m-%d"
        except ValueError:
            continue

    # Parseo de meses
    texto_limpio = texto.lower().replace(".", "").strip()
    meses = {
        "ene": "01", "enero": "01",
        "feb": "02", "febrero": "02",
        "mar": "03", "marzo": "03",
        "abr": "04", "abril": "04",
        "may": "05", "mayo": "05",
        "jun": "06", "junio": "06",
        "jul": "07", "julio": "07",
        "ago": "08", "agosto": "08",
        "sep": "09", "sept": "09", "set": "09", "sept.": "09", "septiembre": "09",
        "oct": "10", "octubre": "10",
        "nov": "11", "noviembre": "11",
        "dic": "12", "diciembre": "12",
    }
    texto_limpio = texto_limpio.replace("de", " ")
    texto_limpio = re.sub(r"\s+", " ", texto_limpio).strip()

    partes = texto_limpio.split(" ")
    if len(partes) >= 3:
        try:
            dia = int(partes[0])
            mes_texto = partes[1]
            ano = int(partes[2])
            mes = meses.get(mes_texto)
            if mes:
                fecha = date(ano, int(mes), dia)
                return fecha.isoformat(), True
        except Exception:
            pass

    return None, False


def parsear_decimal(valor, decimales, permitir_porcentaje=False):
    """Parsea valor a Decimal"""
    if valor is None:
        return None, False
    if isinstance(valor, (int, float, Decimal)):
        numero = Decimal(str(valor))
        cuantizador = Decimal("1." + ("0" * decimales))
        return float(numero.quantize(cuantizador, rounding=ROUND_HALF_UP)), False

    texto = str(valor).strip()
    if not texto:
        return None, False

    texto = texto.replace("€", "").replace("%", "").strip()
    texto = re.sub(r"\s+", "", texto)

    if permitir_porcentaje and texto.endswith("x"):
        texto = texto[:-1]

    if "," in texto and "." in texto:
        texto = texto.replace(".", "")
        texto = texto.replace(",", ".")
    else:
        texto = texto.replace(",", ".")

    texto = re.sub(r"[^0-9\.\-]", "", texto)
    if texto in ["", "-", ".", "-."]:
        return None, False

    try:
        numero = Decimal(texto)
        cuantizador = Decimal("1." + ("0" * decimales))
        return float(numero.quantize(cuantizador, rounding=ROUND_HALF_UP)), True
    except Exception:
        return None, False


def limpiar_vacios(objeto):
    """Elimina campos None/vacíos"""
    if isinstance(objeto, dict):
        return {
            k: limpiar_vacios(v)
            for k, v in objeto.items()
            if v is not None and v != "" and v != {} and v != []
        }
    elif isinstance(objeto, list):
        return [limpiar_vacios(item) for item in objeto if item is not None and item != "" and item != {} and item != []]
    else:
        return objeto


def construir_documento_desde_ui(
    documento_base: Dict[str, Any],
    df_items: pd.DataFrame,
    df_mapa_codigo_impuesto: pd.DataFrame,
    df_resumen_impuestos: pd.DataFrame,
) -> Dict[str, Any]:
    """Construye documento JSON desde UI"""
    documento = dict(documento_base)

    # Items
    if not df_items.empty:
        items = []
        for _, row in df_items.iterrows():
            item = {}
            for col in df_items.columns:
                val = row[col]
                if pd.notna(val):
                    item[col] = val
            if item:
                items.append(item)
        if items:
            documento["items"] = items

    # Totales
    totales = documento.get("totales", {})
    if not isinstance(totales, dict):
        totales = {}
    documento["totales"] = totales

    # Impuestos
    impuestos = documento.get("impuestos", {})
    if not isinstance(impuestos, dict):
        impuestos = {}

    # Mapa código impuesto
    if not df_mapa_codigo_impuesto.empty:
        mapa = {}
        for _, row in df_mapa_codigo_impuesto.iterrows():
            cod = row.get("codigo_impuesto")
            val = row.get("valor")
            if pd.notna(cod) and pd.notna(val):
                mapa[str(cod)] = val
        if mapa:
            impuestos["mapa_codigo_impuesto"] = mapa

    # Resumen impuestos
    if not df_resumen_impuestos.empty:
        resumen = []
        for _, row in df_resumen_impuestos.iterrows():
            item = {}
            for col in ["tipo_impuesto", "base_imponible", "importe"]:
                if col in df_resumen_impuestos.columns:
                    val = row[col]
                    if pd.notna(val):
                        item[col] = val
            if item:
                resumen.append(item)
        if resumen:
            impuestos["resumen_impuestos"] = resumen

    if impuestos:
        documento["impuestos"] = impuestos

    return limpiar_vacios(documento)


def calcular_auditoria_cambios(
    documento_base: Dict[str, Any],
    documento_editado: Dict[str, Any],
    comentario_global: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Calcula cambios entre documento base y editado"""
    filas = []

    def comparar_recursivo(obj_viejo, obj_nuevo, ruta=""):
        if isinstance(obj_viejo, dict) and isinstance(obj_nuevo, dict):
            todas_claves = set(obj_viejo.keys()) | set(obj_nuevo.keys())
            for clave in todas_claves:
                nueva_ruta = f"{ruta}.{clave}" if ruta else clave
                val_viejo = obj_viejo.get(clave)
                val_nuevo = obj_nuevo.get(clave)
                comparar_recursivo(val_viejo, val_nuevo, nueva_ruta)
        elif isinstance(obj_viejo, list) and isinstance(obj_nuevo, list):
            # Comparación simple: si difieren, registrar cambio
            if obj_viejo != obj_nuevo:
                filas.append({
                    "campo": ruta,
                    "valor_anterior": json.dumps(obj_viejo, ensure_ascii=False),
                    "valor_nuevo": json.dumps(obj_nuevo, ensure_ascii=False),
                    "comentario": comentario_global,
                })
        else:
            if obj_viejo != obj_nuevo:
                filas.append({
                    "campo": ruta,
                    "valor_anterior": json.dumps(obj_viejo, ensure_ascii=False) if obj_viejo is not None else None,
                    "valor_nuevo": json.dumps(obj_nuevo, ensure_ascii=False) if obj_nuevo is not None else None,
                    "comentario": comentario_global,
                })

    comparar_recursivo(documento_base, documento_editado)
    return filas


def insertar_auditoria(
    conexion: MySQLConnection,
    id_documento: int,
    filas_auditoria: List[Dict[str, Any]],
) -> bool:
    """Inserta cambios en tabla auditoria_cambios"""
    if not filas_auditoria:
        return True

    try:
        cursor = conexion.cursor()
        sql = """
        INSERT INTO auditoria_cambios (
            id_documento, origen, usuario, campo,
            valor_anterior_json, valor_nuevo_json, comentario, fecha_cambio
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """
        for fila in filas_auditoria:
            cursor.execute(sql, (
                id_documento,
                "STREAMLIT",
                os.environ.get("USER", "usuario_desconocido"),
                fila.get("campo", ""),
                fila.get("valor_anterior"),
                fila.get("valor_nuevo"),
                fila.get("comentario"),
            ))
        conexion.commit()
        cursor.close()
        return True
    except Exception as e:
        registro.error(f"Error insertando auditoria: {e}")
        return False


def insertar_normalizaciones_hist(
    conexion: MySQLConnection,
    id_documento: int,
    ok_normalizacion: bool,
    documento_json: Dict[str, Any],
    avisos: List[str],
    error_mensaje: Optional[str] = None,
) -> bool:
    """Inserta en tabla normalizaciones_hist"""
    try:
        cursor = conexion.cursor()
        sql = """
        INSERT INTO normalizaciones_hist (
            id_documento, ok_normalizacion, documento_normalizado_json,
            avisos_json, error_mensaje, version_modulo, origen, fecha_creacion
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(sql, (
            id_documento,
            1 if ok_normalizacion else 0,
            json.dumps(documento_json, ensure_ascii=False) if documento_json else None,
            json.dumps(avisos, ensure_ascii=False),
            error_mensaje,
            "revision_usuario_prod",
            "USUARIO",
        ))
        conexion.commit()
        cursor.close()
        return True
    except Exception as e:
        registro.error(f"Error insertando normalizaciones_hist: {e}")
        return False


def actualizar_normalizaciones_si_ok(
    conexion: MySQLConnection,
    id_documento: int,
    ok_normalizacion: bool,
    documento_json: Dict[str, Any],
) -> bool:
    """Actualiza tabla normalizaciones si ok=True"""
    if not ok_normalizacion:
        return False

    try:
        cursor = conexion.cursor()
        sql = """
        UPDATE normalizaciones
        SET
            ok_normalizacion = 1,
            documento_normalizado_json = %s,
            fecha_modificacion = NOW()
        WHERE id_documento = %s
        """
        cursor.execute(sql, (
            json.dumps(documento_json, ensure_ascii=False),
            id_documento,
        ))
        conexion.commit()
        cursor.close()
        return True
    except Exception as e:
        registro.error(f"Error actualizando normalizaciones: {e}")
        return False



# REVISIÓN EN STREAMLIT


def mostrar_revisor_documento(
    id_documento: int,
    conexion: MySQLConnection,
    cfg: ConfigProyecto,
) -> Dict[str, Any]:
    """Muestra UI Streamlit para revisar un documento"""
    
    # Cargar documento normalizado (o borrador en hist si no hay actual)
    cursor = conexion.cursor(dictionary=True)
    cursor.execute(
        "SELECT documento_normalizado_json FROM normalizaciones WHERE id_documento=%s",
        (id_documento,)
    )
    row = cursor.fetchone()

    if not row or not row.get("documento_normalizado_json"):
        cursor.execute(
            """
            SELECT documento_normalizado_json
            FROM normalizaciones_hist
            WHERE id_documento=%s
            ORDER BY id_normalizacion DESC
            LIMIT 1
            """,
            (id_documento,)
        )
        row = cursor.fetchone()

    cursor.close()
    
    if not row or not row.get("documento_normalizado_json"):
        st.error(f"No hay documento normalizado para id_documento={id_documento}")
        return {"ok": False, "error": "documento_no_encontrado"}
    
    documento_base = convertir_a_diccionario(row["documento_normalizado_json"])
    if not isinstance(documento_base, dict):
        st.error("Error al parsear documento JSON")
        return {"ok": False, "error": "json_invalido"}
    
    st.markdown(f"## Revisión de Documento {id_documento}")
    st.info(f"Revisa los datos extraídos. Puedes editar todos los campos")
    
    # Iniciar session state
    if "doc_id" not in st.session_state or st.session_state.doc_id != id_documento:
        st.session_state.doc_id = id_documento
        st.session_state.empresa = documento_base.get("empresa", "")
        st.session_state.fecha = documento_base.get("fecha", "")
        
        totales = documento_base.get("totales", {})
        st.session_state.descuento_total = totales.get("descuento_total") if isinstance(totales, dict) else None
        st.session_state.total = totales.get("total") if isinstance(totales, dict) else None
        
        impuestos = documento_base.get("impuestos", {})
        st.session_state.iva_incluido = impuestos.get("iva_incluido_en_precios", False) if isinstance(impuestos, dict) else False
        
        # Items
        items = documento_base.get("items", [])
        columnas_items = [
            "concepto",
            "importe_total",
            "unidades",
            "codigo_impuesto",
            "descuento_importe",
            "descuento_porcentaje",
        ]
        if isinstance(items, list) and items:
            st.session_state.items = pd.DataFrame(items)
        else:
            st.session_state.items = pd.DataFrame(columns=columnas_items)
        
        # Impuestos
        if isinstance(impuestos, dict):
            resumen = impuestos.get("resumen_impuestos", [])
            columnas_resumen = ["tipo_impuesto", "base_imponible", "importe"]
            st.session_state.resumen_impuestos = (
                pd.DataFrame(resumen) if isinstance(resumen, list) and resumen else pd.DataFrame(columns=columnas_resumen)
            )
            
            mapa = impuestos.get("mapa_codigo_impuesto", {})
            if isinstance(mapa, dict):
                mapa_items = [{"codigo": k, "valor": v} for k, v in mapa.items()]
                st.session_state.mapa_impuesto = pd.DataFrame(mapa_items)
            else:
                st.session_state.mapa_impuesto = pd.DataFrame(columns=["codigo", "valor"])
        else:
            st.session_state.resumen_impuestos = pd.DataFrame(columns=["tipo_impuesto", "base_imponible", "importe"])
            st.session_state.mapa_impuesto = pd.DataFrame(columns=["codigo", "valor"])

    if st.session_state.get("mapa_impuesto") is None or st.session_state.mapa_impuesto.empty:
        st.session_state.mapa_impuesto = pd.DataFrame([
            {"codigo": "", "valor": ""}
        ])

    # Formulario
    st.markdown("### Datos principales")
    col1, col2 = st.columns(2)
    with col1:
        empresa = st.text_input("Empresa", value=st.session_state.get("empresa", ""))
    with col2:
        fecha = st.text_input("Fecha (YYYY-MM-DD)", value=st.session_state.get("fecha", ""))
    
    st.markdown("### Items")
    columnas_items = [
        "concepto",
        "importe_total",
        "unidades",
        "codigo_impuesto",
        "descuento_porcentaje",
        "descuento_importe",
    ]
    df_items_base = st.session_state.get("items", pd.DataFrame(columns=columnas_items))
    df_items_base = df_items_base.reindex(columns=columnas_items)
    df_items_base = df_items_base.astype({
        "concepto": "string",
        "importe_total": "string",
        "unidades": "string",
        "codigo_impuesto": "string",
        "descuento_porcentaje": "string",
        "descuento_importe": "string",
    })
    df_items = st.data_editor(
        df_items_base,
        width="stretch",
        num_rows="dynamic",
        key=f"rev_items_{id_documento}",
        column_config={
            "concepto": st.column_config.TextColumn("Concepto"),
            "importe_total": st.column_config.TextColumn("Importe total"),
            "unidades": st.column_config.TextColumn("Unidades"),
            "codigo_impuesto": st.column_config.TextColumn("Código impuesto"),
            "descuento_porcentaje": st.column_config.TextColumn("Descuento %"),
            "descuento_importe": st.column_config.TextColumn("Descuento importe"),
        },
    )
    
    st.markdown("### Totales")
    col1, col2 = st.columns(2)
    with col1:
        descuento = st.text_input(
            "Descuento total",
            value=str(st.session_state.get("descuento_total") or "")
        )
    with col2:
        total = st.text_input(
            "Total",
            value=str(st.session_state.get("total") or "")
        )
    
    st.markdown("### Impuestos")
    iva_incluido = st.checkbox("IVA incluido en precios", value=st.session_state.get("iva_incluido", False))
    
    st.markdown("### Resumen de Impuestos")
    df_resumen = st.data_editor(
        st.session_state.get("resumen_impuestos", pd.DataFrame(columns=["tipo_impuesto", "base_imponible", "importe"])),
        width="stretch",
        num_rows="dynamic",
        key=f"rev_resumen_impuestos_{id_documento}",
    )
    
    st.markdown("### Mapa Código Impuestos")
    df_mapa = st.data_editor(
        st.session_state.get("mapa_impuesto", pd.DataFrame(columns=["codigo", "valor"])),
        width="stretch",
        num_rows="dynamic",
        key=f"rev_mapa_impuesto_{id_documento}",
        disabled=False,
    )
    
    st.markdown("### Comentarios")
    comentarios = st.text_area("Anotaciones (opcional)", value="")
    
    col1, col2 = st.columns(2)
    with col1:
        submit_guardar = st.button("💾 Guardar cambios", type="primary")
    with col2:
        submit_aceptar = st.button("✅ Aceptar sin cambios")
    
    # Procesar envío del formulario
    if submit_guardar or submit_aceptar:
        # Parsear y construir documento editado
        fecha_iso, _ = parsear_fecha_a_sql(fecha)
        empresa_norm = normalizar_texto_empresa(empresa)
        descuento_val, _ = parsear_decimal(descuento, 2)
        total_val, _ = parsear_decimal(total, 2)

        fecha_valor = fecha_iso if fecha_iso else (fecha.strip() if isinstance(fecha, str) else fecha)

        documento_editado = {
            "empresa": empresa_norm,
            "fecha": fecha_valor,
            "items": df_items.to_dict(orient="records") if not df_items.empty else [],
            "totales": {
                "descuento_total": descuento_val,
                "total": total_val,
            },
            "impuestos": {
                "iva_incluido_en_precios": bool(iva_incluido),
                "resumen_impuestos": df_resumen.to_dict(orient="records") if not df_resumen.empty else [],
                "mapa_codigo_impuesto": (
                    dict(zip(df_mapa.get("codigo", []), df_mapa.get("valor", [])))
                    if not df_mapa.empty else {}
                ),
            },
        }

        documento_editado = limpiar_vacios(documento_editado)

        # Calcular cambios
        cambios = calcular_auditoria_cambios(
            documento_base,
            documento_editado,
            comentarios if comentarios else None
        )

        hay_cambios = len(cambios) > 0

  
        # Botón "Aceptar"

        if submit_aceptar and hay_cambios:
            st.warning("Has realizado cambios, pero NO se van a guardar")
            col_a, col_b = st.columns(2)
            with col_a:
                confirmar = st.button("Aceptar sin guardar", type="primary")
            with col_b:
                cancelar = st.button("Cancelar")

            if cancelar:
                st.info("Cancelado. Puedes seguir editando y guardar si lo deseas")
                return {"ok": False, "error": "cancelado_por_usuario"}

            if confirmar:
                escribir_respuesta(accion="aceptar", id_documento=id_documento, cfg=cfg)
                st.success("✅ Continuando sin guardar cambios")
                st.rerun()
                return {
                    "ok": True,
                    "id_documento": id_documento,
                    "documento": documento_base,
                    "cambios_guardados": 0,
                    "accion": "aceptar",
                }

            # Esperar decisión del usuario
            return {"ok": False, "error": "confirmacion_pendiente"}


        # Guardar solo si hay cambios. Si no, como aceptar

        if submit_guardar and not hay_cambios:
            escribir_respuesta(accion="aceptar", id_documento=id_documento, cfg=cfg)
            st.success("✅ No había cambios. Continuando")
            st.rerun()
            return {
                "ok": True,
                "id_documento": id_documento,
                "documento": documento_base,
                "cambios_guardados": 0,
                "accion": "aceptar",
            }

   
        # Aceptar sin cambios: continuar sin persistir
 
        if submit_aceptar and not hay_cambios:
            escribir_respuesta(accion="aceptar", id_documento=id_documento, cfg=cfg)
            st.success("✅ Continuando")
            st.rerun()
            return {
                "ok": True,
                "id_documento": id_documento,
                "documento": documento_base,
                "cambios_guardados": 0,
                "accion": "aceptar",
            }

 
        # Guardar con cambios: persistir auditoría + snapshot y continuar

        if submit_guardar and hay_cambios:
            insertar_auditoria(conexion, id_documento, cambios)

            ok_normalizacion = True
            insertar_normalizaciones_hist(
                conexion,
                id_documento,
                ok_normalizacion,
                documento_editado,
                ["revisado_por_usuario"],
            )

            actualizar_normalizaciones_si_ok(
                conexion,
                id_documento,
                ok_normalizacion,
                documento_editado,
            )

            escribir_respuesta(accion="guardar", id_documento=id_documento, cfg=cfg)

            st.success("✅ Documento guardado exitosamente")
            st.rerun()
            return {
                "ok": True,
                "id_documento": id_documento,
                "documento": documento_editado,
                "cambios_guardados": len(cambios),
                "accion": "guardar",
            }

    return {"ok": False, "error": "esperando_entrada_usuario"}


def ejecutar_aplicacion_revision_usuario():
    """Función de entrada para streamlit_app.py"""
    st.set_page_config(page_title="Revisión de Documentos", layout="wide")
    
    # Obtener id_documento desde session state o argumentos
    id_documento = st.session_state.get("id_documento_actual")
    
    if not id_documento:
        st.warning("No hay documento cargado. Esto debería llamarse desde el orquestrador")
        return
    
    cfg = cargar_config()
    try:
        conexion = crear_conexion_mysql(cfg)
        resultado = mostrar_revisor_documento(id_documento, conexion, cfg)
        conexion.close()
    except Exception as e:
        st.error(f"Error: {e}")
        registro.error(f"Error en revisión usuario: {e}", exc_info=True)