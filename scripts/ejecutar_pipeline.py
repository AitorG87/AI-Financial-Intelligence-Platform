from pathlib import Path
import sys

ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[1]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

from pipeline.pipeline import ejecutar_orquestador


if __name__ == "__main__":
    ejecutar_orquestador()