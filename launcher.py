import tkinter as tk
from tkinter import messagebox
import subprocess
import os
import threading
import webbrowser

class ServerLauncher:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("키움증권 모니터링 시스템")
        self.root.geometry("300x200")
        self.root.resizable(False, False)
        
        self.server_process = None
        self.setup_ui()
        
    def setup_ui(self):
        # 제목
        title_label = tk.Label(self.root, text="키움증권 모니터링 시스템", 
                              font=("맑은 고딕", 14, "bold"))
        title_label.pack(pady=20)
        
        # 서버 시작 버튼
        self.start_btn = tk.Button(self.root, text="서버 시작", 
                                  command=self.start_server,
                                  bg="#4CAF50", fg="white",
                                  font=("맑은 고딕", 12),
                                  width=15, height=2)
        self.start_btn.pack(pady=10)
        
        # 서버 중지 버튼
        self.stop_btn = tk.Button(self.root, text="서버 중지", 
                                 command=self.stop_server,
                                 bg="#f44336", fg="white",
                                 font=("맑은 고딕", 12),
                                 width=15, height=2,
                                 state="disabled")
        self.stop_btn.pack(pady=5)
        
        # 웹 브라우저 열기 버튼
        self.browser_btn = tk.Button(self.root, text="웹 페이지 열기", 
                                    command=self.open_browser,
                                    bg="#2196F3", fg="white",
                                    font=("맑은 고딕", 12),
                                    width=15, height=1,
                                    state="disabled")
        self.browser_btn.pack(pady=5)
        
        # 상태 표시
        self.status_label = tk.Label(self.root, text="서버 중지됨", 
                                    fg="red", font=("맑은 고딕", 10))
        self.status_label.pack(pady=10)
        
    def start_server(self):
        try:
            # 가상환경 활성화 후 uvicorn 실행
            cmd = "venv\\Scripts\\activate && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
            self.server_process = subprocess.Popen(cmd, shell=True, cwd=os.getcwd())
            
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.browser_btn.config(state="normal")
            self.status_label.config(text="서버 실행 중...", fg="green")
            
            # 서버 시작 후 잠시 대기 후 브라우저 자동 열기
            def open_browser_delayed():
                import time
                time.sleep(3)  # 서버가 완전히 시작될 때까지 3초 대기
                webbrowser.open("http://localhost:8000")
            
            # 별도 스레드에서 브라우저 열기
            threading.Thread(target=open_browser_delayed, daemon=True).start()                                          
            
        except Exception as e:
            messagebox.showerror("오류", f"서버 시작 실패: {str(e)}")
            
    def stop_server(self):
        try:
            if self.server_process:
                self.server_process.terminate()
                self.server_process = None
                
            # Python 프로세스 강제 종료
            subprocess.run("taskkill /f /im python.exe", shell=True, capture_output=True)
            
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.browser_btn.config(state="disabled")
            self.status_label.config(text="서버 중지됨", fg="red")
            
            messagebox.showinfo("성공", "서버가 중지되었습니다.")
            
        except Exception as e:
            messagebox.showerror("오류", f"서버 중지 실패: {str(e)}")
            
    def open_browser(self):
        webbrowser.open("http://localhost:8000")
        
    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    launcher = ServerLauncher()
    launcher.run()