# Anaconda 3.8 on this machine needs Library\bin on PATH for OpenSSL and MKL DLLs.
$conda = "$env:USERPROFILE\anaconda3"
$env:PATH = "$conda;$conda\Library\bin;$conda\Scripts;" + $env:PATH
Set-Location $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" -m vessel_tracker @args
