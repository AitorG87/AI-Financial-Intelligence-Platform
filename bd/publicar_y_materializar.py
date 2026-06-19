import sys
from pathlib import Path
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

# Resolver ruta base antes de cualquier import del proyecto
ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[1]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

import mysql.connector

from utilidades.configuracion import cargar_config
from bd.conexion import crear_conexion_mysql


def convertir_decimal(valor: Any) -> Optional[Decimal]:
    if valor is None:
        return None
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    if isinstance(valor, str):
        texto = valor.strip()
        if texto == "":
            return None
        texto = texto.replace(",", ".")
        try:
            return Decimal(texto)
        except InvalidOperation:
            return None
    return None


def convertir_bool(valor: Any) -> Optional[int]:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return 1 if valor else 0
    if isinstance(valor, int):
        return 1 if valor != 0 else 0
    if isinstance(valor, str):
        texto = valor.strip().lower()
        if texto in ["true", "1", "si", "sí", "yes"]:
            return 1
        if texto in ["false", "0", "no"]:
            return 0
    return None


def leer_json_db(valor: Any) -> Dict[str, Any]:
    if valor is None:
        return {}
    if isinstance(valor, (dict, list)):
        return valor  # por si el driver devuelve ya parseado
    if isinstance(valor, (bytes, bytearray)):
        valor = valor.decode("utf-8")
    if isinstance(valor, str):
        return json.loads(valor)
    raise ValueError("No se ha podido interpretar el JSON de la base de datos")


def seleccionar_documentos_candidatos(cursor: mysql.connector.cursor.MySQLCursor, limite: int) -> List[int]:
    """Candidatos: documentos que tienen al menos una normalización histórica OK"""

    cursor.execute(
        """
        SELECT DISTINCT nh.id_documento
        FROM normalizaciones_hist nh
        WHERE nh.ok_normalizacion = 1
        ORDER BY nh.id_documento DESC
        LIMIT %s
        """,
        (limite,),
    )
    filas = cursor.fetchall()
    return [int(f[0]) for f in filas]


def seleccionar_snapshot_publicable(
    cursor: mysql.connector.cursor.MySQLCursor, id_documento: int
) -> Optional[Tuple[int, int]]:
    """
    Devuelve (id_normalizacion, id_clasificacion) si existe un snapshot que cumpla:
    - normalización OK
    - validación contable OK
    - clasificación OK
    Selecciona el más reciente por fecha_creacion
    """
    cursor.execute(
        """
        SELECT
          nh.id_normalizacion,
          ch.id_clasificacion
        FROM normalizaciones_hist nh
        INNER JOIN validaciones_contables vc
          ON vc.id_normalizacion = nh.id_normalizacion AND vc.ok_validacion = 1
        INNER JOIN clasificaciones_hist ch
          ON ch.id_normalizacion = nh.id_normalizacion AND ch.ok_clasificacion = 1
        WHERE nh.id_documento = %s
          AND nh.ok_normalizacion = 1
        ORDER BY nh.fecha_creacion DESC
        LIMIT 1
        """,
        (id_documento,),
    )
    fila = cursor.fetchone()
    if not fila:
        return None
    return int(fila[0]), int(fila[1])


def leer_normalizacion_hist(
    cursor: mysql.connector.cursor.MySQLCursor, id_normalizacion: int
) -> Tuple[int, int, Dict[str, Any], Dict[str, Any], Optional[str], Optional[str]]:
    cursor.execute(
        """
        SELECT id_documento, ok_normalizacion, documento_normalizado_json, avisos_json, error_mensaje, version_modulo
        FROM normalizaciones_hist
        WHERE id_normalizacion = %s
        """,
        (id_normalizacion,),
    )
    fila = cursor.fetchone()
    if not fila:
        raise ValueError(f"No existe normalizaciones_hist.id_normalizacion={id_normalizacion}")

    id_documento = int(fila[0])
    ok_normalizacion = int(fila[1])
    documento = leer_json_db(fila[2])
    avisos = leer_json_db(fila[3])
    error_mensaje = fila[4]
    version_modulo = fila[5]
    return id_documento, ok_normalizacion, documento, avisos, error_mensaje, version_modulo


