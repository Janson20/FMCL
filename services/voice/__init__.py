"""语音识别引擎包 - 基于 SenseVoice-Small 离线模型

包含:
- sensevoice: SenseVoice-Small ONNX 推理引擎（DirectML/CUDA/CPU 自适应）
- models: 模型文件路径与下载配置

依赖缺失时（如 x86 构建无 onnxruntime/sentencepiece 的 win32 轮子）降级为
不可用状态，FMCL 其余功能不受影响（语音按钮会提示缺少依赖）。
"""

try:
    from .sensevoice import SenseVoice, pick_providers

    __all__ = ["SenseVoice", "pick_providers"]
except ImportError:
    SenseVoice = None  # type: ignore[assignment]
    pick_providers = None  # type: ignore[assignment]
    __all__ = []
