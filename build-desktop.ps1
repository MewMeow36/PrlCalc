# Build pearlcalc.exe (single-file desktop app).
# Needs once: python -m pip install pywebview pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name pearlcalc `
  --add-data "index.html;." --add-data "style.css;." --add-data "config.example.json;." `
  --exclude-module tkinter `
  app.py
