from __future__ import annotations

import sys
from pathlib import Path
import os
from huggingface_hub import hf_hub_download

# Resolver ruta base antes de cualquier import del proyecto
ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[2]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import torch
from mysql.connector.connection import MySQLConnection
from tqdm import tqdm
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from utilidades.configuracion import cargar_config
from bd.conexion import crear_conexion_mysql
from scripts.reglas_subetiquetas import obtener_reglas_subetiquetas


def extraer_empresa_y_texto_ticket(documento_normalizado_json: Any) -> Tuple[str, str]:
    """Extrae empresa y texto del ticket desde el JSON normalizado"""
    if documento_normalizado_json is None:
        return "", ""

    if isinstance(documento_normalizado_json, str):
        try:
            obj = json.loads(documento_normalizado_json)
        except Exception:
            return "", ""
    else:
        obj = documento_normalizado_json

    if not isinstance(obj, dict):
        return "", ""

    empresa = str(obj.get("empresa", "") or "")
    items = obj.get("items") or []

    partes: List[str] = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                concepto = str(it.get("concepto", "") or "")
                if concepto:
                    partes.append(concepto)

    texto_ticket = " ".join(partes)
    return empresa, texto_ticket



## Configuración del módulo


VERSION_MODELO = "clasificacion_xlmr_base_macro_v2_len512_v1"
UMBRAL_CONFIANZA = 0.60
LONGITUD_MAXIMA = 512

CATEGORIAS_MACRO = [
    "alimentacion",
    "hogar_servicios_suscripciones",
    "consumo_personal_hogar",
    "salud",
    "restauracion_ocio",
    "educacion",
    "otros",
]

MACRO_FALLBACK = "otros"
ETIQUETA_FALLBACK_PUBLICA = "otros"



## Normalización + etiquetas


def normalizar_texto_para_reglas(texto: str) -> str:
    s = "" if texto is None else str(texto)
    s = s.lower().strip()
    # quitar tildes y diacríticos
    import unicodedata, re
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s


def contiene_alguno(texto_normalizado: str, patrones: List[str]) -> bool:
    if not patrones:
        return False
    return any(p and (p in texto_normalizado) for p in patrones)


# Mapeo sub-etiqueta interna -> etiqueta pública (usuario final)
SUB_A_ETIQUETA_PUBLICA: Dict[str, str] = {
    "alimentacion_supermercado": "supermercado",
    "alimentacion_panaderia": "panaderia",
    "alimentacion_carniceria": "carniceria",
    "alimentacion_verduleria_fruteria": "fruteria",

    "suministros_luz": "luz",
    "suministros_gas": "gas",
    "suministros_agua": "agua",
    "telecomunicaciones": "telecomunicaciones",
    "seguros": "seguros",
    "suscripciones_digitales": "suscripciones",
    "hogar_comunidad_vivienda": "hogar_comunidad_vivienda",

    "transporte_combustible": "combustible",
    "transporte_peaje": "peaje",
    "transporte_mantenimiento": "taller",
    "estanco": "estanco",
    "moda_ropa": "ropa",
    "moda_calzado": "calzado",
    "electronica_tecnologia": "electronica_tecnologia",
    "hogar_brico_menaje_mobiliario": "hogar_brico_menaje_mobiliario",
    "peluqueria_estetica": "peluqueria",
    "papeleria": "papeleria",

    "farmacia": "farmacia",
    "dentista": "dentista",

    "restaurante_bar": "restaurante_bar",
}

# Sub-etiquetas internas
SUBS_FALLBACK_INTERNAS = {
    "alimentacion_otro",
    "hogar_servicios_otro",
    "consumo_otro",
    "salud_otro",
    "ocio_otro",
    "educacion_otro",
    "otros_sin_regla",
}


def es_subetiqueta_especifica(sub_interna: str) -> bool:
    s = (sub_interna or "").strip()
    if not s:
        return False
    if s in SUBS_FALLBACK_INTERNAS:
        return False
    return s in SUB_A_ETIQUETA_PUBLICA


