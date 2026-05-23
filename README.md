<img width="2878" height="738" alt="PyShareX#gh-dark-mode-only" src="https://github.com/user-attachments/assets/18a98e15-916a-4b39-a7d5-d4a5c8913176#gh-dark-mode-only"/>
<img width="2878" height="738" alt="PyShareX#gh-light-mode-only" src="https://github.com/user-attachments/assets/df561e61-48b5-45d5-b9cd-c47e0bacbd2d#gh-light-mode-only"/>
<br><br>

# PyShareX — Open-Source ShareX Alternative Made in Python

PyShareX is an open-source ShareX alternative written in Python for Linux and Windows.  
It provides instant screenshot uploads, screenshot editor, video converter, text and QR recognition, clipboard automation and a fast productivity workflow.  
Built with the help of Claude AI and Gemini.


## For Linux:

Run 
```
python3 install.py
```

If something won't work you can optionlally use this command:
```
sudo apt update && sudo apt install libxcb-cursor0
```

## For Windows:

Before usage please install everything from `requirements.txt` using this cmd:
```
pip install -r requirements.txt
```
You should also run the app by pythonw.exe <br>
In windows it's better to make a shortcut "pythonw.exe <SCRIPT_PATH>"

----
For the icon to be visible in the app, the .ico file must be inside "icons" folder and the "icons" folder must be in the same folder with .pyw script.

----
### Optional - video recording/converter (requires ffmpeg installed on the system)
ffmpeg must be installed separately: 
<br>

#### Windows:  <br>
Download https://ffmpeg.org/download.html  
or  
```
winget install ffmpeg
```

#### Linux: 

```
sudo apt install ffmpeg 
```

----
### Optional - OCR (requires tesseract in the system [or easyocr or paddleocr in pip]) <br>
#### Windows:   
https://github.com/UB-Mannheim/tesseract/wiki <br>

#### Linux: 
```
sudo apt install tesseract-ocr tesseract-ocr-pol
```
----
### PyShareX allows you for:
- capture screen region
- capture active monitor screenshot
- capture selected monitor screenshot
- caputure full screen (from all monitors)
- scrolling capture
- screen region video recording
- screen region GIF recording
- OCR text recognision
- OCR QR recognision
- video converter
- image editor
- OCR/QR Toolbox recognizing text/QR code, creating QR in one window

## Main / Configuration Window
<img width="993" height="722" alt="region_2026-05-10_15-39-15" src="https://github.com/user-attachments/assets/fb465778-9e0c-4634-907f-9245862d68b4" />

## Capture Region with toolbar

<img width="1280" height="720" alt="2026-05-20 21-00-30 mp4_snapshot_03 50 394" src="https://github.com/user-attachments/assets/13ae6501-e231-41bc-a8b2-b73ea0666262" />

## Video Converter Window

<img width="764" height="687" alt="region_2026-05-13_22-15-04" src="https://github.com/user-attachments/assets/556560f2-66ed-4679-b713-8bab0d2ff1f8" />

## OCR / QR Toolbar Window

<img width="918" height="647" alt="image" src="https://github.com/user-attachments/assets/e24f6bfc-a74d-42a3-89ba-5dbff2dcbbf3" />

## Image Editor

<img width="1938" height="1038" alt="region_2026-05-21_20-51-03" src="https://github.com/user-attachments/assets/af240848-4c39-4e7c-8734-7a7ccfe94915" />


## Aplication normally works in the background (System Tray)
<br>
<img width="346" height="501" alt="Zrzut ekranu 2026-05-10 001729" src="https://github.com/user-attachments/assets/3d73ef5d-05ed-42fd-be35-db082947fb97" />




