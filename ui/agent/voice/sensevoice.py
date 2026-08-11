"""SenseVoice-Small 离线语音识别引擎（精简移植自 CapsWriter-Offline）

基于 ONNX Runtime 推理，自动选择 DirectML (Windows GPU) / CUDA / CPU 后端。
仅保留录音识别所需的最小功能集：特征提取、编码器、CTC 解码、分段拼接。
不含热词雷达与文件转录。

依赖: numpy, onnxruntime (或 onnxruntime-directml), sentencepiece
"""

import json
from pathlib import Path
from typing import List, Optional

import numpy as np
import onnxruntime as ort
import sentencepiece as spm


def pick_providers() -> List[str]:
    """按可用性选择推理后端: DirectML (Windows GPU) > CUDA > CPU"""
    available = ort.get_available_providers()
    providers = ["CPUExecutionProvider"]
    if "DmlExecutionProvider" in available:
        providers.insert(0, "DmlExecutionProvider")
    elif "CUDAExecutionProvider" in available:
        providers.insert(0, "CUDAExecutionProvider")
    return providers


def _build_session(model_path: str, providers: Optional[List[str]] = None) -> ort.InferenceSession:
    """创建 ONNX 会话（统一会话配置）"""
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(model_path, providers=providers or pick_providers(), sess_options=opts)


