"""GDK 版游戏包（MSIXVC/XVD 容器）解包

移植自 BedrockLauncher.Core (MIT) 的 MsiXVDStream / MsiXVDDecoder：
- 解析 XVD 文件头（卷属性、页数、用户数据/XVC 数据偏移）
- 读取用户数据区（SegmentMetadata.bin 记录文件清单）
- 解析 XVC 区域表，按 0x1000 页提取各段文件
- 若卷未禁用加密，则使用 XTS-AES（AES-ECB + GF(2^128) 乘法）逐页解密
"""

import re
import struct
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from logzero import logger

PAGE_SIZE = 0x1000
XVD_HEADER_INCL_SIGNATURE_SIZE = 0x3000
HASH_ENTRIES_IN_PAGE = 0xAA
RESILIENT_MULTIPLIER = 2
MAGIC_RE = re.compile(rb"MSXVD|XVD\0|MSXC\0")


class XvdParseError(RuntimeError):
    """XVD 容器解析错误"""


# ─── 二进制结构解析 ─────────────────────────────────────────────


def _read_struct(data: bytes, fmt: str, offset: int = 0, size: Optional[int] = None):
    """按指定格式解析二进制结构"""
    fmt_size = struct.calcsize(fmt)
    if size is None:
        size = fmt_size
    if offset + size > len(data):
        raise XvdParseError(f"结构解析越界: offset={offset}, size={size}, data_len={len(data)}")
    return struct.unpack_from(fmt, data, offset)


class XvdHeader:
    """MsiXVDHeader 头（对齐 C# StructLayout Sequential, Pack=1）"""

    # fmt: (offset, size)
    _MAGIC_OFFSET = 0x200
    _SIGNATURE_SIZE = 0x200
    _MAGIC_SIZE = 8
    _VOLUMES_OFFSET = _MAGIC_OFFSET + _MAGIC_SIZE
    _FORMAT_VERSION_OFFSET = _VOLUMES_OFFSET + 4
    _FILETIME_CREATED_OFFSET = _FORMAT_VERSION_OFFSET + 4
    _DRIVE_SIZE_OFFSET = _FILETIME_CREATED_OFFSET + 8
    _VD_UID_OFFSET = _DRIVE_SIZE_OFFSET + 8
    _UD_UID_OFFSET = _VD_UID_OFFSET + 0x10
    _KIND_OFFSET = 0x280
    _CATEGORY_OFFSET = 0x284
    _EMBEDDED_XVD_LENGTH_OFFSET = 0x288
    _USER_DATA_LENGTH_OFFSET = 0x28C
    _XVC_DATA_LENGTH_OFFSET = 0x290
    _DYNAMIC_HEADER_LENGTH_OFFSET = 0x294
    _BLOCK_SIZE_OFFSET = 0x298
    _MUTABLE_PAGE_COUNT_OFFSET = 0x460
    _RESILIENT_DATA_OFFSET_OFFSET = 0xFE4

    VOLUME_READONLY = 1
    VOLUME_ENCRYPTION_DISABLED = 2
    VOLUME_DATA_INTEGRITY_DISABLED = 4
    VOLUME_RESILIENCY_ENABLED = 0x10

    def __init__(self, data: bytes):
        if len(data) < 0x1000:
            raise XvdParseError("文件头数据不足")
        self.magic = data[self._MAGIC_OFFSET : self._MAGIC_OFFSET + self._MAGIC_SIZE]
        (self.volumes,) = struct.unpack_from("<I", data, self._VOLUMES_OFFSET)
        (self.format_version,) = struct.unpack_from("<I", data, self._FORMAT_VERSION_OFFSET)
        (self.drive_size,) = struct.unpack_from("<Q", data, self._DRIVE_SIZE_OFFSET)
        self.vd_uid = data[self._VD_UID_OFFSET : self._VD_UID_OFFSET + 0x10]
        (self.kind,) = struct.unpack_from("<I", data, self._KIND_OFFSET)
        (self.embedded_xvd_length,) = struct.unpack_from("<I", data, self._EMBEDDED_XVD_LENGTH_OFFSET)
        (self.user_data_length,) = struct.unpack_from("<I", data, self._USER_DATA_LENGTH_OFFSET)
        (self.xvc_data_length,) = struct.unpack_from("<I", data, self._XVC_DATA_LENGTH_OFFSET)
        (self.dynamic_header_length,) = struct.unpack_from("<I", data, self._DYNAMIC_HEADER_LENGTH_OFFSET)
        (self.mutable_data_page_count,) = struct.unpack_from("<B", data, self._MUTABLE_PAGE_COUNT_OFFSET)
        (self.resilient_data_offset,) = struct.unpack_from("<Q", data, self._RESILIENT_DATA_OFFSET_OFFSET)

    @property
    def is_encrypted(self) -> bool:
        return not (self.volumes & self.VOLUME_ENCRYPTION_DISABLED)

    @property
    def data_integrity(self) -> bool:
        return not (self.volumes & self.VOLUME_DATA_INTEGRITY_DISABLED)

    @property
    def resiliency(self) -> bool:
        return bool(self.volumes & self.VOLUME_RESILIENCY_ENABLED)

    @property
    def mutable_data_length(self) -> int:
        return self.mutable_data_page_count * PAGE_SIZE

    @property
    def user_data_page_count(self) -> int:
        return _bytes_to_pages(self.user_data_length)

    @property
    def xvc_info_page_count(self) -> int:
        return _bytes_to_pages(self.xvc_data_length)

    @property
    def embedded_xvd_page_count(self) -> int:
        return _bytes_to_pages(self.embedded_xvd_length)

    @property
    def dynamic_header_page_count(self) -> int:
        return _bytes_to_pages(self.dynamic_header_length)

    @property
    def drive_page_count(self) -> int:
        return _bytes_to_pages(self.drive_size)

    @property
    def number_of_hashed_pages(self) -> int:
        return self.drive_page_count + self.user_data_page_count + self.xvc_info_page_count + self.dynamic_header_page_count

    @property
    def number_of_metadata_pages(self) -> int:
        return self.user_data_page_count + self.xvc_info_page_count + self.dynamic_header_page_count


