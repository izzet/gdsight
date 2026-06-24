#!/usr/bin/env bash
# overhead_bench.sh — end-to-end DataCrumbs tracer overhead (Table C, §5.x).
# Matrix: I/O size sweep in x4 steps (4K,16K,64K,256K,1M,4M) x {sequential, random}
# read, fixed 4 threads, fixed 256 MiB transferred per run. For each cell: N baseline
# (stock gdsio) vs N traced (datacrumbs_track'd gdsio under datacrumbs_run, full eBPF
# tracer attached+firing). Metric = gdsio's OWN reported Throughput (measured inside its
# I/O loop, where probes fire synchronously) — NOT wall-clock (which would include the
# fixed ~0.5s server start/stop). Cold cache before every iter. Reports mean ± sample-std.
#
# Overhead is independent of the trace-write flush bug: probes attach & fire during the
# run regardless of whether the .pfw.gz flushes on stop (verified via bpftool run_cnt).
set -u
PREFIX=$HOME/dc-prefix
CUDA=/usr/local/cuda-12.6
GDSIO=$CUDA/gds/tools/gdsio
TGDSIO=/tmp/gdsio_dc
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
S=${S:-256M}                  # bytes transferred per run
THREADS=${THREADS:-4}
N=${N:-5}
SIZES=(4K 16K 64K 256K 1M 4M) # x4 steps from 4K
PATTERNS=${PATTERNS:-seq rand} # which patterns to run this invocation
TAG=${TAG:-}                   # filename suffix to avoid clobbering prior runs
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
OUT=/home/cc/projects/gdstrace/results/step5-overhead
mkdir -p "$OUT"; RAW="$OUT/overhead_raw${TAG}.txt"; TAB="$OUT/overhead${TAG}.txt"; : >"$RAW"

# 2 GiB backing file (room for random spread); recreate if missing/too small
if [ ! -f "$F" ] || [ "$(stat -c%s "$F")" -lt 2147483648 ]; then
  dd if=/dev/zero of="$F" bs=1M count=2048 oflag=direct status=none
fi
[ -x "$TGDSIO" ] || { cp "$GDSIO" "$TGDSIO"; datacrumbs_track --executable "$TGDSIO" >/dev/null 2>&1; }

dc_reset(){ sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1; sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1; true; }
drop(){ sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }
bw_of(){ grep -oE 'Throughput: [0-9.]+' <<<"$1" | grep -oE '[0-9.]+'; }
# mean + sample std (N-1) from stdin values
stats(){ awk '{x[NR]=$1;s+=$1} END{m=s/NR; for(i=1;i<=NR;i++)v+=(x[i]-m)^2; sd=(NR>1)?sqrt(v/(NR-1)):0; printf "%.4f %.4f",m,sd}'; }

bench(){  # $1=size $2=patname $3=Iflag(0 seq|2 rand)
  local sz="$1" pat="$2" I="$3" bbw=() tbw=()
  for _ in $(seq 1 "$N"); do drop; local o; o=$("$GDSIO" -f "$F" -d 0 -w "$THREADS" -s "$S" -i "$sz" -I "$I" -x 0 2>&1); bbw+=("$(bw_of "$o")"); done
  for _ in $(seq 1 "$N"); do dc_reset; drop; local o; o=$(datacrumbs_run --app "$TGDSIO -f $F -d 0 -w $THREADS -s $S -i $sz -I $I -x 0" 2>/dev/null); tbw+=("$(bw_of "$o")"); done
  local bm bs tm ts; read -r bm bs < <(printf '%s\n' "${bbw[@]}" | stats); read -r tm ts < <(printf '%s\n' "${tbw[@]}" | stats)
  local ov; ov=$(awk -v b="$bm" -v t="$tm" 'BEGIN{printf "%.1f",(b>0?(b-t)/b*100:0)}')
  printf "%-5s | %-4s | %8.3f ± %-6.3f | %8.3f ± %-6.3f | %5s%%\n" "$sz" "$pat" "$bm" "$bs" "$tm" "$ts" "$ov" | tee -a "$TAB"
  { echo "$sz $pat baseline: ${bbw[*]}"; echo "$sz $pat traced:   ${tbw[*]}"; } >>"$RAW"
}

{
echo "=== DataCrumbs tracer overhead — gdsio, ${S} xfer, ${THREADS} threads, N=${N}, cold cache — $(date -u) ==="
echo "size  | pat  | baseline GiB/s ± sd   | traced GiB/s ± sd     | overhead"
echo "------+------+-----------------------+-----------------------+---------"
} | tee "$TAB"
for sz in "${SIZES[@]}"; do
  case " $PATTERNS " in *" seq "*) bench "$sz" seq 0;; esac
  case " $PATTERNS " in *" rand "*) bench "$sz" rand 2;; esac
done
dc_reset
echo "DONE → $TAB (raw: $RAW)"
