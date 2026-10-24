<img width="2878" height="738" alt="PyShareX#gh-light-mode-only" src="https://github.com/user-attachments/assets/df561e61-48b5-45d5-b9cd-c47e0bacbd2d#gh-light-mode-only"/>

<img width="2878" height="738" alt="PyShareX#gh-light-mode-only" src="https://github.com/user-attachments/assets/9882b791-d8e4-48fc-9752-4c86af35b3f5#gh-light-mode-only" />
<img width="2878" height="738" alt="PyShareX#gh-dark-mode-only" src="https://github.com/user-attachments/assets/7a899b83-2bc4-401a-bfe5-6990f18c3c51#gh-dark-mode-only" />


# PyShareX — Open-Source ShareX alternative made in Python

PyShareX is an open-source ShareX alternative written in Python for Linux and Windows.  
It provides instant screenshot uploads, screenshot editor, video converter, text and QR recognition, clipboard automation and a fast productivity workflow.  
Built with the help of Claude AI and Gemini.

For Windows use .pyw script, for Linux .py script

## For Linux:

> Note: The app has been tested on Linux Mint Cinamonn and should work for this distro.<br>
Other Linux distros have not been tested.

Run 
```
python3 install.py
```

If something won't work you can optionally use these commands:

```
sudo apt update
sudo apt install libxcb-cursor0
```

```
sudo apt install libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-util1
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
### video recording/conversion/capture region (one of the methods) with canvas (requires ffmpeg installed on the system)
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
<img width="977" height="721" alt="region_2026-06-08_22-51-13" src="https://github.com/user-attachments/assets/b6510a05-120f-40fc-af36-ed5b0747e163" />


## Capture Region with toolbar
It works in 2 modes:

1) Hover over window/element, choose specific one and click to capture.

<img width="1920" height="1080" alt="window detection 2026-10-01 20-47-15 mkv_snapshot_00 03 448" src="https://github.com/user-attachments/assets/f1b9382d-8697-4792-8046-94ad808151a2" />


<br><br><br>

2) Hold LMB -> drag and release to capture custom screen region.  

<img width="1920" height="1080" alt="custom region selection 2026-10-01 20-47-15 mkv_snapshot_00 08 288" src="https://github.com/user-attachments/assets/f6f708c0-69e3-43f2-b261-b9cbcbee5076" />


<br><br>

#### You can also use tools from toolbar to attach various annotation objects to the screen.

<br>
<img width="1920" height="1080" alt="2026-10-24 21-33-35 mkv_snapshot_04 27 200" src="https://github.com/user-attachments/assets/70a3d416-9368-4a7f-bcb3-bd5e8c3f822a" />
<br>

#### Spotlight (couldn't be show on first screenshot) 

<br>
<img width="1920" height="1080" alt="2026-10-24 21-55-43 mkv_snapshot_01 03 366" src="https://github.com/user-attachments/assets/abe02936-b066-4d5e-9c8a-f4a08488abe3" />




## Video Converter Window

<img width="764" height="687" alt="region_2026-05-13_22-15-04" src="https://github.com/user-attachments/assets/556560f2-66ed-4679-b713-8bab0d2ff1f8" />

## OCR / QR Toolbar Window

<img width="918" height="647" alt="image" src="https://github.com/user-attachments/assets/e24f6bfc-a74d-42a3-89ba-5dbff2dcbbf3" />

## Image Editor

<img width="1920" height="1020" alt="pythonw_ChupxXgZhQ" src="https://github.com/user-attachments/assets/85bc15f1-6f86-463a-a771-d079b62ae9fd" />










## Aplication normally works in the background (System Tray)
<br>
<img width="346" height="501" alt="Zrzut ekranu 2026-05-10 001729" src="https://github.com/user-attachments/assets/3d73ef5d-05ed-42fd-be35-db082947fb97" />




