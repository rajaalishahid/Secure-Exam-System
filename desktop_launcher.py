import os
import sys
import threading
import time
import signal
import subprocess
import atexit
from pathlib import Path

import webview

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
MANAGE_PY = BASE_DIR / "manage.py"
DJANGO_HOST = "127.0.0.1"
DJANGO_PORT = 8000
DJANGO_URL = f"http://{DJANGO_HOST}:{DJANGO_PORT}"

django_process = None


def run_django():
    global django_process
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "secure_exam_system.settings")
    django_process = subprocess.Popen(
        [str(VENV_PYTHON), str(MANAGE_PY), "runserver", f"{DJANGO_HOST}:{DJANGO_PORT}", "--noreload"],
        cwd=BASE_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in django_process.stdout:
        print(f"[Django] {line.rstrip()}")


def wait_for_server(url, timeout=30):
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def cleanup():
    global django_process
    if django_process and django_process.poll() is None:
        if sys.platform == "win32":
            django_process.terminate()
        else:
            os.killpg(os.getpgid(django_process.pid), signal.SIGTERM)
        django_process.wait(timeout=5)


def main():
    if not VENV_PYTHON.exists():
        print("Virtual environment not found. Run Start-Local.cmd first to set up.")
        sys.exit(1)

    print("Starting Django server...")
    django_thread = threading.Thread(target=run_django, daemon=True)
    django_thread.start()

    print("Waiting for server to be ready...")
    if not wait_for_server(DJANGO_URL):
        print("ERROR: Django server failed to start in time")
        cleanup()
        sys.exit(1)

    print("Server ready. Opening desktop window...")
    atexit.register(cleanup)

    window = webview.create_window(
        "Secure Exam System",
        DJANGO_URL,
        width=1280,
        height=800,
        min_size=(1024, 768),
        resizable=True,
        text_select=True,
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()