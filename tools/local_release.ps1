<#
Builds a PAIRED RELEASE CANDIDATE of Zilcho Subs on this Windows PC: the tested image plus the
matching source materials, the same way CI's [release-candidate] path does (see
.github/workflows/test.yml and tools/prepare_release.py). GitHub Free can't store the ~1 GB
result, so this runs locally. See tools/local_build.md.

  powershell -ExecutionPolicy Bypass -File tools\local_release.ps1 -Commit <sha> [-CopyTo <folder>]

Nothing is published, pushed or installed: publishing needs the owner's approval
(docs/RELEASE.md). Output: release-artifacts\zilcho-subs\<version>-rc.<yyyyMMddHHmm>\
#>
param(
  [Parameter(Mandatory = $true)][string]$Commit,
  [string]$CopyTo
)
$ErrorActionPreference = 'Stop'
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
# Keep the repository's own line endings (Git for Windows converts to CRLF on checkout/export).
$env:GIT_CONFIG_COUNT = '2'
$env:GIT_CONFIG_KEY_0 = 'core.autocrlf'; $env:GIT_CONFIG_VALUE_0 = 'false'
$env:GIT_CONFIG_KEY_1 = 'core.eol'; $env:GIT_CONFIG_VALUE_1 = 'lf'
function Step($text) { Write-Host ''; Write-Host "== $text" }
function Run { & $args[0] @($args[1..($args.Count - 1)]); if ($LASTEXITCODE) { throw "Failed ($LASTEXITCODE): $($args -join ' ')" } }
function Quiet { $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'; try { & $args[0] @($args[1..($args.Count - 1)]) 2>&1 | Out-Null } finally { $ErrorActionPreference = $previous } }
function Cleanup { Quiet docker rm -f subs-check; Quiet docker volume rm subs-check-config; Quiet docker volume rm subs-check-media }

$repo = Split-Path $PSScriptRoot -Parent
$workspace = Split-Path $repo -Parent
$python = Join-Path $repo '.venv\Scripts\python.exe'
$sha = (git -C $repo rev-parse --verify "$Commit^{commit}").Trim()
$short = $sha.Substring(0, 7)
$tree = Join-Path $env:TEMP "zilcho-subs-release-$short"
$evidence = Join-Path $env:TEMP "zilcho-subs-evidence-$short"

Step "Clean checkout of $short"
foreach ($old in $tree, $evidence) { if (Test-Path $old) { Quiet git -C $repo worktree remove --force $old; if (Test-Path $old) { Remove-Item $old -Recurse -Force -Confirm:$false } } }
Run git -C $repo worktree add --detach $tree $sha
try {
  $version = ((Get-Content "$tree\zilcho_subs\__init__.py") -replace '.*"(.+)".*', '$1').Trim()
  $label = "$version-rc.$(Get-Date -Format 'yyyyMMddHHmm')"
  $out = Join-Path $workspace "release-artifacts\zilcho-subs\$label"
  if (Test-Path $out) { throw "Refusing to overwrite $out" }
  Write-Host "Version $version, candidate $label"

  Step 'Build the runtime image and export the native build sources'
  Run docker build --target runtime -t zilcho-subs:check $tree
  New-Item -ItemType Directory $evidence | Out-Null
  Run docker build --target native-sources --output "type=local,dest=$evidence\sources\native" $tree

  Step 'Checks: locked-down start, sign-in, real engines offline, native linkage'
  $password = 'local-release-' + [guid]::NewGuid().ToString('N')
  Cleanup
  try {
    Run docker run --rm --user 0 --entrypoint sh -v subs-check-media:/media zilcho-subs:check -c 'touch /media/.keep && chown -R 99:100 /media'
    Run docker run -d --name subs-check --cpus=2 --memory=6g --read-only --tmpfs /tmp:size=256m `
      --security-opt=no-new-privileges --cap-drop=ALL --cap-add=CHOWN --cap-add=SETUID --cap-add=SETGID `
      -e "ZILCHO_PASSWORD=$password" -v subs-check-media:/media -v subs-check-config:/config zilcho-subs:check
    $probe = @'
import json, sys, time, urllib.request, urllib.error
for _ in range(60):
    try:
        health = json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2)); break
    except OSError: time.sleep(1)
else: sys.exit('service did not start')
try:
    urllib.request.urlopen('http://127.0.0.1:8765/api/state'); sys.exit('state visible without sign-in')
except urllib.error.HTTPError as e: assert e.code == 401
print('version', health['version'])
'@
    $check = $probe | docker exec -i --user 99:100 subs-check python -
    if ($LASTEXITCODE -or $check -notmatch [regex]::Escape("version $version")) { throw "Startup / sign-in / version check failed: $check" }
    if ((docker exec subs-check stat -c %u /config/queue.sqlite3).Trim() -ne '99') { throw 'queue.sqlite3 is not owned by 99' }
    Get-Content "$tree\tools\container_smoke.py" -Raw | docker exec -i --user 99:100 subs-check sh -c 'cat > /tmp/container_smoke.py'
    Run docker exec -e "ZILCHO_PASSWORD=$password" --user 99:100 subs-check python /tmp/container_smoke.py prepare
    Run docker network disconnect bridge subs-check
    Run docker exec -e "ZILCHO_PASSWORD=$password" --user 99:100 subs-check python /tmp/container_smoke.py process
    Get-Content "$tree\tools\capture_native.py" -Raw | docker exec -i --user 99:100 subs-check sh -c 'cat > /tmp/capture_native.py'
    Run docker exec --user 99:100 subs-check python /tmp/capture_native.py /tmp/native-linkage.json
    Step 'Collect license evidence from the image'
    Run docker cp subs-check:/usr/share/zilcho-subs/. $evidence
    # /tmp is an in-memory mount, which docker cp can't read; cmd keeps the bytes unchanged.
    cmd /c "docker exec subs-check cat /tmp/native-linkage.json > `"$evidence\native-linkage.json`""
    if ($LASTEXITCODE) { throw 'Could not read native-linkage.json' }
  }
  finally { Cleanup }
  foreach ($need in 'dependencies\inventory.json', 'dependencies\ffmpeg-L.txt', 'dependencies\python\faster-whisper') {
    if (-not (Test-Path "$evidence\$need")) { throw "License evidence missing: $need" }
  }

  Step 'Software list (SBOM), same Syft version as CI'
  $sbom = docker run --rm -v //var/run/docker.sock:/var/run/docker.sock anchore/syft:v1.57.1 zilcho-subs:check -o spdx-json -q
  if ($LASTEXITCODE) { throw 'SBOM generation failed' }
  [IO.File]::WriteAllText("$evidence\sbom.spdx.json", ($sbom -join "`n"))

  Step 'Bind evidence to this image and commit'
  Run $python "$tree\tools\bind_evidence.py" $evidence zilcho-subs:check $sha

  Step 'Prepare the paired candidate: image + matching sources (downloads upstream sources)'
  Push-Location $tree
  try { Run $python tools\prepare_release.py --evidence $evidence --destination $out --image zilcho-subs:check --commit $sha --version $label }
  finally { Pop-Location }

  Step 'Verify the saved files'
  foreach ($line in Get-Content "$out\SHA256SUMS") {
    $hash, $file = $line -split '  ', 2
    if ((Get-FileHash "$out\$file" -Algorithm SHA256).Hash.ToLower() -ne $hash) { throw "Hash check failed: $file" }
  }
  $size = [math]::Round(((Get-ChildItem $out -File | Measure-Object Length -Sum).Sum) / 1MB)
  Write-Host "Saved $out ($size MB)"

  if ($CopyTo) {
    Step "Copy to $CopyTo"
    $dest = Join-Path $CopyTo "release-$label"
    if (Test-Path $dest) { throw "Refusing to overwrite $dest" }
    New-Item -ItemType Directory $dest | Out-Null
    Copy-Item "$out\*" $dest
    foreach ($line in Get-Content "$dest\SHA256SUMS") {
      $hash, $file = $line -split '  ', 2
      if ((Get-FileHash "$dest\$file" -Algorithm SHA256).Hash.ToLower() -ne $hash) { throw "Copy check failed: $file" }
    }
    Write-Host "Copied and verified: $dest"
  }
  Write-Host ''
  Write-Host "Done: $label"
}
finally {
  Quiet git -C $repo worktree remove --force $tree
  if (Test-Path $evidence) { Remove-Item $evidence -Recurse -Force -Confirm:$false }
}
