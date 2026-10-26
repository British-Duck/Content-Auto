import logging
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

import config
DEMO = config.ROOT / "output" / "ladder-still-demo"
DEMO.mkdir(parents=True, exist_ok=True)
config.OUTPUT_DIR = DEMO
config.SPECS_DIR = DEMO
import orchestrate
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S")
brief = ("In April 2019 the Event Horizon Telescope released the first ever image of a "
    "black hole, at the centre of galaxy M87, 55 million light-years away. The black "
    "hole is 6.5 billion times the mass of the Sun. The image was assembled from data "
    "recorded by eight radio telescopes across the globe - about 5 petabytes, too much "
    "to send over the internet, so the hard drives were flown by plane. "
    "Use dark_blue, reveal angle.")
summary = orchestrate.run(brief, force_regen=True)
print("OUTPUT:", summary.get("output"))
print("RUN RECORD:", summary.get("run_record"))
