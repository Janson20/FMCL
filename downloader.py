"""多线程下载模块"""
import os
import threading
import time
from typing import Optional, Callable
from pathlib import Path

import requests
from tqdm import tqdm
from logzero import logger


class MultiThreadDownloader:
    """多线程下载器"""
    
    def __init__(self, num_threads: int = 4, chunk_size: int = 8192):
        """
        初始化下载器
        
        Args:
            num_threads: 线程数
            chunk_size: 分块大小
        """
        self.num_threads = num_threads
        self.chunk_size = chunk_size
        self.downloaded_bytes = 0
        self.total_size = 0
        self.start_time = 0
        self.lock = threading.Lock()
        
    def _download_part(
        self, 
        url: str, 
        start_byte: int, 
        end_byte: int, 
        part_number: int, 
        filename: Path,
        progress_bar: tqdm
    ) -> None:
        """
        下载文件的分段部分
        
        Args:
            url: 下载链接
            start_byte: 起始字节
            end_byte: 结束字节
            part_number: 分段编号
            filename: 文件名
            progress_bar: 进度条对象
        """
        headers = {'Range': f'bytes={start_byte}-{end_byte}'}
        
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            
            part_filename = f"{filename}.part{part_number}"
            
            with open(part_filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if chunk:
                        f.write(chunk)
                        with self.lock:
                            self.downloaded_bytes += len(chunk)
                            progress_bar.update(len(chunk))
                            
        except Exception as e:
            logger.error(f"下载分段 {part_number} 失败: {str(e)}")
            raise
    
    def _merge_files(self, filename: Path) -> None:
        """
        合并分段文件
        
        Args:
            filename: 最终文件名
        """
        try:
            with open(filename, 'wb') as outfile:
                for i in range(self.num_threads):
                    part_filename = f"{filename}.part{i}"
                    if os.path.exists(part_filename):
                        with open(part_filename, 'rb') as infile:
                            outfile.write(infile.read())
                        os.remove(part_filename)
                        
            logger.info(f"文件合并成功: {filename}")
            
        except Exception as e:
            logger.error(f"合并文件失败: {str(e)}")
            raise
    
    def download(self, url: str, output_dir: Optional[str] = None) -> str:
        """
        下载文件
        
        Args:
            url: 下载链接
            output_dir: 输出目录,默认为当前目录
            
        Returns:
            下载的文件名
            
        Raises:
            Exception: 下载失败时抛出异常
        """
        try:
            # 获取文件信息
            response = requests.head(url, timeout=10)
            response.raise_for_status()
            
            self.total_size = int(response.headers.get('Content-Length', 0))
            
            if self.total_size == 0:
                raise ValueError("无法获取文件大小")
            
            # 准备下载
            filename = Path(url.split('/')[-1])
            if output_dir:
                filename = Path(output_dir) / filename
                
            part_size = self.total_size // self.num_threads
            threads = []
            
            # 创建进度条
            progress_bar = tqdm(
                total=self.total_size, 
                unit='B', 
                unit_scale=True, 
                desc=filename.name
            )
            
            self.downloaded_bytes = 0
            self.start_time = time.time()
            
            # 创建并启动下载线程
            for i in range(self.num_threads):
                start_byte = i * part_size
                end_byte = start_byte + part_size - 1 if i < self.num_threads - 1 else self.total_size - 1
                
                thread = threading.Thread(
                    target=self._download_part,
                    args=(url, start_byte, end_byte, i, filename, progress_bar)
                )
                threads.append(thread)
                thread.start()
            
            # 等待所有线程完成
            for thread in threads:
                thread.join()
            
            progress_bar.close()
            
            # 合并文件
            self._merge_files(filename)
            
            logger.info(f"文件下载成功: {filename}")
            return str(filename)
            
        except Exception as e:
            logger.error(f"下载失败: {str(e)}")
            raise


def download_forge(version: str, num_threads: int = 4) -> str:
    """
    下载指定版本的Forge
    
    Args:
        version: Minecraft版本
        num_threads: 线程数
        
    Returns:
        下载的文件名
    """
    try:
        import forgepy
        
        logger.info(f"正在获取 Forge {version} 下载链接")
        forge_url = forgepy.GetLatestURL(version)
        
        downloader = MultiThreadDownloader(num_threads=num_threads)
        filename = downloader.download(forge_url)
        
        logger.info(f"Forge {version} 下载成功")
        return filename
        
    except Exception as e:
        logger.error(f"下载 Forge 失败: {str(e)}")
        raise
