# hw-compat-check

Scan a node running Virtuozzo Infrastructure System 7.x for hardware that may not be supported on V/IS 8.0.

V/IS 8.0 ships on VZLinux 10, which is based on Red Hat Enterprise Linux 10. Drivers that Red Hat dropped from RHEL 10 — older NICs, storage controllers, certain GPU models — are not available on V/IS 8.0 either. Hardware that works on V/IS 7.x may not work on 8.0.

This tool scans a node against the V/IS 8.0 hardware support matrix and reports devices that fall into one of two buckets:

- **Incompatible** — the driver was removed in RHEL 10. The upgrade will not proceed on this node until the device is replaced or removed.
- **Deprecated** — the driver still ships but is no longer actively maintained by Red Hat. The upgrade can proceed; long-term reliability is at your discretion.

The tool also checks the CPU. RHEL 10 requires a CPU compatible with the x86-64-v3 instruction set (Intel Haswell / AMD Excavator or newer). A CPU that lacks any of the required features is reported as incompatible, along with the missing CPU flags.

Run it on every node early — replacement procurement is running 8–16 weeks in the current market.

## Install

```sh
curl -fsSL https://github.com/virtuozzo/vl10-hw-compat/releases/latest/download/install.sh | sh
```

The installer lays the tool into `/usr/local/share/hw-compat-check/` (or `~/.local/share/hw-compat-check/` if not root) and symlinks `hw-compat-check` into the matching `bin/`. Re-run to upgrade.

Requirements:

- Python 3.6 or newer (V/IS 7.x ships 3.9; nothing to install)
- `lspci`, `modprobe` (present by default on V/IS / VZLinux / RHEL)

### Using the tool without using installation script

If you'd rather not pipe a script into `sh`, you can fetch the source yourself and run it in place.

**From a release tarball:**

```sh
curl -fsSLO https://github.com/virtuozzo/vl10-hw-compat/releases/latest/download/hw-compat-check.tar.gz
tar -xzf hw-compat-check.tar.gz
cd hw-compat-check-*
./bin/hw-compat-check
```

**From a git checkout:**

```sh
git clone https://github.com/virtuozzo/vl10-hw-compat.git
cd vl10-hw-compat
./bin/hw-compat-check
```

The `bin/hw-compat-check` wrapper resolves its own location and adds the unpacked tree to `PYTHONPATH`, so it works from any directory. To run via the Python interpreter directly:

```sh
PYTHONPATH=. python3 -m hwcompat
```

## Run

```sh
hw-compat-check
```

That's it. No flags. The scan typically takes a few seconds on a 100–300 device node.

### Sample output — clean

```
✓ No compatibility issues found.
  Scanned 142 devices in 3.4s.
  Target: V/IS 8.0 (VZLinux 10)
```

### Sample output — findings

```
✗ Found 3 incompatible devices. The upgrade will not proceed until they are replaced.

  Incompatible — must be replaced before upgrade
    • Intel(R) Xeon(R) CPU E5-2620 0 @ 2.00GHz
      CPU does not support x86-64-v3 instruction set required by RHEL 10; missing flags: avx2, bmi1, bmi2, f16c, fma, abm, movbe
    • Mellanox Technologies MT27500 Family [ConnectX-3] — driver mlx4_core
      slot 0000:03:00.0
    • Broadcom NetXtreme II BCM5709 Gigabit Ethernet — driver bnx2
      slot 0000:02:00.0

  Deprecated — still works, no Red Hat support
    • Adaptec AAC RAID controller — driver aacraid
      slot 0000:01:00.0

  Scanned 142 devices in 3.4s. 3 incompatible, 1 deprecated.
  Target: V/IS 8.0 (VZLinux 10)
```

### Exit codes

- `0` — no findings
- `1` — only deprecated findings (warning, no action required)
- `2` — at least one incompatible finding (replacement required before upgrade)

## Flags

| Flag | Purpose |
| --- | --- |
| `--json` | Machine-readable output on stdout. Same exit codes. |
| `-v`, `--verbose` | Include modalias and sysfs path on each finding. |
| `--no-progress` | Suppress the progress animation. |
| `--no-color` | Suppress ANSI color. Also honors `NO_COLOR` env var. |
| `--skip-kmod` | Don't consult kmod indexes when checking for missing drivers. Use on systems without `libkmod.so.2`. |
| `--version` | Print the tool version. |

## JSON output

```sh
hw-compat-check --json
```

Schema:

```json
{
  "target_release": "8.0",
  "target_rhel_major": 10,
  "devices_scanned": 142,
  "elapsed_seconds": 3.41,
  "summary": { "incompatible": 3, "deprecated": 1 },
  "findings": [
    {
      "severity": "incompatible",
      "name": "Intel(R) Xeon(R) CPU E5-2620 0 @ 2.00GHz",
      "driver": null,
      "slot": null,
      "modalias": null,
      "sysfs_path": null,
      "kind": "cpu",
      "reason": "CPU does not support x86-64-v3 instruction set required by RHEL 10; missing flags: avx2, bmi1, bmi2, f16c, fma, abm, movbe"
    },
    {
      "severity": "incompatible",
      "name": "Mellanox Technologies MT27500 Family [ConnectX-3]",
      "driver": "mlx4_core",
      "slot": "0000:03:00.0",
      "modalias": "pci:v000015B3d00001003...",
      "sysfs_path": "/sys/bus/pci/devices/0000:03:00.0",
      "kind": "device",
      "reason": null
    }
  ]
}
```

## Running across a fleet

```sh
for host in $(cat hosts.txt); do
    ssh "root@$host" 'hw-compat-check --json' > "reports/$host.json"
done
```

The exit codes survive SSH; check `$?` per host to gate follow-up.

## Uninstall

```sh
rm -rf /usr/local/share/hw-compat-check /usr/local/bin/hw-compat-check
# or, for non-root installs:
rm -rf ~/.local/share/hw-compat-check ~/.local/bin/hw-compat-check
```

## Reporting issues

If a device is misclassified — supported in your testing but flagged here, or the reverse — please submit a support ticket with the relevant `--json --verbose` output.
