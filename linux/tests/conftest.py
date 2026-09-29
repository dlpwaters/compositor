import os

# Omarchy's GTK platform theme opens a real display even with Qt's offscreen backend.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_PLATFORMTHEME"] = ""
