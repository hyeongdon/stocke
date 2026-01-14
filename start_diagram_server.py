#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mermaid 다이어그램 뷰어 서버 실행 스크립트
"""
import http.server
import socketserver
import webbrowser
import os
import sys
from pathlib import Path

PORT = 8080
DIRECTORY = Path(__file__).parent

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)
    
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

def main():
    os.chdir(DIRECTORY)
    
    print("=" * 60)
    print("🚀 Mermaid 다이어그램 뷰어 서버 시작")
    print("=" * 60)
    print(f"📁 디렉토리: {DIRECTORY}")
    print(f"🌐 포트: {PORT}")
    print(f"📄 파일: view_diagram.html")
    print("=" * 60)
    print(f"\n✅ 서버가 시작되었습니다!")
    print(f"🌐 브라우저에서 다음 URL을 열어주세요:")
    print(f"   http://localhost:{PORT}/view_diagram.html")
    print("\n⏹️  서버를 종료하려면 Ctrl+C를 누르세요.")
    print("=" * 60)
    
    # 2초 후 브라우저 자동 열기
    import threading
    import time
    
    def open_browser():
        time.sleep(2)
        url = f"http://localhost:{PORT}/view_diagram.html"
        print(f"\n🌐 브라우저 자동 열기: {url}")
        webbrowser.open(url)
    
    threading.Thread(target=open_browser, daemon=True).start()
    
    # HTTP 서버 시작
    with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n⏹️  서버가 종료되었습니다.")
            sys.exit(0)

if __name__ == "__main__":
    main()

