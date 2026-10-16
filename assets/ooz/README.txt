OOZWorker is a separate general-purpose block decompression process.
The editor communicates with it through bounded stdin/stdout binary messages.
It does not open game files and is not linked into the editor process.

Worker source: worker.py (GPL-3.0-or-later)
Decoder source: pyooz-0.0.8.tar.gz (GPL-3.0-or-later)
Full license: COPYING.txt
The archive includes pyooz's matching complete ooz and SIMDe source.
The decompression implementation originates from Powzix, with further ooz
and Python bindings maintained by the pyooz authors.
Upstream release: https://pypi.org/project/pyooz/0.0.8/
Its own dependency license and copyright notices are preserved in that archive.
Unmodified pyooz 0.0.8 Windows x64 wheel:
SHA256 9c0f22b8bec3277f807215a6256bdc6b77375e4d01067dfb580422c35b1d2d65
Matching source archive:
SHA256 98916331773493764483bc6448c9c6166bf2440939abe77cd140509038cc3adf

Rebuild from the project root with Python 3.11/3.12 on Windows x64:
  python -m pip install pyooz==0.0.8 pyinstaller==6.22.3
  python -m PyInstaller --noconfirm --onefile --console --name OOZWorker --distpath assets/ooz --workpath build/ooz --specpath assets/ooz assets/ooz/worker.py

Or build pyooz itself from the complete included source archive using its
pyproject.toml/setup.py and an appropriate C++ compiler before building worker.py.
No proprietary Oodle DLL is distributed or downloaded by this application.
