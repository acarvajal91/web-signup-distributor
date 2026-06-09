"""
MQL Distribution Algorithm
Deficit-driven greedy: guarantees max diff of 1 MQL between any two reps,
both globally and within each category.
"""

def distribute(mqls: list[dict], present_reps: list[str], day_offset: int = 0) -> list[dict]:
    """
    Assign MQLs to reps fairly.

    Args:
        mqls: list of dicts with keys: vendor_id, vendor_name, category,
              region, phone, mail
        present_reps: ordered list of rep names working today
        day_offset: rotates which rep gets the "extra" MQL each day

    Returns:
        Same list with 'assigned_rep' key added to each MQL.
    """
    if not present_reps or not mqls:
        return mqls

    n = len(present_reps)
    total = len(mqls)
    target = total / n

    global_count = {r: 0 for r in present_reps}

    # Group by category, sort for determinism
    cats: dict[str, list] = {}
    for m in mqls:
        cats.setdefault(m["category"], []).append(m)

    result = []

    for cat in sorted(cats.keys()):
        items = cats[cat]
        cat_count = {r: 0 for r in present_reps}

        for item in items:
            # Step 1: reps with fewest in this category
            min_cat = min(cat_count[r] for r in present_reps)
            eligible = [r for r in present_reps if cat_count[r] == min_cat]

            # Step 2: among eligible, pick highest global deficit
            max_deficit = max(target - global_count[r] for r in eligible)
            candidates = [r for r in eligible
                          if abs((target - global_count[r]) - max_deficit) < 1e-9]

            # Step 3: break ties by daily rotation offset
            candidates.sort(key=lambda r: (present_reps.index(r) + n - day_offset) % n)

            pick = candidates[0]
            cat_count[pick] += 1
            global_count[pick] += 1
            result.append({**item, "assigned_rep": pick})

    return result


def fairness_report(assigned: list[dict], reps: list[str]) -> dict:
    """Returns a summary dict for display."""
    by_rep = {r: 0 for r in reps}
    by_rep_cat: dict[str, dict[str, int]] = {r: {} for r in reps}

    for m in assigned:
        r = m["assigned_rep"]
        by_rep[r] = by_rep.get(r, 0) + 1
        by_rep_cat[r][m["category"]] = by_rep_cat[r].get(m["category"], 0) + 1

    counts = list(by_rep.values())
    cats = sorted({m["category"] for m in assigned})
    cat_diffs = []
    for c in cats:
        vals = [by_rep_cat[r].get(c, 0) for r in reps if r in by_rep]
        if vals:
            cat_diffs.append(max(vals) - min(vals))

    return {
        "by_rep": by_rep,
        "by_rep_cat": by_rep_cat,
        "total_diff": max(counts) - min(counts) if counts else 0,
        "max_cat_diff": max(cat_diffs) if cat_diffs else 0,
        "categories": cats,
    }
