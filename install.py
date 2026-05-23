import os,subprocess,platform
addCMD = "--break-system-packages"
packages = ""
if platform.system()!="Linux":
    addCMD=""
path = os.path.join(os.getcwd(),"requirements.txt")
with open(path) as r:
    packages = " ".join(r.readlines()).replace("\n","")

os.system(f"pip install --no-cache-dir {packages} {addCMD}")
