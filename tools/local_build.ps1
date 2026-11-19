<#
Builds a PRIVATE TEST image of Zilcho Subs on this Windows PC (Docker Desktop, WSL 2)
and runs the same checks GitHub CI runs. See tools/local_build.md.

  powershell -ExecutionPolicy Bypass -File tools\local_build.ps1 -Commit <sha> [-Name song-lyrics] [-CopyTo <folder>]

The image comes from a clean `git archive` of the commit, never the working folder.
Output: release-artifacts\zilcho-subs\<version>-local.<yyyyMMddHHmm>-private\ with
zilcho-subs-image.tar.gz (zilcho-subs:local + a versioned tag), PRIVATE-TEST-ONLY.md
and SHA256SUMS. -CopyTo also copies them into a new subfolder of that folder (e.g. a server share).
Nothing is installed on the server; the owner does that.
#>
param(
  [Parameter(Mandatory = $true)][string]$Commit,
  [string]$Name = 'test',
  [string]$CopyTo,
  [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
# Keep the repository's own line endings (Git for Windows converts to CRLF on export).
$env:GIT_CONFIG_COUNT = '2'
$env:GIT_CONFIG_KEY_0 = 'core.autocrlf'; $env:GIT_CONFIG_VALUE_0 = 'false'
$env:GIT_CONFIG_KEY_1 = 'core.eol'; $env:GIT_CONFIG_VALUE_1 = 'lf'
function Step($text) { Write-Host ''; Write-Host "== $text" }
function Run { & $args[0] @($args[1..($args.Count - 1)]); if ($LASTEXITCODE) { throw "Failed ($LASTEXITCODE): $($args -join ' ')" } }
# Best-effort cleanup: "no such container/volume" is fine. (Windows PowerShell turns any
# native stderr into a terminating error while ErrorActionPreference is Stop.)
function Quiet { $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'; try { & $args[0] @($args[1..($args.Count - 1)]) 2>&1 | Out-Null } finally { $ErrorActionPreference = $previous } }
function Cleanup { Quiet docker rm -f subs-check; Quiet docker volume rm subs-check-config; Quiet docker volume rm subs-check-media }

$repo = Split-Path $PSScriptRoot -Parent
$workspace = Split-Path $repo -Parent
$sha = (git -C $repo rev-parse --verify "$Commit^{commit}").Trim()
$short = $sha.Substring(0, 7)
$build = Join-Path $env:TEMP "zilcho-subs-build-$short"

Step "Clean copy of $short"
if (Test-Path $build) { Remove-Item $build -Recurse -Force -Confirm:$false }
New-Item -ItemType Directory $build | Out-Null
Run git -C $repo archive --format=tar $sha -o "$build.tar"
Run tar -xf "$build.tar" -C $build
Remove-Item "$build.tar"
$version = ((Get-Content "$build\zilcho_subs\__init__.py") -replace '.*"(.+)".*', '$1').Trim()
$stamp = Get-Date -Format 'yyyyMMddHHmm'
$label = "$version-local.$stamp-private"
Write-Host "Version $version, label $label"

if (-not $SkipBuild) {
  Step 'Build image (first build compiles native parts and takes a while)'
  Run docker build --target runtime -t zilcho-subs:check $build
}

Step 'Checks: locked-down start, sign-in required, file owner, version'
$password = 'local-check-' + [guid]::NewGuid().ToString('N')
Cleanup
try {
  # Like CI's host folder: the test media volume belongs to the app user (99:100), as a real share would.
  # It must not stay empty: Docker resets an empty volume's top folder to the image's (root) owner on every mount.
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
  $out = $probe | docker exec -i --user 99:100 subs-check python -
  if ($LASTEXITCODE) { throw 'Startup / sign-in check failed' }
  Write-Host $out
  if ($out -notmatch [regex]::Escape("version $version")) { throw "App reports the wrong version: $out" }
  $owner = (docker exec subs-check stat -c %u /config/queue.sqlite3).Trim()
  if ($owner -ne '99') { throw "queue.sqlite3 owner is $owner, expected 99" }
  Run docker exec --user 99:100 subs-check python -c 'import faster_whisper, zilcho_subs.local_packs'

  Step 'Checks: real speech + translation engines, then offline'
  Get-Content "$build\tools\container_smoke.py" -Raw | docker exec -i --user 99:100 subs-check sh -c 'cat > /tmp/container_smoke.py'
  Run docker exec -e "ZILCHO_PASSWORD=$password" --user 99:100 subs-check python /tmp/container_smoke.py prepare
  Run docker network disconnect bridge subs-check
  Run docker exec -e "ZILCHO_PASSWORD=$password" --user 99:100 subs-check python /tmp/container_smoke.py process
}
finally { Cleanup }

Step 'Save image (tested image, unchanged) and verify it restores'
$out = Join-Path $workspace "release-artifacts\zilcho-subs\$label"
if (Test-Path $out) { throw "Refusing to overwrite $out" }
New-Item -ItemType Directory $out | Out-Null
$id = (docker image inspect --format '{{.Id}}' zilcho-subs:check).Trim()
Run docker tag zilcho-subs:check "zilcho-subs:$label"
Run docker tag zilcho-subs:check zilcho-subs:local
$tar = Join-Path $out 'zilcho-subs-image.tar'
Run docker save -o $tar "zilcho-subs:$label" zilcho-subs:local
# gzip the image archive itself (docker load reads .tar.gz directly).
$in = [IO.File]::OpenRead($tar); $file = [IO.File]::Create("$tar.gz")
$gz = New-Object IO.Compression.GZipStream($file, [IO.Compression.CompressionLevel]::Optimal)
try { $in.CopyTo($gz) } finally { $gz.Dispose(); $file.Dispose(); $in.Dispose() }
Remove-Item $tar
Run docker image rm "zilcho-subs:$label" zilcho-subs:local
Run docker load -i "$tar.gz"
if ((docker image inspect --format '{{.Id}}' "zilcho-subs:$label").Trim() -ne $id) { throw 'Saved image does not restore to the same image ID' }
$size = [math]::Round((Get-Item "$tar.gz").Length / 1MB)
@"
# PRIVATE TEST BUILD: $label

Built on the owner's PC from commit $sha (clean git archive), not distributed.
No matching source package was made, so share it with nobody; install it only on
your own server. Anything shared with other people needs a paired release candidate
(tools/prepare_release.py).

Image ID: $id (restore verified). Size: $size MB.
Checks passed: locked-down start, sign-in required, file owner 99, version $version,
real speech + translation engines, offline processing.

Install: verify SHA256SUMS, stop Zilcho Subs and back up queue.sqlite3, then
  docker load -i zilcho-subs-image.tar.gz
and apply the Unraid template (image zilcho-subs:local).
"@ | Set-Content -Encoding utf8 "$out\PRIVATE-TEST-ONLY.md"
Push-Location $out
try {
  $lines = foreach ($f in 'zilcho-subs-image.tar.gz', 'PRIVATE-TEST-ONLY.md') { '{0}  {1}' -f (Get-FileHash $f -Algorithm SHA256).Hash.ToLower(), $f }
  $lines | Set-Content -Encoding ascii SHA256SUMS
}
finally { Pop-Location }
Write-Host "Saved $out ($size MB)"

if ($CopyTo) {
  Step "Copy to $CopyTo"
  $dest = Join-Path $CopyTo "$Name-$label"
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