class NumPyMelExtractor:
    """纯 NumPy 实现的 Fbank 特征提取（对齐 torchaudio / funasr）"""

    def __init__(self, sr=16000, n_fft=400, n_mels=80, f_min=20, f_max=8000):
        self.sr, self.n_fft, self.n_mels = sr, n_fft, n_mels
        hz_to_mel = lambda f: 2595.0 * np.log10(1.0 + (f / 700.0))
        mel_to_hz = lambda m: 700.0 * (10.0 ** (m / 2595.0) - 1.0)
        all_freqs = np.linspace(0, sr // 2, n_fft // 2 + 1)
        m_pts = np.linspace(hz_to_mel(f_min), hz_to_mel(f_max), n_mels + 2)
        f_pts = mel_to_hz(m_pts)
        f_diff = np.diff(f_pts)
        slopes = f_pts[np.newaxis, :] - all_freqs[:, np.newaxis]
        fb = np.maximum(0, np.minimum((-1.0 * slopes[:, :-2]) / f_diff[:-1], slopes[:, 2:] / f_diff[1:]))
        self.filters = fb.astype(np.float32)
        self.hop_length = 160
        self.window = (0.54 - 0.46 * np.cos(2.0 * np.pi * np.arange(self.n_fft) / self.n_fft)).astype(np.float32)
        self.pre_emphasis = 0.97

    def extract(self, audio: np.ndarray) -> np.ndarray:
        """提取 LFR 特征 (T, 560)"""
        audio = audio - np.mean(audio)
        audio_pe = np.empty_like(audio)
        audio_pe[0] = audio[0]
        audio_pe[1:] = audio[1:] - self.pre_emphasis * audio[:-1]
        half = self.n_fft // 2
        y = np.pad(audio_pe, (half, half), mode="constant")
        num_frames = 1 + (len(y) - self.n_fft) // self.hop_length
        frames = np.lib.stride_tricks.as_strided(
            y, shape=(num_frames, self.n_fft), strides=(y.strides[0] * self.hop_length, y.strides[0])
        )
        stft = np.fft.rfft(frames * self.window, n=self.n_fft, axis=1)
        mel_spec = np.dot(np.abs(stft) ** 2, self.filters)
        log_mel = np.log(mel_spec + 1e-7)
        T_mel = log_mel.shape[0]
        T_lfr = (T_mel + 5) // 6
        left_pad = np.repeat(log_mel[:1, :], 3, axis=0)
        right_pad_len = (T_lfr * 6 + 7) - T_mel
        right_pad = np.repeat(log_mel[-1:, :], right_pad_len, axis=0)
        padded = np.concatenate([left_pad, log_mel, right_pad], axis=0)
        lfr_feat = np.empty((T_lfr, 560), dtype=np.float32)
        for i in range(7):
            lfr_feat[:, i * 80 : (i + 1) * 80] = padded[i : i + T_lfr * 6 : 6, :]
        return lfr_feat


class SenseVoiceEncoder:
    """SenseVoice 编码器（含 Prompt 构造）"""

    def __init__(self, encoder_path: str, providers: Optional[List[str]] = None):
        self.session = _build_session(encoder_path, providers)
        meta = self.session.get_modelmeta().custom_metadata_map
        self.lid_dict = json.loads(meta.get("lid_dict", "{}"))
        self.itn_dict = json.loads(meta.get("textnorm_dict", "{}"))
        in_type = self.session.get_inputs()[0].type
        self.input_dtype = np.float16 if "float16" in in_type else np.float32

    def construct_prompt(self, lid: str = "auto", itn: bool = True) -> np.ndarray:
        lid_idx = self.lid_dict.get(lid, 0)
        itn_idx = self.itn_dict.get("withitn" if itn else "woitn", 14)
        return np.array([lid_idx, 1, 2, itn_idx], dtype=np.int64)[np.newaxis, :]

    def forward(self, lfr_feat: np.ndarray, lid: str = "auto", itn: bool = True) -> np.ndarray:
        T_valid = lfr_feat.shape[0]
        mask = np.ones((1, T_valid), dtype=self.input_dtype)
        enc_out = self.session.run(
            None,
            {
                "speech_feat": lfr_feat[np.newaxis, ...].astype(self.input_dtype),
                "mask": mask,
                "prompt_ids": self.construct_prompt(lid=lid, itn=itn),
            },
        )[0]
        return enc_out


class SenseVoiceDecoder:
    """SenseVoice CTC 解码器（Top-K 输出 + Greedy 解码）"""

    BLANK_ID = 0
    PROMPT_LEN = 4
    FRAME_SEC = 0.060

    def __init__(self, decoder_path: str, providers: Optional[List[str]] = None):
        self.session = _build_session(decoder_path, providers)
        in_type = self.session.get_inputs()[0].type
        self.input_dtype = np.float16 if "float16" in in_type else np.float32

    def decode_all(self, enc_out: np.ndarray, sp: spm.SentencePieceProcessor, T_valid: int) -> List[dict]:
        """CTC 贪心解码，返回 [{'text': str, 'start': float}, ...]"""
        if enc_out.dtype != self.input_dtype:
            enc_out = enc_out.astype(self.input_dtype)
        _, topk_indices = self.session.run(None, {"enc_out": enc_out})
        start = self.PROMPT_LEN
        end = T_valid + self.PROMPT_LEN
        greedy_ids = topk_indices[0, start:end, 0]

        collapsed = []
        if len(greedy_ids) > 0:
            curr_id = int(greedy_ids[0])
            start_frame = 0
            for i in range(1, len(greedy_ids)):
                if int(greedy_ids[i]) != curr_id:
                    collapsed.append((curr_id, start_frame))
                    curr_id = int(greedy_ids[i])
                    start_frame = i
            collapsed.append((curr_id, start_frame))

        results = []
        for tid, fidx in collapsed:
            if tid == self.BLANK_ID:
                continue
            char = sp.id_to_piece(tid).replace("\u2581", " ")
            if char.strip() or char == " ":
                results.append({"text": char, "start": round(fidx * self.FRAME_SEC, 3)})
        return results


class SenseVoice:
    """SenseVoice-Small 识别引擎（一次性整段识别，自动分段拼接）"""

    CHUNK_SEC = 40
    OVERLAP_SEC = 5

    def __init__(self, model_dir: str, providers: Optional[List[str]] = None, itn: bool = True):
        model_dir = Path(model_dir)
        self.encoder = SenseVoiceEncoder(str(model_dir / "SenseVoice-Encoder.int8.onnx"), providers)
        self.decoder = SenseVoiceDecoder(str(model_dir / "SenseVoice-CTC.int8.onnx"), providers)
        self.frontend = NumPyMelExtractor()
        self.sp = spm.SentencePieceProcessor()
        with open(model_dir / "tokenizer.bpe.model", "rb") as f:
            self.sp.load_from_serialized_proto(f.read())
        self.itn = itn

    def recognize(self, audio: np.ndarray, lid: str = "auto", itn: Optional[bool] = None) -> str:
        """识别 16kHz float32 单声道音频，返回识别文本"""
        if itn is None:
            itn = self.itn
        lfr_feat = self.frontend.extract(audio)
        chunk_frames = int(self.CHUNK_SEC * 100 / 6)
        overlap_frames = int(self.OVERLAP_SEC * 100 / 6)
        stride = max(1, chunk_frames - overlap_frames)

        all_chunks = []
        for start in range(0, len(lfr_feat), stride):
            end = min(start + chunk_frames, len(lfr_feat))
            offset_sec = start * 6 * 0.01
            res = self._recognize_lfr(lfr_feat[start:end], lid=lid, itn=itn, offset_sec=offset_sec)
            all_chunks.append(res)
            if end == len(lfr_feat):
                break
        return self._merge_results(all_chunks, self.OVERLAP_SEC)

    def _recognize_lfr(self, chunk_lfr: np.ndarray, lid: str, itn: bool, offset_sec: float) -> List[dict]:
        enc_out = self.encoder.forward(chunk_lfr, lid=lid, itn=itn)
        greedy = self.decoder.decode_all(enc_out, self.sp, T_valid=chunk_lfr.shape[0])
        return [{"text": r["text"], "start": round(r["start"] + offset_sec, 3)} for r in greedy]

    def _merge_results(self, results_list: List[List[dict]], overlap_sec: float) -> str:
        """基于 SequenceMatcher 的分段拼接，去重重叠文本"""
        if not results_list:
            return ""
        if len(results_list) == 1:
            return "".join(r["text"] for r in results_list[0])

        import difflib

        merged = list(results_list[0])
        for i in range(1, len(results_list)):
            new_res = results_list[i]
            if not new_res:
                continue
            if not merged:
                merged.extend(new_res)
                continue
            overlap_window = overlap_sec * 2.0
            last_time = merged[-1]["start"]
            prev_indices = [idx for idx, r in enumerate(merged) if r["start"] >= last_time - overlap_window]
            new_indices = [idx for idx, r in enumerate(new_res) if r["start"] <= new_res[0]["start"] + overlap_window]
            prev_text = "".join(merged[idx]["text"] for idx in prev_indices)
            new_text = "".join(new_res[idx]["text"] for idx in new_indices)
            sm = difflib.SequenceMatcher(None, prev_text, new_text)
            match = sm.find_longest_match(0, len(prev_text), 0, len(new_text))
            if match.size >= 1:
                char_count = 0
                prev_cut = prev_indices[-1] + 1
                for idx in prev_indices:
                    char_count += len(merged[idx]["text"])
                    if char_count > match.a + match.size // 2:
                        prev_cut = idx
                        break
                char_count = 0
                new_start = 0
                for idx in new_indices:
                    char_count += len(new_res[idx]["text"])
                    if char_count > match.b + match.size // 2:
                        new_start = idx + 1
                        break
                merged = merged[:prev_cut] + new_res[new_start:]
            else:
                last_t = merged[-1]["start"]
                new_start = len(new_res)
                for idx, r in enumerate(new_res):
                    if r["start"] > last_t:
                        new_start = idx
                        break
                merged.extend(new_res[new_start:])
        return "".join(r["text"] for r in merged)