def actualizar_estado_actual_normalizaciones(
    cursor: mysql.connector.cursor.MySQLCursor,
    id_documento: int,
    ok_normalizacion: int,
    documento: Dict[str, Any],
    avisos: Dict[str, Any],
    error_mensaje: Optional[str],
    version_modulo: Optional[str],
) -> None:
    cursor.execute(
        """
        INSERT INTO normalizaciones
          (id_documento, ok_normalizacion, documento_normalizado_json, avisos_json, error_mensaje, version_modulo)
        VALUES
          (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          ok_normalizacion = VALUES(ok_normalizacion),
          documento_normalizado_json = VALUES(documento_normalizado_json),
          avisos_json = VALUES(avisos_json),
          error_mensaje = VALUES(error_mensaje),
          version_modulo = VALUES(version_modulo),
          fecha_modificacion = CURRENT_TIMESTAMP
        """,
        (
            id_documento,
            ok_normalizacion,
            json.dumps(documento, ensure_ascii=False),
            json.dumps(avisos, ensure_ascii=False),
            error_mensaje,
            version_modulo,
        ),
    )


def actualizar_estado_actual_clasificaciones(
    cursor: mysql.connector.cursor.MySQLCursor,
    id_documento: int,
    id_clasificacion: int,
) -> None:
    cursor.execute(
        """
        SELECT categoria_nivel_1, etiqueta, confianza
        FROM clasificaciones_hist
        WHERE id_clasificacion = %s
        """,
        (id_clasificacion,),
    )
    fila = cursor.fetchone()
    if not fila:
        raise ValueError(f"No existe clasificaciones_hist.id_clasificacion={id_clasificacion}")

    categoria_n1 = str(fila[0])
    etiqueta = str(fila[1])
    confianza = fila[2]

    cursor.execute(
        """
        INSERT INTO clasificaciones
          (id_documento, categoria_nivel_1, etiqueta, confianza, id_clasificacion_fuente)
        VALUES
          (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          categoria_nivel_1 = VALUES(categoria_nivel_1),
          etiqueta = VALUES(etiqueta),
          confianza = VALUES(confianza),
          id_clasificacion_fuente = VALUES(id_clasificacion_fuente),
          fecha_modificacion = CURRENT_TIMESTAMP
        """,
        (id_documento, categoria_n1, etiqueta, confianza, id_clasificacion),
    )


def obtener_o_crear_empresa(cursor: mysql.connector.cursor.MySQLCursor, nombre_normalizado: str) -> int:
    cursor.execute(
        "SELECT id_empresa FROM empresas WHERE nombre_normalizado = %s",
        (nombre_normalizado,),
    )
    fila = cursor.fetchone()
    if fila:
        return int(fila[0])

    cursor.execute(
        "INSERT INTO empresas (nombre_normalizado) VALUES (%s)",
        (nombre_normalizado,),
    )
    return int(cursor.lastrowid)


