"""语音识别引擎包 - 基于 SenseVoice-Small 离线模型

包含:
- sensevoice: SenseVoice-Small ONNX 推理引擎（DirectML/CUDA/CPU 自适应）
- models: 模型文件路径与下载配置
"""

from .sensevoice import SenseVoice, pick_providers

__all__ = ["SenseVoice", "pick_providers"]
