"""SenseVoice 模型路径与下载配置

模型文件（3 个）存放于 <base_dir>/models/voice/sensevoice/:
- SenseVoice-Encoder.int8.onnx 或 SenseVoice-Encoder.fp16.onnx
- SenseVoice-CTC.int8.onnx 或 SenseVoice-CTC.fp16.onnx
- tokenizer.bpe.model

来源:
- 自动下载: 官方 GitHub Release (fp16, ~414MB): HaujetZhao/CapsWriter-Offline
- 手动导入: 从网盘下载压缩包后在设置窗口中导入
"""

from pathlib import Path
from typing import Optional

MODEL_DIR_NAME = "sensevoice"
MODEL_ZIP = "Sensevoice-Small-ONNX.zip"

# 自动下载源（按顺序尝试）
MODEL_URLS = [
    "https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Sensevoice-Small-ONNX.zip",
]

# 自动下载失败时的手动下载渠道（提示用户从网盘下载后导入）
MODEL_MANUAL_HINT_URL = "https://1829306915.share.123pan.cn/123pan/xaOZjv-GybQH?pwd=gRP8#"

# 支持的模型压缩包（网盘可选项）
SUPPORTED_MODEL_ZIPS = [
    "Sensevoice-Small-ONNX-fp16.zip",
    "Sensevoice-Small-ONNX-int8.zip",
]

# 模型目录内的目标文件名（支持 fp16 与 int8 两种命名）
ENCODER_PATTERNS = ("SenseVoice-Encoder*.onnx",)
DECODER_PATTERNS = ("SenseVoice-CTC*.onnx",)
TOKENIZER_NAMES = ("tokenizer.bpe.model",)


def model_root() -> Path:
    """获取模型存放根目录: <base_dir>/models/voice"""
    try:
        from config import config

        return Path(config.base_dir) / "models" / "voice"
    except Exception:
        return Path.home() / ".fmcl" / "models" / "voice"


def model_dir() -> Path:
    """获取 SenseVoice 模型目录"""
    return model_root() / MODEL_DIR_NAME


def is_model_ready(model_path: Optional[Path] = None) -> bool:
    """检查模型文件是否完整（可指定目录，默认使用全局模型目录）"""
    d = model_path or model_dir()
    return bool(list(d.glob("SenseVoice-Encoder*.onnx"))) and bool(
        list(d.glob("SenseVoice-CTC*.onnx"))
    ) and (d / "tokenizer.bpe.model").exists()


def model_version() -> str:
    """检测已安装模型的版本标识 (fp16 / int8 / 未知)"""
    d = model_dir()
    for name in ("SenseVoice-Encoder.fp16.onnx", "SenseVoice-Encoder.int8.onnx"):
        if (d / name).exists():
            return name.split(".")[-2]
    return ""
