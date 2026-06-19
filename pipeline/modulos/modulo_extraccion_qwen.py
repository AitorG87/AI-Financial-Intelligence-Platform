from __future__ import annotations

import argparse
import json
import re
import time
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from bd.conexion import crear_conexion_mysql
from pipeline.modulos.repositorio_documentos import RepositorioDocumentosMySQL
from pipeline.modulos.repositorio_extraccion_qwen import RepositorioExtraccionesQwenMySQL
from pipeline.modulos.motor_qwen_transformers import ConfigMotorQwen, MotorQwenTransformers
from utilidades.configuracion import cargar_env_desde_raiz, cargar_config



## Ajuste de sys.path para ejecución directa


ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[2]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))


## PROMPT


VERSION_PROMPT = "qwen3_produccion_estable"

TEXTO_PROMPT = """Eres un sistema de extracción de información de tickets y facturas.

Tu tarea es DEVOLVER EXCLUSIVAMENTE un JSON válido que cumpla EXACTAMENTE el schema DocumentoGasto v2.
NO escribas ningún texto fuera del JSON.
NO uses Markdown.
NO incluyas ``` ni comentarios.

========================
ESTRUCTURA OBLIGATORIA
========================

{
  "empresa": "string",
  "fecha": "string",
  "items": [
    {
      "concepto": "string",
      "importe_total": "string"
    }
  ],
  "totales": {
    "total": "string"
  }
}

========================
CAMPOS OPCIONALES (SOLO SI APLICAN)
========================

- En items:
  - "unidades" SOLO si aparece explícitamente en la línea.
  - "codigo_impuesto" SOLO si aparece explícitamente (A/B/C o 1/2/3).

- "impuestos" SOLO si se puede leer del documento:
  - "iva_incluido_en_precios": true/false SOLO si aparece explícitamente.
  - "mapa_codigo_impuesto": SOLO si el ticket incluye una leyenda (A=21%, B=10%, etc).
  - "resumen_impuestos": SOLO si hay un desglose por tipo (IVA 21% BASE ... IMPORTE ...).
    Si no se ve claro, NO lo incluyas.

========================
REGLAS CRÍTICAS (NO NEGOCIABLES)
========================

- Cada línea de producto/servicio debe generar un item independiente.
- NO resumas la compra en una sola línea.
- "importe_total" es SIEMPRE el total de la línea (no el precio unitario).
- NO inventes datos.
- NO incluyas campos vacíos.

========================
PROHIBICIONES IMPORTANTES (EVITAR ERRORES DOWNSTREAM)
========================

1) MÉTODOS DE PAGO NO SON ITEMS.
NO incluyas como items líneas de pago o cobro, por ejemplo:
TARJETA, DATAFONO/DATÁFONO, TPV, VISA, MASTERCARD, AMEX, EFECTIVO,
METALICO/METÁLICO, CAMBIO, ENTREGA, IMPORTE ENTREGADO, A DEVOLVER, PAGO.
Estas líneas NO son productos/servicios.

2) TOTAL/SUBTOTAL/BASE/IVA NO SON ITEMS.
NO incluyas como items líneas tipo:
TOTAL, SUBTOTAL, BASE, IVA, IMPUESTO, TOTAL A PAGAR, TOTAL FACTURA.
El total final pagado debe ir SOLO en "totales.total".

3) SUMINISTROS / SERVICIOS
En facturas de suministros/servicios, NO incluyas como items líneas de subtotal de secciones como:
"TOTAL SUMINISTRO ...", "TOTAL CANON ...", "TOTAL CÁNON ...".
Son subtotales del cuerpo, NO conceptos facturables por línea.


========================
IMPUESTOS (EXTRACCIÓN LITERAL)
========================

- NO uses valores por defecto (por ejemplo, NO asumas "IVA 21%").
- Si aparece un patrón como "(B) IMP 10,00%" o "B IMP 10,00%", interpreta IMP como IVA y:
  - mapa_codigo_impuesto debe ser {"B": "IVA 10%"} (sin decimales innecesarios).
  - tipo_impuesto debe ser "IVA 10%".
- Solo incluye "iva_incluido_en_precios" si aparece explícitamente una frase equivalente a:
  - "IVA incluido", "IVA incl.", "IVA incluido en precios"  -> true
  - "IVA no incluido", "IVA excluido", "precios sin IVA"     -> false
  Si no aparece explícito, NO lo incluyas.

========================
TOTALES
========================

- "totales.total" debe ser el TOTAL FINAL PAGADO (total a pagar),
  NO la base imponible, NO un subtotal y NO un total de sección.
- Si aparecen varios "totales", elige el que sea claramente el total final (total a pagar).

Devuelve EXCLUSIVAMENTE JSON (sin texto adicional).
"""


TEXTO_PROMPT_RETRY_ITEMS_ONLY = """Eres un sistema de extracción de información de tickets y facturas.

Tu tarea es DEVOLVER EXCLUSIVAMENTE un JSON válido que cumpla EXACTAMENTE el schema DocumentoGasto v2.
NO escribas texto fuera del JSON. NO uses Markdown.

FOCO: extrae empresa, fecha, items y totales.total.
IGNORA completamente el bloque 'impuestos' si te dificulta extraer items (NO lo incluyas).
Cada línea de producto/servicio debe generar un item independiente.
Si el ticket es muy largo, extrae tantas líneas como sea posible; prioriza capturar muchas líneas aunque el JSON sea grande.
NO inventes datos.
"""

TEXTO_PROMPT_RETRY_ANTI_RESUMEN = """Tu salida anterior es incorrecta: has resumido la compra.

Vuelve a extraer DESDE LA IMAGEN y devuelve un JSON DocumentoGasto v2 cumpliendo:

- NO incluyas items genéricos como "Total", "Total de compra", "Compra", "Compras", "Subtotal".
- NO incluyas métodos de pago como items (TARJETA/EFECTIVO/DATAFONO/TPV/VISA/MASTERCARD/CAMBIO/etc).
- NO incluyas TOTAL/SUBTOTAL/BASE/IVA como items.
- Extrae TODAS las líneas de productos/servicios.
- Cada línea debe ser un item independiente.
- Incluye codigo_impuesto SOLO si aparece (A/B/C o 1/2/3).
- Devuelve EXCLUSIVAMENTE JSON (sin texto adicional).
"""

TEXTO_PROMPT_RETRY_COBERTURA = """Tu salida anterior es válida pero está incompleta (faltan líneas de items).

Vuelve a extraer DESDE LA IMAGEN y devuelve un JSON DocumentoGasto v2 con estas reglas:

- NO resumas ni acortes el concepto. Copia literal y completo.
- Extrae tantas líneas de items como sea posible.
- Cada línea de producto/servicio debe ser un item independiente.
- Si existe una línea de descuento separada (DTO/DESCUENTO/ABONO/etc.), inclúyela como item (con importe negativo si aparece con signo).
- NO incluyas métodos de pago como items (TARJETA/EFECTIVO/DATAFONO/TPV/VISA/MASTERCARD/CAMBIO/etc).
- NO incluyas TOTAL/SUBTOTAL/BASE/IVA como items.
- Incluye codigo_impuesto SOLO si aparece (A/B/C o 1/2/3).
- NO incluyas claves con valores vacíos.
- Devuelve EXCLUSIVAMENTE JSON (sin texto adicional).
"""

TEXTO_PROMPT_RETRY_ESTRUCTURA = """Corrige el siguiente contenido para que sea UN ÚNICO JSON válido y cumpla EXACTAMENTE el schema DocumentoGasto v2.

- NO cambies valores.
- NO añadas información nueva.
- NO incluyas texto fuera del JSON.
- NO uses Markdown.
"""



## RESULTADO


@dataclass(frozen=True)
class ResultadoExtraccionQwen:
    id_documento: int
    ok: bool
    ruta_jpg: str
    texto_bruto: Optional[str]
    json_extraido: Optional[Dict[str, Any]]
    errores: List[str]
    avisos: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id_documento": self.id_documento,
            "ok": self.ok,
            "ruta_jpg": self.ruta_jpg,
            "texto_bruto": self.texto_bruto,
            "json_extraido": self.json_extraido,
            "errores": list(self.errores or []),
            "avisos": list(self.avisos or []),
        }


## VALIDACIÓN


@dataclass(frozen=True)
class ResultadoValidacion:
    ok: bool
    errores: list[str]


class ValidadorDocumentoGastoV2:
    def __init__(self, schema_o_ruta: Any):
        import json
        from pathlib import Path
        import jsonschema

        # 1) Si ya es dict -> usarlo directamente
        if isinstance(schema_o_ruta, dict):
            schema = schema_o_ruta

        # 2) Si es ruta -> leer y parsear JSON
        elif isinstance(schema_o_ruta, (str, Path)) and Path(schema_o_ruta).exists():
            texto = Path(schema_o_ruta).read_text(encoding="utf-8")
            schema = json.loads(texto)

        # 3) Si es string que contiene JSON -> parsearlo
        elif isinstance(schema_o_ruta, str):
            schema = json.loads(schema_o_ruta)

        else:
            raise TypeError("schema_o_ruta debe ser dict, ruta a .json o string JSON")

        self.schema = schema
        self.validador = jsonschema.Draft7Validator(schema)

    def validar(self, obj: Dict[str, Any]) -> Tuple[bool, list[str]]:
        errores = []
        for e in self.validador.iter_errors(obj):
            path = ".".join([str(p) for p in e.path]) if e.path else ""
            errores.append(f"{path}: {e.message}".strip(": "))
        return (len(errores) == 0), errores



