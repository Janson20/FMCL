import tkinter as tk
from tkinter import Canvas, Button
import pyautogui
import keyboard
import threading

class ScreenshotTool:
    def __init__(self, root):
        self.root = root
        self.root.title("截图工具")
        self.root.attributes("-fullscreen", True)  # 全屏显示
        self.root.attributes("-alpha", 0.3)  # 设置窗口透明度

        self.canvas = Canvas(self.root, cursor="cross", bg="grey11")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.start_x = None
        self.start_y = None
        self.rect = None

        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)

        self.button = Button(self.root, text="截图", command=self.take_screenshot)
        self.button.pack(side=tk.BOTTOM, pady=10)

    def on_button_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline="red", width=2)

    def on_mouse_drag(self, event):
        curX, curY = (event.x, event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, curX, curY)

    def on_button_release(self, event):
        self.end_x = event.x
        self.end_y = event.y

    def take_screenshot(self):
        if self.start_x is not None and self.start_y is not None and self.end_x is not None and self.end_y is not None:
            x1 = min(self.start_x, self.end_x)
            y1 = min(self.start_y, self.end_y)
            x2 = max(self.start_x, self.end_x)
            y2 = max(self.start_y, self.end_y)

            screenshot = pyautogui.screenshot(region=(x1, y1, x2 - x1, y2 - y1))
            screenshot.save("screenshot.png")
            self.root.quit()

def start_screenshot_tool():
    root = tk.Tk()
    app = ScreenshotTool(root)
    root.mainloop()

def listen_for_hotkey():
    # 监听 Ctrl+Alt+T 快捷键
    keyboard.add_hotkey("ctrl+alt+t", start_screenshot_tool)
    print("按下 Ctrl+Alt+T 启动截图工具...")
    keyboard.wait()  # 保持监听状态

if __name__ == "__main__":
    # 在后台运行快捷键监听器
    hotkey_thread = threading.Thread(target=listen_for_hotkey, daemon=True)
    hotkey_thread.start()
    
    # 使用 keyboard.wait() 替代 while True 循环,避免 CPU 100% 占用
    keyboard.wait()