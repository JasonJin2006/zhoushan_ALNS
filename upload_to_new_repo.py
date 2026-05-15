"""
Upload code to NEW repository zhoushan_ALNS
Creates initial commit with all drone delivery scheduling system code
"""

import json
import base64
import subprocess
import os
import tempfile
from pathlib import Path

REPO = "JasonJin2006/zhoushan_ALNS"
BRANCH = "main"
BASE_PATH = Path(r"c:\Users\alina\Desktop\core_src\core_src")
GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"

def gh_api(endpoint, method="GET", data=None, jq_filter=None):
    """Execute GitHub API call using gh CLI"""
    cmd = [GH_EXE, "api", f"repos/{REPO}/{endpoint}", "--method", method]

    if data:
        json_file = os.path.join(tempfile.gettempdir(), "gh_api_input.json")
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        cmd.extend(["--input", json_file])

    if jq_filter:
        cmd.extend(["--jq", jq_filter])

    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if result.returncode != 0:
        print(f"  ⚠ {result.stderr[:100]}")
        return None
    return result.stdout.strip()

def get_blob_sha(content_b64):
    """Create blob and return SHA"""
    data = {"content": content_b64, "encoding": "base64"}
    return gh_api("git/blobs", "POST", data, ".sha")

def create_tree(tree_items):
    """Create tree object"""
    data = {"tree": tree_items}
    return gh_api("git/trees", "POST", data, ".sha")

def create_commit(message, tree_sha, parent_sha=None):
    """Create commit"""
    data = {"message": message, "tree": tree_sha}
    if parent_sha:
        data["parents"] = [parent_sha]
    return gh_api("git/commits", "POST", data, ".sha")

def update_ref(new_sha, force=False):
    """Update branch reference"""
    data = {"sha": new_sha}
    if force:
        data["force"] = True
    return gh_api(f"git/refs/heads/{BRANCH}", "PATCH", data)

def create_initial_branch(tree_sha):
    """Create initial branch reference"""
    data = {"ref": f"refs/heads/{BRANCH}", "sha": tree_sha}
    return gh_api("git/refs", "POST", data)

def main():
    print("=" * 70)
    print("  Uploading to NEW repository: zhoushan_ALNS")
    print("  Repository: JasonJin2006/zhoushan_ALNS")
    print("=" * 70)

    # Check if repository exists
    print("\n[1/5] Checking repository...")
    exists = gh_api("", "GET")
    if not exists:
        print("  ✗ Repository does not exist!")
        return
    print("  ✓ Repository exists")

    # Step 2: Read all files
    print("\n[2/5] Reading files...")
    files = []

    for name in ["run_scheduler.py", "requirements.txt", ".gitignore", "CODE_WIKI.md"]:
        path = BASE_PATH / name
        if path.exists():
            files.append((name, path))
            print(f"  ✓ {name}")

    for name in ["__init__.py", "map_data.py", "drone.py", "order.py",
                 "cost.py", "cruise_ship.py", "flight_plan.py", "scheduler.py"]:
        path = BASE_PATH / "drone_engine" / name
        if path.exists():
            files.append((f"drone_engine/{name}", path))
            print(f"  ✓ drone_engine/{name}")

    for name in ["__init__.py", "config.py", "solution.py", "evaluator.py",
                 "construction.py", "destroy.py", "repair.py", "alns.py"]:
        path = BASE_PATH / "drone_engine" / "optimizer" / name
        if path.exists():
            files.append((f"drone_engine/optimizer/{name}", path))
            print(f"  ✓ drone_engine/optimizer/{name}")

    print(f"\n  Total: {len(files)} files")

    # Step 3: Create blobs
    print("\n[3/5] Creating Git blobs...")
    tree_items = []
    for i, (path, full_path) in enumerate(files, 1):
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')

        blob_sha = get_blob_sha(content_b64)
        if blob_sha:
            tree_items.append({
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha
            })
            print(f"  ✓ {path}")
        else:
            print(f"  ✗ Failed: {path}")

    # Step 4: Create tree
    print("\n[4/5] Creating tree...")
    tree_sha = create_tree(tree_items)
    if not tree_sha:
        print("  ✗ Failed to create tree")
        return
    print(f"  ✓ Tree created")

    # Step 5: Create commit and branch
    print("\n[5/5] Creating initial commit...")

    commit_message = """feat: Initial commit - Drone Delivery Scheduling System with ALNS

Project Overview:
- Complete drone delivery scheduling system
- Adaptive Large Neighborhood Search (ALNS) optimization algorithm
- 3 drones, 180 minutes simulation time
- 5 order types with time windows and late penalties

Key Features:
- Intelligent route planning for 3 drones
- 5 destroy operators (Worst, Shaw, Random, Route, WholeLeg Removal)
- 4 repair operators (Greedy, Regret-2, Regret-3, Batch Insertion)
- Comprehensive documentation (CODE_WIKI.md)
- Flight plan generation and validation
- Support for cruise ship rendezvous calculations

Project Structure:
- drone_engine/          - Core engine package
  ├── map_data.py       - Map and route data
  ├── drone.py          - Drone flight calculations
  ├── order.py          - Order management
  ├── cost.py           - Cost calculations
  ├── cruise_ship.py     - Cruise ship trajectory
  ├── flight_plan.py     - Flight plan generation
  ├── scheduler.py       - ALNS scheduler
  └── optimizer/         - ALNS optimizer package
      ├── config.py      - ALNS parameters
      ├── solution.py    - Solution representation
      ├── evaluator.py   - Solution evaluator
      ├── construction.py - Initial solution
      ├── destroy.py     - Destroy operators
      ├── repair.py      - Repair operators
      └── alns.py       - ALNS engine
- run_scheduler.py       - Main entry point
- CODE_WIKI.md          - Comprehensive documentation

License: MIT
Author: JasonJin2006
"""

    commit_sha = create_commit(commit_message, tree_sha)
    if not commit_sha:
        print("  ✗ Failed to create commit")
        return

    print(f"  ✓ Commit created: {commit_sha[:8]}...")

    # Create or update branch reference
    print("\n[6/6] Creating branch reference...")
    result = create_initial_branch(tree_sha)
    if result is None:
        # Branch might already exist, try to update it
        print("  ⚠ Branch exists, updating...")
        update_ref(commit_sha, force=True)
    else:
        print(f"  ✓ Branch '{BRANCH}' created")

    print("\n" + "=" * 70)
    print("  ✅ Upload Complete!")
    print("=" * 70)
    print(f"\n  Repository: https://github.com/{REPO}")
    print(f"  Branch: {BRANCH}")
    print(f"\n  Commit: {commit_sha}")
    print("\n  🎉 Your new repository is ready!")

if __name__ == "__main__":
    main()
