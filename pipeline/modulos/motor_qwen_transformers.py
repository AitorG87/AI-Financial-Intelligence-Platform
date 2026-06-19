from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import torch
from PIL import Image
import json
from utilidades.configuracion import leer_env_str, leer_env_int, leer_env_bool
from transformers import AutoProcessor, AutoModelForImageTextToText

try:
    from peft import PeftModel
except Exception:
    PeftModel = None


try:
    from qwen_vl_utils import process_vision_info
except Exception as ex:
    raise RuntimeError(
        "Falta dependencia 'qwen-vl-utils'. Instala: pip install qwen-vl-utils"
    ) from ex



@dataclass(frozen=True)
class ConfigMotorQwen:
    model_id: str
    base_model_path: Optional[str] = None
    strict_local_only: bool = True
    dtype: str = "float16"
    device_map: str = "cuda"

    load_in_4bit: bool = False
    max_new_tokens: int = 768

    # Solo cuando device_map="auto"
    max_memory: Optional[Dict[Union[int, str], str]] = None

    # Tamaño máximo imagen
    target_height: int = 1024
    max_pixels: Optional[int] = None

    # Atenttion
    attn_implementation: str = "sdpa"
    force_sdpa_math: bool = True

    # Seguridad anti-offload
    allow_cpu_offload: bool = False

    # Logs
    debug: bool = False

    @staticmethod
    def desde_entorno() -> "ConfigMotorQwen":
        return ConfigMotorQwen(
            model_id=leer_env_str("QWEN_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct"),
            dtype=leer_env_str("QWEN_DTYPE", "float16"),
            device_map=leer_env_str("QWEN_DEVICE_MAP", "cuda"),
            load_in_4bit=leer_env_bool("QWEN_LOAD_IN_4BIT", False),
            max_new_tokens=leer_env_int("QWEN_MAX_NEW_TOKENS", 768),
            attn_implementation=leer_env_str("QWEN_ATTN_IMPLEMENTATION", "sdpa"),
            force_sdpa_math=leer_env_bool("QWEN_FORCE_SDPA_MATH", True),
            target_height=leer_env_int("QWEN_TARGET_HEIGHT", 1024),
            max_pixels=leer_env_int("QWEN_MAX_PIXELS", 1024 * 1024),
            debug=leer_env_bool("QWEN_DEBUG", False),
        )


