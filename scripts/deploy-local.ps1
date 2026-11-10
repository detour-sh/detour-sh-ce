$ErrorActionPreference = "Stop"

Write-Host "1/4 Unit checks"
python -B -m unittest discover -s app -p "test_*.py"

Write-Host "2/4 Build and start Detour"
docker compose up -d --build --remove-orphans detour
if ($LASTEXITCODE -ne 0) { throw "Detour build or startup failed" }

Write-Host "3/4 Wait for container health"
$healthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $containerId = docker compose ps -q detour
    $status = if ($containerId) { docker inspect --format "{{.State.Health.Status}}" $containerId 2>$null } else { "" }
    if ($status -eq "healthy") { $healthy = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $healthy) { throw "Detour did not become healthy" }

Write-Host "4/4 Browser rendering and interaction checks"
New-Item -ItemType Directory -Force -Path "artifacts/ui" | Out-Null
docker compose --profile test build ui-test
if ($LASTEXITCODE -ne 0) { throw "UI test image build failed" }
docker compose --profile test run --rm --no-deps ui-test
if ($LASTEXITCODE -ne 0) { throw "UI rendering or interaction checks failed" }
Write-Host "Detour deployed and UI checks passed. Screenshots: artifacts/ui"
