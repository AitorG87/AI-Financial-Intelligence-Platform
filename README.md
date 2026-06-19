# TFM - Contabilidad Familiar

## Requisitos
- Python 3.10+
- MySQL disponible y configurado en el entorno

## Estructura del proyecto

- `desarrollo/`: Entorno de desarrollo, notebooks, experimentación y utilidades
- `produccion/`: Entorno autocontenido y ejecutable para producción
- `requirements.txt`: Dependencias globales del proyecto (instalar antes de usar cualquier entorno)

## Estructura esperada de modelos (local)

El proyecto está preparado para funcionar con modelos guardados dentro del propio repositorio:

- Extracción (Qwen adapter LoRA):
    - `produccion/modelos/extracción/adapter/adapter_config.json`
    - `produccion/modelos/extracción/adapter/adapter_model.safetensors`
    - `produccion/modelos/extracción/modelo_base/` (modelo base completo de Qwen)
- Clasificación:
    - `produccion/modelos/clasificacion/clasificacion_xlmr_base_macro_v2_len512_v1/`

## Instalación de dependencias

Desde la raíz del proyecto:

```bash
python -m pip install -r requirements.txt
```

## Arranque y ejecución
1) Instala dependencias (desde la raíz del proyecto):

```bash
python -m pip install -r requirements.txt
```

2) Inicializa la base de datos (esquema, rutinas y eventos):

```bash
python -m produccion.bd.bootstrap_bd
```

3) Ejecuta el pipeline (lanza también la UI de Streamlit):

```bash
python -m produccion.pipeline.pipeline
```

### Ejecución desde Spyder / IPython

Opciones correctas:

1) En consola de sistema (Anaconda Prompt / PowerShell):

```bash
python -m produccion.pipeline.pipeline
```

2) Desde Spyder, ejecuta el archivo lanzador:

```python
runfile('produccion/scripts/ejecutar_pipeline.py')
```

3) Desde una celda IPython, como comando de shell:

```python
!python -m produccion.pipeline.pipeline
```

## Configuración
El proyecto está configurado para ser portable. Por defecto usa rutas relativas desde la raíz del repositorio de cada entorno (`desarrollo` o `produccion`)

Valores recomendados en `.env`:

- `QWEN_MODEL_ID=produccion/modelos\extracción\adapter`
- `QWEN_BASE_MODEL_PATH=produccion/modelos\extracción\modelo_base`
- `QWEN_STRICT_LOCAL=true`

Solo necesitas ajustar credenciales/conectividad de MySQL:

- MYSQL_HOST
- MYSQL_PORT
- MYSQL_DATABASE
- MYSQL_USER
- MYSQL_PASSWORD

Notas sobre extracción autocontenida:
- Con `QWEN_STRICT_LOCAL=true`, el motor de extracción no usa descargas remotas ni cachés externas
- Si falta `produccion/modelos/extracción/modelo_base`, el arranque falla de forma explícita para evitar usar recursos fuera del proyecto

## Notas
- La UI de Streamlit se abre en una ventana nueva al iniciar el pipeline
- Al cerrar el pipeline, también se cierra la UI
- Cada entorno es autocontenido y no requiere modificar rutas base
- Los modelos y datos deben estar en las rutas relativas indicadas en cada entorno