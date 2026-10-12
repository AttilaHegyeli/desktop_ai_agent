import subprocess
import os

# javítva: egy felesleges ")" miatt SyntaxError volt, az asd.png a path.join részévé vált
subprocess.run(['snippingtool.exe', '/screenshot', '/save', os.path.join(os.path.expanduser('~'), 'Desktop', 'asd.png')])