class MotorQwenTransformers:
    def __init__(self, config: ConfigMotorQwen):
        self.config = config

        model_id_config = str(self.config.model_id).strip()
        ruta_adapter = Path(model_id_config)
        self.adapter_path: Optional[str] = None
        self.base_model_id: str = model_id_config
        strict_local = bool(self.config.strict_local_only)

        def resolver_local(valor: str, base_dir: Optional[Path] = None) -> Optional[str]:
            txt = str(valor or "").strip()
            if not txt:
                return None
            ruta = Path(txt)
            if ruta.is_absolute():
                return str(ruta.resolve()) if ruta.exists() else None
            if base_dir is not None:
                candidata = (base_dir / ruta).resolve()
                if candidata.exists():
                    return str(candidata)
            candidata = ruta.resolve()
            if candidata.exists():
                return str(candidata)
            return None

        if ruta_adapter.exists() and (ruta_adapter / "adapter_config.json").exists():
            self.adapter_path = str(ruta_adapter)

            base_local_cfg = resolver_local(str(self.config.base_model_path or ""))
            base_local_adapter = None

            try:
                cfg_adapter = json.loads((ruta_adapter / "adapter_config.json").read_text(encoding="utf-8"))
                base = str(cfg_adapter.get("base_model_name_or_path") or "").strip()
                if base and not base_local_cfg:
                    base_local_adapter = resolver_local(base, ruta_adapter.parent)
                    if base_local_adapter:
                        self.base_model_id = base_local_adapter
                    elif not strict_local:
                        self.base_model_id = base
            except Exception:
                pass

            if base_local_cfg:
                self.base_model_id = base_local_cfg
            elif strict_local and not base_local_adapter:
                raise RuntimeError(
                    "Se detectó un adapter LoRA local, pero falta el modelo base local dentro del proyecto"
                    "Define QWEN_BASE_MODEL_PATH a una carpeta local"
                )

        elif strict_local and not ruta_adapter.exists():
            raise RuntimeError(
                f"QWEN_MODEL_ID debe apuntar a una ruta local existente dentro del proyecto. Valor actual: {model_id_config}"
            )

        if strict_local:
            base_local = resolver_local(self.base_model_id)
            if base_local is None:
                raise RuntimeError(
                    "Modo local estricto activado (QWEN_STRICT_LOCAL=true), pero no se encontró el modelo base local de Qwen"
                    "Define QWEN_BASE_MODEL_PATH a una carpeta local"
                )
            self.base_model_id = base_local

        # 1) Forzar device_map = cuda
        device_map = str(self.config.device_map or "cuda").strip().lower()
        if device_map == "auto":
            device_map = "cuda"
        if device_map not in {"cuda", "cpu"}:
            device_map = "cuda"
        self.device_map = device_map

        # 2) dtype
        dtype_str = str(self.config.dtype or "float16").strip().lower()
        dtype = getattr(torch, dtype_str, torch.float16)
        self.dtype = dtype

        # 3) Processor + Modelo
        processor_source = self.adapter_path or self.base_model_id
        kwargs_local = {"local_files_only": True} if strict_local else {}

        try:
            self.procesador = AutoProcessor.from_pretrained(processor_source, **kwargs_local)
        except Exception:
            self.procesador = AutoProcessor.from_pretrained(self.base_model_id, **kwargs_local)

        argumentos_modelo: Dict[str, Any] = {
            "dtype": self.dtype,
            "device_map": self.device_map,
        }

        # 4) Atenttion
        if self.config.attn_implementation:
            argumentos_modelo["attn_implementation"] = self.config.attn_implementation

        if self.config.load_in_4bit:
            argumentos_modelo["load_in_4bit"] = True

        if strict_local:
            argumentos_modelo["local_files_only"] = True

        modelo_base = AutoModelForImageTextToText.from_pretrained(self.base_model_id, **argumentos_modelo)

        if self.adapter_path:
            if PeftModel is None:
                raise RuntimeError(
                    "Se detectó un adapter LoRA local, pero falta dependencia 'peft'. "
                    "Instala: pip install peft"
                )
            self.modelo = PeftModel.from_pretrained(modelo_base, self.adapter_path)
        else:
            self.modelo = modelo_base

        if self.config.debug:
            print(f"[MotorQwen] model_id={self.config.model_id}")
            print(f"[MotorQwen] base_model_id={self.base_model_id}")
            print(f"[MotorQwen] adapter_path={self.adapter_path}")
            print(f"[MotorQwen] device_map={self.device_map}")
            print(f"[MotorQwen] dtype={dtype_str}")

    def inferir(
        self,
        imagen: Image.Image,
        texto_prompt: str,
        max_new_tokens: Optional[int] = None,
    ) -> str:
        max_new_tokens_final = int(max_new_tokens) if max_new_tokens is not None else int(self.config.max_new_tokens)

        try:
            mensajes = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": imagen},
                        {"type": "text", "text": texto_prompt},
                    ],
                }
            ]
            texto = self.procesador.apply_chat_template(
                mensajes,
                tokenize=False,
                add_generation_prompt=True,
            )
            image_inputs, video_inputs = process_vision_info(mensajes)
            entradas = self.procesador(
                text=[texto],
                images=image_inputs,
                videos=video_inputs,
                return_tensors="pt",
            )
        except Exception:
            entradas = self.procesador(
                text=texto_prompt,
                images=imagen,
                return_tensors="pt",
            )

        if self.device_map == "cuda":
            entradas = {k: v.to("cuda") for k, v in entradas.items()}

        with torch.no_grad():
            salida_ids = self.modelo.generate(
                **entradas,
                max_new_tokens=max_new_tokens_final,
            )

        input_len = entradas["input_ids"].shape[-1]
        salida_recortada = salida_ids[:, input_len:]
        texto = self.procesador.batch_decode(salida_recortada, skip_special_tokens=True)
        return texto[0] if texto else ""

    def extraer_mejor_json_balanceado(self, texto: str) -> Optional[str]:
        """Extrae el mejor bloque JSON balanceado de un texto"""
        candidatos = self.extraer_jsons_balanceados(texto)
        if not candidatos:
            return None
        # El más largo tien más campos válidos
        candidatos.sort(key=len, reverse=True)
        return candidatos[0]

    def extraer_jsons_balanceados(self, texto: str) -> List[str]:
        """Devuelve todos los candidatos JSONencontrados"""
        resultados: List[str] = []
        pila = 0
        inicio = None

        for i, ch in enumerate(texto):
            if ch == "{":
                if pila == 0:
                    inicio = i
                pila += 1
            elif ch == "}":
                if pila > 0:
                    pila -= 1
                    if pila == 0 and inicio is not None:
                        candidato = texto[inicio : i + 1].strip()
                        resultados.append(candidato)
                        inicio = None
        return resultados

    def parsear_json(self, texto_json: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(texto_json)
        except Exception:
            return None