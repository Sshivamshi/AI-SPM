import psutil
import time
from datetime import datetime
import os
import csv

# --- Configuration ---
LOG_FILE = 'system_performance_log.csv'
LOG_INTERVAL_SECONDS = 3 # Set the logging and refresh interval
TOP_N_PROCESSES = 5

def setup_csv():
    """Initializes the CSV log file with headers if it doesn't exist."""
    header = [
        'timestamp', 'cpu_usage_total', 'cpu_usage_per_core', 'memory_usage_percent',
        'memory_used', 'memory_available', 'memory_cached', 'disk_usage_percent',
        'disk_io_read_bytes_s', 'disk_io_write_bytes_s', 'network_io_sent_mbps',
        'network_io_recv_mbps', 'system_uptime_seconds', 'system_temp_celsius'
    ]

    # Add headers for top N processes for each category
    for category in ['cpu', 'memory', 'disk_io']:
        for i in range(1, TOP_N_PROCESSES + 1):
            header.extend([
                f'top_{category}_{i}_pid', f'top_{category}_{i}_name', f'top_{category}_{i}_cpu_percent',
                f'top_{category}_{i}_mem_rss', f'top_{category}_{i}_mem_vms', f'top_{category}_{i}_disk_read_bytes',
                f'top_{category}_{i}_disk_write_bytes', f'top_{category}_{i}_execution_time_s'
            ])

    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)