def etiqueta_publica_desde_sub(sub_interna: str) -> str:
    if es_subetiqueta_especifica(sub_interna):
        return SUB_A_ETIQUETA_PUBLICA[sub_interna]
    return ETIQUETA_FALLBACK_PUBLICA





## Modelo


@dataclass(frozen=True)
class ArtefactosModeloClasificacion:
    ruta_modelo: Path
    tokenizador: Any
    modelo: Any
    metadatos: Dict[str, Any]
    dispositivo: str


def cargar_modelo_clasificacion_desde_disco(
    ruta_base_proyecto: Optional[Path] = None,
    version_modelo: str = VERSION_MODELO,
) -> ArtefactosModeloClasificacion:
    cfg = cargar_config(ruta_base=ruta_base_proyecto)

    model_id = os.getenv("CLASIFICADOR_MODEL_ID", "").strip()

    if model_id:
        ruta_modelo_repr = Path(model_id)

        metadatos_path = hf_hub_download(
            repo_id=model_id,
            filename="metadatos.json",
            repo_type="model",
        )

        metadatos = json.loads(Path(metadatos_path).read_text(encoding="utf-8"))

        tokenizador = AutoTokenizer.from_pretrained(model_id)
        modelo = AutoModelForSequenceClassification.from_pretrained(model_id)

    else:
        ruta_modelo = (cfg.rutas.modelos / "clasificacion" / version_modelo).resolve()

        if not ruta_modelo.exists():
            raise FileNotFoundError(f"No existe la ruta del modelo: {ruta_modelo}")

        metadatos_path = ruta_modelo / "metadatos.json"
        if not metadatos_path.exists():
            raise FileNotFoundError(f"No existe metadatos.json en: {metadatos_path}")

        metadatos = json.loads(metadatos_path.read_text(encoding="utf-8"))

        tokenizador = AutoTokenizer.from_pretrained(str(ruta_modelo))
        modelo = AutoModelForSequenceClassification.from_pretrained(str(ruta_modelo))
        ruta_modelo_repr = ruta_modelo

    dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
    modelo.to(dispositivo)
    modelo.eval()

    return ArtefactosModeloClasificacion(
        ruta_modelo=ruta_modelo_repr,
        tokenizador=tokenizador,
        modelo=modelo,
        metadatos=metadatos,
        dispositivo=dispositivo,
    )


## Lectura de datos a clasificar


def leer_documentos_normalizados_para_clasificar(
    conexion: MySQLConnection,
    limite: Optional[int] = None,
    solo_no_clasificados: bool = True,
) -> pd.DataFrame:
    """Devuelve id_documento + documento_normalizado_json"""
    sql_base = """
    SELECT
        n.id_documento,
        n.documento_normalizado_json
    FROM normalizaciones n
    WHERE n.ok_normalizacion = 1
    """

    if solo_no_clasificados:
        sql_base += """
        AND NOT EXISTS (
            SELECT 1
            FROM clasificaciones c
            WHERE c.id_documento = n.id_documento
        )
        """

    sql_base += " ORDER BY n.id_documento ASC"

    if limite is not None:
        sql_base += " LIMIT %s"

    cur = conexion.cursor(dictionary=True)
    try:
        if limite is not None:
            cur.execute(sql_base, (int(limite),))
        else:
            cur.execute(sql_base)
        filas = cur.fetchall() or []
        df = pd.DataFrame(filas)
        if df.empty:
            return pd.DataFrame(columns=["id_documento", "documento_normalizado_json"])
        return df
    finally:
        cur.close()


## Inferencia


def leer_documento_normalizado_por_id(
    conexion: MySQLConnection,
    id_documento: int,
) -> Optional[Dict[str, Any]]:
    """Lee el JSON normalizado (última normalización OK) para un id_documento"""
    sql = """
        SELECT
            n.id_documento,
            n.documento_normalizado_json
        FROM normalizaciones n
        WHERE n.id_documento = %s
          AND n.ok_normalizacion = 1
        LIMIT 1
    """
    cursor = conexion.cursor()
    try:
        cursor.execute(sql, (int(id_documento),))
        fila = cursor.fetchone()
        if not fila:
            return None
        return {
            "id_documento": int(fila[0]),
            "documento_normalizado_json": fila[1],
        }
    finally:
        cursor.close()

