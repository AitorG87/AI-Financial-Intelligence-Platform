import sys
import time
from pathlib import Path

import streamlit as st

# Resolver ruta base antes de cualquier import del proyecto
ruta_app = Path(__file__).resolve()
ruta_raiz = ruta_app.parents[1]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

from utilidades.configuracion import cargar_env_desde_raiz, cargar_config
from utilidades.puente_ui import (
    asegurar_puente,
    escribir_estado,
    leer_estado,
)

from pipeline.modulos.modulo_revision_usuario import ejecutar_aplicacion_revision_usuario


def mostrar_logo() -> None:
    ruta_logo = ruta_app.parent / "logo" / "equilibria - LOGO V20260202_equilibria - COLOR.png"
    if ruta_logo.exists():
        st.image(str(ruta_logo), width=160)


def guardar_archivo_en_originales(cfg, archivo_subido) -> Path:
    """Guarda el archivo subido por el usuario en outputs/originales_subidos y devuelve la ruta final"""
    carpeta_destino = cfg.rutas.originales_subidos
    carpeta_destino.mkdir(parents=True, exist_ok=True)

    nombre_original = archivo_subido.name
    extension = Path(nombre_original).suffix.lower().strip(".")
    if not extension:
        extension = "bin"

    marca_tiempo = time.strftime("%Y%m%d_%H%M%S")
    nombre_destino = f"{marca_tiempo}_{nombre_original}"
    ruta_destino = carpeta_destino / nombre_destino

    ruta_destino.write_bytes(archivo_subido.getbuffer())
    return ruta_destino


def mostrar_pantalla_carga(cfg) -> None:
    st.title("Contabilidad Familiar")
    st.subheader("Carga de documento")

    archivo = st.file_uploader(
        "Selecciona una imagen o PDF",
        type=["png", "jpg", "jpeg", "pdf"],
        accept_multiple_files=False,
    )

    ruta_guardada = st.session_state.get("ruta_guardada_ui")
    if ruta_guardada:
        st.text_input("Ruta de destino", value=ruta_guardada, disabled=True)

    boton_enviar = st.button("Aceptar / Enviar", type="primary")

    if archivo is not None and boton_enviar:
        ruta_destino = guardar_archivo_en_originales(cfg, archivo)
        st.session_state["ruta_guardada_ui"] = str(ruta_destino)

        escribir_estado(
            fase="procesando",
            mensaje="Archivo recibido. Iniciando pipeline...",
            ruta_entrada=str(ruta_destino),
            cfg=cfg,
        )
        estado_post = leer_estado(cfg)
        st.write("DEBUG estado tras escribir:", estado_post)
        st.rerun()



    if archivo is None and boton_enviar:
        st.warning("Selecciona un archivo antes de enviar")


def mostrar_pantalla_procesando(estado: dict) -> None:
    st.title("Contabilidad Familiar")
    st.subheader("Procesando...")

    mensaje = estado.get("mensaje") or "Procesando..."
    with st.spinner("Procesando..."):
        st.write(mensaje)

    time.sleep(1)
    st.rerun()


def mostrar_pantalla_revision(estado: dict) -> None:
    st.title("Contabilidad Familiar")

    id_documento = estado.get("id_documento")
    if isinstance(id_documento, int):
        st.session_state["id_documento_actual"] = id_documento

    ejecutar_aplicacion_revision_usuario()


