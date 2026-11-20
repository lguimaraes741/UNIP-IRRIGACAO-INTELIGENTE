$ErrorActionPreference = 'Continue'
Write-Output 'Ferramentas no PATH'
Get-Command docker,git,python,idf.py -ErrorAction SilentlyContinue | Select-Object Name,Source
Write-Output 'Docker e motor'
docker version
Write-Output 'Compose'
docker compose version
Write-Output 'Estado Git'
git status --short
