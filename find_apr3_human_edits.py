"""
Find all human edits on April 3rd, 2026 for MR Pipeline translation tasks.

Approach:
1. Fetch all MR summaries from /api/v1/dashboard/mrs
2. For each MR with cases, fetch detailed cases from /api/v1/dashboard/cases
3. Filter for fixed_at on 2026-04-03 (Language Lead fixes)
4. Also note any reviewer_comments
"""

import json
import os
import sys
import time
import requests

TRANZOR_URL = "http://tranzor-platform.int.rclabenv.com"
MR_API = f"{TRANZOR_URL}/api/v1"

TARGET_DATE = "2026-04-03"

session = requests.Session()
MAX_RETRIES = 3


def api_get(url, **kwargs):
    kwargs.setdefault("timeout", 30)
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  ⚠ Request timeout, retrying in {wait}s ({attempt+1}/{MAX_RETRIES})...")
                time.sleep(wait)
            else:
                raise


def fetch_all_mrs():
    """Fetch all MR summaries from dashboard"""
    all_mrs = []
    offset = 0
    batch_size = 100
    while True:
        data = api_get(f"{MR_API}/dashboard/mrs", params={
            "mr_limit": batch_size, "mr_offset": offset
        })
        mrs = data.get("mrs", [])
        if not mrs:
            break
        all_mrs.extend(mrs)
        has_more = data.get("has_more", False)
        if not has_more:
            break
        offset += batch_size
    return all_mrs


def fetch_mr_cases(project_id, mr_iid):
    """Fetch cases for a specific MR via dashboard/cases"""
    try:
        data = api_get(f"{MR_API}/dashboard/cases", params={
            "project_id": project_id,
            "mr_id": mr_iid,
            "mr_limit": 1,
        })
        mrs = data.get("mrs", [])
        if mrs:
            return mrs[0].get("cases", [])
        return []
    except Exception as e:
        print(f"  ⚠ Failed to fetch cases for {project_id} MR#{mr_iid}: {e}")
        return []


def fetch_all_tasks():
    """Fetch ALL MR pipeline tasks"""
    all_tasks = []
    for status_filter in ["completed", "running", "failed", "pending", "skipped"]:
        offset = 0
        batch_size = 200
        while True:
            data = api_get(f"{MR_API}/tasks", params={
                "status": status_filter, "limit": batch_size, "offset": offset
            })
            tasks = data.get("tasks", [])
            all_tasks.extend(tasks)
            total = data.get("total", 0)
            if not tasks or offset + batch_size >= total:
                break
            offset += batch_size
    return all_tasks