def mostrar_pantalla_final(estado: dict) -> None:
    st.title("Contabilidad Familiar")
    st.subheader("Proceso finalizado")
    mensaje = estado.get("mensaje") or "Completado"
    st.success(mensaje)

    if st.button("Volver a carga", type="primary"):
        escribir_estado(
            fase="carga",
            mensaje="Sube un archivo en la UI para comenzar.",
            ruta_entrada=None,
            id_documento=None,
            motivo_repetir_revision=None,
            cfg=None,
        )
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Contabilidad Familiar", layout="wide")

    st.markdown(
        """
        <style>
        .stApp { background-color: #ffffff; }
        body, p, span, label, div, input, textarea, select, button { color: #0C4F63 !important; }
        h1, h2, h3, h4, h5, h6 { color: #0C4F63 !important; }
        :root {
            --data-grid-text-color: #0C4F63;
            --data-grid-header-text-color: #0C4F63;
            --data-grid-text-dark: #0C4F63;
            --data-grid-text-light: #0C4F63;
            --data-grid-background-color: #E6F2FF;
            --data-grid-border-color: #0C4F63;
            --data-grid-row-hover-color: #f2fbfb;
            --data-grid-selection-color: #e3f6f6;
        }
        header { background: #ffffff !important; }
        [data-testid="stAppViewContainer"] { background: #ffffff !important; }
        [data-testid="stHeader"] { background: #ffffff !important; }
        [data-testid="stToolbar"] { background: #ffffff !important; }
        [data-testid="stDecoration"] { background: #ffffff !important; }
        .stButton > button {
            background-color: #F28E5D !important;
            color: #ffffff !important;
            border: 1px solid #F28E5D !important;
        }
        .stButton > button:hover {
            background-color: #e48052 !important;
            border-color: #e48052 !important;
        }
        .stButton > button:active {
            background-color: #d67348 !important;
            border-color: #d67348 !important;
        }
        [data-testid="stFileUploader"] section {
            background-color: #4EBBBB !important;
            border-color: #4EBBBB !important;
        }
        [data-testid="stFileUploader"] button {
            background-color: #0C4F63 !important;
            color: #ffffff !important;
            border: 1px solid #0C4F63 !important;
        }
        input, textarea, select {
            background-color: #ffffff !important;
            border: 1px solid #0C4F63 !important;
            color: #0C4F63 !important;
        }
        [data-testid="stDataEditor"],
        [data-testid="stDataEditor"] div,
        [data-testid="stDataEditor"] section {
            background-color: #4EBBBB !important;
            color: #0C4F63 !important;
            border-color: #0C4F63 !important;
        }
        [data-testid="stDataEditor"] [data-baseweb="table"],
        .stDataFrameGlideDataEditor {
            resize: none !important;
        }
        [data-testid="stDataEditor"] [data-baseweb="textarea"] textarea,
        [data-testid="stDataEditor"] [data-baseweb="select"] input {
            background-color: #ffffff !important;
            color: #0C4F63 !important;
            border-color: #0C4F63 !important;
        }
        [data-testid="stDataEditor"] [data-baseweb="input"] div {
            background-color: #ffffff !important;
            color: #0C4F63 !important;
            border-color: #0C4F63 !important;
        }
        [data-testid="stDataEditor"] [data-baseweb="table"] div {
            background-color: #ffffff !important;
            color: #0C4F63 !important;
        }
        [data-testid="stFileUploader"] div,
        [data-testid="stFileUploader"] label,
        [data-testid="stFileUploader"] span,
        [data-testid="stFileUploader"] small {
            color: #ffffff !important;
        }
        [data-testid="stFileUploader"] [data-testid="stFileUploaderFileName"],
        [data-testid="stFileUploader"] [data-testid="stFileUploaderFileSize"],
        [data-testid="stFileUploader"] [data-testid="stFileUploaderFileName"] *,
        [data-testid="stFileUploader"] [data-testid="stFileUploaderFileSize"] * {
            color: #0C4F63 !important;
        }
        [data-testid="stFileUploader"] small {
            color: #0C4F63 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    mostrar_logo()

    cargar_env_desde_raiz()
    cfg = cargar_config()

    asegurar_puente(cfg)


    estado = leer_estado(cfg)
    if estado is None:
        st.info("Esperando al orquestador...")
        time.sleep(1)
        st.rerun()
        return

    fase = estado.get("fase")

    if fase == "carga":
        mostrar_pantalla_carga(cfg)
        if "debug_envio" in st.session_state:
            st.write("DEBUG envio:", st.session_state["debug_envio"])

    elif fase == "procesando":
        mostrar_pantalla_procesando(estado)
    elif fase == "revision":
        mostrar_pantalla_revision(estado)
    elif fase == "final":
        mostrar_pantalla_final(estado)
    else:
        st.error(f"Fase desconocida: {fase!r}")
        st.json(estado)


if __name__ == "__main__":
    main()