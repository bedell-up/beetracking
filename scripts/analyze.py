"""
Bee Movement Analysis Script
==============================
Reads detection logs from Site A and Site B and produces:
  1. Movement summary — bees confirmed at both sites
  2. Per-site visit counts
  3. Timeline of detections per individual
  4. Summary statistics suitable for a methods/results section

Usage:
    python analyze.py

Input:  ../data/site_A_detections.csv
        ../data/site_B_detections.csv
        ../tags/tag_manifest.csv  (optional — adds bee metadata)

Output: ../data/analysis_results.csv
        ../data/movement_summary.txt  (copy-paste ready for publication)

Requirements:
    pip install pandas
"""

import pandas as pd
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
TAGS_DIR = os.path.join(BASE_DIR, "..", "tags")


def load_data():
    """Load detection CSVs from both sites."""
    dfs = []
    for site in ["A", "B"]:
        path = os.path.join(DATA_DIR, f"site_{site}_detections.csv")
        if os.path.exists(path):
            df = pd.read_csv(path)
            df["timestamp"] = pd.to_datetime(df["date"] + " " + df["time"])
            dfs.append(df)
            print(f"[LOAD] Site {site}: {len(df)} detections")
        else:
            print(f"[WARN] No data file found for Site {site} at {path}")

    if not dfs:
        print("[ERROR] No data files found. Run detect.py at each site first.")
        return None

    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    return combined


def load_manifest():
    """Load optional tag manifest for bee metadata."""
    manifest_path = os.path.join(TAGS_DIR, "tag_manifest.csv")
    if os.path.exists(manifest_path):
        df = pd.read_csv(manifest_path)
        df = df[df["capture_date"] != ""]  # only rows with actual data
        print(f"[LOAD] Manifest: {len(df)} tagged bees")
        return df
    return None


def analyze(combined, manifest=None):
    results = {}

    # ── Per-site visit counts ──────────────────────────────────────────────
    site_counts = combined.groupby(["tag_id", "site"]).size().unstack(fill_value=0)
    for site in ["A", "B"]:
        if site not in site_counts.columns:
            site_counts[site] = 0
    site_counts = site_counts.rename(columns={"A": "visits_site_A", "B": "visits_site_B"})

    # ── Movement classification ────────────────────────────────────────────
    site_counts["seen_at_A"] = site_counts["visits_site_A"] > 0
    site_counts["seen_at_B"] = site_counts["visits_site_B"] > 0
    site_counts["moved_between_sites"] = site_counts["seen_at_A"] & site_counts["seen_at_B"]
    site_counts["site_fidelity"] = site_counts.apply(
        lambda r: "Both" if r["moved_between_sites"]
        else ("A only" if r["seen_at_A"] else "B only"),
        axis=1
    )

    # ── First and last detection per bee ──────────────────────────────────
    first_last = combined.groupby("tag_id").agg(
        first_detection=("timestamp", "min"),
        last_detection=("timestamp", "max"),
        total_detections=("tag_id", "count")
    )

    # ── Combine ────────────────────────────────────────────────────────────
    summary = site_counts.join(first_last)

    # Optional: join bee metadata from manifest
    if manifest is not None:
        manifest_indexed = manifest.set_index("tag_id")[
            ["capture_site", "capture_date", "bee_sex", "bee_size_estimate", "notes"]
        ]
        summary = summary.join(manifest_indexed, how="left")

    # ── Inter-site timing — for bees that moved ────────────────────────────
    movers = summary[summary["moved_between_sites"]].index.tolist()
    inter_site_times = []

    for tag_id in movers:
        bee_data = combined[combined["tag_id"] == tag_id].sort_values("timestamp")
        site_changes = bee_data[bee_data["site"] != bee_data["site"].shift()]
        if len(site_changes) >= 2:
            for i in range(len(site_changes) - 1):
                t1 = site_changes.iloc[i]["timestamp"]
                t2 = site_changes.iloc[i + 1]["timestamp"]
                delta_min = (t2 - t1).total_seconds() / 60
                inter_site_times.append({
                    "tag_id": tag_id,
                    "from_site": site_changes.iloc[i]["site"],
                    "to_site": site_changes.iloc[i + 1]["site"],
                    "elapsed_minutes": round(delta_min, 1)
                })

    inter_site_df = pd.DataFrame(inter_site_times) if inter_site_times else pd.DataFrame()
    results["summary"] = summary
    results["inter_site_movements"] = inter_site_df

    return results


