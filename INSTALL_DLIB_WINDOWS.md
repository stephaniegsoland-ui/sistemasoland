Instalación de `dlib` y `face_recognition` en Windows

Resumen
-------
`dlib` (requerido por `face_recognition`) necesita herramientas de compilación C++ en Windows. Sigue estos pasos con privilegios de administrador.

1) Instalar Visual C++ Build Tools (Visual Studio)
- Descarga el instalador desde:
  https://learn.microsoft.com/en-us/visualstudio/install/install-visual-studio
- Ejecuta el instalador y selecciona la carga de trabajo:
  "Desktop development with C++" (incluye MSVC toolset y Windows SDK).
- Asegúrate de incluir el "Windows 10/11 SDK" y el compilador MSVC.

2) Instalar CMake
- Descarga e instala CMake (versión reciente): https://cmake.org/download/
- Añade `cmake` al PATH si el instalador no lo hizo.

3) Preparar Python virtualenv
```powershell
cd solandBackend
python -m venv env
.\env\Scripts\activate
pip install --upgrade pip setuptools wheel
```

4) Instalar dependencias compilación y Python
```powershell
pip install cmake
pip install numpy
```

5) Instalar `dlib` y `face_recognition`
```powershell
pip install dlib
pip install face_recognition
```

Notas y problemas comunes
------------------------
- Si `pip install dlib` falla con mensajes sobre CMake o MSVC, revisa que Visual C++ Build Tools y CMake estén correctamente instalados y en `PATH`.
- Alternativa: usa Conda/Miniconda y paquetes precompilados (recomendado si la compilación falla):
  - `conda create -n soland python=3.11`
  - `conda activate soland`
  - `conda install -c conda-forge dlib face_recognition opencv numpy`

Alternativa sin dlib (recomendada para Windows sin herramientas de compilación)
- Usar la implementación LBPH basada en OpenCV (`opencv-contrib-python`) — ya implementada como fallback en el backend.

Si quieres que intente automatizar más pasos (por ejemplo, un script PowerShell que compruebe herramientas y lance instaladores), indícamelo y lo preparo.
