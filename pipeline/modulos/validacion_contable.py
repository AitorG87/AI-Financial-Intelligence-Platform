from __future__ import annotations

import sys
from pathlib import Path

# Resolver ruta base antes de cualquier import del proyecto
ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[2]  # pipeline/modulos -> TFM (raíz)
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from mysql.connector.connection import MySQLConnection

from bd.conexion import crear_conexion_mysql


@dataclass(frozen=True)
class ToleranciasValidacionContable:
    tolerancia_ok_absoluta: Decimal
    tolerancia_aviso_absoluta: Decimal
    tolerancia_ok_relativa: Decimal
    tolerancia_aviso_relativa: Decimal

    def a_json(self) -> Dict[str, Any]:
        return {
            "tolerancia_ok_absoluta": str(self.tolerancia_ok_absoluta),
            "tolerancia_aviso_absoluta": str(self.tolerancia_aviso_absoluta),
            "tolerancia_ok_relativa": str(self.tolerancia_ok_relativa),
            "tolerancia_aviso_relativa": str(self.tolerancia_aviso_relativa),
            "formula": "max(absoluta, relativa*|total|)",
        }


@dataclass(frozen=True)
class ResultadoValidacionContable:
    id_normalizacion: int
    ok_validacion: bool
    errores: List[Dict[str, Any]]
    avisos: List[Dict[str, Any]]
    tolerancias: Dict[str, Any]
    error_mensaje: Optional[str]
    version_modulo: str
    fecha_creacion: str