def log_to_csv(data):
    """Appends a row of data to the CSV log file."""
    with open(LOG_FILE, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        writer.writerow(data)

def get_size(bytes, suffix="B"):
    """Scale bytes to its proper format."""
    factor = 1024
    for unit in ["", "K", "M", "G", "T", "P"]:
        if bytes < factor:
            return f"{bytes:.2f}{unit}{suffix}"
        bytes /= factor
    return f"{bytes:.2f}P{suffix}"

def get_io_rates():
    """Calculates disk and network I/O rates over a 1-second interval."""
    last_disk = psutil.disk_io_counters()
    last_net = psutil.net_io_counters()
    time.sleep(1)
    new_disk = psutil.disk_io_counters()
    new_net = psutil.net_io_counters()

    # Disk I/O bytes per second
    disk_read_s = new_disk.read_bytes - last_disk.read_bytes
    disk_write_s = new_disk.write_bytes - last_disk.write_bytes

    # Network I/O Mbps
    bytes_sent_s = new_net.bytes_sent - last_net.bytes_sent
    bytes_recv_s = new_net.bytes_recv - last_net.bytes_recv
    mbps_sent = (bytes_sent_s * 8) / 1_000_000
    mbps_recv = (bytes_recv_s * 8) / 1_000_000

    return disk_read_s, disk_write_s, mbps_sent, mbps_recv

def get_all_processes_info():
    """Gathers detailed information for all running processes."""
    processes = []
    current_time = time.time()
    for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_info', 'io_counters', 'create_time']):
        try:
            pinfo = proc.info
            # Calculate total disk I/O for sorting
            disk_io_total = (pinfo['io_counters'].read_bytes + pinfo['io_counters'].write_bytes) if pinfo['io_counters'] else 0
            
            processes.append({
                'pid': pinfo['pid'],
                'name': pinfo['name'],
                'cpu_percent': pinfo['cpu_percent'],
                'mem_rss': pinfo['memory_info'].rss,
                'mem_vms': pinfo['memory_info'].vms,
                'disk_read_bytes': pinfo['io_counters'].read_bytes if pinfo['io_counters'] else 0,
                'disk_write_bytes': pinfo['io_counters'].write_bytes if pinfo['io_counters'] else 0,
                'disk_io_total': disk_io_total,
                'execution_time_s': current_time - pinfo['create_time']
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return processes

def run_spm():
    """Main function to run the System Performance Monitor."""
    setup_csv()
    # Initial call to get a baseline for CPU usage
    psutil.cpu_percent(interval=None)

    try:
        while True:
            # --- Data Collection ---
            log_data = {}
            
            # --- System-Level Metrics (takes ~1 second due to I/O calculation) ---
            disk_read_s, disk_write_s, mbps_sent, mbps_recv = get_io_rates()
            
            cpu_total = psutil.cpu_percent(interval=None)
            cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
            svmem = psutil.virtual_memory()
            disk_usage = psutil.disk_usage('/')
            uptime = time.time() - psutil.boot_time()
            
            try:
                temps = psutil.sensors_temperatures()
                # Find a core temperature, fallback to the first available sensor
                cpu_temp = temps.get('coretemp', [{}])[0].get('current', 'N/A')
                if cpu_temp == 'N/A' and temps:
                     # Fallback to the first available sensor if coretemp is not found
                    first_sensor = list(temps.values())[0][0]
                    cpu_temp = first_sensor.current
            except (AttributeError, IndexError, KeyError):
                cpu_temp = 'N/A'

            # Populate system metrics for logging
            log_data.update({
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'cpu_usage_total': cpu_total, 'cpu_usage_per_core': ','.join(map(str, cpu_per_core)),
                'memory_usage_percent': svmem.percent, 'memory_used': svmem.used,
                'memory_available': svmem.available,
                'memory_cached': getattr(svmem, 'cached', 0), # Safely get 'cached' attribute
                'disk_usage_percent': disk_usage.percent, 'disk_io_read_bytes_s': disk_read_s,
                'disk_io_write_bytes_s': disk_write_s, 'network_io_sent_mbps': mbps_sent,
                'network_io_recv_mbps': mbps_recv, 'system_uptime_seconds': uptime,
                'system_temp_celsius': cpu_temp
            })
            
            # --- Process-Level Metrics ---
            all_procs = get_all_processes_info()
            
            top_cpu = sorted(all_procs, key=lambda p: p['cpu_percent'], reverse=True)[:TOP_N_PROCESSES]
            top_mem = sorted(all_procs, key=lambda p: p['mem_rss'], reverse=True)[:TOP_N_PROCESSES]
            top_disk = sorted(all_procs, key=lambda p: p['disk_io_total'], reverse=True)[:TOP_N_PROCESSES]

            # Flatten process data for logging
            for category, data in [('cpu', top_cpu), ('memory', top_mem), ('disk_io', top_disk)]:
                for i in range(TOP_N_PROCESSES):
                    proc_data = data[i] if i < len(data) else {}
                    log_data[f'top_{category}_{i+1}_pid'] = proc_data.get('pid', 'N/A')
                    log_data[f'top_{category}_{i+1}_name'] = proc_data.get('name', 'N/A')
                    log_data[f'top_{category}_{i+1}_cpu_percent'] = proc_data.get('cpu_percent', 'N/A')
                    log_data[f'top_{category}_{i+1}_mem_rss'] = proc_data.get('mem_rss', 'N/A')
                    log_data[f'top_{category}_{i+1}_mem_vms'] = proc_data.get('mem_vms', 'N/A')
                    log_data[f'top_{category}_{i+1}_disk_read_bytes'] = proc_data.get('disk_read_bytes', 'N/A')
                    log_data[f'top_{category}_{i+1}_disk_write_bytes'] = proc_data.get('disk_write_bytes', 'N/A')
                    log_data[f'top_{category}_{i+1}_execution_time_s'] = proc_data.get('execution_time_s', 'N/A')

            # --- Log Data to CSV ---
            log_to_csv(log_data)

            # --- Display in CLI ---
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"--- System Performance Monitor --- (Logging to {LOG_FILE})")
            print(f"--- Timestamp: {log_data['timestamp']} | Uptime: {datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M')} ---")

            print(f"\nCPU Usage: {cpu_total}% | Per Core: {cpu_per_core} | Temp: {cpu_temp}°C")
            print("Top CPU Consuming Tasks:")
            for p in top_cpu:
                print(f"  PID: {p['pid']:<6} | Name: {p['name']:<20} | Usage: {p['cpu_percent']:.2f}%")
            
            print(f"\nMemory Usage: {svmem.percent}% (Used: {get_size(svmem.used)} / Total: {get_size(svmem.total)})")
            print("Top Memory Consuming Tasks (RSS):")
            for p in top_mem:
                print(f"  PID: {p['pid']:<6} | Name: {p['name']:<20} | Usage: {get_size(p['mem_rss'])}")

            print(f"\nDisk Usage (/): {disk_usage.percent}% (Read: {get_size(disk_read_s)}/s | Write: {get_size(disk_write_s)}/s)")
            print("Top Disk I/O Tasks:")
            for p in top_disk:
                print(f"  PID: {p['pid']:<6} | Name: {p['name']:<20} | I/O: {get_size(p['disk_io_total'])}")

            print(f"\nNetwork Speed (Upload: {mbps_sent:.2f} Mbps | Download: {mbps_recv:.2f} Mbps)")

            # Wait for the remainder of the interval
            time.sleep(max(0, LOG_INTERVAL_SECONDS - 1)) # Subtract 1s for the I/O calculation sleep

    except KeyboardInterrupt:
        print("\nSPM stopped by user. Exiting.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    run_spm()

