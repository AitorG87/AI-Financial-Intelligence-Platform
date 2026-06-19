from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Union


EntradaNormalizacion = Dict[str, Any]
SalidaNormalizacion = Dict[str, Any]


def registrar_aviso(avisos: List[str], codigo: str, ruta: str = "", detalle: str = "") -> None:
    mensaje = codigo
    if ruta:
        mensaje += f":{ruta}"
    if detalle:
        mensaje += f"|{detalle}"
    avisos.append(mensaje)



## TEXTOS


LEGAL_SUFFIXES = [
    r"S\.A\.U\.?", r"SAU",
    r"S\.A\.?", r"SA",
    r"S\.L\.U\.?", r"SLU",
    r"S\.L\.?", r"SL",
    r"EPEL",
    r"EU\s*S\.à\s*r\.l\.",
    r"EU\s*S\.a\s*r\.L\.",
    r"EU\s*S\.a\s*r\.l\.",
    r"Sucursal\s*en\s*España",
    r"EU\s*S\.à\s*r\.l\.,\s*Sucursal\s*en\s*España",
    r"A\s*&\s*A",
]

LEGAL_SUFFIX_RE = re.compile(
    r"(?:\s*,\s*|\s+)(?:" + "|".join(LEGAL_SUFFIXES) + r")\s*$",
    flags=re.IGNORECASE
)

RE_ESPACIOS = re.compile(r"\s+")


def quitar_diacriticos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in normalizado if not unicodedata.combining(ch))


def normalizar_empresa(valor: Any, avisos: List[str], ruta: str) -> Optional[str]:
    if valor is None:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    if not isinstance(valor, str):
        valor = str(valor)

    texto = valor.strip()
    if not texto:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    texto = re.sub(r"[,\.;:\-]+$", "", texto).strip()

    anterior = None
    while anterior != texto:
        anterior = texto
        texto = LEGAL_SUFFIX_RE.sub("", texto).strip()
        texto = re.sub(r"[,\.;:\-]+$", "", texto).strip()

    texto = quitar_diacriticos(texto)
    texto = texto.lower()
    texto = " ".join(p.capitalize() for p in RE_ESPACIOS.split(texto) if p)
    texto = RE_ESPACIOS.sub(" ", texto).strip()

    if not texto:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    return texto


def normalizar_concepto(valor: Any, avisos: List[str], ruta: str) -> Optional[str]:
    if valor is None:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    if not isinstance(valor, str):
        valor = str(valor)

    texto = valor.strip()
    if not texto:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    texto = quitar_diacriticos(texto)
    texto = texto.upper()
    texto = RE_ESPACIOS.sub(" ", texto).strip()

    if not texto:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    return texto


## IMPORTES / UNIDADES / PORCENTAJES

DECIMAL_2 = Decimal("0.01")
DECIMAL_3 = Decimal("0.001")
DECIMAL_2_PCT = Decimal("0.01")


def decimal_desde_texto(texto: str) -> Optional[Decimal]:
    """
    Extrae Decimal

    Casos:
    - "0,76", " ,76", "0, 76", "0.76" -> 0.76
    - ",76" / ".76" -> 0.76
    - "1.234,56" -> 1234.56
    - "1,234.56" -> 1234.56
    - "€ 12,30" -> 12.30
    """
    if texto is None:
        return None

    if not isinstance(texto, str):
        texto = str(texto)

    s = texto.replace("\u00a0", " ").strip()
    if not s:
        return None

    # Mantener solo dígitos, separadores y signo
    s = re.sub(r"[^\d,\.\+\-]+", "", s)
    if not s:
        return None

    # Normalizar signos repetidos o sueltos
    if s in {"+", "-", ".", ",", "+.", "-.", "+,", "-,"}:
        return None

    # Permitir decimales sin 0: ",76" o ".76"
    if s.startswith(",") or s.startswith("."):
        s = "0" + s
    if s.startswith("-,") or s.startswith("-."):
        s = s.replace("-,", "-0,").replace("-.", "-0.")

    s = s.replace(" ", "")

    tiene_coma = "," in s
    tiene_punto = "." in s

    # arregla separadores (miles vs decimal)
    if tiene_coma and tiene_punto:
        pos_coma = s.rfind(",")
        pos_punto = s.rfind(".")
        if pos_coma > pos_punto:
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif tiene_coma:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    else:
        pass

    if s.count("-") > 1 or (s.count("-") == 1 and not s.startswith("-")):
        return None

    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_importe_2(valor: Any, avisos: List[str], ruta: str, requerido: bool) -> Optional[Decimal]:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        if requerido:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "requerido_vacio")
            return None
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    dec: Optional[Decimal] = None
    if isinstance(valor, (int, Decimal)):
        try:
            dec = Decimal(str(valor))
        except InvalidOperation:
            dec = None
    elif isinstance(valor, float):
        dec = decimal_desde_texto(f"{valor}")
    else:
        dec = decimal_desde_texto(str(valor))

    if dec is None:
        registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta)
        return None

    return dec.quantize(DECIMAL_2, rounding=ROUND_HALF_UP)


