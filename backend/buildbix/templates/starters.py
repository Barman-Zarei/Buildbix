"""Starter projects. NOT verified by real builds; versions in package.json/requirements are best-effort."""
STARTERS = {
    "kivy": {"title": "Kivy hello app (Android-ready)", "targets": ["android-apk"], "files": {
        "main.py": 'from kivy.app import App\nfrom kivy.uix.label import Label\n\n\nclass HelloApp(App):\n    def build(self):\n        return Label(text="Hello from BUILDBIX")\n\n\nif __name__ == "__main__":\n    HelloApp().run()\n',
        "requirements.txt": "kivy==2.3.0\n",
        "buildozer.spec": "[app]\ntitle = Hello BUILDBIX\npackage.name = hellobuildbix\npackage.domain = org.buildbix\nsource.dir = .\n"
                          "source.include_exts = py,png,jpg,kv,atlas\nversion = 0.1\nrequirements = python3,kivy\norientation = portrait\n"
                          "android.archs = arm64-v8a\n\n[buildozer]\nlog_level = 2\n"}},
    "fastapi": {"title": "FastAPI service with a test", "targets": ["run-tests"], "files": {
        "main.py": 'from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n',
        "requirements.txt": "fastapi\nuvicorn\nhttpx\npytest\n",
        "tests/test_main.py": 'from fastapi.testclient import TestClient\n\nfrom main import app\n\n\ndef test_health():\n    assert TestClient(app).get("/health").json() == {"status": "ok"}\n'}},
    "vite-react": {"title": "Vite + React web app", "targets": ["web-bundle"], "files": {
        "package.json": '{\n  "name": "app",\n  "private": true,\n  "version": "0.1.0",\n  "type": "module",\n  "scripts": {"dev": "vite", "build": "vite build"},\n'
                        '  "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},\n  "devDependencies": {"@vitejs/plugin-react": "^4.3.1", "vite": "^5.4.0"}\n}\n',
        "index.html": '<!doctype html>\n<html><body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body></html>\n',
        "src/main.jsx": 'import React from "react";\nimport { createRoot } from "react-dom/client";\n\ncreateRoot(document.getElementById("root")).render(<h1>Hello from BUILDBIX</h1>);\n',
        "vite.config.js": 'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\nexport default defineConfig({ plugins: [react()] });\n'}},
}
