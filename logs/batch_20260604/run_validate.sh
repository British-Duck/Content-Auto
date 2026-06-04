#!/usr/bin/env bash
# Validation run for scheme-fix + spec-review loop + Pro reviewer (2026-06-04).
set -u
cd "C:/dev/Content Automation/Enhanced games"
LOGDIR="logs/batch_20260604"

declare -a NAMES=( "maradona_v2" "ftx_v2" )
declare -a BRIEFS=(
"1986 World Cup quarter-final, Argentina versus England. Diego Maradona punched the ball into the net with his hand and the referee allowed the goal. Four minutes later he scored the Goal of the Century, dribbling past five England players. Argentina won 2-1 and went on to win the World Cup. Use dark_purple, sports angle."
"FTX was one of the biggest crypto exchanges in the world, valued at \$32 billion. Its founder Sam Bankman-Fried was treated like a genius. In November 2022 a single report triggered a bank run and the entire company collapsed into bankruptcy within days. Billions in customer funds were missing. He was later sentenced to 25 years for fraud. Use dark_red, scandal angle."
)

for i in "${!NAMES[@]}"; do
  name="${NAMES[$i]}"
  brief="${BRIEFS[$i]}"
  echo "############################################################"
  echo "### VALIDATE $((i+1))/2: $name  $(date)"
  echo "############################################################"
  python orchestrate.py "$brief" -v --force-regen > "$LOGDIR/${name}.log" 2>&1
  echo "RUN $name exit code: $?"
  if [ -f logs/pipeline_report.json ]; then
    cp logs/pipeline_report.json "$LOGDIR/${name}_report.json"
  fi
done
echo "VALIDATION COMPLETE $(date)"