def parse_unidades_3(valor: Any, avisos: List[str], ruta: str) -> Optional[Decimal]:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    dec: Optional[Decimal] = None
    if isinstance(valor, (int, Decimal)):
        try:
            dec = Decimal(str(valor))
        except InvalidOperation:
            dec = None
    elif isinstance(valor, float):
        dec = decimal_desde_texto(f"{valor}")
    else:
        dec = decimal_desde_texto(str(valor))

    if dec is None:
        registrar_aviso(avisos, "UNIDADES_NO_PARSEABLES", ruta)
        return None

    return abs(dec).quantize(DECIMAL_3, rounding=ROUND_HALF_UP)


def parse_porcentaje_0_1(valor: Any, avisos: List[str], ruta: str) -> Optional[Decimal]:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    raw = str(valor).strip()
    tiene_pct = "%" in raw
    dec = decimal_desde_texto(raw)

    if dec is None:
        registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "porcentaje")
        return None

    dec = abs(dec)

    if tiene_pct or dec > 1:
        dec = dec / Decimal("100")

    return dec.quantize(DECIMAL_2_PCT, rounding=ROUND_HALF_UP)



## CÓDIGOS DE IMPUESTO


def normalizar_codigo_impuesto(valor: Any, avisos: List[str], ruta: str) -> Optional[Union[str, int]]:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    if isinstance(valor, bool):
        registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "codigo_impuesto_bool")
        return None

    if isinstance(valor, int):
        if valor <= 0:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "codigo_impuesto_int_no_positivo")
            return None
        return valor

    if isinstance(valor, (float, Decimal)):
        try:
            i = int(Decimal(str(valor)))
            if i > 0:
                return i
        except Exception:
            pass

    s = str(valor).strip()
    if not s:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    if re.fullmatch(r"\d+", s):
        i = int(s)
        if i > 0:
            return i
        registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "codigo_impuesto_num_no_positivo")
        return None

    return s.upper()


def normalizar_mapa_codigo_impuesto(valor: Any, avisos: List[str], ruta: str) -> Optional[Dict[Union[str, int], Decimal]]:
    if valor is None or not isinstance(valor, dict) or not valor:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    resultado: Dict[Union[str, int], Decimal] = {}

    for k, v in valor.items():
        clave = normalizar_codigo_impuesto(k, avisos, f"{ruta}.clave")
        if clave is None:
            continue
        porc = parse_porcentaje_0_1(v, avisos, f"{ruta}[{k}]")
        if porc is None:
            continue
        resultado[clave] = porc

    if not resultado:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    return resultado


## BOOLEANOS

VALORES_TRUE = {"true", "1", "t", "si", "sí", "s", "y", "yes"}
VALORES_FALSE = {"false", "0", "f", "no", "n"}


def normalizar_bool(valor: Any, avisos: List[str], ruta: str) -> Optional[bool]:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
        return None

    if isinstance(valor, bool):
        return valor

    if isinstance(valor, (int, float, Decimal)):
        try:
            return bool(int(Decimal(str(valor))))
        except Exception:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "bool_num")
            return None

    s = str(valor).strip().lower()
    if s in VALORES_TRUE:
        return True
    if s in VALORES_FALSE:
        return False

    registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "bool_no_convertible")
    return None


## FECHA

