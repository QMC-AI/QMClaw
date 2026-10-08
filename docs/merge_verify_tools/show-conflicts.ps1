$scratch = 'D:\semester4\QMClaw\.merge-scratch'
$files = Get-ChildItem $scratch -Filter 'm__*' | Where-Object { $_.Length -gt 0 }
foreach ($f in $files) {
  $name = $f.Name -replace '^m__', ''
  $lines = Get-Content $f.FullName -Encoding UTF8
  Write-Output ""
  Write-Output ("=" * 78)
  Write-Output "FILE: $name   (total $($lines.Count) lines)"
  Write-Output ("=" * 78)
  $i = 0
  $hunk = 0
  while ($i -lt $lines.Count) {
    if ($lines[$i] -like '<<<<<<<*') {
      $hunk++
      $start = $i
      $end = $i
      while ($end -lt $lines.Count -and -not ($lines[$end] -like '>>>>>>>*')) { $end++ }
      Write-Output ""
      Write-Output "--- conflict #$hunk  (merged-file lines $($start+1)..$($end+1)) ---"
      $ctx0 = [Math]::Max(0, $start - 4)
      for ($k = $ctx0; $k -lt $start; $k++) { Write-Output ("  ctx | " + $lines[$k]) }
      for ($k = $start; $k -le $end; $k++) {
        $mark = '     '
        if ($lines[$k] -like '<<<<<<<*') { $mark = 'OURS ' }
        elseif ($lines[$k] -like '|||||||*') { $mark = 'BASE ' }
        elseif ($lines[$k] -like '=======*') { $mark = 'SPLIT' }
        elseif ($lines[$k] -like '>>>>>>>*') { $mark = 'THEIR' }
        Write-Output ("  $mark| " + $lines[$k])
      }
      $ctx1 = [Math]::Min($lines.Count - 1, $end + 4)
      for ($k = $end + 1; $k -le $ctx1; $k++) { Write-Output ("  ctx | " + $lines[$k]) }
      $i = $end + 1
    } else { $i++ }
  }
}
