"""下载连接池全局配置

HTTP 连接池大小常量，供 curseforge.py / modrinth.py / downloader.py 统一引用。
"""

# 连接池基础大小（pool_connections / max_concurrent / limit_per_host）
DOWNLOAD_POOL_SIZE = 25

# 连接池最大容量（pool_maxsize）
DOWNLOAD_POOL_MAXSIZE = 50