MESES = {
    "ene": 1, "enero": 1,
    "feb": 2, "febrero": 2,
    "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6,
    "jul": 7, "julio": 7,
    "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "septiembre": 9, "set": 9, "setiembre": 9,
    "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}

RE_FECHA_NUM = re.compile(r"^\s*(\d{1,2})[\/\-\.\s](\d{1,2})[\/\-\.\s](\d{2}|\d{4})\s*$")
RE_FECHA_TEXTO = re.compile(
    r"^\s*(\d{1,2})\s+([a-zA-Záéíóúüñ\.]+)\s+(\d{2}|\d{4})\s*$",
    flags=re.IGNORECASE
)

RE_FECHA_NUM_EN_TEXTO = re.compile(r"(\d{1,2})[\/\-\.\s](\d{1,2})[\/\-\.\s](\d{2}|\d{4})")
RE_FECHA_ISO_EN_TEXTO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

RE_FECHA_MES_TEXTO_BARRAS_EN_TEXTO = re.compile(
    r"(\d{1,2})\s*[\/\-\.\s]\s*([a-zA-Z0-9áéíóúüñ\.]+)\s*[\/\-\.\s]\s*(\d{2}|\d{4})",
    flags=re.IGNORECASE
)

RE_FECHA_MES_TEXTO_CON_HORA_EN_TEXTO = re.compile(
    r"(\d{1,2})\s+([a-zA-Z0-9áéíóúüñ\.]+)\s+(\d{2}|\d{4})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
    flags=re.IGNORECASE
)

RE_FECHA_DE_EN_TEXTO = re.compile(
    r"(\d{1,2})\s+de\s+([a-zA-Z0-9áéíóúüñ\.]+)\s+de\s+(\d{2}|\d{4})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
    flags=re.IGNORECASE
)


def convertir_anio_2_a_4(anio: int) -> int:
    if 0 <= anio <= 69:
        return 2000 + anio
    return 1900 + anio


def normalizar_token_mes_textual(token_mes: str) -> str:
    token = quitar_diacriticos(token_mes).lower().strip()
    token = token.replace(".", "").replace(",", "")
    token = token.replace("0ct", "oct")
    token = token.replace("0c", "oc")
    token = RE_ESPACIOS.sub(" ", token).strip()
    return token


def mes_textual_a_numero(token_mes: str) -> Optional[int]:
    token = normalizar_token_mes_textual(token_mes)
    if token in MESES:
        return MESES[token]
    if len(token) >= 3 and token[:3] in MESES:
        return MESES[token[:3]]
    return None


def parse_fecha_mysql(valor: Any, avisos: List[str], ruta: str) -> Optional[str]:
    if valor is None or (isinstance(valor, str) and not str(valor).strip()):
        registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_vacia")
        return None

    s = str(valor).strip()
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_iso_invalida")
            return None

    m_iso = RE_FECHA_ISO_EN_TEXTO.search(s)
    if m_iso:
        try:
            anio = int(m_iso.group(1))
            mes = int(m_iso.group(2))
            dia = int(m_iso.group(3))
            dt = date(anio, mes, dia)
            return dt.isoformat()
        except Exception:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_iso_en_texto_invalida")
            return None

    m_num_en_texto = RE_FECHA_NUM_EN_TEXTO.search(s)
    if m_num_en_texto:
        try:
            d = int(m_num_en_texto.group(1))
            mes = int(m_num_en_texto.group(2))
            anio_raw = m_num_en_texto.group(3)
            anio = int(anio_raw)
            if len(anio_raw) == 2:
                anio = convertir_anio_2_a_4(anio)
            dt = date(anio, mes, d)
            return dt.isoformat()
        except Exception:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_num_en_texto_invalida")
            return None

    m_barras_mes = RE_FECHA_MES_TEXTO_BARRAS_EN_TEXTO.search(s)
    if m_barras_mes:
        try:
            d = int(m_barras_mes.group(1))
            mes_txt = m_barras_mes.group(2)
            anio_raw = m_barras_mes.group(3)
            anio = int(anio_raw)
            if len(anio_raw) == 2:
                anio = convertir_anio_2_a_4(anio)

            mes = mes_textual_a_numero(mes_txt)
            if mes is None:
                registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "mes_texto_desconocido")
                return None

            dt = date(anio, mes, d)
            return dt.isoformat()
        except Exception:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_mes_texto_barras_invalida")
            return None

    patron_fecha_de_sin_segundo_de = re.compile(
        r"(\d{1,2})\s+de\s+([a-zA-Z0-9áéíóúüñ\.]+)\s+(\d{2}|\d{4})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
        flags=re.IGNORECASE
    )
    m_de_sin = patron_fecha_de_sin_segundo_de.search(s)
    if m_de_sin:
        try:
            d = int(m_de_sin.group(1))
            mes_txt = m_de_sin.group(2)
            anio_raw = m_de_sin.group(3)
            anio = int(anio_raw)
            if len(anio_raw) == 2:
                anio = convertir_anio_2_a_4(anio)

            mes = mes_textual_a_numero(mes_txt)
            if mes is None:
                registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "mes_texto_desconocido")
                return None

            dt = date(anio, mes, d)
            return dt.isoformat()
        except Exception:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_de_sin_segundo_de_invalida")
            return None

    m_de = RE_FECHA_DE_EN_TEXTO.search(s)
    if m_de:
        try:
            d = int(m_de.group(1))
            mes_txt = m_de.group(2)
            anio_raw = m_de.group(3)
            anio = int(anio_raw)
            if len(anio_raw) == 2:
                anio = convertir_anio_2_a_4(anio)

            mes = mes_textual_a_numero(mes_txt)
            if mes is None:
                registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "mes_texto_desconocido")
                return None

            dt = date(anio, mes, d)
            return dt.isoformat()
        except Exception:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_de_invalida")
            return None

    m_texto_hora = RE_FECHA_MES_TEXTO_CON_HORA_EN_TEXTO.search(s)
    if m_texto_hora:
        try:
            d = int(m_texto_hora.group(1))
            mes_txt = m_texto_hora.group(2)
            anio_raw = m_texto_hora.group(3)
            anio = int(anio_raw)
            if len(anio_raw) == 2:
                anio = convertir_anio_2_a_4(anio)

            mes = mes_textual_a_numero(mes_txt)
            if mes is None:
                registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "mes_texto_desconocido")
                return None

            dt = date(anio, mes, d)
            return dt.isoformat()
        except Exception:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_texto_con_hora_invalida")
            return None

    m = RE_FECHA_NUM.match(s)
    if m:
        d = int(m.group(1))
        mes = int(m.group(2))
        anio_raw = m.group(3)
        anio = int(anio_raw)
        if len(anio_raw) == 2:
            anio = convertir_anio_2_a_4(anio)
        try:
            dt = date(anio, mes, d)
            return dt.isoformat()
        except ValueError:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_num_invalida")
            return None

    texto = quitar_diacriticos(s).lower().strip()
    texto = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s*$", "", texto).strip()

    m2 = RE_FECHA_TEXTO.match(texto)
    if m2:
        d = int(m2.group(1))
        mes_txt = m2.group(2)
        anio_raw = m2.group(3)
        anio = int(anio_raw)
        if len(anio_raw) == 2:
            anio = convertir_anio_2_a_4(anio)

        mes = mes_textual_a_numero(mes_txt)
        if mes is None:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "mes_texto_desconocido")
            return None

        try:
            dt = date(anio, mes, d)
            return dt.isoformat()
        except ValueError:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_texto_invalida")
            return None

    registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", ruta, "fecha_no_parseable")
    return None



