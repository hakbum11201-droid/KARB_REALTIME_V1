import os
import sys
import time
import webbrowser
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def main():
    print("============================================")
    print("  KARB_REALTIME_V1 - App Launcher")
    print("============================================")
    print("[Launcher] Starting Web Server...")
    
    server_script = os.path.join(BASE_DIR, 'src', 'web_server.py')
    
    # Run web server as a subprocess
    server_process = subprocess.Popen([sys.executable, server_script, '--port', '8000'])
    
    time.sleep(1.5)  # Wait for server to start
    
    if '--no-browser' not in sys.argv:
        url = "http://localhost:8000"
        print(f"[Launcher] Opening Browser at {url}...")
        webbrowser.open(url)
    else:
        print("[Launcher] --no-browser flag detected. Skipping browser auto-open.")
    
    print("\n[Launcher] Keep this window open. Close it to stop the server.")
    print("[Launcher] ALL operations (Start, Stop) should be done from the Dashboard UI.\n")
    
    try:
        server_process.wait()
    except KeyboardInterrupt:
        print("[Launcher] Stopping Web Server...")
        server_process.terminate()
        server_process.wait()
        print("[Launcher] Server stopped.")

if __name__ == '__main__':
    main()