## JSON PARSEO


PATRON_BLOQUE_CODIGO = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def intentar_pasar_json(texto: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if texto is None:
        return None, "JSON no parseable: texto=None"

    t = str(texto).strip()
    if not t:
        return None, "JSON no parseable: texto vacío"

    m = PATRON_BLOQUE_CODIGO.search(t)
    if m:
        t = m.group(1).strip()

    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj, None
        return None, f"JSON no parseable: raíz no es objeto: {type(obj).__name__}"
    except Exception:
        pass

    i = t.find("{")
    j = t.rfind("}")
    if i >= 0 and j > i:
        sub = t[i : j + 1]
        try:
            obj = json.loads(sub)
            if isinstance(obj, dict):
                return obj, None
            return None, f"JSON no parseable: raíz no es objeto: {type(obj).__name__}"
        except Exception as ex:
            return None, f"JSON no parseable: {type(ex).__name__}: {ex}"

    return None, "JSON no parseable: no se encontró bloque {...}"


def detectar_truncado_estructural(texto: str) -> bool:
    if not texto:
        return False
    t = str(texto).strip()
    if not t:
        return False

    if not t.endswith("}"):
        return True
    if t.count("{") > t.count("}"):
        return True
    if (t.count('"') % 2) != 0:
        return True
    return False


def extraer_texto_fuera_json(texto_bruto: str) -> str:
    """
    A veces el motor devuelve:
      <JSON>\n<texto adicional>
    Analizamos lo que está fuera del JSON
    """
    if not texto_bruto:
        return ""
    t = str(texto_bruto)
    idx = t.rfind("}")
    if idx == -1:
        return t.strip()
    return t[idx + 1 :].strip()


## POST-PROCESO: codigo_impuesto y limpieza numérica

PATRON_CODIGO_SUF_FINAL = re.compile(r"^(.*?)(?:\s+)([A-Za-z0-9])$")


def separar_sufijo_codigo(valor: str) -> Tuple[str, Optional[str]]:
    if valor is None:
        return "", None

    texto = str(valor).strip()
    if not texto:
        return "", None

    m = PATRON_CODIGO_SUF_FINAL.match(texto)
    if not m:
        return texto, None

    base = (m.group(1) or "").strip()
    sufijo = (m.group(2) or "").strip().upper()
    return base, sufijo


def limpiar_numero_str(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip()
    if not texto:
        return ""
    texto = texto.replace("€", "").replace("\u00a0", " ").strip()
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def normalizar_codigo_impuesto_en_items(obj: Dict[str, Any], avisos: list[str]) -> Dict[str, Any]:
    """
    - Extrae A/B/C o 1/2/3 si está al final de unidades o importe_total
    - Limpia unidades e importe_total
    """
    if not isinstance(obj, dict):
        return obj

    items = obj.get("items")
    if not isinstance(items, list):
        return obj

    cambios_limpieza = 0
    cambios_codigo = 0

    items_salida: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            items_salida.append(item)
            continue

        nuevo = dict(item)

        # UNIDADES
        if "unidades" in nuevo and nuevo.get("unidades") is not None:
            original = limpiar_numero_str(nuevo.get("unidades"))
            base, sufijo = separar_sufijo_codigo(original)

            base = limpiar_numero_str(base)
            if base != original:
                cambios_limpieza += 1

            nuevo["unidades"] = base

            if sufijo and "codigo_impuesto" not in nuevo:
                if sufijo in {"A", "B", "C", "1", "2", "3"}:
                    nuevo["codigo_impuesto"] = sufijo
                    cambios_codigo += 1

            if not nuevo["unidades"]:
                nuevo.pop("unidades", None)

        # IMPORTE TOTAL
        if "importe_total" in nuevo and nuevo.get("importe_total") is not None:
            original = limpiar_numero_str(nuevo.get("importe_total"))
            base, sufijo = separar_sufijo_codigo(original)

            base = limpiar_numero_str(base)
            if base != original:
                cambios_limpieza += 1

            nuevo["importe_total"] = base

            if sufijo and "codigo_impuesto" not in nuevo:
                if sufijo in {"A", "B", "C", "1", "2", "3"}:
                    nuevo["codigo_impuesto"] = sufijo
                    cambios_codigo += 1

        items_salida.append(nuevo)

    obj2 = dict(obj)
    obj2["items"] = items_salida

    if cambios_limpieza > 0:
        avisos.append(f"limpieza_campos_numericos=1:cambios={cambios_limpieza}")
    if cambios_codigo > 0:
        avisos.append(f"codigo_impuesto_extraido=1:cambios={cambios_codigo}")

    return obj2



## POST-PROCESO: eliminación de items no facturables


PATRON_ESPACIOS = re.compile(r"\s+")
PATRON_SOLO_PUNTUACION = re.compile(r"^[\W_]+$", flags=re.UNICODE)

PATRON_METODO_PAGO = re.compile(
    r"\b("
    r"tarjeta|tpv|datafono|dat[áa]fono|visa|mastercard|maestro|amex|american\s*express|"
    r"efectivo|met[áa]lico|metallic|cambio|entrega|a\s*devolver|devuelto|devoluci[óo]n|"
    r"importe\s*entregado|importe\s*recibido|pago|cobro|autorizaci[óo]n|aprobaci[óo]n|"
    r"contactless|pin|firma"
    r")\b",
    flags=re.IGNORECASE,
)

PATRON_TOTAL_SUBTOTAL = re.compile(
    r"\b("
    r"total\s*a\s*pagar|total\s*factura|importe\s*total|total|subtotal|base\s*imponible|base|"
    r"iva|impuesto|cuota\s*iva|total\s*iva|total\s*base"
    r")\b",
    flags=re.IGNORECASE,
)

PATRON_TOTALES_SUMINISTRO = re.compile(
    r"^\s*total\s+(suministro|canon|c[áa]non|servicio|agua|luz|gas)\b",
    flags=re.IGNORECASE,
)

PATRON_DESCUENTO = re.compile(
    r"\b(dto|descuento|promoci[óo]n|cup[óo]n|rebaja|ahorro|abono)\b",
    flags=re.IGNORECASE,
)


def normalizar_texto_concepto(texto: Any) -> str:
    if texto is None:
        return ""
    t = str(texto).strip()
    if not t:
        return ""
    t = t.replace("\u00a0", " ")
    t = PATRON_ESPACIOS.sub(" ", t).strip()
    return t


def es_item_metodo_pago(concepto: str) -> bool:
    if not concepto:
        return False
    c = concepto.strip()
    if not c:
        return False
    if PATRON_SOLO_PUNTUACION.match(c):
        return False

    # Si parece descuento, no se considera método de pago
    if PATRON_DESCUENTO.search(c):
        return False

    # Coincidencia por palabras clave
    if PATRON_METODO_PAGO.search(c):
        return True

    return False


def es_item_total_subtotal(concepto: str) -> bool:
    if not concepto:
        return False
    c = concepto.strip()
    if not c:
        return False
    if PATRON_DESCUENTO.search(c):
        return False
    return bool(PATRON_TOTAL_SUBTOTAL.search(c))


def es_item_total_suministro(concepto: str) -> bool:
    if not concepto:
        return False
    c = concepto.strip()
    if not c:
        return False
    if PATRON_DESCUENTO.search(c):
        return False
    return bool(PATRON_TOTALES_SUMINISTRO.match(c))


def intentar_parsear_importe(valor: Any) -> Optional[float]:
    if valor is None:
        return None
    t = str(valor).strip()
    if not t:
        return None
    t = t.replace("€", "").replace(" ", "").replace("\u00a0", "")
    # Normalización simple: si hay coma y punto, el punto es miles y la coma decimal
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except Exception:
        return None


def eliminar_items_no_facturables(obj: Dict[str, Any], avisos: list[str]) -> Dict[str, Any]:
    """
    Elimina items:
      - métodos de pago (tarjeta/efectivo/tpv/cambio...)
      - totales/subtotales/base/iva
    """
    if not isinstance(obj, dict):
        return obj

    items = obj.get("items")
    if not isinstance(items, list) or not items:
        return obj

    total_doc = None
    totales = obj.get("totales")
    if isinstance(totales, dict):
        total_doc = intentar_parsear_importe(totales.get("total"))

    vistos_clave_total: Dict[str, int] = {}
    items_filtrados: list[Any] = []

    eliminados_pago = 0
    eliminados_totales = 0
    eliminados_suministro = 0
    eliminados_total_coincidente = 0
    eliminados_repetidos_total = 0

    for it in items:
        if not isinstance(it, dict):
            items_filtrados.append(it)
            continue

        concepto = normalizar_texto_concepto(it.get("concepto"))
        if not concepto:
            items_filtrados.append(it)
            continue

        concepto_min = concepto.lower()
        importe_item = intentar_parsear_importe(it.get("importe_total"))

        # 1) métodos de pago
        if es_item_metodo_pago(concepto):
            eliminados_pago += 1
            continue

        # 2) totales de secciones en suministros
        if es_item_total_suministro(concepto):
            eliminados_suministro += 1
            continue

        # 3) total/subtotal/base/iva como item
        if es_item_total_subtotal(concepto):
            eliminados_totales += 1

            # si coincide con total del documento
            if total_doc is not None and importe_item is not None:
                if abs(importe_item - total_doc) <= 0.05:
                    eliminados_total_coincidente += 1
            continue

        # 4) repetidos tipo TOTAL
        clave = concepto_min
        if "total" in clave or "subtotal" in clave:
            vistos_clave_total[clave] = vistos_clave_total.get(clave, 0) + 1
            if vistos_clave_total[clave] >= 2:
                # Solo eliminamos repetición si no es descuento
                if not PATRON_DESCUENTO.search(concepto):
                    eliminados_repetidos_total += 1
                    continue

        items_filtrados.append(it)

    if (
        eliminados_pago
        or eliminados_totales
        or eliminados_suministro
        or eliminados_total_coincidente
        or eliminados_repetidos_total
    ):
        obj2 = dict(obj)
        obj2["items"] = items_filtrados
        avisos.append(
            "items_no_facturables_filtrados=1:"
            f"pago={eliminados_pago},"
            f"totales={eliminados_totales},"
            f"suministro={eliminados_suministro},"
            f"total_coincidente={eliminados_total_coincidente},"
            f"repetidos_total={eliminados_repetidos_total}"
        )
        return obj2

    return obj



## POST-PROCESO: vacíos


REQUIRED_ROOT = {"empresa", "fecha", "items", "totales"}
REQUIRED_ITEM = {"concepto", "importe_total"}
REQUIRED_TOTALES = {"total"}


def es_vacio(valor: Any) -> bool:
    if valor is None:
        return True
    if isinstance(valor, str):
        return len(valor.strip()) == 0
    if isinstance(valor, dict):
        return len(valor) == 0
    if isinstance(valor, list):
        return len(valor) == 0
    return False


def limpiar_vacios(obj: Any) -> Any:
    if isinstance(obj, list):
        out = []
        for x in obj:
            y = limpiar_vacios(x)
            if isinstance(y, dict) and len(y) == 0:
                continue
            if isinstance(y, list) and len(y) == 0:
                continue
            if es_vacio(y):
                continue
            out.append(y)
        return out

    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            y = limpiar_vacios(v)

            if es_vacio(y):
                if k in REQUIRED_ROOT:
                    out[k] = y
                else:
                    continue
            else:
                out[k] = y

        if len(out) == 0:
            return {}
        return out

    return obj



## POST-PROCESO: mapa/resumen impuestos fuera de JSON


PATRON_MAPA_CODIGO = re.compile(
    r"([A-C1-3])\s*[:=]\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*%",
    flags=re.IGNORECASE,
)

PATRON_RESUMEN_IMPUESTOS_LINEA = re.compile(
    r"\biva\b\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*%\s*"
    r"\bbase\b\s*([0-9][0-9\.\,]*)\s*"
    r"\bimporte\b\s*([0-9][0-9\.\,]*)",
    flags=re.IGNORECASE,
)


PATRON_RESUMEN_IMPUESTOS_LINEA_CODIGO = re.compile(
    r"\(([A-Z])\)\s*(?:IMP|IVA)\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*%\s*"
    r"(?:\bbase\b|\bb\.?\s*i\.?\b)\s*([0-9][0-9\.,]*)\s*"
    r"(?:\bimporte\b|\bcuota\b|\biva\b)\s*([0-9][0-9\.,]*)",
    flags=re.IGNORECASE,
)




def aplicar_regla_iva_incluido_texto(obj: Dict[str, Any], texto_bruto: str, avisos: list[str]) -> Dict[str, Any]:
    """Ajusta iva_incluido_en_precios si texto fuera del JSON"""
    texto = extraer_texto_fuera_json(texto_bruto).lower()
    if not texto:
        return obj

    true_hits = ["iva incluido", "iva incl.", "iva incl", "iva incluido en precios"]
    false_hits = ["iva no incluido", "iva excluido", "precios sin iva", "sin iva"]

    val = None
    if any(h in texto for h in true_hits):
        val = True
    if any(h in texto for h in false_hits):
        val = False if val is None else None

    if val is None:
        return obj

    if not isinstance(obj, dict):
        return obj
    imp = obj.get("impuestos")
    if not isinstance(imp, dict):
        return obj

    actual = imp.get("iva_incluido_en_precios")
    if isinstance(actual, bool) and actual == val:
        return obj

    imp2 = dict(imp)
    imp2["iva_incluido_en_precios"] = val
    obj2 = dict(obj)
    obj2["impuestos"] = imp2
    avisos.append(f"iva_incluido_en_precios_texto=1:val={val}")
    return obj2


def aplicar_regla_mapa_codigo_impuesto_texto(obj: Dict[str, Any], texto_bruto: str, avisos: list[str]) -> Dict[str, Any]:
    """
    Extraer leyenda de códigos impuesto fuera del JSON
      - "(B) IMP 10,00%"
      - "B IMP 10,00%"
      - "A=21%" / "B:10%"
    """
    texto = extraer_texto_fuera_json(texto_bruto)
    if not texto:
        return obj

    encontrados = PATRON_MAPA_CODIGO.findall(texto)
    if not encontrados:
        return obj

    # PATRON_MAPA_CODIGO
    # Formato:
    #   - Caso 1: (codigo, IMP|IVA, porcentaje, '', '')
    #   - Caso 2: ('', '', '', codigo, porcentaje)
    mapa: Dict[str, str] = {}
    for t in encontrados:
        if not isinstance(t, (tuple, list)):
            continue
        t = list(t) + [""] * (5 - len(t))
        g1, g2, g3, g4, g5 = t[:5]

        codigo = (g1 or g4 or "").strip().upper()
        pct = (g3 or g5 or "").strip()
        if not codigo or not pct:
            continue

        pct_norm = pct.replace(",", ".")
        try:
            f = float(pct_norm)
            if abs(f - round(f)) < 1e-9:
                pct_norm = str(int(round(f)))
            else:
                pct_norm = ("%.2f" % f).rstrip("0").rstrip(".")
        except Exception:
            pass

        if codigo in {"A", "B", "C", "1", "2", "3"}:
            mapa[codigo] = f"IVA {pct_norm}%"

    if not mapa:
        return obj

    obj2 = dict(obj)
    impuestos = obj2.get("impuestos")
    impuestos = dict(impuestos) if isinstance(impuestos, dict) else {}

    existente = impuestos.get("mapa_codigo_impuesto")
    existente = dict(existente) if isinstance(existente, dict) else {}

    merged = dict(existente)
    cambios = 0
    for k, v in mapa.items():
        if merged.get(k) != v:
            merged[k] = v
            cambios += 1

    if cambios:
        impuestos["mapa_codigo_impuesto"] = merged
        obj2["impuestos"] = impuestos
        avisos.append(f"mapa_codigo_impuesto_texto=1:cambios={cambios}:claves={sorted(list(mapa.keys()))}")
        return obj2

    return obj


def aplicar_regla_resumen_impuestos_texto(obj: Dict[str, Any], texto_bruto: str, avisos: list[str]) -> Dict[str, Any]:
    """
    Extrae resumen de impuestos desde fuera del JSON
      - "IVA 10% BASE 12,34 IMPORTE 1,23"
      - "(B) IMP 10,00% BASE 12,34 IMPORTE 1,23"
    """
    texto = extraer_texto_fuera_json(texto_bruto)
    if not texto:
        return obj

    encontrados = []
    for m in PATRON_RESUMEN_IMPUESTOS_LINEA.finditer(texto):
        pct, base, imp = m.group(1), m.group(2), m.group(3)
        encontrados.append(("", pct, base, imp))

    for m in PATRON_RESUMEN_IMPUESTOS_LINEA_CODIGO.finditer(texto):
        codigo, pct, base, imp = m.group(1), m.group(2), m.group(3), m.group(4)
        encontrados.append((codigo, pct, base, imp))

    if not encontrados:
        return obj

    def _norm_num(s: str) -> str:
        return str(s).strip().replace(".", "").replace(",", ".")

    def _pct_fmt(pct: str) -> str:
        pct_s = str(pct).strip().replace(",", ".")
        try:
            f = float(pct_s)
            if abs(f - round(f)) < 1e-9:
                return str(int(round(f)))
            return ("%.2f" % f).rstrip("0").rstrip(".")
        except Exception:
            return pct_s

    resumen_extraido: list[dict] = []
    for codigo, pct, base, imp in encontrados:
        pct_s = _pct_fmt(pct)
        resumen_extraido.append(
            {
                "tipo_impuesto": f"IVA {pct_s}%",
                "base_imponible": str(base).strip(),
                "importe": str(imp).strip(),
            }
        )

    # Dedup por (tipo, base, importe)
    seen = set()
    resumen_dedup = []
    for r in resumen_extraido:
        key = (r["tipo_impuesto"], _norm_num(r["base_imponible"]), _norm_num(r["importe"]))
        if key in seen:
            continue
        seen.add(key)
        resumen_dedup.append(r)

    if not resumen_dedup:
        return obj

    obj2 = dict(obj)
    impuestos = obj2.get("impuestos")
    impuestos = dict(impuestos) if isinstance(impuestos, dict) else {}

    existente = impuestos.get("resumen_impuestos")
    if isinstance(existente, list) and existente:
        cambios = 0
        existente2 = []
        for e in existente:
            if not isinstance(e, dict):
                existente2.append(e)
                continue
            base_e = _norm_num(e.get("base_imponible", ""))
            imp_e = _norm_num(e.get("importe", ""))
            tipo_e = str(e.get("tipo_impuesto", "")).strip()

            mejor = None
            for r in resumen_dedup:
                if _norm_num(r["base_imponible"]) == base_e and _norm_num(r["importe"]) == imp_e:
                    mejor = r
                    break

            if mejor and tipo_e and tipo_e != mejor["tipo_impuesto"]:
                e2 = dict(e)
                e2["tipo_impuesto"] = mejor["tipo_impuesto"]
                existente2.append(e2)
                cambios += 1
            else:
                existente2.append(e)

        if cambios:
            impuestos["resumen_impuestos"] = existente2
            obj2["impuestos"] = impuestos
            avisos.append(f"resumen_impuestos_texto_corrige_tipo=1:cambios={cambios}")
            return obj2

        return obj

    impuestos["resumen_impuestos"] = resumen_dedup
    obj2["impuestos"] = impuestos
    avisos.append(f"resumen_impuestos_texto=1:n={len(resumen_dedup)}")
    return obj2




def limpiar_codigo_impuesto(obj: Dict[str, Any], avisos: list[str]) -> Dict[str, Any]:
    """Elimina codigo_impuesto NOK"""
    if not isinstance(obj, dict):
        return obj
    items = obj.get("items")
    if not isinstance(items, list) or not items:
        return obj

    cambios = 0
    items2 = []
    for it in items:
        if not isinstance(it, dict):
            items2.append(it)
            continue
        cod = str(it.get("codigo_impuesto", "")).strip()
        if cod in {"1","2","3"}:
            concepto = str(it.get("concepto","") or "")
            # detecta "NUMERO 1" / "NÚMERO 1"
            if re.search(r"\bN[UÚ]MERO\s*"+re.escape(cod)+r"\b", concepto, flags=re.IGNORECASE):
                it2 = dict(it)
                it2.pop("codigo_impuesto", None)
                items2.append(it2)
                cambios += 1
                continue
        items2.append(it)

    if cambios:
        obj2 = dict(obj)
        obj2["items"] = items2
        avisos.append(f"codigo_impuesto_espurio_eliminado=1:cambios={cambios}")
        return obj2

    return obj


def eliminar_mapa_si_iva_unico(obj: Dict[str, Any], avisos: list[str]) -> Dict[str, Any]:
    """Eliminar mapa_codigo_impuesto si no aporta"""
    if not isinstance(obj, dict):
        return obj
    imp = obj.get("impuestos")
    if not isinstance(imp, dict):
        return obj
    mapa = imp.get("mapa_codigo_impuesto")
    if not isinstance(mapa, dict) or not mapa:
        return obj

    # evidencia en items
    hay_codigos_items = False
    items = obj.get("items")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("codigo_impuesto"):
                hay_codigos_items = True
                break

    if hay_codigos_items:
        return obj

    if len(mapa) == 1:
        imp2 = dict(imp)
        imp2.pop("mapa_codigo_impuesto", None)
        obj2 = dict(obj)
        obj2["impuestos"] = imp2
        avisos.append("mapa_codigo_impuesto_eliminado_iva_unico=1")
        return obj2

    # mapas raros tipo {"IVA":"21%"} o {"21%":"IVA 21%"}: limpiar si no hay codigos
    claves = list(mapa.keys())
    if all(k.upper() not in {"A","B","C","1","2","3"} for k in claves):
        imp2 = dict(imp)
        imp2.pop("mapa_codigo_impuesto", None)
        obj2 = dict(obj)
        obj2["impuestos"] = imp2
        avisos.append("mapa_codigo_impuesto_eliminado_claves_invalidas=1")
        return obj2

    return obj


def normalizar_impuestos_schema_safe(obj: Dict[str, Any], avisos: list[str]) -> Dict[str, Any]:
    """Normaliza el bloque 'impuestos' para cumplir schema y evitar None/estructuras inválidas
    Reglas:
    - Si 'impuestos' no es dict => eliminar
    - Si 'mapa_codigo_impuesto' no es dict => eliminar
    - Si 'resumen_impuestos' existe, filtrar entradas inválidas (deben tener tipo_impuesto, base_imponible, importe)
      Si tras filtrar queda vacío, eliminar resumen_impuestos
    - NO inventa base_imponible: si falta, se elimina la entrada
    """
    if not isinstance(obj, dict):
        return obj

    imp = obj.get("impuestos")
    if imp is None:
        return obj
    if not isinstance(imp, dict):
        obj2 = dict(obj)
        obj2.pop("impuestos", None)
        avisos.append("impuestos_eliminados_no_obj=1")
        return obj2

    cambios = 0
    imp2 = dict(imp)

    # mapa_codigo_impuesto
    mapa = imp2.get("mapa_codigo_impuesto")
    if mapa is not None and not isinstance(mapa, dict):
        imp2.pop("mapa_codigo_impuesto", None)
        cambios += 1

    # resumen_impuestos
    ri = imp2.get("resumen_impuestos")
    if ri is not None:
        if not isinstance(ri, list):
            imp2.pop("resumen_impuestos", None)
            cambios += 1
        else:
            filtrado = []
            drop = 0
            for r in ri:
                if not isinstance(r, dict):
                    drop += 1
                    continue
                if not all(k in r and str(r.get(k)).strip() != "" for k in ("tipo_impuesto", "base_imponible", "importe")):
                    drop += 1
                    continue
                # evita propiedades extra
                filtrado.append(
                    {
                        "tipo_impuesto": str(r.get("tipo_impuesto")).strip(),
                        "base_imponible": str(r.get("base_imponible")).strip(),
                        "importe": str(r.get("importe")).strip(),
                    }
                )
            if drop:
                avisos.append(f"resumen_impuestos_filtrado_invalidos=1:drop={drop}")
                cambios += 1
            if filtrado:
                imp2["resumen_impuestos"] = filtrado
            else:
                imp2.pop("resumen_impuestos", None)
                cambios += 1

    # iva_incluido_en_precios: debe ser bool si existe
    if "iva_incluido_en_precios" in imp2 and not isinstance(imp2["iva_incluido_en_precios"], bool):
        imp2.pop("iva_incluido_en_precios", None)
        cambios += 1

    if not imp2:
        obj2 = dict(obj)
        obj2.pop("impuestos", None)
        avisos.append("impuestos_eliminados_vacios=1")
        return obj2

    if cambios:
        obj2 = dict(obj)
        obj2["impuestos"] = imp2
        avisos.append(f"impuestos_normalizados_schema_safe=1:cambios={cambios}")
        return obj2

    return obj


def ajustar_a_schema_documento_gasto_v2(obj: Dict[str, Any], avisos: list[str]) -> Dict[str, Any]:
    """
    Ajusta el JSON extraído para cumplir el schema
    - totales: SOLO {total, descuento_total} (elimina base/total_impuestos...
    - impuestos.resumen_impuestos: renombra claves {tipo: tipo_impuesto, base: base_imponible}
    - Recorta claves desconocidas en totales y impuestos
    """
    if not isinstance(obj, dict):
        return obj

    def _es_placeholder(v: Any) -> bool:
        return v is None or v == "" or (isinstance(v, str) and v.strip().lower() == "string")

    # totales
    tot = obj.get("totales")
    if isinstance(tot, dict):
        tot2: Dict[str, Any] = {}
        if "descuento_total" in tot and not _es_placeholder(tot.get("descuento_total")):
            tot2["descuento_total"] = tot.get("descuento_total")
        if "total" in tot and not _es_placeholder(tot.get("total")):
            tot2["total"] = tot.get("total")
        if "total" not in tot2:
            for alt in ("importe_total", "total_pagar", "total_a_pagar", "total_factura"):
                if alt in tot and not _es_placeholder(tot.get(alt)):
                    tot2["total"] = tot.get(alt)
                    avisos.append(f"totales_mapeado:{alt}->total")
                    break
        obj["totales"] = tot2
    elif tot is not None:
        # si viene mal tipado, lo quitamos para que no rompa
        avisos.append("totales_mal_tipo_eliminado=1")
        obj.pop("totales", None)

    # impuestos
    imp = obj.get("impuestos")
    if isinstance(imp, dict):
        imp2: Dict[str, Any] = {}

        # iva_incluido_en_precios
        if "iva_incluido_en_precios" in imp and isinstance(imp.get("iva_incluido_en_precios"), bool):
            imp2["iva_incluido_en_precios"] = imp.get("iva_incluido_en_precios")

        # mapa_codigo_impuesto
        mci = imp.get("mapa_codigo_impuesto")
        if isinstance(mci, dict):
            mci2: Dict[str, str] = {}
            for k, v in mci.items():
                if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip() and v.strip().lower() != "string":
                    mci2[k.strip()] = v.strip()
            if mci2:
                imp2["mapa_codigo_impuesto"] = mci2

        # resumen_impuestos
        resumen = imp.get("resumen_impuestos")
        if isinstance(resumen, list):
            out = []
            for it in resumen:
                if not isinstance(it, dict):
                    continue
                tipo = it.get("tipo_impuesto")
                base = it.get("base_imponible")
                importe = it.get("importe")

                # mapeos frecuentes del modelo
                if _es_placeholder(tipo):
                    tipo = it.get("tipo") or it.get("tipo_iva") or it.get("iva") or it.get("impuesto")
                if _es_placeholder(base):
                    base = it.get("base") or it.get("base_iva") or it.get("baseimponible")
                if _es_placeholder(importe):
                    importe = it.get("importe") or it.get("cuota") or it.get("impuesto") or it.get("iva_importe")

                if _es_placeholder(tipo) or _es_placeholder(base) or _es_placeholder(importe):
                    continue

                out.append(
                    {
                        "tipo_impuesto": str(tipo).strip(),
                        "base_imponible": str(base).strip(),
                        "importe": str(importe).strip(),
                    }
                )
            if out:
                imp2["resumen_impuestos"] = out

        obj["impuestos"] = imp2 if imp2 else obj.get("impuestos")
        # si ha quedado vacío, eliminar
        if isinstance(obj.get("impuestos"), dict) and len(obj["impuestos"]) == 0:
            obj.pop("impuestos", None)

    elif imp is not None:
        avisos.append("impuestos_mal_tipo_eliminado=1")
        obj.pop("impuestos", None)

    # placeholders en root
    for k in ("empresa", "fecha"):
        if _es_placeholder(obj.get(k)):
            obj.pop(k, None)

    return obj


def mejorar_codigo_impuesto_en_items(obj: Dict[str, Any], avisos: list[str]) -> Dict[str, Any]:
    """
    Heurística para rellenar item.codigo_impuesto cuando el modelo no lo da:
    - Si existe impuestos.mapa_codigo_impuesto, busca en el concepto
    - Si no existe mapa, intenta detectar A/B/C o 1/2/3
    """
    if not isinstance(obj, dict):
        return obj
    items = obj.get("items")
    if not isinstance(items, list):
        return obj

    mapa = None
    imp = obj.get("impuestos")
    if isinstance(imp, dict) and isinstance(imp.get("mapa_codigo_impuesto"), dict):
        mapa = {str(k).strip() for k in imp["mapa_codigo_impuesto"].keys() if str(k).strip()}
        # si el mapa trae cosas raras, nos quedamos con A/B/C/1/2/3
        mapa = {c for c in mapa if c in {"A", "B", "C", "1", "2", "3"}}
        if not mapa:
            mapa = None

    # patrón token
    token_pat = re.compile(r"(?<![A-Z0-9])([ABC123])(?![A-Z0-9])")

    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("codigo_impuesto"):
            continue
        concepto = it.get("concepto")
        if not isinstance(concepto, str) or not concepto.strip():
            continue

        cod = None
        if mapa:
            # buscamos códigos del mapa como token separado
            encontrados = token_pat.findall(concepto.upper())
            for c in encontrados:
                if c in mapa:
                    cod = c
                    break
        else:
            # token final
            m2 = re.search(r"(?:\s|\*)+([ABC123])\s*$", concepto.upper())
            if m2:
                cod = m2.group(1)

        if cod:
            it["codigo_impuesto"] = cod
            avisos.append("codigo_impuesto_inferido=1")

    impuestos = obj.get("impuestos")
    if not isinstance(impuestos, dict):
        return obj

    imp2 = dict(impuestos)
    cambios = 0

    if "iva_incluido_en_precios" in imp2 and not isinstance(imp2.get("iva_incluido_en_precios"), bool):
        imp2.pop("iva_incluido_en_precios", None)
        cambios += 1

    if "mapa_codigo_impuesto" in imp2 and not isinstance(imp2.get("mapa_codigo_impuesto"), dict):
        imp2.pop("mapa_codigo_impuesto", None)
        cambios += 1

    if "resumen_impuestos" in imp2 and not isinstance(imp2.get("resumen_impuestos"), list):
        imp2.pop("resumen_impuestos", None)
        cambios += 1

    if cambios:
        obj2 = dict(obj)
        if len(imp2) == 0:
            obj2.pop("impuestos", None)
        else:
            obj2["impuestos"] = imp2
        avisos.append(f"impuestos_normalizados_schema_safe=1:cambios={cambios}")
        return obj2

    return obj



## AMAZON


MESES_ESPANOL = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
PATRON_ASIN = re.compile(r"\bB0[A-Z0-9]{8,12}\b", re.IGNORECASE)


def aplicar_regla_amazon(obj: Dict[str, Any], texto_bruto: str, avisos: list[str]) -> Dict[str, Any]:
    texto = (texto_bruto or "").lower()

    hay_envio = False
    hay_asin = False

    items = obj.get("items")
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            c = str(it.get("concepto", "")).strip().lower()
            if c == "envío" or c == "envio":
                hay_envio = True
            if PATRON_ASIN.search(str(it.get("concepto", "")) or ""):
                hay_asin = True

    hay_mes = any(mes in texto for mes in MESES_ESPANOL)

    if (hay_envio or hay_mes or hay_asin) and obj.get("empresa") != "Amazon":
        obj2 = dict(obj)
        obj2["empresa"] = "Amazon"
        motivo = []
        if hay_envio:
            motivo.append("envio")
        if hay_mes:
            motivo.append("mes")
        if hay_asin:
            motivo.append("asin")
        avisos.append(f"regla_amazon=1:motivo={'+'.join(motivo)}")
        return obj2

    return obj



## DETECTOR RESUMEN/COBERTURA


def items_parecen_resumen(items: list) -> bool:
    if not isinstance(items, list):
        return False
    if len(items) > 2:
        return False

    palabras = {"total", "total de compra", "compra", "compras", "total compra", "subtotal"}
    for it in items:
        if not isinstance(it, dict):
            continue
        c = str(it.get("concepto", "")).lower().strip()
        if c in palabras:
            return True
    return False


def cobertura_baja_por_resumen(items: list) -> bool:
    if not isinstance(items, list):
        return False
    if len(items) != 1:
        return False
    if not items:
        return False
    it = items[0]
    if not isinstance(it, dict):
        return False
    c = str(it.get("concepto", "")).lower().strip()
    palabras = {"total", "total de compra", "compra", "compras", "total compra"}
    return c in palabras



## EJECUCIÓN INFERENCIA + PARSE + VALIDACIÓN



def salida_parece_plantilla(texto: str, obj: Dict[str, Any] | None) -> bool:
    """Detecta salidas donde el modelo copia la plantilla del prompt/schema en vez de extraer"""
    t = (texto or "").lower()
    if '"empresa": "string"' in t or '"fecha": "string"' in t or '"concepto": "string"' in t:
        return True
    if obj and isinstance(obj, dict):
        if obj.get("empresa") in (None, "", "string") and obj.get("fecha") in (None, "", "string"):
            return True
        items = obj.get("items")
        if isinstance(items, list) and items:
            it0 = items[0] if isinstance(items[0], dict) else {}
            if isinstance(it0, dict) and it0.get("concepto") == "string" and it0.get("importe_total") == "string":
                return True
    return False


def debe_escalar_por_errs(errs: list[str], texto: str, obj: Dict[str, Any] | None) -> bool:
    """Escala tokens aunque no haya truncado estructural cuando ticket largo"""
    if salida_parece_plantilla(texto, obj):
        return True

    joined = " | ".join(errs or []).lower()
    if "items: [] should be non-empty" in joined:
        return True
    if "'empresa' is a required property" in joined or "'fecha' is a required property" in joined:
        return True
    if "totales: 'total' is a required property" in joined:
        return True
    return False



def merge_impuestos_pass_a(
    principal: Dict[str, Any],
    pass_a_obj: Dict[str, Any] | None,
    avisos: list[str],
) -> Dict[str, Any]:
    """Une bloque impuestos desde Pass A

    - Solo copia claves si en principal no existen
    - Si Pass A trae un mapa con una única entrada NOK"""
    if not isinstance(principal, dict) or not isinstance(pass_a_obj, dict):
        return principal

    imp_a = pass_a_obj.get("impuestos")
    if not isinstance(imp_a, dict) or not imp_a:
        return principal

    imp_p = principal.get("impuestos")
    imp_p = dict(imp_p) if isinstance(imp_p, dict) else {}

    cambios = 0

    # iva_incluido_en_precios: copiar si falta
    if "iva_incluido_en_precios" in imp_a and "iva_incluido_en_precios" not in imp_p:
        if isinstance(imp_a.get("iva_incluido_en_precios"), bool):
            imp_p["iva_incluido_en_precios"] = imp_a["iva_incluido_en_precios"]
            cambios += 1

    # resumen_impuestos: copiar si falta y parece válido (items con 3 campos)
    if "resumen_impuestos" in imp_a and "resumen_impuestos" not in imp_p:
        ri = imp_a.get("resumen_impuestos")
        if isinstance(ri, list) and ri:
            ok = True
            for r in ri:
                if not isinstance(r, dict):
                    ok = False
                    break
                if not all(k in r for k in ("tipo_impuesto", "base_imponible", "importe")):
                    ok = False
                    break
            if ok:
                imp_p["resumen_impuestos"] = ri
                cambios += 1

    # mapa_codigo_impuesto: copiar si falta, pero evita 1 sola entrada sin evidencia en items
    if "mapa_codigo_impuesto" in imp_a and "mapa_codigo_impuesto" not in imp_p:
        mapa = imp_a.get("mapa_codigo_impuesto")
        if isinstance(mapa, dict) and mapa:
            # evidencia: algún item tiene codigo_impuesto
            hay_codigos_items = False
            items = principal.get("items")
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and it.get("codigo_impuesto"):
                        hay_codigos_items = True
                        break

            if len(mapa) == 1 and not hay_codigos_items:
                # IVA único: no incluir mapa
                avisos.append("pass_a_mapa_omitido_iva_unico=1")
            else:
                imp_p["mapa_codigo_impuesto"] = mapa
                cambios += 1

    if cambios:
        principal2 = dict(principal)
        principal2["impuestos"] = imp_p
        avisos.append(f"merge_impuestos_pass_a=1:cambios={cambios}")
        return principal2

    return principal



def generar_crops_verticales(ruta_jpg: str, avisos: list[str], n: int = 3, solape: float = 0.12) -> list[str]:
    """Genera crops verticales con solape para tickets largos"""
    try:
        p = Path(ruta_jpg)
        out_dir = p.parent / "crops_w3"
        out_dir.mkdir(parents=True, exist_ok=True)

        img = Image.open(ruta_jpg)
        w, h = img.size
        if h < 900:
            return []

        ventana = int(h / n) + int(h * solape)
        step = int(h / n)
        rutas: list[str] = []
        for i in range(n):
            y0 = max(0, i * step - int(step * solape))
            y1 = min(h, y0 + ventana)
            crop = img.crop((0, y0, w, y1))
            out_path = out_dir / f"{p.stem}_crop{i+1}_{y0}_{y1}.jpg"
            crop.save(out_path, format="JPEG", quality=95)
            rutas.append(str(out_path))
        avisos.append(f"crops_generados=1:n={len(rutas)}:h={h}")
        return rutas
    except Exception as e:
        avisos.append(f"crops_generados_error=1:{type(e).__name__}")
        return []


def extraer_por_crops_ticket_largo(
    motor_vlm,
    ruta_jpg: str,
    validador,
    avisos: list[str],
    budgets: list[int],
    pass_a_obj: dict | None,
):
    """
    Crops tickets largos:
    - TOP: empresa
    - MIDDLE: items + total
    - BOTTOM: fecha + totales + impuestos
    """

    crops = generar_crops_verticales(ruta_jpg, avisos)
    if not crops:
        return None, "", ["fallback_crops_no_generados"], False

    prompt_top = (
        "Devuelve SOLO JSON con la empresa si aparece. "
        "Sin fecha. Sin items. Sin totales. Sin impuestos. "
        "Estructura: {\"empresa\":\"...\"}"
    )

    prompt_mid = TEXTO_PROMPT_RETRY_ITEMS_ONLY

    prompt_bot = (
        "Devuelve SOLO JSON con fecha, totales e impuestos si aparecen. "
        "Sin items. "
        "Estructura: {\"fecha\":\"...\",\"totales\":{\"total\":\"...\"},\"impuestos\":{...}}"
    )

    rutas = crops[:3] if len(crops) >= 3 else crops

    obj_top = None
    obj_mid = None
    obj_bot = None
    texto_top = texto_mid = texto_bot = ""

    # TOP (empresa)
    for max_tokens in (1024, 2048):
        o, t, errs, trunc = ejecutar_intento(
            motor_vlm=motor_vlm,
            ruta_jpg=rutas[0],
            prompt=prompt_top,
            max_tokens=min(max_tokens, budgets[-1]),
            validador=validador,
            avisos=avisos,
            etiqueta="crop_top",
        )
        texto_top = t
        if o is not None and not errs:
            obj_top = o
            break

    # MIDDLE (items)
    mid_idx = 1 if len(rutas) > 1 else 0
    for max_tokens in budgets:
        o, t, errs, trunc = ejecutar_intento(
            motor_vlm=motor_vlm,
            ruta_jpg=rutas[mid_idx],
            prompt=prompt_mid,
            max_tokens=max_tokens,
            validador=validador,
            avisos=avisos,
            etiqueta="crop_mid_items",
        )
        texto_mid = t
        if o is not None and not errs:
            obj_mid = o
            break

    if not isinstance(obj_mid, dict) or not obj_mid.get("items"):
        return None, texto_mid or texto_top, ["fallback_crops_sin_items"], False

    merged = dict(obj_mid)

    # BOTTOM (fecha + totales + impuestos)
    bot_idx = 2 if len(rutas) > 2 else (len(rutas) - 1)
    for max_tokens in (1024, 2048):
        o, t, errs, trunc = ejecutar_intento(
            motor_vlm=motor_vlm,
            ruta_jpg=rutas[bot_idx],
            prompt=prompt_bot,
            max_tokens=min(max_tokens, budgets[-1]),
            validador=validador,
            avisos=avisos,
            etiqueta="crop_bot_totales",
        )
        texto_bot = t
        if o is not None and not errs:
            obj_bot = o
            break

    # Unir
    if isinstance(obj_top, dict) and not merged.get("empresa"):
        if obj_top.get("empresa"):
            merged["empresa"] = obj_top["empresa"]

    if isinstance(obj_bot, dict):
        if not merged.get("fecha") and obj_bot.get("fecha"):
            merged["fecha"] = obj_bot["fecha"]
        if not merged.get("totales") and obj_bot.get("totales"):
            merged["totales"] = obj_bot["totales"]
        if not merged.get("impuestos") and obj_bot.get("impuestos"):
            merged["impuestos"] = obj_bot["impuestos"]

    # Post-proceso
    merged = merge_impuestos_pass_a(merged, pass_a_obj, avisos)
    merged = aplicar_regla_amazon(merged, texto_mid or "", avisos)
    merged = normalizar_codigo_impuesto_en_items(merged, avisos)
    merged = eliminar_items_no_facturables(merged, avisos)
    merged = limpiar_codigo_impuesto(merged, avisos)
    merged = aplicar_regla_iva_incluido_texto(merged, texto_bot or texto_mid or "", avisos)
    merged = aplicar_regla_mapa_codigo_impuesto_texto(merged, texto_bot or texto_mid or "", avisos)
    merged = aplicar_regla_resumen_impuestos_texto(merged, texto_bot or texto_mid or "", avisos)
    merged = eliminar_mapa_si_iva_unico(merged, avisos)
    merged = normalizar_impuestos_schema_safe(merged, avisos)
    merged = limpiar_vacios(merged)

    errs_final = validador.validar(merged)
    if errs_final:
        avisos.append("fallback_crops_schema_fail=1")
        return merged, texto_mid or texto_bot or texto_top, errs_final, False

    avisos.append("fallback_crops_ok=1")
    return merged, texto_mid or texto_bot or texto_top, [], False


def validar_schema(validador: ValidadorDocumentoGastoV2, obj: Dict[str, Any]) -> Tuple[bool, list[str]]:
    try:
        ok, errs = validador.validar(obj)
        errs2 = [str(e) for e in (errs or [])]
        return ok, errs2
    except Exception as ex:
        return False, [f"Fallo validación schema: {type(ex).__name__}: {ex}"]


def inferir_motor(motor_vlm, ruta_jpg: str, prompt: str, max_new_tokens: int) -> str:
    """
    Con imagen
    """
    try:
        return motor_vlm.inferir(ruta_jpg=ruta_jpg, prompt=prompt, max_new_tokens=max_new_tokens)
    except TypeError:
        try:
            return motor_vlm.inferir(ruta_jpg=ruta_jpg, prompt=prompt)
        except TypeError:
            imagen = Image.open(ruta_jpg).convert("RGB")
            return motor_vlm.inferir(imagen=imagen, texto_prompt=prompt, max_new_tokens=max_new_tokens)


def intentar_reparar_json_con_modelo(
    motor_vlm,
    ruta_jpg: str,
    texto: str,
    max_tokens: int,
    avisos: list[str],
    etiqueta: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Retry con prompt con imagen
    """
    avisos.append(f"{etiqueta}_reparacion_json=1")
    prompt = TEXTO_PROMPT_RETRY_ESTRUCTURA + "\n\n" + str(texto)

    texto_reparado = inferir_motor(motor_vlm, ruta_jpg, prompt, max_tokens)

    obj, err = intentar_pasar_json(texto_reparado)
    if err:
        avisos.append(f"{etiqueta}_reparacion_json_fail=1")
        return None, err
    avisos.append(f"{etiqueta}_reparacion_json_ok=1")
    return obj, None


def ejecutar_intento(
    *,
    motor_vlm,
    ruta_jpg: str,
    prompt: str,
    max_tokens: int,
    validador: ValidadorDocumentoGastoV2,
    avisos: list[str],
    etiqueta: str,
    validar_schema_flag: bool = True,
) -> Tuple[Optional[Dict[str, Any]], str, List[str], bool]:
    errores_intento: list[str] = []
    texto = ""
    try:
        texto = inferir_motor(motor_vlm, ruta_jpg, prompt, max_tokens)
    except Exception as ex:
        errores_intento.append(f"{etiqueta}_inferencia_fail: {type(ex).__name__}: {ex}")
        return None, texto, errores_intento, False

    truncado = detectar_truncado_estructural(texto)
    obj, err = intentar_pasar_json(texto)

    # Parse fail
    if err:
        if truncado:
            avisos.append(f"{etiqueta}_truncado_estructural=1")
            errores_intento.append(err)
            return None, texto, errores_intento, True

        obj2, err2 = intentar_reparar_json_con_modelo(motor_vlm, ruta_jpg, texto, max_tokens, avisos, etiqueta)
        if err2:
            errores_intento.append(f"{etiqueta}_parse_fail=1")
            errores_intento.append(err2)
            return None, texto, errores_intento, False
        obj = obj2

    if obj is None:
        errores_intento.append(f"{etiqueta}_parse_fail=1")
        return None, texto, errores_intento, truncado

    # Normalizaciones antes de schema
    obj = normalizar_impuestos_schema_safe(obj, avisos)
    obj = ajustar_a_schema_documento_gasto_v2(obj, avisos)

    if not validar_schema_flag:
        return obj, texto, [], truncado


    ok_schema, errs = validar_schema(validador, obj)
    if not ok_schema:
        avisos.append(f"{etiqueta}_schema_fail=1")
        errores_intento.extend(errs)

        obj3, err3 = intentar_reparar_json_con_modelo(
            motor_vlm,
            ruta_jpg,
            json.dumps(obj, ensure_ascii=False),
            max_tokens,
            avisos,
            etiqueta + "_schema",
        )
        if err3 is None and obj3 is not None:
            obj3 = normalizar_impuestos_schema_safe(obj3, avisos)
            ok2, errs2 = validar_schema(validador, obj3)
            if ok2:
                avisos.append(f"{etiqueta}_schema_reparado_ok=1")
                return obj3, texto, [], truncado
            errores_intento.extend(errs2)

        return None, texto, errores_intento, truncado

    return obj, texto, [], truncado



## EXTRACCIÓN PRINCIPAL


def extraer_documento(
    *,
    id_documento: int,
    documentos_repositorio,
    extracciones_repositorio,
    motor_vlm,
    validador: ValidadorDocumentoGastoV2,
    modelo_nombre: str = "UNKNOWN",
    version_prompt: str = VERSION_PROMPT,
    texto_prompt: str = TEXTO_PROMPT,
) -> ResultadoExtraccionQwen:

    avisos: list[str] = []
    errores: list[str] = []
    texto_bruto: str = ""
    objeto_json: Dict[str, Any] | None = None

    budgets = [768, 1024, 2048, 3072, 4096]
    avisos.append(f"budgets_plan={budgets}")

    t0_total = time.perf_counter()

    # 1) ruta_jpg
    try:
        ruta_jpg = documentos_repositorio.obtener_ruta_jpg(id_documento)
    except Exception as ex:
        errores.append(f"Fallo al recuperar ruta_jpg: {type(ex).__name__}: {ex}")
        extracciones_repositorio.insertar_extraccion(
            id_documento=id_documento,
            modelo=modelo_nombre,
            version_prompt=version_prompt,
            texto_prompt=texto_prompt,
            ruta_jpg_usada="",
            texto_bruto="",
            json_extraido=None,
            ok=False,
            errores=errores,
            avisos=avisos,
            duracion_ms=0,
            fecha_extraccion=datetime.now(),
        )
        return ResultadoExtraccionQwen(
            id_documento=id_documento,
            ok=False,
            ruta_jpg="",
            texto_bruto=None,
            json_extraido=None,
            errores=errores,
            avisos=avisos,
        )

    # 2) Pass A (totales + impuestos)
    prompt_pass_a = """Eres un sistema de extracción de información de tickets y facturas.

Devuelve EXCLUSIVAMENTE un JSON válido (sin Markdown ni texto adicional).
En esta PASADA A, extrae SOLO: empresa, fecha, totales e impuestos.
NO incluyas items.

IMPORTANTE: NO copies la plantilla del schema ni devuelvas literales como 'string'.

Estructura esperada (solo como guía):
{
  "empresa": "...",
  "fecha": "...",
  "totales": { "total": "..." },
  "impuestos": { ... }
}

Impuestos (SOLO si aparece explícitamente):
- iva_incluido_en_precios: solo si aparece literal 'IVA incluido' (true) o 'IVA no incluido/sin IVA' (false).
- mapa_codigo_impuesto: solo si hay leyenda de códigos (A/B/C/1/2/3).
- resumen_impuestos: solo si hay desglose con base + importe.
Si aparece '(B) IMP 10,00%' o 'B IMP 10,00%', interpreta IMP como IVA y usa 'IVA 10%'.
No inventes datos.
"""
    pass_a_obj = None
    pass_a_texto = None
    try:
        pass_a_obj, pass_a_texto, pass_a_errs, _ = ejecutar_intento(
            motor_vlm=motor_vlm,
            ruta_jpg=ruta_jpg,
            prompt=prompt_pass_a,
            max_tokens=min(384, budgets[0]),
            validador=validador,
            avisos=avisos,
            etiqueta="pass_a",
            validar_schema_flag=False,
        )
        if pass_a_errs:
            avisos.append("pass_a_parse_fail=1")
    except Exception as ex:
        avisos.append(f"pass_a_exception:{type(ex).__name__}")

    # 3) Pass B (items + schema)
    for i, max_tokens in enumerate(budgets):
        t0 = time.perf_counter()
        obj, texto, errs, truncado = ejecutar_intento(
            motor_vlm=motor_vlm,
            ruta_jpg=ruta_jpg,
            prompt=texto_prompt,
            max_tokens=max_tokens,
            validador=validador,
            avisos=avisos,
            etiqueta="principal",
        )
        duracion_ms_intento = int((time.perf_counter() - t0) * 1000)
        texto_bruto = texto

        # Si el modelo copia la plantilla del prompt, es fallo
        if salida_parece_plantilla(texto, obj):
            errs = (errs or [])
            errs.append("salida_parece_plantilla")
            avisos.append("principal_salida_plantilla=1")

        if obj is not None and not errs:
            objeto_json = obj
            avisos.append(f"max_new_tokens_usado={max_tokens}")
            avisos.append(f"duracion_ms_intento={duracion_ms_intento}")
            if i > 0:
                avisos.append("reintento_escalado_tokens=1")
            break

        if truncado and (i + 1) < len(budgets):
            avisos.append(f"escalado_tokens=1:motivo=final_sin_cierre:{max_tokens}->{budgets[i+1]}")
            continue

        if errs:
            errores = errs
            avisos.append(f"max_new_tokens_usado={max_tokens}")
            avisos.append(f"duracion_ms_intento={duracion_ms_intento}")

            if debe_escalar_por_errs(errs, texto, obj) and (i + 1) < len(budgets):
                avisos.append(f"escalado_tokens=1:motivo=errores_sin_truncado:{max_tokens}->{budgets[i+1]}")
                continue

            break

    duracion_ms_total = int((time.perf_counter() - t0_total) * 1000)
    fecha_extraccion = datetime.now()

    # 3) fallo total

    # crops verticales si falla en tickets largos
    obj_crops, texto_crops, errs_crops, _ = extraer_por_crops_ticket_largo(
        motor_vlm=motor_vlm,
        ruta_jpg=ruta_jpg,
        validador=validador,
        avisos=avisos,
        budgets=budgets,
        pass_a_obj=pass_a_obj,
    )
    if obj_crops is not None and not errs_crops:
        objeto_json = obj_crops
        texto_bruto = texto_crops or texto_bruto
        errores = []
    elif obj_crops is not None and errs_crops:
        # si devuelve algo pero no pasa schema, registramos errores para diagnóstico
        errores = errs_crops
        texto_bruto = texto_crops or texto_bruto

    if objeto_json is None:
        # retry para items
        obj_retry = None
        texto_retry_final = ""
        errores_retry: list[str] = []
        for j, max_tokens in enumerate(budgets):
            obj2, texto2, errs2, trunc2 = ejecutar_intento(
                motor_vlm=motor_vlm,
                ruta_jpg=ruta_jpg,
                prompt=TEXTO_PROMPT_RETRY_ITEMS_ONLY,
                max_tokens=max_tokens,
                validador=validador,
                avisos=avisos,
                etiqueta="retry_items_only",
            )
            texto_retry_final = texto2
            if obj2 is not None and not errs2:
                obj_retry = obj2
                avisos.append(f"retry_items_only_ok=1:max_new_tokens={max_tokens}")
                break
            if trunc2 and (j + 1) < len(budgets):
                avisos.append(
                    f"retry_items_only_escalado_tokens=1:motivo=truncado_estructural:{max_tokens}->{budgets[j+1]}"
                )
                continue
            errores_retry = errs2 or errores_retry
            avisos.append("retry_items_only_fallo=1")
            break

        if obj_retry is not None:
            texto_bruto = texto_retry_final or texto_bruto
            objeto_json = obj_retry

            # post-proceso
            objeto_json = merge_impuestos_pass_a(objeto_json, pass_a_obj, avisos)
            objeto_json = aplicar_regla_amazon(objeto_json, texto_bruto or "", avisos)
            objeto_json = normalizar_codigo_impuesto_en_items(objeto_json, avisos)
            objeto_json = eliminar_items_no_facturables(objeto_json, avisos)
            objeto_json = limpiar_codigo_impuesto(objeto_json, avisos)
            objeto_json = aplicar_regla_iva_incluido_texto(objeto_json, texto_bruto or "", avisos)
            objeto_json = aplicar_regla_mapa_codigo_impuesto_texto(objeto_json, texto_bruto or "", avisos)
            objeto_json = aplicar_regla_resumen_impuestos_texto(objeto_json, texto_bruto or "", avisos)
            objeto_json = eliminar_mapa_si_iva_unico(objeto_json, avisos)
            objeto_json = normalizar_impuestos_schema_safe(objeto_json, avisos)
            objeto_json = limpiar_vacios(objeto_json)

            # validar
            ok_retry, errs_final = validador.validar(objeto_json)
            if not ok_retry:
                errores = errs_final
                avisos.append("retry_items_only_schema_fail=1")

            else:
                errores = []

        if objeto_json is None:
            errores = errores or errores_retry
            extracciones_repositorio.insertar_extraccion(
                id_documento=id_documento,
                modelo=modelo_nombre,
                version_prompt=version_prompt,
                texto_prompt=texto_prompt,
                ruta_jpg_usada=ruta_jpg,
                texto_bruto=texto_bruto or "",
                json_extraido=None,
                ok=False,
                errores=errores or [],
                avisos=avisos or [],
                duracion_ms=duracion_ms_total,
                fecha_extraccion=fecha_extraccion,
            )
            return ResultadoExtraccionQwen(
                id_documento=id_documento,
                ok=False,
                ruta_jpg=ruta_jpg,
                texto_bruto=texto_bruto,
                json_extraido=None,
                errores=errores,
                avisos=avisos,
            )

    # 4) post-proceso
    # unir impuestos dePass A
    objeto_json = merge_impuestos_pass_a(objeto_json, pass_a_obj, avisos)
    objeto_json = aplicar_regla_amazon(objeto_json, texto_bruto or "", avisos)
    objeto_json = normalizar_codigo_impuesto_en_items(objeto_json, avisos)

    # Primero eliminar items no facturables
    objeto_json = eliminar_items_no_facturables(objeto_json, avisos)
    objeto_json = limpiar_codigo_impuesto(objeto_json, avisos)
    objeto_json = aplicar_regla_iva_incluido_texto(objeto_json, texto_bruto or "", avisos)

    # Reglas texto fuera de JSON
    objeto_json = aplicar_regla_mapa_codigo_impuesto_texto(objeto_json, texto_bruto or "", avisos)
    objeto_json = aplicar_regla_resumen_impuestos_texto(objeto_json, texto_bruto or "", avisos)
    objeto_json = eliminar_mapa_si_iva_unico(objeto_json, avisos)

    objeto_json = normalizar_impuestos_schema_safe(objeto_json, avisos)
    objeto_json = limpiar_vacios(objeto_json)
    
    # 5) retry anti-resumen
    items = objeto_json.get("items")
    if isinstance(items, list) and items_parecen_resumen(items):
        avisos.append(f"items_parecen_resumen=1:n_items={len(items)}")
        obj_retry = None
        texto_retry_final = ""
        for j, max_tokens in enumerate(budgets):
            obj2, texto2, errs2, trunc2 = ejecutar_intento(
                motor_vlm=motor_vlm,
                ruta_jpg=ruta_jpg,
                prompt=TEXTO_PROMPT_RETRY_ANTI_RESUMEN,
                max_tokens=max_tokens,
                validador=validador,
                avisos=avisos,
                etiqueta="retry_anti_resumen",
            )
            texto_retry_final = texto2
            if obj2 is not None and not errs2:
                obj_retry = obj2
                avisos.append(f"retry_anti_resumen_ok=1:max_new_tokens={max_tokens}")
                break
            if trunc2 and (j + 1) < len(budgets):
                avisos.append(
                    f"retry_anti_resumen_escalado_tokens=1:motivo=truncado_estructural:{max_tokens}->{budgets[j+1]}"
                )
                continue
            avisos.append("retry_anti_resumen_fallo=1")
            break

        if obj_retry is not None:
            texto_bruto = texto_retry_final or texto_bruto
            objeto_json = obj_retry

            objeto_json = aplicar_regla_amazon(objeto_json, texto_bruto or "", avisos)
            objeto_json = normalizar_codigo_impuesto_en_items(objeto_json, avisos)
            objeto_json = eliminar_items_no_facturables(objeto_json, avisos)
            objeto_json = limpiar_codigo_impuesto(objeto_json, avisos)
            objeto_json = aplicar_regla_iva_incluido_texto(objeto_json, texto_bruto or "", avisos)
            objeto_json = aplicar_regla_mapa_codigo_impuesto_texto(objeto_json, texto_bruto or "", avisos)
            objeto_json = aplicar_regla_resumen_impuestos_texto(objeto_json, texto_bruto or "", avisos)
            objeto_json = eliminar_mapa_si_iva_unico(objeto_json, avisos)
            objeto_json = normalizar_impuestos_schema_safe(objeto_json, avisos)
            objeto_json = limpiar_vacios(objeto_json)

    # 6) retry cobertura
    items2 = objeto_json.get("items")
    if isinstance(items2, list) and cobertura_baja_por_resumen(items2):
        avisos.append(f"cobertura_baja_items=1:n_items={len(items2)}")
        obj_retry = None
        texto_retry_final = ""
        for j, max_tokens in enumerate(budgets):
            obj2, texto2, errs2, trunc2 = ejecutar_intento(
                motor_vlm=motor_vlm,
                ruta_jpg=ruta_jpg,
                prompt=TEXTO_PROMPT_RETRY_COBERTURA,
                max_tokens=max_tokens,
                validador=validador,
                avisos=avisos,
                etiqueta="retry_cobertura",
            )
            texto_retry_final = texto2
            if obj2 is not None and not errs2:
                obj_retry = obj2
                avisos.append(f"retry_cobertura_ok=1:max_new_tokens={max_tokens}")
                break
            if trunc2 and (j + 1) < len(budgets):
                avisos.append(
                    f"retry_cobertura_escalado_tokens=1:motivo=truncado_estructural:{max_tokens}->{budgets[j+1]}"
                )
                continue
            avisos.append("retry_cobertura_fallo=1")
            break

        if obj_retry is not None:
            texto_bruto = texto_retry_final or texto_bruto
            objeto_json = obj_retry

            objeto_json = aplicar_regla_amazon(objeto_json, texto_bruto or "", avisos)
            objeto_json = normalizar_codigo_impuesto_en_items(objeto_json, avisos)
            objeto_json = eliminar_items_no_facturables(objeto_json, avisos)
            objeto_json = aplicar_regla_mapa_codigo_impuesto_texto(objeto_json, texto_bruto or "", avisos)
            objeto_json = aplicar_regla_resumen_impuestos_texto(objeto_json, texto_bruto or "", avisos)
            objeto_json = normalizar_impuestos_schema_safe(objeto_json, avisos)
            objeto_json = limpiar_vacios(objeto_json)


    # 6.5) Unir info de Pass A y Pass B
    # - Pasamos impuestos/totales de Pass A a Pass B si faltan
    if pass_a_obj and isinstance(pass_a_obj, dict):
        if pass_a_texto:
            texto_bruto = (pass_a_texto + "\n\n---PASS_B---\n\n" + (texto_bruto or "")).strip()

        # unir empresa/fecha
        for k in ("empresa", "fecha"):
            if (not objeto_json.get(k)) and pass_a_obj.get(k):
                objeto_json[k] = pass_a_obj.get(k)

        # unir impuestos
        imp_a = pass_a_obj.get("impuestos")
        if imp_a and (not objeto_json.get("impuestos")):
            objeto_json["impuestos"] = imp_a

        # unir totales
        tot_a = pass_a_obj.get("totales")
        tot_b = objeto_json.get("totales")
        if isinstance(tot_a, dict):
            if not isinstance(tot_b, dict):
                objeto_json["totales"] = tot_a
            else:
                for kk, vv in tot_a.items():
                    if kk not in tot_b and vv not in (None, "", "string"):
                        tot_b[kk] = vv
                objeto_json["totales"] = tot_b

    # 7) ajuste final a schema
    objeto_json = ajustar_a_schema_documento_gasto_v2(objeto_json, avisos)
    objeto_json = normalizar_codigo_impuesto_en_items(objeto_json, avisos)
    objeto_json = mejorar_codigo_impuesto_en_items(objeto_json, avisos)

    # 8) validación final
    ok_final, errs_final = validar_schema(validador, objeto_json)
    ok_bool = bool(ok_final)
    errores = [] if ok_bool else (errs_final or [])

    # 8) persistencia
    extracciones_repositorio.insertar_extraccion(
        id_documento=id_documento,
        modelo=modelo_nombre,
        version_prompt=version_prompt,
        texto_prompt=texto_prompt,
        ruta_jpg_usada=ruta_jpg,
        texto_bruto=texto_bruto or "",
        json_extraido=objeto_json if ok_bool else objeto_json,  # guardamos aunque NOK
        ok=ok_bool,
        errores=errores or [],
        avisos=avisos or [],
        duracion_ms=duracion_ms_total,
        fecha_extraccion=fecha_extraccion,
    )

    return ResultadoExtraccionQwen(
        id_documento=id_documento,
        ok=ok_bool,
        ruta_jpg=ruta_jpg,
        texto_bruto=texto_bruto,
        json_extraido=objeto_json,
        errores=errores,
        avisos=avisos,
    )


## CLI


def ejecutar_cli() -> int:
    cargar_env_desde_raiz()
    cfg = cargar_config()

    parser = argparse.ArgumentParser(description="Extracción Qwen")
    parser.add_argument("--id-documento", type=int, required=True, help="id_documento a extraer")
    args = parser.parse_args()

    conexion = crear_conexion_mysql(cfg)
    try:
        documentos_repositorio = RepositorioDocumentosMySQL(conexion, cfg)
        extracciones_repositorio = RepositorioExtraccionesQwenMySQL(conexion)

        # Config motor
        config_motor = ConfigMotorQwen(
            model_id=str(cfg.qwen.model_id),
            base_model_path=str(cfg.qwen.base_model_path),
            strict_local_only=bool(cfg.qwen.strict_local_only),
            dtype=str(cfg.qwen.dtype),
            load_in_4bit=bool(cfg.qwen.load_in_4bit),
            max_new_tokens=int(cfg.qwen.max_new_tokens),
        )
        motor_vlm = MotorQwenTransformers(config_motor)

        validador = ValidadorDocumentoGastoV2()

        salida = extraer_documento(
            id_documento=int(args.id_documento),
            documentos_repositorio=documentos_repositorio,
            extracciones_repositorio=extracciones_repositorio,
            motor_vlm=motor_vlm,
            validador=validador,
            modelo_nombre=str(config_motor.model_id),
            version_prompt=VERSION_PROMPT,
            texto_prompt=TEXTO_PROMPT,
        )

        print(json.dumps({
            "id_documento": salida.id_documento,
            "ok": salida.ok,
            "errores": salida.errores,
            "avisos": salida.avisos,
        }, ensure_ascii=False, indent=2))

        return 0 if salida.ok else 2
    finally:
        conexion.close()


if __name__ == "__main__":
    raise SystemExit(ejecutar_cli())