## ELIMINACIÓN VACÍOS


def eliminar_vacios(obj: Any, avisos: List[str], ruta: str = "") -> Any:
    if obj is None:
        return None

    if isinstance(obj, str):
        if not obj.strip():
            registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", ruta)
            return None
        return obj

    if isinstance(obj, list):
        lista = []
        for i, v in enumerate(obj):
            ruta_hija = f"{ruta}[{i}]" if ruta else f"[{i}]"
            vv = eliminar_vacios(v, avisos, ruta_hija)
            if vv is not None:
                lista.append(vv)
        return lista if lista else None

    if isinstance(obj, dict):
        dic: Dict[str, Any] = {}
        for k, v in obj.items():
            ruta_hija = f"{ruta}.{k}" if ruta else str(k)
            vv = eliminar_vacios(v, avisos, ruta_hija)
            if vv is not None:
                dic[k] = vv
        return dic if dic else None

    return obj


def avisar_importes_anomalos(doc: Dict[str, Any], avisos: List[str]) -> None:
    if not isinstance(doc, dict):
        return

    totales = doc.get("totales")
    if not isinstance(totales, dict):
        return

    total = totales.get("total")
    if not isinstance(total, Decimal):
        return

    total_abs = total.copy_abs()
    if total_abs <= Decimal("0.00"):
        return

    items = doc.get("items")
    if not isinstance(items, list) or not items:
        return

    max_item: Optional[Decimal] = None
    for it in items:
        if not isinstance(it, dict):
            continue
        imp = it.get("importe_total")
        if isinstance(imp, Decimal):
            imp_abs = imp.copy_abs()
            if max_item is None or imp_abs > max_item:
                max_item = imp_abs

    if max_item is None:
        return

    umbral = (total_abs * Decimal("2")).quantize(DECIMAL_2, rounding=ROUND_HALF_UP)
    if max_item > umbral:
        registrar_aviso(
            avisos,
            "W_IMPORTES_ANOMALOS_NORMALIZACION",
            "items[].importe_total",
            f"max_item={max_item} total={total_abs} umbral={umbral}",
        )