def inferir_macro_top1(
    artefactos: ArtefactosModeloClasificacion,
    textos: List[str],
    max_length: int = LONGITUD_MAXIMA,
    batch_size: int = 32,
) -> Tuple[List[str], List[float]]:
    """
    Devuelve:
      - macro_pred_top1
      - conf_top1
    """
    id_a_macro = artefactos.metadatos.get("id_a_macro", {})
    if not id_a_macro:
        raise RuntimeError("metadatos.json no contiene id_a_macro")

    tokenizador = artefactos.tokenizador
    modelo = artefactos.modelo
    dispositivo = artefactos.dispositivo

    macros: List[str] = []
    confs: List[float] = []

    for i in tqdm(range(0, len(textos), batch_size), desc="Inferencia"):
        batch = textos[i : i + batch_size]
        enc = tokenizador(
            batch,
            truncation=True,
            max_length=int(max_length),
            padding=True,
            return_tensors="pt",
        )
        enc = {k: v.to(dispositivo) for k, v in enc.items()}

        with torch.no_grad():
            salida = modelo(**enc)
            logits = salida.logits

        probas = torch.softmax(logits, dim=-1).detach().cpu().numpy()
        top1 = probas.argmax(axis=1)
        conf_top1 = probas.max(axis=1)

        for idx, c in zip(top1.tolist(), conf_top1.tolist()):
            macro = id_a_macro.get(str(idx), MACRO_FALLBACK)
            macros.append(str(macro))
            confs.append(float(c))

    return macros, confs



## Sub-etiquetado GLOBAL


def subetiquetar_global(empresa: str, texto: str) -> Tuple[str, str, str, bool]:
    """
    Evalúa TODAS las reglas, con fallback_por_empresa al final
    Devuelve:
      - macro_regla
      - sub_interna
      - regla_aplicada
      - fue_fallback_gas_por_empresa
    """
    reglas = obtener_reglas_subetiquetas()

    empresa_n = normalizar_texto_para_reglas(empresa)
    texto_n = normalizar_texto_para_reglas(texto)

    reglas_no_fallback = sorted([r for r in reglas if not getattr(r, "fallback_por_empresa", False)],
                                key=lambda r: int(getattr(r, "prioridad", 0)), reverse=True)
    reglas_fallback = sorted([r for r in reglas if getattr(r, "fallback_por_empresa", False)],
                             key=lambda r: int(getattr(r, "prioridad", 0)), reverse=True)

    # 1) reglas normales
    for r in reglas_no_fallback:
        macro = str(getattr(r, "macro", "") or "")
        sub = str(getattr(r, "sub_etiqueta", "") or "")
        prioridad = int(getattr(r, "prioridad", 0))

        empresa_contiene = [normalizar_texto_para_reglas(x) for x in (getattr(r, "empresa_contiene", []) or [])]
        texto_contiene = [normalizar_texto_para_reglas(x) for x in (getattr(r, "texto_contiene", []) or [])]
        requiere_texto = bool(getattr(r, "requiere_texto_contiene", False))

        coincide_empresa = contiene_alguno(empresa_n, empresa_contiene) if empresa_contiene else False
        coincide_texto = contiene_alguno(texto_n, texto_contiene) if texto_contiene else False

        if requiere_texto:
            ok = coincide_empresa and coincide_texto
        else:
            ok = coincide_empresa or coincide_texto

        if ok:
            return macro, sub, f"{macro}:{sub}:prioridad={prioridad}", False

    # 2) fallback por empresa (gas)
    for r in reglas_fallback:
        macro = str(getattr(r, "macro", "") or "")
        sub = str(getattr(r, "sub_etiqueta", "") or "")
        empresa_contiene = [normalizar_texto_para_reglas(x) for x in (getattr(r, "empresa_contiene", []) or [])]
        coincide_empresa = contiene_alguno(empresa_n, empresa_contiene) if empresa_contiene else False
        if coincide_empresa:
            return macro, sub, f"{macro}:{sub}:fallback_por_empresa", True

    # 3) sin regla
    return "otros", "otros_sin_regla", "fallback_sin_regla", False



