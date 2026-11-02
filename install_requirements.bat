@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

if "%~1"=="" goto :usage
if not "%~3"=="" (
  echo ERROR: too many arguments.
  goto :usage
)

set "ROOT=%~1"
set "MYSTIC=0"
if "%~2"=="" goto :args_ok
if /I "%~2"=="/with-mystic07" goto :set_mystic
if /I "%~2"=="--with-mystic07" goto :set_mystic
echo ERROR: unknown option: %~2
goto :usage
:set_mystic
set "MYSTIC=1"

:args_ok
if not exist "%ROOT%\" (
  echo ERROR: ComfyUI root does not exist: %ROOT%
  exit /b 1
)
if not exist "%ROOT%\custom_nodes\" (
  echo ERROR: missing custom_nodes directory under: %ROOT%
  echo Pass the ComfyUI folder that contains custom_nodes and models.
  exit /b 1
)
if not exist "%ROOT%\models\" (
  echo ERROR: missing models directory under: %ROOT%
  echo Pass the ComfyUI folder that contains custom_nodes and models.
  exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
  echo ERROR: git is not installed or not on PATH.
  exit /b 1
)
where curl.exe >nul 2>&1
if errorlevel 1 (
  echo ERROR: curl.exe is not installed or not on PATH.
  exit /b 1
)
where certutil >nul 2>&1
if errorlevel 1 (
  echo ERROR: certutil is not installed or not on PATH. It is required to verify downloads.
  exit /b 1
)

call :clone
if errorlevel 1 exit /b 1

rem Sizes and SHA256 are the Hugging Face x-linked-size / x-linked-etag values.
rem The upscaler file was renamed upstream; these bytes are the workflow filename.
call :download "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" "%ROOT%\models\loras\minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" "1956193000" "5b9ab5ade15d0775676d01a907268a69a1468dc6033b3b0d3ded5502f3ebb84c"
if errorlevel 1 exit /b 1
call :download "https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler/resolve/main/minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors" "%ROOT%\models\latent_upscale_models\minimax_h3_latent_upscaler_3d_fp16.safetensors" "690592672" "043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2"
if errorlevel 1 exit /b 1
call :download "https://huggingface.co/Comfy-Org/Frame-Interpolation/resolve/main/frame_interpolation/rife_v4.26.safetensors" "%ROOT%\models\frame_interpolation\rife_v4.26.safetensors" "22674688" "151874592c877740e5db11522f4514df569eeafb0a0fcb2696f16e9e8d317c94"
if errorlevel 1 exit /b 1

if not "%MYSTIC%"=="1" goto :finish
call :download "https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot/resolve/main/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors" "%ROOT%\models\diffusion_models\minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors" "20980178976" "4262e4e9963c553fa00016bbe83961407a4fc0a888be95fd836c8d4f2304e48b"
if errorlevel 1 exit /b 1

:finish

echo Done. Restart ComfyUI so the new custom node is loaded.
exit /b 0

:usage
echo Usage: install_requirements.bat COMFYUI_ROOT [/with-mystic07]
echo   COMFYUI_ROOT     ComfyUI folder that contains custom_nodes\ and models\
echo   /with-mystic07   also download the optional ~20 GB derivative diffusion model
exit /b 1

:clone
set "NODE=%ROOT%\custom_nodes\Comfyui_Minimax_h3_latent_Upscaler"
if exist "%NODE%\.git\" (
  echo skip existing custom node Comfyui_Minimax_h3_latent_Upscaler
  exit /b 0
)
if exist "%NODE%\" (
  echo ERROR: %NODE% already exists and is not a git clone.
  echo This script will not overwrite it. Move it aside and re-run.
  exit /b 1
)
echo cloning Comfyui_Minimax_h3_latent_Upscaler
git clone --depth 1 "https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git" "%NODE%"
if errorlevel 1 (
  if exist "%NODE%\" rmdir /s /q "%NODE%"
  echo ERROR: git clone failed. Check network access and that git can write to:
  echo   %NODE%
  exit /b 1
)
exit /b 0

:download
set "URL=%~1"
set "DEST=%~2"
set "EXPECT_SIZE=%~3"
set "EXPECT_SHA=%~4"
for %%D in ("%DEST%") do set "DEST_DIR=%%~dpD"
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%"
if not exist "%DEST%" goto :download_body
for %%A in ("%DEST%") do set "EXIST_SIZE=%%~zA"
if "%EXIST_SIZE%"=="0" goto :replace_empty
echo skip existing %~nx2
exit /b 0
:replace_empty
echo replacing empty file %~nx2
:download_body
set "TMP=%DEST%.partial"
if exist "%TMP%" del /f /q "%TMP%"
echo downloading %~nx2
curl.exe -fL --retry 3 --retry-delay 2 -o "%TMP%" "%URL%"
if errorlevel 1 (
  if exist "%TMP%" del /f /q "%TMP%"
  echo ERROR: download failed: %URL%
  echo Nothing was written to %DEST%. Re-run this script to retry.
  exit /b 1
)
set "DL_FILE=%TMP%"
for /f %%S in ('powershell -NoProfile -Command "(Get-Item -LiteralPath $env:DL_FILE).Length"') do set "GOT_SIZE=%%S"
if not "%GOT_SIZE%"=="%EXPECT_SIZE%" (
  del /f /q "%TMP%"
  echo ERROR: size mismatch for %~nx2: expected %EXPECT_SIZE% bytes, got %GOT_SIZE%.
  echo The file was not installed. Re-run this script to retry.
  exit /b 1
)
set "HASH_FILE=%TMP%.hash"
certutil -hashfile "%TMP%" SHA256 > "%HASH_FILE%"
if errorlevel 1 (
  del /f /q "%TMP%" "%HASH_FILE%" 2>nul
  echo ERROR: could not checksum %~nx2. certutil failed.
  exit /b 1
)
set "GOT_SHA="
for /f "usebackq skip=1 delims=" %%H in ("%HASH_FILE%") do (
  if not defined GOT_SHA set "GOT_SHA=%%H"
)
del /f /q "%HASH_FILE%"
if not defined GOT_SHA (
  del /f /q "%TMP%"
  echo ERROR: certutil produced no checksum for %~nx2.
  exit /b 1
)
set "GOT_SHA=%GOT_SHA: =%"
if /I not "%GOT_SHA%"=="%EXPECT_SHA%" (
  del /f /q "%TMP%"
  echo ERROR: checksum mismatch for %~nx2.
  echo   expected %EXPECT_SHA%
  echo   got      %GOT_SHA%
  echo The file was not installed. Re-run this script to retry.
  exit /b 1
)
move /y "%TMP%" "%DEST%" >nul
if errorlevel 1 (
  echo ERROR: could not move the download into place: %DEST%
  echo The partial file is still at %TMP%. Re-run after fixing permissions.
  exit /b 1
)
echo installed %~nx2
exit /b 0