## NORMALIZACIÓN


def normalizar_documento_gasto(entrada: EntradaNormalizacion) -> SalidaNormalizacion:
    avisos: List[str] = []

    id_documento = int(entrada.get("id_documento"))
    ok_extraccion = bool(entrada.get("ok_extraccion", False))

    if not ok_extraccion:
        return {
            "id_documento": id_documento,
            "ok_normalizacion": False,
            "documento_normalizado": None,
            "avisos": ["NO_EJECUTADO_OK_EXTRACCION_FALSE"],
        }

    raw = entrada.get("json_extraido")
    if not isinstance(raw, dict):
        return {
            "id_documento": id_documento,
            "ok_normalizacion": False,
            "documento_normalizado": None,
            "avisos": ["JSON_EXTRAIDO_INVALIDO_NO_DICT"],
        }

    doc = dict(raw)

    if "empresa" in doc:
        doc["empresa"] = normalizar_empresa(doc.get("empresa"), avisos, "empresa")
    else:
        registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", "empresa")

    items = doc.get("items")
    if isinstance(items, list):
        lista_items = []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            it2 = dict(it)

            if "concepto" in it2:
                it2["concepto"] = normalizar_concepto(it2.get("concepto"), avisos, f"items[{i}].concepto")
            else:
                registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", f"items[{i}].concepto")

            lista_items.append(it2)
        doc["items"] = lista_items

    if isinstance(doc.get("items"), list):
        for i, it in enumerate(doc["items"]):
            if not isinstance(it, dict):
                continue

            if "importe_total" in it:
                it["importe_total"] = parse_importe_2(it.get("importe_total"), avisos, f"items[{i}].importe_total", True)
            else:
                registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", f"items[{i}].importe_total", "requerido_missing")

            if "descuento_importe" in it:
                it["descuento_importe"] = parse_importe_2(it.get("descuento_importe"), avisos, f"items[{i}].descuento_importe", False)

            if "descuento_porcentaje" in it:
                it["descuento_porcentaje"] = parse_porcentaje_0_1(it.get("descuento_porcentaje"), avisos, f"items[{i}].descuento_porcentaje")

            if "unidades" in it:
                it["unidades"] = parse_unidades_3(it.get("unidades"), avisos, f"items[{i}].unidades")

            if "codigo_impuesto" in it:
                it["codigo_impuesto"] = normalizar_codigo_impuesto(it.get("codigo_impuesto"), avisos, f"items[{i}].codigo_impuesto")

    totales = doc.get("totales")
    if isinstance(totales, dict):
        tot2 = dict(totales)

        if "total" in tot2:
            tot2["total"] = parse_importe_2(tot2.get("total"), avisos, "totales.total", True)
        else:
            registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", "totales.total", "requerido_missing")
            tot2["total"] = None

        if "descuento_total" in tot2:
            tot2["descuento_total"] = parse_importe_2(tot2.get("descuento_total"), avisos, "totales.descuento_total", False)

        doc["totales"] = tot2

    impuestos = doc.get("impuestos")
    if isinstance(impuestos, dict):
        imp2 = dict(impuestos)

        resumen = imp2.get("resumen_impuestos")
        if isinstance(resumen, list):
            lista_resumen = []
            for i, fila in enumerate(resumen):
                if not isinstance(fila, dict):
                    continue
                f2 = dict(fila)

                if "base_imponible" in f2:
                    f2["base_imponible"] = parse_importe_2(
                        f2.get("base_imponible"),
                        avisos,
                        f"impuestos.resumen_impuestos[{i}].base_imponible",
                        False
                    )
                if "importe" in f2:
                    f2["importe"] = parse_importe_2(
                        f2.get("importe"),
                        avisos,
                        f"impuestos.resumen_impuestos[{i}].importe",
                        False
                    )
                if "tipo_impuesto" in f2:
                    f2["tipo_impuesto"] = parse_porcentaje_0_1(
                        f2.get("tipo_impuesto"),
                        avisos,
                        f"impuestos.resumen_impuestos[{i}].tipo_impuesto"
                    )

                lista_resumen.append(f2)

            imp2["resumen_impuestos"] = lista_resumen

        if "mapa_codigo_impuesto" in imp2:
            imp2["mapa_codigo_impuesto"] = normalizar_mapa_codigo_impuesto(
                imp2.get("mapa_codigo_impuesto"),
                avisos,
                "impuestos.mapa_codigo_impuesto"
            )

        if "iva_incluido_en_precios" in imp2:
            imp2["iva_incluido_en_precios"] = normalizar_bool(
                imp2.get("iva_incluido_en_precios"),
                avisos,
                "impuestos.iva_incluido_en_precios"
            )

        doc["impuestos"] = imp2

    if "fecha" in doc:
        doc["fecha"] = parse_fecha_mysql(doc.get("fecha"), avisos, "fecha")
    else:
        registrar_aviso(avisos, "IMPORTE_NO_PARSEABLE", "fecha", "requerido_missing")
        doc["fecha"] = None

    doc = eliminar_vacios(doc, avisos, "")

    ok_normalizacion = True

    empresa = doc.get("empresa") if isinstance(doc, dict) else None
    if not empresa:
        ok_normalizacion = False

    fecha = doc.get("fecha") if isinstance(doc, dict) else None
    if not fecha:
        ok_normalizacion = False

    total = None
    if isinstance(doc, dict) and isinstance(doc.get("totales"), dict):
        total = doc["totales"].get("total")
    if not isinstance(total, Decimal):
        ok_normalizacion = False

    items_validos = []
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        for it in doc["items"]:
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("concepto"), str) and it.get("concepto") and isinstance(it.get("importe_total"), Decimal):
                items_validos.append(it)

    if not items_validos:
        ok_normalizacion = False
    else:
        if len(items_validos) != len(doc.get("items") or []):
            registrar_aviso(avisos, "CAMPO_ELIMINADO_VACIO", "items", "items_invalidos_descartados")
        doc["items"] = items_validos

    avisar_importes_anomalos(doc, avisos)

    if not ok_normalizacion:
        return {
            "id_documento": id_documento,
            "ok_normalizacion": False,
            "documento_normalizado": None,
            "avisos": avisos,
        }

    return {
        "id_documento": id_documento,
        "ok_normalizacion": True,
        "documento_normalizado": doc,
        "avisos": avisos,
    }



