#!/usr/bin/env python3
import subprocess
import time
import sys
import argparse

def get_running_containers():
    """
    Discovers all running clab-e2e-topology containers.
    """
    cmd = ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=clab-e2e-topology-"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("\033[91mError listing containers via Docker. Is Docker running?\033[0m")
        sys.exit(1)
    return sorted(res.stdout.strip().split())

def get_interface_stats(container):
    """
    Parses /proc/net/dev inside the container to retrieve Rx/Tx byte counts.
    """
    stats = {}
    cmd = ["docker", "exec", container, "cat", "/proc/net/dev"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return stats
    
    for line in res.stdout.splitlines():
        if ":" not in line:
            continue
        parts = line.split(":")
        iface = parts[0].strip()
        
        # Skip loopback and control plane/management interfaces (eth0)
        if iface == "lo" or iface == "eth0":
            continue
            
        data = parts[1].split()
        if len(data) >= 9:
            try:
                rx_bytes = int(data[0])
                tx_bytes = int(data[8])
                stats[iface] = (rx_bytes, tx_bytes)
            except ValueError:
                pass
    return stats

def format_speed(bps):
    """
    Formats bit-rate into human-readable units (bps, Kbps, Mbps, Gbps).
    """
    if bps < 1000:
        return f"{bps:.1f} bps"
    elif bps < 1000000:
        return f"{bps / 1000:.1f} Kbps"
    elif bps < 1000000000:
        return f"{bps / 1000000:.1f} Mbps"
    else:
        return f"{bps / 1000000000:.1f} Gbps"

def measure_utilization(interval, hide_idle=True):
    """
    Measures and prints Rx/Tx utilization over a single interval.
    """
    containers = get_running_containers()
    if not containers:
        print("No running clab-e2e-topology containers found.")
        return

    # Sample 1: Get initial byte counts
    start_stats = {}
    for container in containers:
        start_stats[container] = get_interface_stats(container)

    time.sleep(interval)

    # Sample 2: Get final byte counts
    end_stats = {}
    for container in containers:
        end_stats[container] = get_interface_stats(container)

    # Print Table Header
    print(f"\nReal-Time Link Utilization (Polling Interval: {interval}s)")
    print("-" * 90)
    print(f"{'Container Node':<30} | {'Iface':<12} | {'Rx Speed':<18} | {'Tx Speed':<18}")
    print("-" * 90)

    total_rx_bps = 0
    total_tx_bps = 0
    active_links_count = 0

    for container in containers:
        node_name = container.replace("clab-e2e-topology-", "")
        start_ifaces = start_stats.get(container, {})
        end_ifaces = end_stats.get(container, {})

        for iface, (end_rx, end_tx) in end_ifaces.items():
            if iface not in start_ifaces:
                continue
            
            start_rx, start_tx = start_ifaces[iface]
            
            # Calculate speeds in bits per second
            rx_bps = max(0, (end_rx - start_rx) * 8 / interval)
            tx_bps = max(0, (end_tx - start_tx) * 8 / interval)

            total_rx_bps += rx_bps
            total_tx_bps += tx_bps

            # Filter idle interfaces if requested
            if hide_idle and rx_bps < 100 and tx_bps < 100:
                continue

            active_links_count += 1
            print(f"{node_name:<30} | {iface:<12} | {format_speed(rx_bps):<18} | {format_speed(tx_bps):<18}")

    if active_links_count == 0:
        print(f" [No active traffic detected on non-management links. All interfaces are idle.]")
    print("-" * 90)
    print(f"{'Total Aggregate Traffic':<30} | {'*':<12} | {format_speed(total_rx_bps):<18} | {format_speed(total_tx_bps):<18}")
    print("-" * 90 + "\n")

def main():
    parser = argparse.ArgumentParser(
        description="Measures and displays real-time link utilization in Kbps/Mbps for the containerlab topology."
    )
    parser.add_argument(
        "-i", "--interval", type=float, default=2.0,
        help="Polling interval in seconds (default: 2.0)."
    )
    parser.add_argument(
        "-c", "--count", type=int, default=1,
        help="Number of times to measure (default: 1, set 0 to run continuously)."
    )
    parser.add_argument(
        "--show-idle", action="store_true",
        help="Include idle interfaces (traffic < 100 bps) in the output."
    )

    args = parser.parse_args()

    hide_idle = not args.show_idle

    if args.count == 1:
        measure_utilization(args.interval, hide_idle)
    else:
        measure_count = 0
        try:
            while True:
                measure_count += 1
                if args.count > 0 and measure_count > args.count:
                    break
                measure_utilization(args.interval, hide_idle)
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")

if __name__ == "__main__":
    main()