## Persistencia (clasificaciones_hist + clasificaciones)


def insertar_clasificacion_hist(
    conexion: MySQLConnection,
    id_normalizacion: int,
    ok_clasificacion: int,
    categoria_nivel_1: str,
    etiqueta: str,
    confianza: Optional[float],
    avisos_json: Any,
    error_mensaje: Optional[str],
    version_modulo: str,
) -> int:
    sql = """
    INSERT INTO clasificaciones_hist (
        id_normalizacion,
        ok_clasificacion,
        categoria_nivel_1,
        etiqueta,
        confianza,
        avisos_json,
        error_mensaje,
        version_modulo
    )
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """
    cur = conexion.cursor()
    try:
        cur.execute(
            sql,
            (
                int(id_normalizacion),
                int(ok_clasificacion),
                str(categoria_nivel_1),
                str(etiqueta),
                float(confianza) if confianza is not None else None,
                json.dumps(avisos_json, ensure_ascii=False),
                error_mensaje,
                version_modulo,
            ),
        )
        conexion.commit()
        return int(cur.lastrowid)
    finally:
        cur.close()


def upsert_clasificacion_actual(
    conexion: MySQLConnection,
    id_documento: int,
    categoria_nivel_1: str,
    etiqueta: str,
    confianza: Optional[float],
    id_clasificacion_fuente: int,
) -> None:
    sql = """
    INSERT INTO clasificaciones (
        id_documento,
        categoria_nivel_1,
        etiqueta,
        confianza,
        id_clasificacion_fuente
    )
    VALUES (%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
        categoria_nivel_1=VALUES(categoria_nivel_1),
        etiqueta=VALUES(etiqueta),
        confianza=VALUES(confianza),
        id_clasificacion_fuente=VALUES(id_clasificacion_fuente)
    """
    cur = conexion.cursor()
    try:
        cur.execute(
            sql,
            (
                int(id_documento),
                str(categoria_nivel_1),
                str(etiqueta),
                float(confianza) if confianza is not None else None,
                int(id_clasificacion_fuente),
            ),
        )
        conexion.commit()
    finally:
        cur.close()


def leer_id_normalizacion_ultima_ok(conexion: MySQLConnection, id_documento: int) -> int:
    """
    FK de clasificaciones_hist -> normalizaciones_hist
    Tomamos la última normalización OK del documento
    """
    sql = """
    SELECT id_normalizacion
    FROM normalizaciones_hist
    WHERE id_documento=%s AND ok_normalizacion=1
    ORDER BY id_normalizacion DESC
    LIMIT 1
    """
    cur = conexion.cursor()
    try:
        cur.execute(sql, (int(id_documento),))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"No hay normalizacion_hist OK para id_documento={id_documento}")
        return int(row[0])
    finally:
        cur.close()



## Ejecución



