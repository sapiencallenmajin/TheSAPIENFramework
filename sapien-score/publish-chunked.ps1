<#
.SYNOPSIS
    Publish a SAPIEN v1.5 batch JSON file to the chunked ingest endpoint.

.DESCRIPTION
    Splits the batch's results[] array into chunks, then POSTs them sequentially:
      - Chunk 1:     full run metadata + first N scenarios            → captures run_id
      - Chunks 2..N-1: { run_id, chunk_info } + next N scenarios      → append
      - Chunk N:     { run_id, chunk_info } + last scenarios + aggregates → finalize

    On any chunk failure the script halts and reports the failing chunk.
    No automatic retry — user decides whether to re-run (only chunk 1 is safely
    re-runnable; mid-chunks would duplicate scenario_results under the current
    server contract).

.PARAMETER BatchPath
    Absolute or relative path to the batch JSON file.

.PARAMETER AuthToken
    SAPIEN_INGEST_API_KEY bearer token.

.PARAMETER Endpoint
    Full URL of the ingest endpoint, e.g. https://sapien-framework.com/api/ingest-results

.PARAMETER RunLabel
    Human-readable run label stored on runs.run_label (e.g. "v1.5-batch-2026-04-19").
    Supplied at publish time; not read from the batch JSON.

.PARAMETER JudgeModel
    Judge model identifier stored on runs.judge_model (e.g. "bedrock/amazon.nova-pro-v1:0").

.PARAMETER JudgeFamily
    Judge family name stored on runs.judge_family (e.g. "Amazon", "OpenAI").

.PARAMETER ChunkSize
    Number of scenarios per chunk. Default 25. Server has no contract — any size
    works as long as chunk_index values sum correctly to total_chunks.

.PARAMETER DryRun
    If set, prints the chunk plan and first chunk's payload summary but does NOT
    make any HTTP calls.

.EXAMPLE
    .\publish-chunked.ps1 `
        -BatchPath .\batch-2026-04-19-gpt-5-4.json `
        -AuthToken $env:SAPIEN_INGEST_API_KEY `
        -Endpoint https://sapienframework.org/api/ingest-results `
        -RunLabel "v1.5-batch-2026-04-19" `
        -JudgeModel "bedrock/amazon.nova-pro-v1:0" `
        -JudgeFamily "Amazon"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BatchPath,

    [Parameter(Mandatory = $true)]
    [string]$AuthToken,

    [Parameter(Mandatory = $true)]
    [string]$Endpoint,

    [Parameter(Mandatory = $true)]
    [string]$RunLabel,

    [Parameter(Mandatory = $true)]
    [string]$JudgeModel,

    [Parameter(Mandatory = $true)]
    [string]$JudgeFamily,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 200)]
    [int]$ChunkSize = 25,

    [Parameter(Mandatory = $false)]
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# ─── Load and validate batch ────────────────────────────────────────────────

if (-not (Test-Path -LiteralPath $BatchPath)) {
    Write-Error "Batch file not found: $BatchPath"
    exit 1
}

Write-Host "Reading $BatchPath..." -ForegroundColor Cyan
$raw = Get-Content -LiteralPath $BatchPath -Raw -Encoding UTF8
$batch = $raw | ConvertFrom-Json -Depth 50

$required = @('model', 'results')
foreach ($field in $required) {
    if (-not $batch.PSObject.Properties[$field]) {
        Write-Error "Batch missing required field: $field"
        exit 1
    }
}

$results = @($batch.results)
if ($results.Count -eq 0) {
    Write-Error "Batch has empty results[] array"
    exit 1
}

$totalScenarios = $results.Count
$totalChunks = [math]::Ceiling($totalScenarios / $ChunkSize)

if ($totalChunks -lt 2) {
    Write-Warning "Batch has $totalScenarios scenarios with ChunkSize=$ChunkSize → only 1 chunk needed. Chunked endpoint requires total_chunks >= 2. Use the regular (non-chunked) publish path instead, or lower -ChunkSize."
    exit 1
}

Write-Host ""
Write-Host "Batch:          $BatchPath" -ForegroundColor White
Write-Host "Model:          $($batch.model)"
Write-Host "Run label:      $RunLabel"
Write-Host "Judge:          $JudgeModel ($JudgeFamily)"
Write-Host "Schema version: $($batch.schema_version)"
Write-Host "Scenarios:      $totalScenarios"
Write-Host "Chunk size:     $ChunkSize"
Write-Host "Total chunks:   $totalChunks"
Write-Host "Endpoint:       $Endpoint"
Write-Host ""

if ($DryRun) {
    Write-Host "[DryRun] No HTTP calls will be made." -ForegroundColor Yellow
    for ($i = 1; $i -le $totalChunks; $i++) {
        $start = ($i - 1) * $ChunkSize
        $end = [math]::Min($start + $ChunkSize - 1, $totalScenarios - 1)
        Write-Host "  Chunk $i/$totalChunks : scenarios[$start..$end] ($($end - $start + 1) items)"
    }
    exit 0
}

# ─── Build run-level metadata (sent only on chunk 1) ────────────────────────