## CLI


def decimal_default(obj: Any):
    if isinstance(obj, Decimal):
        return str(obj)  # mantener trazabilidad Decimal
    raise TypeError(f"Tipo no serializable: {type(obj)}")


def leer_json_entrada(path: Optional[str]) -> Any:
    if path and path != "-":
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.load(sys.stdin)


def escribir_json_salida(obj: Any, path: Optional[str]) -> None:
    texto = json.dumps(obj, ensure_ascii=False, indent=2, default=decimal_default)
    if path and path != "-":
        with open(path, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
    else:
        sys.stdout.write(texto + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="modulo_normalizacion.py",
        description="Normalización del JSON",
    )
    parser.add_argument(
        "-i", "--input",
        default="-",
        help="Ruta a JSON de entrada",
    )
    parser.add_argument(
        "-o", "--output",
        default="-",
        help="Ruta para escribir JSON de salida",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Alias: salida con indent=2",
    )
    args = parser.parse_args(argv)

    try:
        data = leer_json_entrada(args.input)
    except Exception as e:
        sys.stderr.write(f"ERROR Normalizando JSON de entrada: {e}\n")
        return 2

    # Acepta 1 doc o lista de docs
    if isinstance(data, list):
        salida = [normalizar_documento_gasto(x) for x in data]
    elif isinstance(data, dict):
        salida = normalizar_documento_gasto(data)
    else:
        sys.stderr.write("ERROR: el JSON de entrada debe ser dict o list[dict]\n")
        return 2

    try:
        escribir_json_salida(salida, args.output)
    except Exception as e:
        sys.stderr.write(f"ERROR escribiendo JSON de salida: {e}\n")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())