def crear_evento(
    codigo: str,
    mensaje: str,
    ruta: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Crea un objeto de evento (error/aviso) en formato JSON"""
    evento: Dict[str, Any] = {"codigo": codigo, "mensaje": mensaje}
    if ruta:
        evento["ruta"] = ruta
    if meta:
        evento["meta"] = meta
    return evento


def convertir_a_decimal(valor: Any) -> Optional[Decimal]:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        return Decimal(str(valor))
    if isinstance(valor, str):
        texto = valor.strip()
        if texto == "":
            return None
        try:
            texto = texto.replace("€", "").replace("\u00a0", " ").strip()
            texto = "".join(texto.split())
            if "," in texto and "." in texto:
                if texto.rfind(",") > texto.rfind("."):
                    texto = texto.replace(".", "")
                    texto = texto.replace(",", ".")
                else:
                    texto = texto.replace(",", "")
            elif "," in texto:
                texto = texto.replace(",", ".")
            return Decimal(texto)
        except InvalidOperation:
            return None
    return None


def cuantizar_dinero(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def leer_json_seguro(valor: Any) -> Any:
    if valor is None:
        return None
    if isinstance(valor, (dict, list)):
        return valor
    if isinstance(valor, str):
        texto = valor.strip()
        if texto == "":
            return None
        try:
            return json.loads(texto)
        except Exception:
            return valor
    try:
        return json.loads(str(valor))
    except Exception:
        return valor


def obtener_por_tokens(documento: Any, tokens: List[str]) -> Tuple[bool, Any]:
    if not tokens:
        return False, None

    actual: Any = documento
    for token in tokens:
        if isinstance(actual, dict):
            if token not in actual:
                return False, None
            actual = actual[token]
            continue

        if isinstance(actual, list):
            if not token.isdigit():
                return False, None
            indice = int(token)
            if indice < 0 or indice >= len(actual):
                return False, None
            actual = actual[indice]
            continue

        return False, None

    return True, actual


def tokenizar_campo(campo: str) -> List[str]:
    """Interpreta la ruta:
    - Separador de niveles: '.'
    - Acceso a listas: [i]
    - Sin $, sin @, sin expresiones complejas
    """
    texto = str(campo).strip()
    if texto == "":
        return []

    tokens: List[str] = []
    actual = ""
    i = 0

    while i < len(texto):
        ch = texto[i]

        if ch == ".":
            if actual != "":
                tokens.append(actual)
                actual = ""
            i += 1
            continue

        if ch == "[":
            if actual != "":
                tokens.append(actual)
                actual = ""
            j = texto.find("]", i + 1)
            if j == -1:
                return []
            indice = texto[i + 1 : j].strip()
            if indice == "" or (not indice.isdigit()):
                return []
            tokens.append(indice)
            i = j + 1
            continue

        actual += ch
        i += 1

    if actual != "":
        tokens.append(actual)

    tokens_final = [t.strip() for t in tokens if t.strip() != ""]
    return tokens_final


def asegurar_contenedor(actual: Any, token: str, siguiente_token: Optional[str]) -> Tuple[bool, Any]:
    siguiente_es_indice = False
    if siguiente_token is not None:
        siguiente_es_indice = siguiente_token.isdigit()

    if token.isdigit():
        if not isinstance(actual, list):
            return False, None
        indice = int(token)
        if indice < 0:
            return False, None
        while len(actual) <= indice:
            actual.append([] if siguiente_es_indice else {})
        if not isinstance(actual[indice], (dict, list)):
            actual[indice] = [] if siguiente_es_indice else {}
        return True, actual[indice]

    if not isinstance(actual, dict):
        return False, None

    if token not in actual or not isinstance(actual[token], (dict, list)):
        actual[token] = [] if siguiente_es_indice else {}
    return True, actual[token]


def establecer_por_tokens(documento: Any, tokens: List[str], valor: Any) -> bool:
    """Establece valor en documento siguiendo tokens
    Soporta:
    - Cambios de subcampo: items[2].concepto
    - Sustitución de item completo: items[1]
    - Sustitución de ramas: impuestos.resumen_impuestos
    """
    if not tokens:
        return False

    actual: Any = documento
    for pos, token in enumerate(tokens[:-1]):
        siguiente = tokens[pos + 1] if pos + 1 < len(tokens) else None
        ok, nuevo_actual = asegurar_contenedor(actual, token, siguiente)
        if not ok:
            return False
        actual = nuevo_actual

    ultimo = tokens[-1]
    if ultimo.isdigit():
        if not isinstance(actual, list):
            return False
        indice_final = int(ultimo)
        if indice_final < 0:
            return False
        while len(actual) <= indice_final:
            actual.append(None)
        actual[indice_final] = valor
        return True

    if not isinstance(actual, dict):
        return False
    actual[ultimo] = valor
    return True


def aplicar_cambios_a_json(documento_base: Any, cambios: List[Dict[str, Any]]) -> Tuple[Any, List[Dict[str, Any]]]:
    """Aplica auditoría de cambios basada en:
      - campo
      - valor_nuevo_json
    """
    avisos: List[Dict[str, Any]] = []
    documento = json.loads(json.dumps(documento_base, ensure_ascii=False))

    for i, cambio in enumerate(cambios):
        campo = str(cambio.get("campo", "")).strip()
        valor_nuevo = cambio.get("valor_nuevo_json", None)

        tokens = tokenizar_campo(campo)
        if not tokens:
            avisos.append(
                crear_evento(
                    "CAMPO_CAMBIO_INVALIDO",
                    "El campo del cambio no se puede interpretar; se omite",
                    ruta=f"cambios[{i}].campo",
                    meta={"campo": campo},
                )
            )
            continue

        ok = establecer_por_tokens(documento, tokens, valor_nuevo)
        if not ok:
            avisos.append(
                crear_evento(
                    "CAMBIO_NO_APLICADO",
                    "No se pudo aplicar el cambio al JSON; se omite",
                    ruta=f"cambios[{i}]",
                    meta={"campo": campo},
                )
            )

    return documento, avisos


def obtener_iva_incluido_en_precios(json_doc: Dict[str, Any]) -> Tuple[bool, bool]:
    """- Si existe impuestos.iva_incluido_en_precios (bool) -> usar
    - Si no existe -> True
    Devuelve: (valor_usado, existia_en_json)
    """
    impuestos = json_doc.get("impuestos", None)
    if isinstance(impuestos, dict):
        valor = impuestos.get("iva_incluido_en_precios", None)
        if isinstance(valor, bool):
            return valor, True
    return True, False


def calcular_tolerancias_efectivas(tolerancias: ToleranciasValidacionContable, total_declarado: Decimal) -> Tuple[Decimal, Decimal]:
    base = abs(total_declarado)
    tol_ok = max(tolerancias.tolerancia_ok_absoluta, tolerancias.tolerancia_ok_relativa * base)
    tol_aviso = max(tolerancias.tolerancia_aviso_absoluta, tolerancias.tolerancia_aviso_relativa * base)
    return tol_ok, tol_aviso


def validar_estructura_minima(json_doc: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    errores: List[Dict[str, Any]] = []
    avisos: List[Dict[str, Any]] = []

    items = json_doc.get("items", None)
    if not isinstance(items, list) or len(items) == 0:
        errores.append(crear_evento("ERROR_ITEMS_VACIO", "items no puede estar vacío", ruta="items"))
        return errores, avisos

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errores.append(crear_evento("ERROR_ITEM_INVALIDO", "Cada item debe ser un objeto", ruta=f"items[{i}]"))
            continue

        concepto = item.get("concepto", None)
        if not isinstance(concepto, str) or concepto.strip() == "":
            errores.append(
                crear_evento(
                    "ITEM_CONCEPTO_INVALIDO",
                    "concepto debe ser un string no vacío",
                    ruta=f"items[{i}].concepto",
                )
            )

        importe_total = convertir_a_decimal(item.get("importe_total", None))
        if importe_total is None:
            errores.append(
                crear_evento(
                    "ERROR_ITEM_IMPORTE_TOTAL_INVALIDO",
                    "importe_total debe ser numérico (puede ser negativo)",
                    ruta=f"items[{i}].importe_total",
                )
            )

    totales = json_doc.get("totales", None)
    if not isinstance(totales, dict):
        errores.append(crear_evento("ERROR_TOTALES_TOTAL_FALTA", "totales.total es obligatorio", ruta="totales.total"))
        return errores, avisos

    total = convertir_a_decimal(totales.get("total", None))
    if total is None:
        errores.append(crear_evento("ERROR_TOTALES_TOTAL_FALTA", "totales.total es obligatorio", ruta="totales.total"))

    return errores, avisos


def calcular_total_esperado(json_doc: Dict[str, Any]) -> Optional[Decimal]:
    items = json_doc.get("items", [])
    if not isinstance(items, list) or len(items) == 0:
        return None

    suma = Decimal("0")
    for item in items:
        if not isinstance(item, dict):
            continue

        importe_total = convertir_a_decimal(item.get("importe_total", None))
        if importe_total is None:
            continue

        descuento_importe = convertir_a_decimal(item.get("descuento_importe", None))
        if descuento_importe is None:
            descuento_importe = Decimal("0")

        base = importe_total - descuento_importe

        descuento_porcentaje = convertir_a_decimal(item.get("descuento_porcentaje", None))
        if descuento_porcentaje is not None:
            factor = Decimal("1") - (descuento_porcentaje / Decimal("100"))
            precio_item = base * factor
        else:
            precio_item = base

        suma += precio_item

    totales = json_doc.get("totales", {})
    descuento_total = convertir_a_decimal(totales.get("descuento_total", None))
    if descuento_total is None:
        descuento_total = Decimal("0")

    return suma - descuento_total


def validar_total_principal(
    total_esperado: Decimal,
    total_declarado: Decimal,
    tolerancias: ToleranciasValidacionContable,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    errores: List[Dict[str, Any]] = []
    avisos: List[Dict[str, Any]] = []

    tol_ok, tol_aviso = calcular_tolerancias_efectivas(tolerancias, total_declarado)
    diff = abs(total_esperado - total_declarado)

    meta = {
        "total_esperado": str(cuantizar_dinero(total_esperado)),
        "total_declarado": str(cuantizar_dinero(total_declarado)),
        "diferencia": str(cuantizar_dinero(diff)),
        "tolerancia_ok": str(cuantizar_dinero(tol_ok)),
        "tolerancia_aviso": str(cuantizar_dinero(tol_aviso)),
    }

    if diff <= tol_ok:
        return errores, avisos

    if diff <= tol_aviso:
        avisos.append(
            crear_evento(
                "TOTAL_DIF_EN_AVISO",
                "La diferencia entre total esperado y total declarado está en banda de aviso",
                ruta="totales.total",
                meta=meta,
            )
        )
        return errores, avisos

    errores.append(
        crear_evento(
            "ERROR_TOTAL_DIF_FUERA_DE_AVISO",
            "La diferencia entre total esperado y total declarado supera la tolerancia de aviso",
            ruta="totales.total",
            meta=meta,
        )
    )
    return errores, avisos


def validar_iva(
    json_doc: Dict[str, Any],
    total_declarado: Decimal,
    tolerancias: ToleranciasValidacionContable,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    errores: List[Dict[str, Any]] = []
    avisos: List[Dict[str, Any]] = []

    impuestos = json_doc.get("impuestos", None)
    if not isinstance(impuestos, dict):
        return errores, avisos

    resumen = impuestos.get("resumen_impuestos", None)
    if not isinstance(resumen, list) or len(resumen) == 0:
        return errores, avisos

    suma_resumen = Decimal("0")
    resumen_completo = True

    for i, entrada in enumerate(resumen):
        if not isinstance(entrada, dict):
            resumen_completo = False
            continue

        tipo = entrada.get("tipo_impuesto", None)
        if tipo is not None:
            tipo_dec = convertir_a_decimal(tipo)
            if tipo_dec is None:
                avisos.append(
                    crear_evento(
                        "IVA_TIPO_NO_PARSEABLE",
                        "tipo_impuesto no es decimal parseable; se omite validación de tipo",
                        ruta=f"impuestos.resumen_impuestos[{i}].tipo_impuesto",
                        meta={"valor": str(tipo)},
                    )
                )
            else:
                if tipo_dec < Decimal("0") or tipo_dec > Decimal("1"):
                    avisos.append(
                        crear_evento(
                            "IVA_TIPO_FUERA_DE_RANGO",
                            "tipo_impuesto fuera de rango [0,1]; se omite validación de tipo",
                            ruta=f"impuestos.resumen_impuestos[{i}].tipo_impuesto",
                            meta={"valor": str(tipo_dec)},
                        )
                    )

        base = convertir_a_decimal(entrada.get("base_imponible", None))
        importe = convertir_a_decimal(entrada.get("importe", None))

        if base is None or importe is None:
            resumen_completo = False
            continue

        suma_resumen += (base + importe)

    if not resumen_completo:
        return errores, avisos

    tol_ok, tol_aviso = calcular_tolerancias_efectivas(tolerancias, total_declarado)
    diff = abs(suma_resumen - total_declarado)

    meta = {
        "total_resumen": str(cuantizar_dinero(suma_resumen)),
        "total_declarado": str(cuantizar_dinero(total_declarado)),
        "diferencia": str(cuantizar_dinero(diff)),
        "tolerancia_ok": str(cuantizar_dinero(tol_ok)),
        "tolerancia_aviso": str(cuantizar_dinero(tol_aviso)),
    }

    if diff <= tol_ok:
        return errores, avisos

    if diff <= tol_aviso:
        avisos.append(
            crear_evento(
                "IVA_RESUMEN_TOTAL_EN_AVISO",
                "La suma del resumen de impuestos difiere del total en banda de aviso",
                ruta="impuestos.resumen_impuestos",
                meta=meta,
            )
        )
        return errores, avisos

    errores.append(
        crear_evento(
            "ERROR_IVA_RESUMEN_TOTAL_FUERA_DE_AVISO",
            "La suma del resumen de impuestos difiere del total y supera la tolerancia de aviso",
            ruta="impuestos.resumen_impuestos",
            meta=meta,
        )
    )
    return errores, avisos


class RepositorioValidacionContableMySQL:
    def __init__(self, conn: MySQLConnection):
        self.conn = conn

    def obtener_ultima_normalizacion_ok(self, id_documento: int) -> Tuple[int, Any]:
        sql = """
        SELECT
            id_normalizacion,
            documento_normalizado_json
        FROM normalizaciones_hist
        WHERE id_documento=%s AND ok_normalizacion=1
        ORDER BY id_normalizacion DESC
        LIMIT 1
        """
        cur = self.conn.cursor()
        try:
            cur.execute(sql, (int(id_documento),))
            fila = cur.fetchone()
            if not fila:
                raise ValueError(f"No existe normalización OK en normalizaciones_hist para id_documento={id_documento}")
            id_normalizacion = int(fila[0])
            json_base = leer_json_seguro(fila[1])
            return id_normalizacion, json_base
        finally:
            cur.close()

    
    def obtener_ultima_normalizacion(self, id_documento: int) -> Tuple[int, Any, bool]:
        sql = """
        SELECT
            id_normalizacion,
            documento_normalizado_json,
            ok_normalizacion
        FROM normalizaciones_hist
        WHERE id_documento=%s
        ORDER BY id_normalizacion DESC
        LIMIT 1
        """
        cur = self.conn.cursor()
        try:
            cur.execute(sql, (int(id_documento),))
            fila = cur.fetchone()
            if not fila:
                raise ValueError(f"No existe ninguna normalización en normalizaciones_hist para id_documento={id_documento}")
            id_normalizacion = int(fila[0])
            json_base = leer_json_seguro(fila[1])
            ok_normalizacion = bool(fila[2])
            return id_normalizacion, json_base, ok_normalizacion
        finally:
            cur.close()


    def obtener_cambios_humanos(self, id_documento: int) -> List[Dict[str, Any]]:
        """auditoria_cambios:
          - origen ENUM(STREAMLIT, SISTEMA)
        """
        sql = """
        SELECT
            id_cambio,
            fecha_cambio,
            campo,
            valor_nuevo_json
        FROM auditoria_cambios
        WHERE id_documento=%s AND origen='STREAMLIT'
        ORDER BY fecha_cambio ASC, id_cambio ASC
        """
        cur = self.conn.cursor(dictionary=True)
        try:
            cur.execute(sql, (int(id_documento),))
            filas = cur.fetchall() or []
            cambios: List[Dict[str, Any]] = []
            for fila in filas:
                cambios.append(
                    {
                        "id_cambio": int(fila.get("id_cambio")),
                        "fecha_cambio": str(fila.get("fecha_cambio")),
                        "campo": str(fila.get("campo") or ""),
                        "valor_nuevo_json": leer_json_seguro(fila.get("valor_nuevo_json")),
                    }
                )
            return cambios
        finally:
            cur.close()

    def insertar_validacion(
        self,
        id_normalizacion: int,
        ok_validacion: bool,
        errores: List[Dict[str, Any]],
        avisos: List[Dict[str, Any]],
        tolerancias: Dict[str, Any],
        error_mensaje: Optional[str],
        version_modulo: str,
    ) -> None:
        sql = """
        INSERT INTO validaciones_contables (
            id_normalizacion,
            ok_validacion,
            errores_json,
            avisos_json,
            tolerancias_json,
            error_mensaje,
            version_modulo,
            fecha_creacion
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                sql,
                (
                    int(id_normalizacion),
                    1 if ok_validacion else 0,
                    json.dumps(errores, ensure_ascii=False),
                    json.dumps(avisos, ensure_ascii=False),
                    json.dumps(tolerancias, ensure_ascii=False),
                    error_mensaje,
                    version_modulo,
                ),
            )
            self.conn.commit()
        finally:
            cur.close()


def validar_documento_contable(
    id_documento: int,
    *,
    tolerancias: Optional[ToleranciasValidacionContable] = None,
    version_modulo: str = "validacion_contable",
    conn: Optional[MySQLConnection] = None,
) -> ResultadoValidacionContable:
    tolerancias_final = tolerancias or ToleranciasValidacionContable(
        tolerancia_ok_absoluta=Decimal("0.01"),
        tolerancia_aviso_absoluta=Decimal("0.05"),
        tolerancia_ok_relativa=Decimal("0.001"),
        tolerancia_aviso_relativa=Decimal("0.005"),
    )

    errores: List[Dict[str, Any]] = []
    avisos: List[Dict[str, Any]] = []
    error_mensaje: Optional[str] = None

    id_normalizacion: int = 0
    conexion_local = conn is None
    conexion = conn or crear_conexion_mysql()

    try:
        repo = RepositorioValidacionContableMySQL(conexion)

        '''Obtener una normalización con FK válida:
           - Preferir ok_normalizacion=1
           - Si no existe, usar la última disponible (aunque ok_normalizacion=0)
        '''
        try:
            id_normalizacion, json_base = repo.obtener_ultima_normalizacion_ok(int(id_documento))
            ok_normalizacion = True
        except ValueError:
            id_normalizacion, json_base, ok_normalizacion = repo.obtener_ultima_normalizacion(int(id_documento))
            errores.append(
                crear_evento(
                    "ERROR_NO_NORMALIZACION_OK",
                    "No existe una normalización OK; el documento no es válido para validación contable",
                    ruta="normalizaciones_hist",
                    meta={"id_documento": int(id_documento), "id_normalizacion_usada": int(id_normalizacion)},
                )
            )

        # 2) Validación de tipo del JSON
        if not isinstance(json_base, dict):
            errores.append(
                crear_evento(
                    "ERROR_JSON_NORMALIZADO_INVALIDO",
                    "documento_normalizado_json no es un objeto JSON",
                    ruta="normalizaciones_hist.documento_normalizado_json",
                )
            )

            ok_validacion = False
            repo.insertar_validacion(
                id_normalizacion=id_normalizacion,
                ok_validacion=ok_validacion,
                errores=errores,
                avisos=avisos,
                tolerancias=tolerancias_final.a_json(),
                error_mensaje=None,
                version_modulo=version_modulo,
            )

            return ResultadoValidacionContable(
                id_normalizacion=id_normalizacion,
                ok_validacion=ok_validacion,
                errores=errores,
                avisos=avisos,
                tolerancias=tolerancias_final.a_json(),
                error_mensaje=None,
                version_modulo=version_modulo,
                fecha_creacion=datetime.now().isoformat(timespec="seconds"),
            )

        # 3) Aplicar cambios humanos (STREAMLIT)
        cambios = repo.obtener_cambios_humanos(int(id_documento))
        json_efectivo, avisos_cambios = aplicar_cambios_a_json(json_base, cambios)
        avisos.extend(avisos_cambios)

        if not isinstance(json_efectivo, dict):
            errores.append(
                crear_evento(
                    "ERROR_JSON_EFECTIVO_INVALIDO",
                    "El JSON efectivo no es un objeto JSON",
                    ruta="documento",
                )
            )

            ok_validacion = False
            repo.insertar_validacion(
                id_normalizacion=id_normalizacion,
                ok_validacion=ok_validacion,
                errores=errores,
                avisos=avisos,
                tolerancias=tolerancias_final.a_json(),
                error_mensaje=None,
                version_modulo=version_modulo,
            )

            return ResultadoValidacionContable(
                id_normalizacion=id_normalizacion,
                ok_validacion=ok_validacion,
                errores=errores,
                avisos=avisos,
                tolerancias=tolerancias_final.a_json(),
                error_mensaje=None,
                version_modulo=version_modulo,
                fecha_creacion=datetime.now().isoformat(timespec="seconds"),
            )


        iva_incluido, existia = obtener_iva_incluido_en_precios(json_efectivo)
        avisos.append(
            crear_evento(
                "IVA_INCLUIDO_USADO" if existia else "IVA_INCLUIDO_POR_DEFECTO",
                "Valor de impuestos.iva_incluido_en_precios usado (no afecta a la validación)",
                ruta="impuestos.iva_incluido_en_precios",
                meta={"valor": iva_incluido},
            )
        )

        # 4) Reglas estructurales
        errores_estructura, avisos_estructura = validar_estructura_minima(json_efectivo)
        errores.extend(errores_estructura)
        avisos.extend(avisos_estructura)

        # 5) Regla contable principal + IVA
        total_declarado: Optional[Decimal] = None
        totales = json_efectivo.get("totales", None)
        if isinstance(totales, dict):
            total_declarado = convertir_a_decimal(totales.get("total", None))

        if total_declarado is not None:
            total_esperado = calcular_total_esperado(json_efectivo)
            if total_esperado is not None:
                err_total, av_total = validar_total_principal(total_esperado, total_declarado, tolerancias_final)
                errores.extend(err_total)
                avisos.extend(av_total)

            err_iva, av_iva = validar_iva(json_efectivo, total_declarado, tolerancias_final)
            errores.extend(err_iva)
            avisos.extend(av_iva)

        # 6) Decisión final (warnings no tumba)
        ok_validacion = len(errores) == 0

        # 7) Persistencia
        repo.insertar_validacion(
            id_normalizacion=id_normalizacion,
            ok_validacion=ok_validacion,
            errores=errores,
            avisos=avisos,
            tolerancias=tolerancias_final.a_json(),
            error_mensaje=None,
            version_modulo=version_modulo,
        )

        return ResultadoValidacionContable(
            id_normalizacion=id_normalizacion,
            ok_validacion=ok_validacion,
            errores=errores,
            avisos=avisos,
            tolerancias=tolerancias_final.a_json(),
            error_mensaje=None,
            version_modulo=version_modulo,
            fecha_creacion=datetime.now().isoformat(timespec="seconds"),
        )

    except Exception as exc:
        error_mensaje = f"{type(exc).__name__}: {exc}"

        # Intento de persistir fallo técnico si ya tengo FK
        if id_normalizacion != 0:
            try:
                repo = RepositorioValidacionContableMySQL(conexion)
                repo.insertar_validacion(
                    id_normalizacion=id_normalizacion,
                    ok_validacion=False,
                    errores=errores,
                    avisos=avisos,
                    tolerancias=tolerancias_final.a_json(),
                    error_mensaje=error_mensaje,
                    version_modulo=version_modulo,
                )
            except Exception:
                pass

        return ResultadoValidacionContable(
            id_normalizacion=id_normalizacion,
            ok_validacion=False,
            errores=errores,
            avisos=avisos,
            tolerancias=tolerancias_final.a_json(),
            error_mensaje=error_mensaje,
            version_modulo=version_modulo,
            fecha_creacion=datetime.now().isoformat(timespec="seconds"),
        )

    finally:
        if conexion_local:
            try:
                conexion.close()
            except Exception:
                pass


def ejecutar_validacion_contable_desde_cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validación contable para un documento")
    parser.add_argument("id_documento", type=int, help="Identificador del documento")
    args = parser.parse_args()

    resultado = validar_documento_contable(args.id_documento)
    print(json.dumps(resultado.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ejecutar_validacion_contable_desde_cli()