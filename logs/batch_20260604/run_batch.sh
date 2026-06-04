#!/usr/bin/env bash
# Batch runner for the 5-topic generalisation test (2026-06-04).
set -u
cd "C:/dev/Content Automation/Enhanced games"
LOGDIR="logs/batch_20260604"

declare -a NAMES=( "theranos" "maradona" "apollo13" "ftx" "evergiven" )
declare -a BRIEFS=(
"Theranos claimed it could run hundreds of medical tests from a single drop of blood. The company hit a \$9 billion valuation and founder Elizabeth Holmes was called the next Steve Jobs. The technology never actually worked. In 2022 Holmes was convicted of fraud and sentenced to over 11 years in prison. Use dark_red, scandal angle."
"1986 World Cup quarter-final, Argentina versus England. Diego Maradona punched the ball into the net with his hand and the referee allowed the goal. Four minutes later he scored the Goal of the Century, dribbling past five England players. Argentina won 2-1 and went on to win the World Cup. Use dark_purple, sports angle."
"In April 1970, an oxygen tank exploded on Apollo 13 around 200,000 miles from Earth. The crew of three lost their main power and breathable air. NASA engineers improvised a way to use the lunar module as a lifeboat and rig a CO2 filter from spare parts. All three astronauts made it home alive. Use dark_blue, survival angle."
"FTX was one of the biggest crypto exchanges in the world, valued at \$32 billion. Its founder Sam Bankman-Fried was treated like a genius. In November 2022 a single report triggered a bank run and the entire company collapsed into bankruptcy within days. Billions in customer funds were missing. He was later sentenced to 25 years for fraud. Use dark_red, scandal angle."
"In March 2021, a single container ship called the Ever Given ran aground and wedged itself across the Suez Canal. The canal carries roughly 12% of global trade. For six days nearly \$10 billion of goods a day sat stuck while the world watched a digger try to free a 400-metre ship. Use dark_gold, news angle."
)

for i in "${!NAMES[@]}"; do
  name="${NAMES[$i]}"
  brief="${BRIEFS[$i]}"
  echo "############################################################"
  echo "### RUN $((i+1))/5: $name  $(date)"
  echo "############################################################"
  python orchestrate.py "$brief" -v > "$LOGDIR/${name}.log" 2>&1
  rc=$?
  echo "RUN $name exit code: $rc"
  # preserve the per-run pipeline report (orchestrate overwrites it each run)
  if [ -f logs/pipeline_report.json ]; then
    cp logs/pipeline_report.json "$LOGDIR/${name}_report.json"
  fi
done
echo "ALL RUNS COMPLETE $(date)"