def _bytes_to_pages(size: int) -> int:
    return (size + PAGE_SIZE - 1) // PAGE_SIZE


def _page_to_offset(pages: int) -> int:
    return pages * PAGE_SIZE


def _get_page_offset(value: int) -> int:
    return value // PAGE_SIZE


def _pow_aa(level: int) -> int:
    return 0xAA**level


def compute_hash_block_index(
    image_type: int,
    hash_tree_depth: int,
    total_hashed_pages: int,
    data_block_index: int,
    current_hash_level: int,
    is_resilient: bool = False,
) -> int:
    """计算数据块对应的哈希块索引（对齐 C# Extensions.ComputeHashBlockIndexForDataBlock）"""
    if image_type > 1 or current_hash_level > 3:
        return 0xFFFF
    if current_hash_level == 0:
        entry_index = data_block_index % 0xAA
    else:
        entry_index = (data_block_index // _pow_aa(current_hash_level)) % 0xAA
    if current_hash_level == 3:
        return 0
    hash_block_index = data_block_index // _pow_aa(current_hash_level + 1)
    hash_tree_depth -= current_hash_level + 1
    if current_hash_level == 0 and hash_tree_depth > 0:
        hash_block_index += (total_hashed_pages + _pow_aa(2) - 1) // _pow_aa(2)
        hash_tree_depth -= 1
    if (current_hash_level in (0, 1)) and hash_tree_depth > 0:
        hash_block_index += (total_hashed_pages + _pow_aa(3) - 1) // _pow_aa(3)
        hash_tree_depth -= 1
    if hash_tree_depth > 0:
        hash_block_index += (total_hashed_pages + _pow_aa(4) - 1) // _pow_aa(4)
    if is_resilient:
        hash_block_index *= 2
    return hash_block_index


def calculate_number_hash_pages(hashed_pages_count: int, resilient: bool) -> Tuple[int, int]:
    """计算哈希树页数与层数

    标准 XVD 哈希树：L0 = ceil(N/0xAA)，每层哈希上一层页数（ceil(prev/0xAA)），
    直到 1 页为止，最后再补 1 页顶层哈希（实测 mcappx GDK 包比
    BedrockLauncher.Core 的算法多这 1 页）。
    """
    hash_tree_page_count = (hashed_pages_count + HASH_ENTRIES_IN_PAGE - 1) // HASH_ENTRIES_IN_PAGE
    hash_tree_levels = 1
    current = hash_tree_page_count
    while current > 1:
        current = (current + HASH_ENTRIES_IN_PAGE - 1) // HASH_ENTRIES_IN_PAGE
        hash_tree_page_count += current
        hash_tree_levels += 1
    if hash_tree_page_count > 1:
        hash_tree_page_count += 1  # 顶层哈希页
        hash_tree_levels += 1
    if resilient:
        hash_tree_page_count *= 2
    return hash_tree_page_count, hash_tree_levels


# ─── 用户数据区（SegmentMetadata.bin）─────────────────────────


class SegmentMetadata:
    """SegmentMetadata.bin 解析结果：段清单

    注意：真实包（mcappx 重新打包）的 SegmentMetadata.bin 开头有 16 字节
    文本前缀（如 b"6100.1897\" } }{}"），SegmentMetadataHeader 实际从
    偏移 0x10 开始；解析时自动回退。
    """

    _HEADER = "<IIIII I16x 60x"  # Magic, Version0, Version1, HeaderLength, SegmentCount, FilePathsLength + 0x10 PDUID + 0x3c Unknown
    _ENTRY = "<HHIQ"  # Flags(u16), PathLength(u16), PathOffset(u32), FileSize(u64)

    def __init__(self, raw: bytes):
        if len(raw) < 80:
            raise XvdParseError("SegmentMetadata.bin 数据过短")
        header_base = 0
        header_length, segment_count, file_paths_length = self._parse_header(raw, 0)
        # 头部合理性校验：不合理的头部（mcappx 包带 0x10 文本前缀）回退到偏移 0x10
        if not (0x40 <= header_length < len(raw) and 0 < segment_count < 1000000):
            header_base = 0x10
            header_length, segment_count, file_paths_length = self._parse_header(raw, 0x10)
        self.segment_count = segment_count
        self.header_length = header_length
        self.file_paths_length = file_paths_length
        entries_offset = header_base + header_length
        entries = []
        for i in range(segment_count):
            try:
                flags, path_length, path_offset, file_size = struct.unpack_from(
                    self._ENTRY, raw, entries_offset + i * 16
                )
            except struct.error as e:
                raise XvdParseError(f"SegmentMetadata 段条目解析失败: {e}") from e
            entries.append((flags, path_length, path_offset, file_size))
        self.entries: List[Tuple[int, int, int, int]] = entries
        self.paths: List[str] = []
        paths_start = header_base + self.header_length + segment_count * 0x10
        for _, path_length, path_offset, _ in entries:
            start = paths_start + path_offset
            end = start + path_length * 2
            if end > len(raw):
                raise XvdParseError(f"段路径越界: offset={path_offset}, length={path_length}")
            path_bytes = raw[start:end]
            self.paths.append(path_bytes.decode("utf-16-le", errors="replace"))

    @staticmethod
    def _parse_header(raw: bytes, base: int) -> Tuple[int, int, int]:
        if base + 24 > len(raw):
            raise XvdParseError("SegmentMetadata 头越界")
        try:
            (_magic, _v0, _v1, header_length, segment_count, file_paths_length) = struct.unpack_from(
                SegmentMetadata._HEADER, raw, base
            )
        except struct.error as e:
            raise XvdParseError(f"SegmentMetadata 头解析失败: {e}") from e
        return header_length, segment_count, file_paths_length


class SegmentIndex:
    """文件路径与长度的映射"""

    def __init__(self, metadata: SegmentMetadata):
        self.files: List[Tuple[str, int]] = []
        for entry, path in zip(metadata.entries, metadata.paths):
            self.files.append((path, entry[3]))


# ─── XVC 区域表 ───────────────────────────────────────────────


class XvcInfo:
    """XvcInfo：XVC 数据区结构（C# struct 布局, size=0xDA8, regions 起始于 0xDA8）"""

    _REGION_HEADER_SIZE = 0x80
    _UPDATE_SEGMENT_SIZE = 0x10
    _VERSION_OFFSET = 0xD10
    _REGION_COUNT_OFFSET = 0xD14
    _UPDATE_SEGMENT_COUNT_OFFSET = 0xD3C
    _REGION_SPECIFIER_COUNT_OFFSET = 0xD50
    _REGIONS_BASE = 0xDA8

    def __init__(self, raw: bytes):
        if len(raw) < self._REGIONS_BASE + self._REGION_HEADER_SIZE:
            raise XvdParseError("XVC 数据区过短")
        (self.version,) = struct.unpack_from("<I", raw, self._VERSION_OFFSET)
        (self.region_count,) = struct.unpack_from("<I", raw, self._REGION_COUNT_OFFSET)
        (self.update_segment_count,) = struct.unpack_from("<I", raw, self._UPDATE_SEGMENT_COUNT_OFFSET)
        (self.region_specifier_count,) = struct.unpack_from("<I", raw, self._REGION_SPECIFIER_COUNT_OFFSET)
        self.regions: List[Dict[str, int]] = []
        for i in range(self.region_count):
            off = self._REGIONS_BASE + i * self._REGION_HEADER_SIZE
            region_id, key_id, _padding6, flags, first_segment_index = struct.unpack_from("<IHHII", raw, off)
            offset, length, _hash, _u68, _u70, _u78 = struct.unpack_from("<QQQQQQ", raw, off + 0x50)
            self.regions.append(
                {
                    "id": region_id,
                    "key_id": key_id,
                    "flags": flags,
                    "first_segment_index": first_segment_index,
                    "offset": offset,
                    "length": length,
                }
            )
        self.update_segments: List[int] = []
        update_base = self._REGIONS_BASE + self.region_count * self._REGION_HEADER_SIZE
        for i in range(self.update_segment_count):
            (page_num, _hash) = struct.unpack_from("<IQ", raw, update_base + i * self._UPDATE_SEGMENT_SIZE)
            self.update_segments.append(page_num)


# ─── XTS-AES 解密（AES-ECB 模式）──────────────────────────────


def _gf128_mul(iv: bytearray) -> bytearray:
    """GF(2^128) 域乘法（对齐 C# Gf128MulSoftware）"""
    carry_bit127 = (iv[15] >> 7) & 1
    carry_bit63 = (iv[7] >> 7) & 1
    for i in range(7, 0, -1):
        iv[i] = ((iv[i] << 1) | (iv[i - 1] >> 7)) & 0xFF
    iv[0] = (iv[0] << 1) & 0xFF
    for i in range(15, 8, -1):
        iv[i] = ((iv[i] << 1) | (iv[i - 1] >> 7)) & 0xFF
    iv[8] = (iv[8] << 1) & 0xFF
    if carry_bit63:
        iv[8] ^= 0x01
    if carry_bit127:
        iv[0] ^= 0x87
    return iv


class XtsAesDecryptor:
    """XTS-AES 解密器（128 位密钥）

    使用 cryptography 的 modes.XTS 整页一次性解密（OpenSSL C 实现），
    相比逐块 AES-ECB + GF(2^128) 乘法约快 100 倍。
    """

    def __init__(self, d_key: bytes, t_key: bytes):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        if len(d_key) < 16 or len(t_key) < 16:
            raise XvdParseError("XTS-AES 密钥长度不足")
        # modes.XTS 密钥 = data_key(16) + tweak_key(16)
        self._key = d_key[:16] + t_key[:16]

    def decrypt_page(self, page: bytearray, tweak_iv: bytes) -> bytearray:
        """解密一个 0x1000 页（XTS 数据单元 = 一页，tweak = tweak_iv）"""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        cipher = Cipher(algorithms.AES(self._key), modes.XTS(bytes(tweak_iv[:16])))
        decryptor = cipher.decryptor()
        return decryptor.update(page) + decryptor.finalize()


# ─── XVD 提取器 ──────────────────────────────────────────────


class XvdExtractor:
    """MSIXVC/XVD 容器提取器（对齐 BedrockLauncher.Core 的 MsiXVDStream）"""

    PAGE_CACHE_SIZE = 0x100000  # 0x100 页
    HASH_ENTRY_LENGTH_ENCRYPTED = 0x14
    HASH_ENTRY_LENGTH_PLAIN = 0x18

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.header: Optional[XvdHeader] = None
        self.segments: Optional[SegmentIndex] = None
        self.xvc_info: Optional[XvcInfo] = None
        self._hash_tree_page_offset = 0
        self._xvd_user_data_offset = 0
        self._hash_tree_levels = 0
        self._hash_tree_page_count = 0

    def parse(self) -> "XvdExtractor":
        with open(self.file_path, "rb") as f:
            header_data = f.read(PAGE_SIZE)
            self.header = XvdHeader(header_data)

            mutable_data_offset = _page_to_offset(self.header.embedded_xvd_page_count) + XVD_HEADER_INCL_SIGNATURE_SIZE
            self._hash_tree_page_offset = self.header.mutable_data_length + mutable_data_offset
            self._hash_tree_page_count, self._hash_tree_levels = calculate_number_hash_pages(
                self.header.number_of_hashed_pages, self.header.resiliency
            )
            self._xvd_user_data_offset = (
                (_page_to_offset(self._hash_tree_page_count) if self.header.data_integrity else 0)
                + self._hash_tree_page_offset
            )

            user_data = self._read_user_data(f)
            if "SegmentMetadata.bin" in user_data:
                metadata = SegmentMetadata(user_data["SegmentMetadata.bin"])
                self.segments = SegmentIndex(metadata)
            self.xvc_info = self._read_xvc_info(f)
        return self

    def _read_user_data(self, f) -> Dict[str, bytes]:
        """读取用户数据区（PackageFiles 类型时包含 SegmentMetadata.bin 等小文件）"""
        header = self.header
        f.seek(self._xvd_user_data_offset)
        buffer = f.read(header.user_data_length)
        if len(buffer) < 16:
            raise XvdParseError("用户数据区过短")
        length, version, ud_type, unknown = struct.unpack_from("<IIII", buffer, 0)
        if ud_type != 0:  # PackageFiles
            return {}
        if len(buffer) < length + 528:
            return {}
        # UserDataPackageFilesHeader: Version + PackageFullName(char[260]) + FileCount
        file_count = struct.unpack_from("<I", buffer, length + 4 + 520)[0]
        contents: Dict[str, bytes] = {}
        entries_offset = length + 4 + 520 + 4
        for i in range(file_count):
            entry_off = entries_offset + i * 528
            if entry_off + 528 > len(buffer):
                break
            path_raw = buffer[entry_off : entry_off + 520]
            file_size, file_offset = struct.unpack_from("<II", buffer, entry_off + 520)
            file_path = path_raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
            if not file_path:
                continue
            data_start = length + file_offset
            data_end = min(data_start + file_size, len(buffer))
            contents[file_path] = buffer[data_start:data_end]
        return contents

    def _read_xvc_info(self, f) -> XvcInfo:
        header = self.header
        xvc_info_offset = _page_to_offset(header.user_data_page_count) + self._xvd_user_data_offset
        f.seek(xvc_info_offset)
        raw = f.read(header.xvc_data_length)
        return XvcInfo(raw)

    def extract(
        self,
        output_dir: Path,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
        stop_event: Optional[threading.Event] = None,
        game_type: str = "release",
    ) -> int:
        """提取全部文件到输出目录，返回文件数"""
        stop_event = stop_event or threading.Event()
        header = self.header
        if self.segments is None or self.xvc_info is None:
            raise XvdParseError("XVD 尚未解析完成")
        if not self.xvc_info.update_segments:
            raise XvdParseError("XVC 缺少更新段信息")

        decryptor: Optional[XtsAesDecryptor] = None
        if header.is_encrypted:
            d_key, t_key = get_cik_key(game_type)
            decryptor = XtsAesDecryptor(d_key, t_key)

        first_segment_offset = _page_to_offset(self.xvc_info.update_segments[0])
        extractable = [
            region
            for region in self.xvc_info.regions
            if region["first_segment_index"] != 0 or first_segment_offset == region["offset"]
        ]
        if not extractable:
            raise XvdParseError("没有可提取的区域")
        output_dir.mkdir(parents=True, exist_ok=True)
        total_files = 0
        for region in extractable:
            if stop_event.is_set():
                raise XvdParseError("提取已取消")
            total_files += self._extract_region(
                region,
                output_dir,
                decryptor,
                progress_cb,
                stop_event,
            )
        return total_files

    def _extract_region(
        self,
        region: Dict[str, int],
        output_dir: Path,
        decryptor: Optional[XtsAesDecryptor],
        progress_cb: Optional[Callable[[int, int, str], None]],
        stop_event: threading.Event,
    ) -> int:
        header = self.header
        should_decrypt = bool(decryptor) and region["key_id"] != 0xFFFF
        region_id = region["id"]
        region_start = region["offset"]
        region_length = region["length"]

        tweak_iv = bytearray(16)
        if should_decrypt:
            struct.pack_into("<I", tweak_iv, 4, region_id)
            tweak_iv[8:16] = header.vd_uid[0:8]

        hash_cache_offset = 0
        hash_entry_index = 0
        hash_cache = bytearray()
        should_refresh_hash_cache = header.data_integrity
        if header.data_integrity:
            block_no = _get_page_offset(region_start - self._xvd_user_data_offset)
            hash_block_page = compute_hash_block_index(
                header.kind,
                self._hash_tree_levels,
                header.number_of_hashed_pages,
                block_no,
                0,
                header.resiliency,
            )
            total_hash_cache_offset = self._hash_tree_page_offset + _page_to_offset(hash_block_page)
            hash_entry_index = block_no % 0xAA
            hash_cache_offset = hash_entry_index * self.HASH_ENTRY_LENGTH_PLAIN

        page_cache_offset = 0
        total_page_cache_offset = region_start
        should_refresh_page_cache = True

        segments = self.segments.files
        current_segment_index = region["first_segment_index"]
        processed_page_count = 0
        total_page_count = _get_page_offset(region_length)
        file_count = 0
        hash_entry_length = self.HASH_ENTRY_LENGTH_ENCRYPTED if should_decrypt else self.HASH_ENTRY_LENGTH_PLAIN

        with open(self.file_path, "rb") as f:
            while current_segment_index < len(segments) and total_page_count > processed_page_count:
                if stop_event.is_set():
                    raise XvdParseError("提取已取消")
                segment_path, segment_size = segments[current_segment_index]
                output_file = output_dir / segment_path
                output_file.parent.mkdir(parents=True, exist_ok=True)

                remaining_segment = segment_size
                # 1MB 写入缓冲，减少系统调用
                with open(output_file, "wb", buffering=1024 * 1024) as out:
                    while True:
                        if should_refresh_hash_cache:
                            f.seek(total_hash_cache_offset)
                            hash_cache = bytearray(f.read(self.PAGE_CACHE_SIZE))
                            should_refresh_hash_cache = False
                        if should_refresh_page_cache:
                            f.seek(total_page_cache_offset)
                            page_cache = bytearray(f.read(self.PAGE_CACHE_SIZE))
                            should_refresh_page_cache = False

                        current_page = page_cache[page_cache_offset : page_cache_offset + PAGE_SIZE]
                        if header.data_integrity:
                            current_hash_entry = hash_cache[hash_cache_offset : hash_cache_offset + 0x18]
                            if should_decrypt and len(current_hash_entry) >= hash_entry_length + 4:
                                tweak_iv[0:4] = current_hash_entry[hash_entry_length : hash_entry_length + 4]
                            hash_cache_offset += 0x18
                            hash_entry_index += 1
                            if hash_entry_index == 0xAA:
                                hash_entry_index = 0
                                hash_cache_offset += 0x10
                            if hash_cache_offset == len(hash_cache):
                                total_hash_cache_offset += hash_cache_offset
                                hash_cache_offset = 0
                                hash_entry_index = 0
                                should_refresh_hash_cache = True

                        if should_decrypt:
                            current_page = decryptor.decrypt_page(current_page, tweak_iv)

                        chunk_size = min(remaining_segment, PAGE_SIZE)
                        out.write(current_page[:chunk_size])
                        remaining_segment -= chunk_size

                        page_cache_offset += PAGE_SIZE
                        if page_cache_offset == len(page_cache):
                            total_page_cache_offset += page_cache_offset
                            page_cache_offset = 0
                            should_refresh_page_cache = True

                        processed_page_count += 1
                        # 0 字节段（如 hbui 的 MGE 标记文件）在数据流中仍占一页
                        # （打包器按页写占位零），必须消费该页否则后续段全部错位
                        if remaining_segment <= 0:
                            break
                file_count += 1
                if progress_cb:
                    progress_cb(current_segment_index + 1, len(segments), segment_path)
                current_segment_index += 1
        return file_count


def extract_gdk_package(
    package_path: Path,
    output_dir: Path,
    progress_cb=None,
    stop_event=None,
    game_type: str = "release",
) -> int:
    """解包 GDK 游戏包（.msixvc / .insPack）到指定目录

    game_type: release / preview / beta，决定 XTS-AES 解密使用的 CIK 密钥
    """
    extractor = XvdExtractor(package_path).parse()
    return extractor.extract(output_dir, progress_cb=progress_cb, stop_event=stop_event, game_type=game_type)


# CIK 密钥（来自 BedrockLauncher.Core NuGet 2.0.4.9 反编译 _DEFINE_REF2，
# 格式: Guid(0x10) + TKey(0x10) + DKey(0x10)）
_CIK_RELEASE = bytes.fromhex(
    "***REMOVED***"
)
_CIK_PREVIEW = bytes.fromhex(
    "***REMOVED***"
)


def get_cik_key(game_type: str) -> Tuple[bytes, bytes]:
    """返回 (DKey, TKey)；game_type: release / preview / beta"""
    cik = _CIK_RELEASE if game_type == "release" else _CIK_PREVIEW
    return cik[0x20:0x30], cik[0x10:0x20]


