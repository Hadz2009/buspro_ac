#!/usr/bin/env python3
"""
Automated Git Push and Tag Script for HDL AC Control
====================================================
This script automates the release process by:
1. Reading the version from manifest.json
2. Creating a git tag with that version
3. Pushing commits to origin
4. Pushing the tag to origin

Usage:
    python push_release.py
"""

import json
import subprocess
import sys
from pathlib import Path

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def run_command(cmd, check=True):
    """Run a shell command and return the result."""
    print(f"Running: {cmd}")
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        check=False
    )
    
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    
    if check and result.returncode != 0:
        print(f"❌ Command failed with exit code {result.returncode}")
        sys.exit(1)
    
    return result


def get_version():
    """Read version from manifest.json."""
    manifest_path = Path(__file__).parent / "custom_components" / "buspro_ac" / "manifest.json"
    
    if not manifest_path.exists():
        print(f"❌ manifest.json not found at {manifest_path}")
        sys.exit(1)
    
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)
    
    version = manifest.get('version')
    if not version:
        print("❌ No version found in manifest.json")
        sys.exit(1)
    
    return version


def main():
    """Main function to push commits and create tag."""
    print("=" * 60)
    print("HDL AC Control - Automated Release Push")
    print("=" * 60)
    print()
    
    # Get version from manifest
    version = get_version()
    tag_name = f"v{version}"
    
    print(f"📦 Current version: {version}")
    print(f"🏷️  Tag to create: {tag_name}")
    print()
    
    # Check git status
    print("🔍 Checking git status...")
    status_result = run_command("git status --porcelain", check=False)
    
    if status_result.stdout.strip():
        print("⚠️  Warning: You have uncommitted changes:")
        print(status_result.stdout)
        response = input("\nDo you want to continue anyway? (y/n): ")
        if response.lower() != 'y':
            print("❌ Aborted by user")
            sys.exit(0)
        print()
    else:
        print("✅ Working tree is clean")
        print()
    
    # Check if tag already exists locally
    print(f"🔍 Checking if tag {tag_name} exists...")
    tag_check = run_command(f"git tag -l {tag_name}", check=False)
    
    if tag_check.stdout.strip():
        print(f"⚠️  Tag {tag_name} already exists locally")
        response = input(f"Delete and recreate tag {tag_name}? (y/n): ")
        if response.lower() == 'y':
            print(f"🗑️  Deleting local tag {tag_name}...")
            run_command(f"git tag -d {tag_name}")
            print()
        else:
            print("❌ Aborted by user")
            sys.exit(0)
    
    # Push commits
    print("📤 Pushing commits to origin...")
    push_result = run_command("git push", check=False)
    
    if push_result.returncode != 0:
        if "Everything up-to-date" in push_result.stdout or "Everything up-to-date" in push_result.stderr:
            print("✅ Everything up-to-date")
        else:
            print("❌ Failed to push commits")
            sys.exit(1)
    else:
        print("✅ Commits pushed successfully")
    print()
    
    # Create tag
    print(f"🏷️  Creating tag {tag_name}...")
    run_command(f'git tag -a {tag_name} -m "Release {version}"')
    print(f"✅ Tag {tag_name} created")
    print()
    
    # Push tag
    print(f"📤 Pushing tag {tag_name} to origin...")
    run_command(f"git push origin {tag_name}")
    print(f"✅ Tag {tag_name} pushed successfully")
    print()
    
    print("=" * 60)
    print("✅ Release process completed successfully!")
    print("=" * 60)
    print()
    print(f"🎉 Version {version} has been released!")
    print(f"🔗 Check your repository for tag: {tag_name}")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Aborted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

