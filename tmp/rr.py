import os
import shlex
import subprocess
from pathlib import Path

from dotenv import load_dotenv


def str2bool(v):
    return str(v).lower() in ("1", "true", "yes", "on")


# ============================================================
# load env
# ============================================================

load_dotenv()

INPUT_FITS = os.getenv("INPUT_FITS")
OUTPUT_FITS = os.getenv("OUTPUT_FITS", "outputs/redrock.fits")
DETAILS_H5 = os.getenv("DETAILS_H5", "outputs/rrdetails.h5")

USE_ARCHETYPES = str2bool(os.getenv("USE_ARCHETYPES", "true"))
ARCHETYPE_DIR = os.getenv("ARCHETYPE_DIR")

NMINIMA = os.getenv("NMINIMA", "9")
NNEAREST = os.getenv("NNEAREST", "2")

PER_CAMERA = os.getenv("PER_CAMERA", "true")
NO_LEGENDRE = str2bool(os.getenv("NO_LEGENDRE", "false"))

LEGENDRE_DEGREE = os.getenv("LEGENDRE_DEGREE", "2")
LEGENDRE_PRIOR = os.getenv("LEGENDRE_PRIOR", "0.1")

OMP_NUM_THREADS = os.getenv("OMP_NUM_THREADS", "1")

RR_TEMPLATE_DIR = os.getenv("RR_TEMPLATE_DIR")

# ============================================================
# checks
# ============================================================

if INPUT_FITS is None:
    raise ValueError("INPUT_FITS is not set in .env")

if RR_TEMPLATE_DIR is None:
    raise ValueError("RR_TEMPLATE_DIR is not set in .env")

if USE_ARCHETYPES and ARCHETYPE_DIR is None:
    raise ValueError("ARCHETYPE_DIR is not set in .env")

Path("outputs").mkdir(exist_ok=True)

# ============================================================
# environment
# ============================================================

env = os.environ.copy()

env["OMP_NUM_THREADS"] = OMP_NUM_THREADS
env["RR_TEMPLATE_DIR"] = RR_TEMPLATE_DIR

# ============================================================
# build command
# ============================================================

cmd = [
    "rrdesi",
    "-i", INPUT_FITS,
    "-o", OUTPUT_FITS,
    "-d", DETAILS_H5,
    "--nminima", str(NMINIMA),
]

# ============================================================
# archetypes
# ============================================================

if USE_ARCHETYPES:

    cmd.extend([
        "--archetypes",
        ARCHETYPE_DIR,
    ])

    cmd.extend([
        "--archetype-nnearest",
        str(NNEAREST),
    ])

    cmd.extend([
        "--archetype-legendre-percamera",
        str(PER_CAMERA),
    ])

    if NO_LEGENDRE:
        cmd.append("--archetypes-no-legendre")

    else:
        cmd.extend([
            "--archetype-legendre-degree",
            str(LEGENDRE_DEGREE),
        ])

        cmd.extend([
            "--archetype-legendre-prior",
            str(LEGENDRE_PRIOR),
        ])

# ============================================================
# run
# ============================================================

print("=" * 80)
print("Running redrock")
print("=" * 80)

print("\nCommand:\n")
print(" ".join(shlex.quote(x) for x in cmd))

print("\n")

result = subprocess.run(
    cmd,
    env=env,
    text=True,
)

print("\n")

if result.returncode == 0:
    print("=" * 80)
    print("Redrock finished successfully")
    print("=" * 80)

    print(f"\nOutput FITS : {OUTPUT_FITS}")
    print(f"Details H5  : {DETAILS_H5}")

else:
    print("=" * 80)
    print("Redrock failed")
    print("=" * 80)

    raise SystemExit(result.returncode)