---
name: prefer-cli-destroy
enabled: true
event: bash
pattern: ssh\s+exe\.dev\s+rm\s+
action: block
---

**Use CLI commands instead of direct exe.dev SSH**

You're using `ssh exe.dev rm` to destroy VMs directly. Use the ohcommodore CLI instead:

**For ships:**
```bash
./ohcommodore ship destroy <ship-id-prefix>
```

**For entire fleet (including flagship):**
```bash
./ohcommodore fleet sink --scuttle --force
```

**Why CLI is better:**
- Updates the ships registry in JSON
- Cleans up local config when scuttling
- Provides consistent UX with prefix matching
