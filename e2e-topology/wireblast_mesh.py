#!/usr/bin/env python3
import re
import subprocess
import argparse
import sys
import os

# Node definitions based on our containerlab E2E topology
DEFAULT_SRC_NODES = ["clab-e2e-topology-br-h1"]
DEFAULT_DST_NODES = ["clab-e2e-topology-dc-h1", "clab-e2e-topology-dc-h2"]

# Configuration from Streamlit NOC UI / local env
LOCAL_BINARY = "/root/wireblast"
REMOTE_BINARY = "/usr/local/bin/wireblast"

def normalize_node_name(node_name):
    """
    Ensures node names are always fully qualified containerlab names.
    """
    if not node_name.startswith("clab-e2e-topology-"):
        return f"clab-e2e-topology-{node_name}"
    return node_name

def get_node_physical_ip_and_iface(node, dst_ip):
    """
    Discovers the physical egress interface and the corresponding physical IP 
    of that interface inside the container.
    """
    # 1. Get the egress interface using 'ip route get'
    cmd_route = ["docker", "exec", node, "ip", "route", "get", dst_ip]
    try:
        res_route = subprocess.run(cmd_route, capture_output=True, text=True, check=True)
        output_route = res_route.stdout.strip()
        
        # Parse the 'dev' parameter
        dev_match = re.search(r'dev\s+(\S+)', output_route)
        if not dev_match:
            return None, None
            
        iface = dev_match.group(1)
        
        # 2. Get the IP configured on that specific interface
        cmd_ip = ["docker", "exec", node, "ip", "-4", "-o", "addr", "show", "dev", iface]
        res_ip = subprocess.run(cmd_ip, capture_output=True, text=True, check=True)
        output_ip = res_ip.stdout.strip()
        
        # Parse the IP address (e.g., "inet 10.200.1.4/24")
        ip_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', output_ip)
        if ip_match:
            return iface, ip_match.group(1)
            
    except subprocess.CalledProcessError:
        pass
        
    return None, None

def get_node_ip_by_node_id(node_name):
    """
    Provides a fast topology fallback mapping to find the physical IP of a node
    if routing tables are not yet fully converged.
    """
    clean_name = node_name.replace("clab-e2e-topology-", "")
    if clean_name == "br-h1":
        return "10.1.10.11"
    elif clean_name == "dc-h1":
        return "10.2.110.11"
    elif clean_name == "dc-h2":
        return "10.2.120.11"
    elif clean_name == "br-dist1":
        return "10.1.10.1"
    elif clean_name == "br-dist2":
        return "10.1.20.1"
    return None

def deploy_wireblast_binary(nodes):
    """
    Copies the wireblast binary from the host to the target docker containers
    and configures executive privileges.
    """
    print("\033[1;34m[SYSTEM]\033[0m Starting deployment of wireblast binary to selected nodes...")
    success_nodes = []
    for node in nodes:
        check_cmd = f"docker inspect -f '{{{{.State.Running}}}}' {node}"
        res = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
        if res.stdout.strip() != "true":
            print(f" [\033[91mOFFLINE\033[0m] Container {node} is not running. Skipping.")
            continue
            
        cp_cmd = f"docker cp {LOCAL_BINARY} {node}:{REMOTE_BINARY}"
        cp_res = subprocess.run(cp_cmd, shell=True, capture_output=True, text=True)
        if cp_res.returncode != 0:
            print(f" [\033[91mFAILED\033[0m] Could not copy binary to {node}. Err: {cp_res.stderr.strip()}")
            continue
            
        chmod_cmd = f"docker exec -u 0 {node} chmod +x {REMOTE_BINARY}"
        chmod_res = subprocess.run(chmod_cmd, shell=True, capture_output=True, text=True)
        if chmod_res.returncode != 0:
            print(f" [\033[91mFAILED\033[0m] Could not set execute permissions on {node}.")
            continue
            
        print(f" [\033[92mOK\033[0m] Deployed to {node} successfully.")
        success_nodes.append(node)
    
    print(f"\033[1;34m[SYSTEM]\033[0m Deployment complete. Deployed to {len(success_nodes)}/{len(nodes)} nodes.\n")
    return success_nodes

