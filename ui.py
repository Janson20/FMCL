"""UI界面模块"""
import tkinter as tk
from tkinter import messagebox
from typing import List, Dict, Optional, Any
from logzero import logger


class VersionSelector:
    """版本选择器"""
    
    def __init__(self, title: str = "版本列表"):
        """
        初始化版本选择器
        
        Args:
            title: 窗口标题
        """
        self.title = title
        self.selected_version = None
        
    def show(self, versions: List[Dict[str, Any]], width: int = 800, height: int = 600) -> Optional[str]:
        """
        显示版本选择窗口
        
        Args:
            versions: 版本列表
            width: 窗口宽度
            height: 窗口高度
            
        Returns:
            选择的版本ID,如果取消则返回None
        """
        root = tk.Tk()
        root.title(self.title)
        root.geometry(f"{width}x{height}")
        
        # 创建框架容器
        frame = tk.Frame(root)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建滚动条
        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 创建列表框
        listbox = tk.Listbox(
            frame,
            yscrollcommand=scrollbar.set,
            font=("微软雅黑", 12),
            selectbackground="#4a6984",
            selectmode=tk.SINGLE
        )
        listbox.pack(fill=tk.BOTH, expand=True)
        
        # 填充数据
        for idx, version in enumerate(versions):
            version_text = f"{version.get('id', 'Unknown')} ({version.get('type', 'Unknown')})"
            listbox.insert(tk.END, version_text)
        
        # 配置滚动条
        scrollbar.config(command=listbox.yview)
        
        # 存储选择结果的变量
        result = [None]
        
        def on_select():
            """选择按钮回调"""
            try:
                selection = listbox.curselection()
                if selection:
                    result[0] = versions[selection[0]]["id"]
                root.destroy()
            except Exception as e:
                logger.error(f"选择版本时出错: {str(e)}")
                messagebox.showerror("错误", f"选择版本时出错: {str(e)}")
        
        def on_cancel():
            """取消按钮回调"""
            result[0] = None
            root.destroy()
        
        # 添加按钮框架
        btn_frame = tk.Frame(root)
        btn_frame.pack(fill=tk.X, pady=5)
        
        select_btn = tk.Button(
            btn_frame,
            text="选择",
            command=on_select,
            bg="#4a6984",
            fg="white",
            font=("微软雅黑", 12),
            width=10
        )
        select_btn.pack(side=tk.LEFT, padx=10)
        
        cancel_btn = tk.Button(
            btn_frame,
            text="取消",
            command=on_cancel,
            bg="#666666",
            fg="white",
            font=("微软雅黑", 12),
            width=10
        )
        cancel_btn.pack(side=tk.RIGHT, padx=10)
        
        # 绑定双击事件
        listbox.bind('<Double-Button-1>', lambda e: on_select())
        
        # 启动主循环
        root.mainloop()
        
        return result[0]


def show_confirmation(message: str, title: str = "确认") -> bool:
    """
    显示确认对话框
    
    Args:
        message: 消息内容
        title: 窗口标题
        
    Returns:
        是否确认
    """
    try:
        import pyautogui
        result = pyautogui.confirm(text=message, title=title, buttons=['是', '取消'])
        return result == "是"
    except Exception as e:
        logger.error(f"显示确认对话框失败: {str(e)}")
        # 降级到tkinter
        root = tk.Tk()
        root.withdraw()
        result = messagebox.askyesno(title, message)
        root.destroy()
        return result


def show_alert(message: str, title: str = "提示") -> None:
    """
    显示提示对话框
    
    Args:
        message: 消息内容
        title: 窗口标题
    """
    try:
        import pyautogui
        pyautogui.alert(text=message, title=title, button='确定')
    except Exception as e:
        logger.error(f"显示提示对话框失败: {str(e)}")
        # 降级到tkinter
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(title, message)
        root.destroy()
