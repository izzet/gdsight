#!/usr/bin/env bash
# amp_perf.sh — does command over-splitting (block max_sectors_kb soft-cap < hw) COST anything?
# For sizes that split (>1.25 MiB) measure UNTRACED gdsio throughput + per-op latency under
# max_sectors_kb in {1280 (default, more cmds), 2048 (=hw, fewer cmds)} x concurrency. A_cmd is
# the validated law ceil(size/cap). Perf measured WITHOUT the tracer (true cost, not traced cost).
set -u
CUDA=/usr/local/cuda-12.6; GDSIO=$CUDA/gds/tools/gdsio
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
DEV=nvme1n1; Q=/sys/block/$DEV/queue/max_sectors_kb
S=${S:-512M}; N=${N:-5}
SIZES=(${SIZES:-1M 4M 8M 16M}); CAPS=(${CAPS:-1280 2048}); THREADS=(${THREADS:-1 4})
OUT=/home/cc/projects/gdstrace/results/xlayer; mkdir -p "$OUT"; CSV="$OUT/amp_perf.csv"
echo "size,threads,cap_kb,A_cmd,GiBps_mean,GiBps_sd,lat_us_mean,lat_us_sd" > "$CSV"
[ -f "$F" ] || dd if=/dev/zero of="$F" bs=1M count=2048 oflag=direct status=none
sz_kb(){ case "$1" in *M) echo $(( ${1%M}*1024 ));; *K) echo ${1%K};; esac; }
drop(){ sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }
stat(){ awk '{x[NR]=$1;s+=$1} END{m=s/NR; for(i=1;i<=NR;i++)v+=(x[i]-m)^2; sd=(NR>1)?sqrt(v/(NR-1)):0; printf "%.3f %.3f",m,sd}'; }
orig=$(cat "$Q")
for cap in "${CAPS[@]}"; do
  echo "$cap" | sudo tee "$Q" >/dev/null
  for th in "${THREADS[@]}"; do
    for sz in "${SIZES[@]}"; do
      acmd=$(( ( $(sz_kb "$sz") + cap - 1 ) / cap ))
      bw=(); lat=()
      for _ in $(seq 1 "$N"); do
        drop
        o=$("$GDSIO" -f "$F" -d 0 -w "$th" -s "$S" -i "$sz" -I 0 -x 0 2>&1)
        bw+=("$(grep -oE 'Throughput: [0-9.]+' <<<"$o" | grep -oE '[0-9.]+')")
        lat+=("$(grep -oE 'Avg_Latency: [0-9.]+' <<<"$o" | grep -oE '[0-9.]+')")
      done
      read -r bm bs < <(printf '%s\n' "${bw[@]}" | stat)
      read -r lm ls < <(printf '%s\n' "${lat[@]}" | stat)
      printf "%-4s th=%s cap=%-4s A_cmd=%-2s | %7.3f ± %-5.3f GiB/s | %9.1f ± %-7.1f us\n" \
        "$sz" "$th" "$cap" "$acmd" "$bm" "$bs" "$lm" "$ls"
      echo "$sz,$th,$cap,$acmd,$bm,$bs,$lm,$ls" >> "$CSV"
    done
  done
done
echo "$orig" | sudo tee "$Q" >/dev/null; echo "restored max_sectors_kb=$(cat "$Q")"
echo "DONE → $CSV"