def run_wireblast(node, binary_path, iface, src_ip, dst_ip, pps, duration, dry_run=False):
    """
    Constructs and triggers the wireblast background execution on the specified Docker node.
    """
    docker_cmd = [
        "docker", "exec", "-u", "0", "-d", node, binary_path, "--no-tui",
        "-i", iface,
        "--src-ip", src_ip,
        "--dst-ip", dst_ip,
        "--pps", str(pps),
        "--duration", str(duration),
        "--start"
    ]
    
    cmd_str = " ".join(docker_cmd)
    
    if dry_run:
        print(f"[DRY-RUN] {node} -> {dst_ip} via {iface} (Source IP: {src_ip}): {cmd_str}")
        return True
        
    try:
        subprocess.run(docker_cmd, check=True)
        print(f"[\033[92mLAUNCHED\033[0m] Node: {node:22s} | Out: {iface:5s} | {src_ip} -> {dst_ip} ({pps} pps, {duration})")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[\033[91mFAILED\033[0m] Node: {node:22s} to {dst_ip} | Err: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(
        description="Automates physical-interface based full-mesh traffic generation to guarantee wireblast interface validation passes."
    )
    parser.add_argument(
        "--src-nodes", nargs="+", 
        help="List of source containers (defaults to br-h1)."
    )
    parser.add_argument(
        "--dst-nodes", nargs="+", 
        help="List of destination containers (defaults to dc-h1, dc-h2)."
    )
    parser.add_argument(
        "--binary", default=REMOTE_BINARY, 
        help=f"Absolute path of the wireblast executable inside the container (default: {REMOTE_BINARY})."
    )
    parser.add_argument(
        "--deploy", action="store_true",
        help="Deploy wireblast binary from host to all participating containers before generating traffic."
    )
    parser.add_argument(
        "--pps", type=int, default=50000, 
        help="Packets per second rate (default: 50000)."
    )
    parser.add_argument(
        "--duration", default="300s", 
        help="Traffic generation duration string, e.g., '300s', '5m' (default: '300s')."
    )
    parser.add_argument(
        "--dry-run", action="store_true", 
        help="Print the generated commands without executing them."
    )
    parser.add_argument(
        "--bidirectional", action="store_true", 
        help="Automatically generate return traffic paths (dst-nodes -> src-nodes)."
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Stop and kill all running wireblast processes on all participating containers."
    )

    args = parser.parse_args()

    # Normalize duration to append 's' if a pure integer/digit is passed
    duration = str(args.duration)
    if duration.isdigit():
        duration += "s"

    raw_src_nodes = args.src_nodes if args.src_nodes else DEFAULT_SRC_NODES
    raw_dst_nodes = args.dst_nodes if args.dst_nodes else DEFAULT_DST_NODES

    # Normalize to fully qualified containerlab names
    src_nodes = [normalize_node_name(n) for n in raw_src_nodes]
    dst_nodes = [normalize_node_name(n) for n in raw_dst_nodes]

    print("=" * 75)
    print("            WIREBLAST PHYSICAL INTERFACE TRAFFIC GENERATOR")
    print("=" * 75)
    print(f"Sources:      {', '.join(src_nodes)}")
    print(f"Destinations: {', '.join(dst_nodes)}")
    print(f"Binary:       {args.binary}")
    print(f"PPS Rate:     {args.pps}")
    print(f"Duration:     {duration}")
    if args.dry_run:
        print("Mode:         DRY-RUN (Simulating commands only)")
    print("=" * 75)

    if args.stop:
        print("\033[1;34m[SYSTEM]\033[0m Stopping and killing all wireblast processes...")
        # Get unique list of all participating containers
        all_participating = list(set(src_nodes + dst_nodes))
        for node in all_participating:
            kill_cmd = ["docker", "exec", "-u", "0", node, "pkill", "-f", "wireblast"]
            if args.dry_run:
                print(f"[DRY-RUN] Would stop wireblast on {node}: {' '.join(kill_cmd)}")
                continue
            res = subprocess.run(kill_cmd, capture_output=True, text=True)
            # pkill returns 0 if matches are killed, 1 if no processes matched
            if res.returncode == 0:
                print(f" [\033[92mSTOPPED\033[0m] Terminated running wireblast processes on {node}.")
            else:
                print(f" [\033[90mIDLE\033[0m] No running wireblast processes found on {node}.")
        print("\033[1;34m[SYSTEM]\033[0m Teardown complete.\n")
        sys.exit(0)

    if args.deploy:
        nodes_to_deploy = list(set(src_nodes + dst_nodes))
        deploy_wireblast_binary(nodes_to_deploy)

    flows = []
    for s_node in src_nodes:
        for d_node in dst_nodes:
            if s_node == d_node:
                continue
            flows.append((s_node, d_node))

    if args.bidirectional:
        for d_node in dst_nodes:
            for s_node in src_nodes:
                if s_node == d_node:
                    continue
                if (d_node, s_node) not in flows:
                    flows.append((d_node, s_node))

    success_count = 0
    total_flows = len(flows)

    for src, dst in flows:
        # Determine target physical IP
        dst_ip = get_node_ip_by_node_id(dst)
        if not dst_ip:
            print(f"[\033[93mSKIP\033[0m] Could not map node {dst} to a physical IP.")
            continue
            
        # Dynamically discover local egress interface and corresponding local source physical IP
        iface, src_ip = get_node_physical_ip_and_iface(src, dst_ip)
        
        # Fallback if route lookup fails
        if not iface or not src_ip:
            iface = "eth1"
            src_ip = get_node_ip_by_node_id(src)
            
        if not src_ip:
            print(f"[\033[91mNO ROUTE\033[0m] Could not resolve path or IPs for pair: {src} -> {dst}")
            continue

        # Execute wireblast
        status = run_wireblast(
            node=src,
            binary_path=args.binary,
            iface=iface,
            src_ip=src_ip,
            dst_ip=dst_ip,
            pps=args.pps,
            duration=duration,
            dry_run=args.dry_run
        )
        if status:
            success_count += 1

    print("=" * 75)
    print(f"Deployment Completed: {success_count}/{total_flows} traffic flows successfully initialized.")
    print("=" * 75)

if __name__ == "__main__":
    main()