def print_report(results, combined):
    summary = results["summary"]
    inter = results["inter_site_movements"]

    total_bees = len(summary)
    only_a = (summary["site_fidelity"] == "A only").sum()
    only_b = (summary["site_fidelity"] == "B only").sum()
    both = (summary["site_fidelity"] == "Both").sum()

    print("\n" + "═" * 60)
    print("  BEE MOVEMENT ANALYSIS REPORT")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 60)

    print(f"\n  Total unique tagged bees detected : {total_bees}")
    print(f"  Site A only                       : {only_a}  ({100*only_a/total_bees:.1f}%)")
    print(f"  Site B only                       : {only_b}  ({100*only_b/total_bees:.1f}%)")
    print(f"  Detected at BOTH sites            : {both}  ({100*both/total_bees:.1f}%)")

    print(f"\n  Total detection events            : {len(combined)}")
    print(f"  Site A events                     : {(combined['site']=='A').sum()}")
    print(f"  Site B events                     : {(combined['site']=='B').sum()}")

    if not inter.empty:
        print(f"\n  Inter-site movements recorded     : {len(inter)}")
        print(f"  Median transit time (min)         : {inter['elapsed_minutes'].median():.1f}")
        print(f"  Fastest transit (min)             : {inter['elapsed_minutes'].min():.1f}")
        print(f"  Slowest transit (min)             : {inter['elapsed_minutes'].max():.1f}")

    if both > 0:
        print(f"\n  ── Bees confirmed at both sites ──")
        movers = summary[summary["moved_between_sites"]][
            ["visits_site_A", "visits_site_B", "total_detections", "first_detection", "last_detection"]
        ]
        print(movers.to_string())

    # ── Publication-ready text ─────────────────────────────────────────────
    pub_text = f"""
──────────────────────────────────────────────────────────────
DRAFT RESULTS TEXT (edit as needed for publication)
──────────────────────────────────────────────────────────────
Of {total_bees} individually tagged Bombus vosnesenskii workers
detected across both study sites, {both} individuals ({100*both/total_bees:.1f}%)
were recorded at both Green Space A and Green Space B,
confirming inter-site movement across the ~0.25-mile corridor
with approximately 180 ft of elevation change. {only_a} individuals
({100*only_a/total_bees:.1f}%) were detected exclusively at Site A, and {only_b}
({100*only_b/total_bees:.1f}%) exclusively at Site B.
"""
    if not inter.empty:
        pub_text += f"""
Inter-site transit times ranged from {inter['elapsed_minutes'].min():.1f} to
{inter['elapsed_minutes'].max():.1f} minutes (median: {inter['elapsed_minutes'].median():.1f} min,
n={len(inter)} recorded movements), suggesting active foraging
movement between the two habitat patches.
"""
    pub_text += "──────────────────────────────────────────────────────────────\n"
    print(pub_text)

    return pub_text


def save_outputs(results, pub_text):
    os.makedirs(DATA_DIR, exist_ok=True)

    # Full summary CSV
    out_csv = os.path.join(DATA_DIR, "analysis_results.csv")
    results["summary"].to_csv(out_csv)
    print(f"[SAVED] {out_csv}")

    # Inter-site movements CSV
    if not results["inter_site_movements"].empty:
        inter_csv = os.path.join(DATA_DIR, "inter_site_movements.csv")
        results["inter_site_movements"].to_csv(inter_csv, index=False)
        print(f"[SAVED] {inter_csv}")

    # Publication text
    txt_path = os.path.join(DATA_DIR, "movement_summary.txt")
    with open(txt_path, "w") as f:
        f.write(pub_text)
    print(f"[SAVED] {txt_path}")


if __name__ == "__main__":
    print("\nBee Movement Analysis")
    print("─" * 40)

    combined = load_data()
    if combined is None:
        exit(1)

    manifest = load_manifest()
    results = analyze(combined, manifest)
    pub_text = print_report(results, combined)
    save_outputs(results, pub_text)
