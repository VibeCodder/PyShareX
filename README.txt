PyshareX - required packages
Install with the command: pip install -r requirements.txt
Program should be run with pythonw.exe
in Windows you can make shortcut: "pythonw.exe <SCRIPT_PATH>"

For Windows use .pyw script, for Linux .py script

For the icon to be visible in the app, the .ico file must be inside "icons" folder and the "icons" folder must be in the same folder with .pyw script.

video recording/conversion/capture region with canvas (requires ffmpeg installed on the system)
ffmpeg must be installed separately:
Windows: https://ffmpeg.org/download.html (or: winget install ffmpeg)
Linux: sudo apt install ffmpeg
macOS: brew install ffmpeg

Optional - OCR (requires tesseract in the system)
Windows: https://github.com/UB-Mannheim/tesseract/wiki
Linux: sudo apt install tesseract-ocr tesseract-ocr-pol