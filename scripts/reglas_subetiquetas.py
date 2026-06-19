from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class ReglaSubetiqueta:
    macro: str
    sub_etiqueta: str
    prioridad: int
    empresa_contiene: List[str]
    texto_contiene: List[str]
    requiere_texto_contiene: bool  # si True: empresa y texto (AND). si False: empresa o texto (OR)
    fallback_por_empresa: bool      # si True: si empresa coincide y no hubo match previo -> asigna esta sub_etiqueta


def obtener_reglas_subetiquetas() -> List[ReglaSubetiqueta]:

    reglas: List[ReglaSubetiqueta] = []


    ## alimentacion

    reglas += [
        ReglaSubetiqueta(
            macro="alimentacion",
            sub_etiqueta="alimentacion_supermercado",
            prioridad=100,
            empresa_contiene=["mercadona", "carrefour", "lidl", "aldi", "dia", "eroski", "alcampo", "hipercor", "supercor", "caprabo"],
            texto_contiene=["supermercado", "hipermercado"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="alimentacion",
            sub_etiqueta="alimentacion_panaderia",
            prioridad=60,
            empresa_contiene=["panader", "pasteler", "confiter", "fleca", "boheme"],
            texto_contiene=["barra", "hogaza", "boll", "croissant", "napolitana", "empanada"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="alimentacion",
            sub_etiqueta="alimentacion_carniceria",
            prioridad=70,
            empresa_contiene=["carnicer", "charcuter"],
            texto_contiene=["pollo", "ternera", "cerdo", "jamon", "lomo", "embut"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="alimentacion",
            sub_etiqueta="alimentacion_verduleria_fruteria",
            prioridad=65,
            empresa_contiene=["fruites", "verduler", "llegums"],
            texto_contiene=["manzana", "platano", "tomate", "lechuga", "uva", "naranja"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
    ]


    ## hogar_servicios_suscripciones

    reglas += [
        ReglaSubetiqueta(
            macro="hogar_servicios_suscripciones",
            sub_etiqueta="suministros_luz",
            prioridad=100,
            empresa_contiene=["iberdrola", "endesa", "naturgy", "repsol luz", "totalenergies", "holaluz", "podo"],
            texto_contiene=["kwh", "potencia", "contador", "factura electr", "luz", "impuesto electrico"],
            requiere_texto_contiene=True,   # empresa Y texto
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="hogar_servicios_suscripciones",
            sub_etiqueta="suministros_agua",
            prioridad=80,
            empresa_contiene=["canal de isabel", "aqualia", "agbar", "emasesa", "aquona", "cicle de l'aigua", "aigues"],
            texto_contiene=["agua", "contador", "m3 agua", "suministro de agua", "canon aigua"],
            requiere_texto_contiene=False,  # empresa O texto
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="hogar_servicios_suscripciones",
            sub_etiqueta="telecomunicaciones",
            prioridad=75,
            empresa_contiene=["movistar", "vodafone", "orange", "jazztel", "masmovil", "yoigo", "digi", "pepephone"],
            texto_contiene=["fibra", "movil", "roaming"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="hogar_servicios_suscripciones",
            sub_etiqueta="seguros",
            prioridad=70,
            empresa_contiene=["mapfre", "mutua", "allianz", "axa", "generali", "zurich", "linea directa", "ocaso", "santalucia"],
            texto_contiene=["poliza", "seguro", "prima", "renovacion"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="hogar_servicios_suscripciones",
            sub_etiqueta="suscripciones_digitales",
            prioridad=65,
            empresa_contiene=["spotify", "netflix", "hbo", "prime video", "disney", "apple", "google"],
            texto_contiene=["suscripcion", "subscription", "mensualidad"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="hogar_servicios_suscripciones",
            sub_etiqueta="hogar_comunidad_vivienda",
            prioridad=60,
            empresa_contiene=[],
            texto_contiene=["comunidad", "cuota comunidad", "administrador", "finca", "propietarios", "biscaia"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        # GAS: regla normal (empresa y texto)
        ReglaSubetiqueta(
            macro="hogar_servicios_suscripciones",
            sub_etiqueta="suministros_gas",
            prioridad=90,
            empresa_contiene=["naturgy", "nedgia", "repsol gas", "cepsa gas", "gas natural", "podo", "endesa"],
            texto_contiene=["m3", "gas", "termo", "caldera", "ieh"],
            requiere_texto_contiene=True,   # empresa Y texto
            fallback_por_empresa=False,
        ),
        # GAS: fallback por empresa
        ReglaSubetiqueta(
            macro="hogar_servicios_suscripciones",
            sub_etiqueta="suministros_gas",
            prioridad=10,  # baja, se evalúa al final como fallback
            empresa_contiene=["naturgy", "nedgia", "repsol gas", "cepsa gas", "gas natural", "podo", "endesa"],
            texto_contiene=[],
            requiere_texto_contiene=False,
            fallback_por_empresa=True,
        ),
    ]


    ## consumo_personal_hogar

    reglas += [
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="transporte_combustible",
            prioridad=120,
            empresa_contiene=["repsol", "cepsa", "bp", "shell", "galp", "petronor", "bonarea energia"],
            texto_contiene=["diesel", "gasolina", "euros/l", "€/l", "combustible", "gasoleo", "sin plomo"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="transporte_peaje",
            prioridad=110,
            empresa_contiene=[],
            texto_contiene=["peaje", "autopista", "telepeaje", "via-t", "viat"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="transporte_mantenimiento",
            prioridad=105,
            empresa_contiene=["taller", "norauto", "feuvert", "midas", "pneumatics"],
            texto_contiene=["itv", "neumatic", "aceite", "revision", "filtro", "bateria"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="estanco",
            prioridad=100,
            empresa_contiene=["estanco", "elena"],
            texto_contiene=["tabaco", "cigarr", "cajetilla", "mechero", "papel de fumar", "filtros", "filter"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="moda_ropa",
            prioridad=40,
            empresa_contiene=["h&m", "kiabi", "okaidi", "zeeman"],
            texto_contiene=["camis", "pantal", "chaquet", "vestid", "sudadera", "polo", "mitjo", "pij"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="moda_calzado",
            prioridad=45,
            empresa_contiene=["andadas"],
            texto_contiene=["zapat", "bota", "sandalia", "calzado", "suela", "bamba"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="electronica_tecnologia",
            prioridad=70,
            empresa_contiene=["mediamarkt", "fnac", "pccomponentes", "apple", "amazon", "carcamovil", "setecem", "informatica", "web"],
            texto_contiene=["cable", "usb", "hdmi", "auricular", "raton", "teclado", "pantalla", "tv", "tablet", "reloj", "amazfit", "ipad", "altavoz", "bluetooth"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="hogar_brico_menaje_mobiliario",
            prioridad=65,
            empresa_contiene=["leroy merlin", "ikea", "conforama", "bricomart", "obis"],
            texto_contiene=["mueble", "silla", "mesa", "sarten", "cacerola", "taladro", "pintura", "bombilla", "led", "herramienta", "arandela", "tuerca", "tornillo", "cuna"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="peluqueria_estetica",
            prioridad=55,
            empresa_contiene=["peluquer", "perruquer"],
            texto_contiene=["corte", "tinte", "mechas", "barber", "tallar"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="consumo_personal_hogar",
            sub_etiqueta="papeleria",
            prioridad=50,
            empresa_contiene=["papeler", "paper"],
            texto_contiene=["cuaderno", "boligrafo", "folio", "impresion"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
    ]


    ## salud

    reglas += [
        ReglaSubetiqueta(
            macro="salud",
            sub_etiqueta="farmacia",
            prioridad=80,
            empresa_contiene=["farmacia"],
            texto_contiene=["paracetamol", "ibuprofeno", "receta", "medic", "jarabe", "cepillo", "piel", "balsamo", "toallit", "panal", "cambiador", "embarazada"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
        ReglaSubetiqueta(
            macro="salud",
            sub_etiqueta="dentista",
            prioridad=90,
            empresa_contiene=["dent", "clinica dental"],
            texto_contiene=["ortodon", "empaste", "higiene", "implante", "periodontal", "ortopanto"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
    ]

    ## restauracion_ocio
    
    reglas += [
        ReglaSubetiqueta(
            macro="restauracion_ocio",
            sub_etiqueta="restaurante_bar",
            prioridad=80,
            empresa_contiene=["viena", "teikit", "morrita", "carbonic", "just at", "ibericus", "terraza"],
            texto_contiene=["bar", "restaurante", "cafe", "menu", "tapa", "cerveza", "vino", "berenj", "croqueta", "cortado", "muffin"],
            requiere_texto_contiene=False,
            fallback_por_empresa=False,
        ),
    ]

    return reglas


FALLBACKS_SUBETIQUETAS: Dict[str, str] = {
    "alimentacion": "alimentacion_otro",
    "hogar_servicios_suscripciones": "hogar_servicios_otro",
    "consumo_personal_hogar": "consumo_otro",
    "salud": "salud_otro",
    "restauracion_ocio": "ocio_otro",
    "educacion": "educacion_otro",
    "otros": "otros_sin_regla",
}