def materializar_ticket_items_impuestos(
    cursor: mysql.connector.cursor.MySQLCursor,
    id_documento: int,
    id_normalizacion: int,
    id_clasificacion: int,
    documento: Dict[str, Any],
) -> None:
    empresa = str(documento.get("empresa", "")).strip()
    if not empresa:
        raise ValueError("El documento normalizado no contiene 'empresa'")

    fecha = str(documento.get("fecha", "")).strip()
    if not fecha:
        raise ValueError("El documento normalizado no contiene 'fecha'")

    totales = documento.get("totales", {}) or {}
    total = convertir_decimal(totales.get("total"))
    if total is None:
        raise ValueError("El documento normalizado no contiene 'totales.total' parseable")

    descuento_total = convertir_decimal(totales.get("descuento_total"))

    impuestos_bloque = documento.get("impuestos", {}) or {}
    iva_incluido = convertir_bool(impuestos_bloque.get("iva_incluido_en_precios"))

    id_empresa = obtener_o_crear_empresa(cursor, empresa)

    cursor.execute(
        """
        INSERT INTO tickets
          (id_documento, id_empresa, fecha, total, descuento_total, iva_incluido_en_precios,
           id_normalizacion_fuente, id_clasificacion_fuente)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          id_empresa = VALUES(id_empresa),
          fecha = VALUES(fecha),
          total = VALUES(total),
          descuento_total = VALUES(descuento_total),
          iva_incluido_en_precios = VALUES(iva_incluido_en_precios),
          id_normalizacion_fuente = VALUES(id_normalizacion_fuente),
          id_clasificacion_fuente = VALUES(id_clasificacion_fuente),
          fecha_modificacion = CURRENT_TIMESTAMP
        """,
        (
            id_documento,
            id_empresa,
            fecha,
            str(total),
            str(descuento_total) if descuento_total is not None else None,
            iva_incluido,
            id_normalizacion,
            id_clasificacion,
        ),
    )

    # Items: reemplazo completo (idempotente por documento)
    cursor.execute("DELETE FROM items WHERE id_documento = %s", (id_documento,))
    items = documento.get("items", []) or []
    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("El documento normalizado no contiene 'items' válido")

    numero_linea = 1
    for item in items:
        if not isinstance(item, dict):
            continue

        concepto = str(item.get("concepto", "")).strip()
        if not concepto:
            continue

        unidades = convertir_decimal(item.get("unidades"))
        importe_total_item = convertir_decimal(item.get("importe_total"))
        if importe_total_item is None:
            continue

        descuento_porcentaje = convertir_decimal(item.get("descuento_porcentaje"))
        descuento_importe = convertir_decimal(item.get("descuento_importe"))
        codigo_impuesto = item.get("codigo_impuesto")
        codigo_impuesto = str(codigo_impuesto).strip() if codigo_impuesto is not None else None
        if codigo_impuesto == "":
            codigo_impuesto = None

        cursor.execute(
            """
            INSERT INTO items
              (id_documento, numero_linea, concepto, unidades, importe_total,
               descuento_porcentaje, descuento_importe, codigo_impuesto)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                id_documento,
                numero_linea,
                concepto,
                str(unidades) if unidades is not None else None,
                str(importe_total_item),
                str(descuento_porcentaje) if descuento_porcentaje is not None else None,
                str(descuento_importe) if descuento_importe is not None else None,
                codigo_impuesto,
            ),
        )
        numero_linea += 1

    # Impuestos: reemplazo completo
    cursor.execute("DELETE FROM impuestos WHERE id_documento = %s", (id_documento,))
    resumen_impuestos = impuestos_bloque.get("resumen_impuestos", []) or []
    if isinstance(resumen_impuestos, list):
        for imp in resumen_impuestos:
            if not isinstance(imp, dict):
                continue
            tipo_impuesto = str(imp.get("tipo_impuesto", "")).strip()
            if not tipo_impuesto:
                continue
            base_imponible = convertir_decimal(imp.get("base_imponible"))
            importe = convertir_decimal(imp.get("importe"))
            if importe is None:
                continue

            cursor.execute(
                """
                INSERT INTO impuestos
                  (id_documento, tipo_impuesto, base_imponible, importe)
                VALUES
                  (%s, %s, %s, %s)
                """,
                (
                    id_documento,
                    tipo_impuesto,
                    str(base_imponible) if base_imponible is not None else None,
                    str(importe),
                ),
            )


def publicar_y_materializar_documento(
    cursor: mysql.connector.cursor.MySQLCursor,
    id_documento: int,
) -> bool:
    """
    Publica (actualiza estado actual) y materializa (tickets/items/impuestos/empresas)
    si existe snapshot publicable (ok_normalizacion + validacion_ok + clasificacion_ok)

    True si materializa
    """
    snapshot = seleccionar_snapshot_publicable(cursor, id_documento)
    if snapshot is None:
        return False

    id_normalizacion, id_clasificacion = snapshot
    id_doc, ok_normalizacion, documento, avisos, error_mensaje, version_modulo = leer_normalizacion_hist(cursor, id_normalizacion)

    actualizar_estado_actual_normalizaciones(
        cursor=cursor,
        id_documento=id_doc,
        ok_normalizacion=ok_normalizacion,
        documento=documento,
        avisos=avisos,
        error_mensaje=error_mensaje,
        version_modulo=version_modulo,
    )

    actualizar_estado_actual_clasificaciones(
        cursor=cursor,
        id_documento=id_doc,
        id_clasificacion=id_clasificacion,
    )

    materializar_ticket_items_impuestos(
        cursor=cursor,
        id_documento=id_doc,
        id_normalizacion=id_normalizacion,
        id_clasificacion=id_clasificacion,
        documento=documento,
    )

    return True


def main() -> None:
    cfg = cargar_config()
    conn = crear_conexion_mysql(cfg)
    cursor = conn.cursor()

    total_candidatos = 0
    total_materializados = 0

    try:
        candidatos = seleccionar_documentos_candidatos(cursor, limite=500)
        total_candidatos = len(candidatos)

        for id_documento in candidatos:
            try:
                materializado = publicar_y_materializar_documento(cursor, id_documento)
                if materializado:
                    total_materializados += 1
                conn.commit()
            except Exception as exc:
                conn.rollback()
                # No aborta todo el lote: registra por consola y sigue.
                print(f"[ERROR] id_documento={id_documento}: {exc}")

    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    print(f"Publicación/materialización finalizada. Candidatos={total_candidatos} Materializados={total_materializados}")


if __name__ == "__main__":
    main()