def ejecutar_clasificacion_documento(
    conexion: MySQLConnection,
    id_documento: int,
    version_modulo: Optional[str] = None,
    umbral_confianza: float = UMBRAL_CONFIANZA,
) -> Dict[str, Any]:
    """Clasifica y persiste la clasificación del documento"""
    version_modulo_final = version_modulo or f"{VERSION_MODELO}_thr{umbral_confianza:.2f}"

    registro = leer_documento_normalizado_por_id(conexion=conexion, id_documento=int(id_documento))
    if registro is None:
        return {
            "ok": False,
            "motivo": "no_existe_normalizacion_ok",
            "id_documento": int(id_documento),
            "version_modulo": version_modulo_final,
        }

    artefactos = cargar_modelo_clasificacion_desde_disco(version_modelo=VERSION_MODELO)

    empresa, texto_ticket = extraer_empresa_y_texto_ticket(registro["documento_normalizado_json"])
    texto_entrada = f"EMPRESA: {empresa}\nTEXTO: {texto_ticket}"

    macro_pred, confs = inferir_macro_top1(
        artefactos=artefactos,
        textos=[str(texto_entrada)],
        max_length=int(artefactos.metadatos.get("max_length", LONGITUD_MAXIMA)),
        batch_size=1,
    )
    macro_modelo = str(macro_pred[0] or MACRO_FALLBACK)
    confianza = float(confs[0]) if confs and confs[0] is not None else None

    macro_r, sub_i, regla, fb_gas = subetiquetar_global(empresa=str(empresa or ""), texto=str(texto_ticket or ""))

    if es_subetiqueta_especifica(sub_i):
        macro_final = macro_r if macro_r in CATEGORIAS_MACRO else MACRO_FALLBACK
        etiqueta_final = etiqueta_publica_desde_sub(sub_i)
        regla_especifica = True
    else:
        macro_final = macro_modelo if macro_modelo in CATEGORIAS_MACRO else MACRO_FALLBACK
        etiqueta_final = ETIQUETA_FALLBACK_PUBLICA
        regla_especifica = False

    ok_por_confianza = (confianza is not None) and (float(confianza) >= float(umbral_confianza))
    ok_clasificacion = 1 if (ok_por_confianza or regla_especifica) else 0

    avisos: List[Dict[str, Any]] = []
    if (confianza is None) or (float(confianza) < float(umbral_confianza)):
        avisos.append({"motivo": "confianza_baja", "umbral_confianza": float(umbral_confianza)})

    if regla_especifica and (macro_final != macro_modelo):
        avisos.append(
            {
                "motivo": "post_clasificacion_por_reglas",
                "macro_pred_modelo": macro_modelo,
                "macro_final": macro_final,
                "sub_etiqueta_interna": sub_i,
                "etiqueta_publica": etiqueta_final,
                "regla_aplicada": str(regla or ""),
                "fallback_gas_por_empresa": bool(fb_gas),
            }
        )

    id_norm_hist = leer_id_normalizacion_ultima_ok(conexion=conexion, id_documento=int(id_documento))

    id_clasif_hist = insertar_clasificacion_hist(
        conexion=conexion,
        id_normalizacion=int(id_norm_hist),
        ok_clasificacion=int(ok_clasificacion),
        categoria_nivel_1=str(macro_final),
        etiqueta=str(etiqueta_final),
        confianza=confianza,
        avisos_json=avisos,
        error_mensaje=None,
        version_modulo=version_modulo_final,
    )

    upsert_clasificacion_actual(
        conexion=conexion,
        id_documento=int(id_documento),
        categoria_nivel_1=str(macro_final),
        etiqueta=str(etiqueta_final),
        confianza=confianza,
        id_clasificacion_fuente=int(id_clasif_hist),
    )

    return {
        "ok": True,
        "id_documento": int(id_documento),
        "macro_categoria": macro_final,
        "etiqueta_publica": etiqueta_final,
        "confianza": confianza,
        "ok_clasificacion": int(ok_clasificacion),
        "version_modulo": version_modulo_final,
        "version_modelo": VERSION_MODELO,
    }



## CLI


if __name__ == "__main__":
    import argparse
    import json

    from utilidades.configuracion import cargar_env_desde_raiz, cargar_config
    from bd.conexion import crear_conexion_mysql

    parser = argparse.ArgumentParser(description="Clasificación de un único documento (modo depuración)")
    parser.add_argument("--id-documento", type=int, required=True, help="id_documento a clasificar")
    args = parser.parse_args()

    cargar_env_desde_raiz()
    cfg = cargar_config()

    conexion = crear_conexion_mysql(cfg)
    try:
        resultado = ejecutar_clasificacion_documento(
            conexion=conexion,
            id_documento=int(args.id_documento),
            version_modulo=None,
            umbral_confianza=float(UMBRAL_CONFIANZA),
        )
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    finally:
        conexion.close()