def main():
    print("=" * 70)
    print(f"Finding ALL human edits on {TARGET_DATE} for MR Pipeline tasks")
    print("=" * 70)

    # Step 1: Get all MR summaries
    print(f"\n📋 Step 1: Fetching MR summaries from dashboard...")
    mr_summaries = fetch_all_mrs()
    print(f"  Found {len(mr_summaries)} MRs total")

    # Step 2: For each MR, fetch cases and check for fixes on Apr 3
    human_edits = []
    comments_found = []
    total_cases_checked = 0

    print(f"\n📋 Step 2: Checking each MR for human edits...")
    for i, mr_info in enumerate(mr_summaries):
        project_id = mr_info.get("project_id", "")
        mr_iid = mr_info.get("mr_iid", "")
        case_count = mr_info.get("case_count", 0)

        if case_count == 0:
            continue

        cases = fetch_mr_cases(project_id, mr_iid)
        total_cases_checked += len(cases)

        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(mr_summaries)}] Checked {project_id} MR#{mr_iid} - {len(cases)} cases (total so far: {total_cases_checked})")

        for case in cases:
            fixed_at = case.get("fixed_at") or ""
            fixed_by = case.get("fixed_by_lead") or ""
            fixed_text = case.get("fixed_text") or ""
            comment = case.get("reviewer_comment") or ""

            # Check if fix was done on April 3
            if fixed_at and TARGET_DATE in fixed_at:
                human_edits.append({
                    "type": "FIX_TRANSLATION",
                    "project_id": project_id,
                    "mr_iid": mr_iid,
                    "mr_link": mr_info.get("mr_link", ""),
                    "opus_id": case.get("opus_id", ""),
                    "target_language": case.get("target_language", ""),
                    "source_text": case.get("source_text", ""),
                    "original_text": case.get("translated_text", ""),
                    "fixed_text": fixed_text,
                    "fixed_by": fixed_by,
                    "fixed_at": fixed_at,
                    "final_score": case.get("final_score"),
                    "error_category": case.get("error_category"),
                    "reason": case.get("reason"),
                })

            # Record any comments (no timestamp available)
            if comment:
                comments_found.append({
                    "project_id": project_id,
                    "mr_iid": mr_iid,
                    "opus_id": case.get("opus_id", ""),
                    "target_language": case.get("target_language", ""),
                    "reviewer_comment": comment,
                })

    # Step 3: Also get task list to cross-reference
    print(f"\n📋 Step 3: Fetching task list for context...")
    all_tasks = fetch_all_tasks()
    apr3_tasks = [
        t for t in all_tasks
        if (TARGET_DATE in (t.get("created_at") or "")) or
           (TARGET_DATE in (t.get("updated_at") or ""))
    ]
    print(f"  {len(apr3_tasks)} tasks created/updated on {TARGET_DATE} (out of {len(all_tasks)} total)")

    # ===== RESULTS =====
    print(f"\n{'=' * 70}")
    print(f"RESULTS: Human Edits on {TARGET_DATE}")
    print(f"{'=' * 70}")
    print(f"  Total MRs checked: {len(mr_summaries)}")
    print(f"  Total cases checked: {total_cases_checked}")
    print(f"  Fix-translation edits on {TARGET_DATE}: {len(human_edits)}")
    print(f"  Reviewer comments found (all dates): {len(comments_found)}")

    if human_edits:
        print(f"\n{'─' * 70}")
        print(f"FIX-TRANSLATION EDITS ({len(human_edits)} records)")
        print(f"{'─' * 70}")
        for i, edit in enumerate(human_edits, 1):
            print(f"\n  ✏️  Edit #{i}")
            print(f"  Project/MR:   {edit['project_id']} MR#{edit['mr_iid']}")
            print(f"  MR Link:      {edit['mr_link']}")
            print(f"  Opus ID:      {edit['opus_id']}")
            print(f"  Language:     {edit['target_language']}")
            print(f"  Source:       {edit['source_text'][:120]}")
            print(f"  Original:     {edit['original_text'][:120]}")
            print(f"  Fixed to:     {edit['fixed_text'][:120]}")
            print(f"  Fixed by:     {edit['fixed_by']}")
            print(f"  Fixed at:     {edit['fixed_at']}")
            print(f"  Score:        {edit['final_score']}")
            if edit.get('error_category'):
                print(f"  Error Cat:    {edit['error_category']}")
            if edit.get('reason'):
                print(f"  Reason:       {edit['reason'][:120]}")
    else:
        print(f"\n  🔍 No fix-translation edits found on {TARGET_DATE}.")

    if comments_found:
        print(f"\n{'─' * 70}")
        print(f"REVIEWER COMMENTS (all dates, {len(comments_found)} records)")
        print(f"{'─' * 70}")
        for c in comments_found[:20]:  # Show first 20
            print(f"  📝 {c['project_id']} MR#{c['mr_iid']} | {c['opus_id']} [{c['target_language']}]")
            print(f"     Comment: {c['reviewer_comment'][:150]}")

    if apr3_tasks:
        print(f"\n{'─' * 70}")
        print(f"TASKS on {TARGET_DATE} ({len(apr3_tasks)} tasks)")
        print(f"{'─' * 70}")
        for t in apr3_tasks[:30]:
            print(f"  📦 {t['task_id'][:12]}… | {t.get('project_id', '')} MR#{t.get('merge_request_iid', '')} | "
                  f"status={t.get('status', '')} | step={t.get('current_step', '')}")

    # Save full results to JSON
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, f"apr3_human_edits_{TARGET_DATE.replace('-', '')}.json")
    output = {
        "query_date": TARGET_DATE,
        "total_mrs_checked": len(mr_summaries),
        "total_cases_checked": total_cases_checked,
        "fix_edits": human_edits,
        "reviewer_comments": comments_found,
        "apr3_tasks": apr3_tasks,
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n📄 Full JSON data saved to: {output_file}")
    return human_edits


if __name__ == "__main__":
    edits = main()
    print(f"\n{'=' * 70}")
    print(f"✅ Total human edits found on {TARGET_DATE}: {len(edits)}")
