import PyInstaller.__main__

def build():
    PyInstaller.__main__.run([
        'src/main.py',
        '--name=SmartPhotoCleaner',
        '--onefile',
        '--windowed',
        '--noconfirm',
        '--collect-all=customtkinter',
        '--collect-all=imageio_ffmpeg',
        '--hidden-import=PIL._tkinter_finder',
        '--hidden-import=pythoncom',
        '--hidden-import=winshell',
        '--hidden-import=send2trash',
        '--clean'
    ])

if __name__ == '__main__':
    build()
