# Prompt Group — 5-topic generalisation test (2026-06-04)

Five briefs run end-to-end through `orchestrate.py … -v`, deliberately spread across
domains to test generalisation. Stored together so the set can be re-run as a group.
Re-run any one with: `python orchestrate.py "<brief>" -v`

| # | Key | Requested scheme | Angle | Output file |
|---|-----|------------------|-------|-------------|
| 1 | theranos  | dark_red    | scandal  | output/theranos-elizabeth-holmes-fraud.mp4 |
| 2 | maradona  | dark_purple | sports   | output/maradona-hand-of-god-1986.mp4 |
| 3 | apollo13  | dark_blue   | survival | output/apollo-13-successful-failure.mp4 |
| 4 | ftx       | dark_red    | scandal  | output/ftx-collapse-sam-bankman-fried.mp4 |
| 5 | evergiven | dark_gold   | news     | output/ever-given-suez-canal-blockage.mp4 |

## Prompts

### 1. theranos
> Theranos claimed it could run hundreds of medical tests from a single drop of blood. The company hit a $9 billion valuation and founder Elizabeth Holmes was called the next Steve Jobs. The technology never actually worked. In 2022 Holmes was convicted of fraud and sentenced to over 11 years in prison. Use dark_red, scandal angle.

### 2. maradona
> 1986 World Cup quarter-final, Argentina versus England. Diego Maradona punched the ball into the net with his hand and the referee allowed the goal. Four minutes later he scored the Goal of the Century, dribbling past five England players. Argentina won 2-1 and went on to win the World Cup. Use dark_purple, sports angle.

### 3. apollo13
> In April 1970, an oxygen tank exploded on Apollo 13 around 200,000 miles from Earth. The crew of three lost their main power and breathable air. NASA engineers improvised a way to use the lunar module as a lifeboat and rig a CO2 filter from spare parts. All three astronauts made it home alive. Use dark_blue, survival angle.

### 4. ftx
> FTX was one of the biggest crypto exchanges in the world, valued at $32 billion. Its founder Sam Bankman-Fried was treated like a genius. In November 2022 a single report triggered a bank run and the entire company collapsed into bankruptcy within days. Billions in customer funds were missing. He was later sentenced to 25 years for fraud. Use dark_red, scandal angle.

### 5. evergiven
> In March 2021, a single container ship called the Ever Given ran aground and wedged itself across the Suez Canal. The canal carries roughly 12% of global trade. For six days nearly $10 billion of goods a day sat stuck while the world watched a digger try to free a 400-metre ship. Use dark_gold, news angle.
