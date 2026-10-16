# Third-party references

This project does not bundle code or binaries from the following projects, but
their public documentation and format research were useful for validating the
Monster Hunter: World MOD3 structure:

- AsteriskAmpersand/Mod3-MHW-Importer
  https://github.com/AsteriskAmpersand/Mod3-MHW-Importer
- Mi1ice/MHWI-VisualControllerExtended
  https://github.com/Mi1ice/MHWI-VisualControllerExtended
- AsteriskAmpersand/Material-Editing (MRL3 binary layout and resource index research)
  https://github.com/AsteriskAmpersand/Material-Editing

materials.py is an independent read-only parser; upstream editor code and hash
dictionary files are not bundled. Material names come from the loaded MOD3.

splitting.py is independently implemented using the published MOD3 mesh header,
vertex/index offset and 144-byte bounding-box layouts in Mod3Mesh.py and
Mod3Components.py of Mod3-MHW-Importer. No upstream implementation is bundled.

textures.py independently reads TEX headers and constructs DDS containers using
format research in JodoZT/MHWTexConvertor:
https://github.com/JodoZT/MHWTexConvertor
UV attribute layout references Mod3VertexBuffers.py in Mod3-MHW-Importer.
No upstream converter or importer implementation is bundled. Texture pixels are
read from user-provided mod/game assets, not distributed with the application.

Runtime dependencies installed during packaging:

- NumPy
- Pillow
- PyInstaller
- Numba (BSD 2-Clause)
- llvmlite (BSD 2-Clause; includes LLVM under its distribution license)
- rarfile (ISC)

Refer to each dependency distribution for its license.

Archive input components bundled with the Windows application:

- UnRAR 7.23, Copyright (c) Alexander Roshal. The free UnRAR command-line
  component is redistributed under its own UnRAR license, preserved at
  `_internal/assets/unrar/LICENSE.txt`. WinRAR and Rar.exe are not bundled.
  Official distribution: https://www.rarlab.com/rar_add.htm
  Stable binary obtained from the official WinRAR 7.23 distribution without
  installing it: https://www.rarlab.com/rar/winrar-x64-723.exe
- 7-Zip Extra 26.04, Copyright (c) 1999-2026 Igor Pavlov. The unmodified
  x64 7za.exe is used as a separate command-line process. Its LGPL 2.1-or-later
  and BSD license notices and full LGPL text are included in
  `_internal/assets/7zip/`. The matching complete upstream source archive is
  included as `_internal/assets/7zip/7z2604-src.7z`, alongside source notices.
  Official binary: https://www.7-zip.org/a/7z2604-extra.7z
  Matching source: https://www.7-zip.org/a/7z2604-src.7z
  These independent components may be replaced with compatible versions.

The MRL3 color-map slot identification was verified against the public format
research in Feuleur/blender-mhworld-tools-suite's mrl3_parser.py and
texture_dict.json, and against user-provided BaoYu and Kuroki assets.
https://github.com/Feuleur/blender-mhworld-tools-suite/tree/main/mrl3
No upstream parser or hash dictionary implementation is bundled.

Game resource texture lookup:

- The independently written Iceborne CMP/PKG reader uses format research from
  JodoZT/MHWNoChunk and mhvuze/WorldChunkTool. The public chunk.key lookup table
  from MHWNoChunk is included at `_internal/assets/mhwchunk/chunk.key` with its
  original MIT license and contributor notice in that directory.
  https://github.com/JodoZT/MHWNoChunk
- OOZWorker is an independently running GPL-3.0-or-later decompression helper,
  using pyooz 0.0.8 / ooz. It exchanges bounded byte messages with the editor.
  The editor does not load the GPL decoder as an in-process module. The worker,
  full GPL text, worker source/build instructions, and complete matching decoder
  source are included at `_internal/assets/ooz/`. pyooz's ooz/SIMDe dependency
  license notices remain in its source archive. No proprietary Oodle DLL is
  bundled and no runtime download is needed.
  https://pypi.org/project/pyooz/0.0.8/

Game texture data is read from the user's installation and cached locally.
No game textures, game packages or model assets are distributed with the app.

The installer uses the Chinese Simplified Inno Setup messages maintained by
Zhenghan Yang, downloaded from the Inno Setup source repository:
https://github.com/jrsoftware/issrc/blob/main/Files/Languages/ChineseSimplified.isl
The file's original contributor notices are preserved in assets/ChineseSimplified.isl.
