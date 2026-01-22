#!/usr/bin/env bash
# Clean up orphaned test VMs and local state
#
# Usage:
#   ./tests/clean-env.sh

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "Cleaning up test environment..."

# Remove all VMs on exe.dev
echo -e "${YELLOW}Checking for VMs on exe.dev...${NC}"
# Parse VM names from exe.dev ls output (format: "  • hostname.exe.xyz - status (image)")
all_vms=$(ssh exe.dev ls 2>/dev/null | grep -oE '[a-zA-Z0-9_-]+\.exe\.xyz' | sed 's/\.exe\.xyz$//' || true)
if [[ -n "$all_vms" ]]; then
  vm_count=$(echo "$all_vms" | wc -l | tr -d ' ')
  echo "Found $vm_count VMs to delete"
  for vm in $all_vms; do
    [[ -n "$vm" ]] || continue
    echo "Deleting $vm..."
    ssh exe.dev rm "$vm" 2>/dev/null || true
  done
  echo -e "${GREEN}Deleted all VMs${NC}"
else
  echo "No VMs found"
fi

# Remove local state
echo -e "${YELLOW}Removing local ohcommodore state...${NC}"
if [[ -d "$HOME/.ohcommodore" ]]; then
  rm -rf "$HOME/.ohcommodore"
  echo -e "${GREEN}Removed ~/.ohcommodore${NC}"
else
  echo "No local state found"
fi

echo ""
# Remove SSH keys created by ohcommodore (comment contains "ohcommodore")
echo -e "${YELLOW}Checking for ohcommodore SSH keys on exe.dev...${NC}"
ohcom_keys=$(ssh exe.dev ssh-key list --json 2>/dev/null | jq -r '.ssh_keys[] | select(.comment != null and (.comment | contains("ohcommodore")) and .current == false) | .public_key' || true)
if [[ -n "$ohcom_keys" ]]; then
  key_count=$(echo "$ohcom_keys" | wc -l | tr -d ' ')
  echo "Found $key_count ohcommodore SSH keys to remove"
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    echo "Removing key: ${key:0:50}..."
    ssh -n exe.dev ssh-key remove "$key" 2>/dev/null || true
  done <<< "$ohcom_keys"
  echo -e "${GREEN}Removed ohcommodore SSH keys${NC}"
else
  echo "No ohcommodore SSH keys found"
fi

echo ""
echo -e "${GREEN}Cleanup complete!${NC}"