function Copy-OptionalField {
    param($SourceObject, [string]$FieldName, $Target)
    if ($SourceObject.PSObject.Properties[$FieldName]) {
        $Target | Add-Member -MemberType NoteProperty -Name $FieldName -Value $SourceObject.$FieldName -Force
    }
}

$runMeta = [ordered]@{
    model          = $batch.model
    judge_model    = $JudgeModel
    judge_family   = $JudgeFamily
    run_label      = $RunLabel
    schema_version = if ($batch.PSObject.Properties['schema_version']) { $batch.schema_version } else { 2 }
    is_primary     = if ($batch.PSObject.Properties['is_primary']) { [bool]$batch.is_primary } else { $false }
}
$runMetaObj = [pscustomobject]$runMeta

# Forward all optional run-level fields the endpoint knows about.
# scoring_mode / council_size / council_seats_min are REQUIRED for council v2
# runs to be labeled correctly — without them the endpoint defaults the run to
# 'single' even when every scenario was council-scored.
foreach ($field in @(
    'prompt_version', 'framework_version', 'total_cost_usd',
    'overall_health', 'mean_health', 'p10_health',
    'publisher', 'owner_id',
    'scoring_mode', 'council_size', 'council_seats_min'
)) {
    Copy-OptionalField -SourceObject $batch -FieldName $field -Target $runMetaObj
}

# ─── Send chunks ────────────────────────────────────────────────────────────

$headers = @{
    'Authorization' = "Bearer $AuthToken"
    'Content-Type'  = 'application/json'
}

$runId = $null

for ($i = 1; $i -le $totalChunks; $i++) {
    $startIdx = ($i - 1) * $ChunkSize
    $endIdx = [math]::Min($startIdx + $ChunkSize - 1, $totalScenarios - 1)
    $chunkResults = @($results[$startIdx..$endIdx])

    $chunkInfo = [ordered]@{
        chunk_index  = $i
        total_chunks = $totalChunks
    }
    if ($i -gt 1) {
        if (-not $runId) {
            Write-Error "Lost run_id after chunk 1 — cannot continue."
            exit 1
        }
        $chunkInfo['run_id'] = $runId
    }

    $payload = [ordered]@{
        results    = $chunkResults
        chunk_info = $chunkInfo
    }

    # Chunk 1: attach run metadata. Last chunk: attach aggregates.
    if ($i -eq 1) {
        foreach ($prop in $runMetaObj.PSObject.Properties) {
            $payload[$prop.Name] = $prop.Value
        }
    }
    if ($i -eq $totalChunks) {
        foreach ($field in @(
            'risk_summary', 'overall_health', 'mean_health', 'p10_health'
        )) {
            if ($batch.PSObject.Properties[$field]) {
                $payload[$field] = $batch.$field
            }
        }
    }

    $body = $payload | ConvertTo-Json -Depth 50 -Compress
    $bodyKB = [math]::Round($body.Length / 1024, 1)

    Write-Host "→ POST chunk $i/$totalChunks  ($($chunkResults.Count) scenarios, ${bodyKB} KB)" -ForegroundColor Cyan

    try {
        $response = Invoke-RestMethod `
            -Method Post `
            -Uri $Endpoint `
            -Headers $headers `
            -Body $body `
            -ContentType 'application/json' `
            -TimeoutSec 120
    }
    catch {
        $statusCode = $null
        $responseBody = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            try {
                $stream = $_.Exception.Response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($stream)
                $responseBody = $reader.ReadToEnd()
            }
            catch { $responseBody = '<could not read response body>' }
        }

        Write-Host ""
        Write-Host "✗ Chunk $i/$totalChunks FAILED" -ForegroundColor Red
        Write-Host "  HTTP status:   $statusCode" -ForegroundColor Red
        Write-Host "  Response body: $responseBody" -ForegroundColor Red
        Write-Host "  Error message: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""
        if ($runId) {
            Write-Host "  run_id so far: $runId" -ForegroundColor Yellow
            Write-Host "  Run is in a non-finalized state. The scoreboard will NOT show it." -ForegroundColor Yellow
            Write-Host "  Do NOT naively retry this chunk — duplicate scenario_results will be inserted." -ForegroundColor Yellow
            Write-Host "  Diagnose the error, then manually decide: abandon (orphan run) or DB-surgery to resume." -ForegroundColor Yellow
        }
        exit 1
    }

    if ($i -eq 1) {
        if (-not $response.run_id) {
            Write-Error "Chunk 1 response missing run_id. Response: $($response | ConvertTo-Json -Compress)"
            exit 1
        }
        $runId = $response.run_id
        Write-Host "  ✓ run_id = $runId" -ForegroundColor Green
    }
    else {
        Write-Host "  ✓ $($response | ConvertTo-Json -Compress)" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "✓ Published $totalScenarios scenarios in $totalChunks chunks." -ForegroundColor Green
Write-Host "  run_id:    $runId"
Write-Host "  run_label: $($batch.run_label)"
Write-Host "  model:     $($batch.model)"
Write-Host ""
Write-Host "Verify on scoreboard: the run should now appear with finalized=true." -ForegroundColor Cyan
