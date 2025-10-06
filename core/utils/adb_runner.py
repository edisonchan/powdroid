import os
import subprocess
import sys
import threading
import time
import itertools
from pathlib import Path

DUMP_DIR = Path(os.getcwd()) / "dump"
GO_DIR = Path(__file__).resolve().parent / "../libs/battery-historian"


def run_adb_command(cmd_args, **kwargs):
    """运行ADB命令并处理编码问题"""
    try:
        # 设置默认编码参数
        kwargs.setdefault('text', True)
        kwargs.setdefault('encoding', 'utf-8')
        kwargs.setdefault('errors', 'ignore')
        return subprocess.check_output(cmd_args, **kwargs)
    except subprocess.CalledProcessError as e:
        print(f"ADB command failed: {e}")
        return None


def run_adb_shell(cmd_args, **kwargs):
    """运行ADB shell命令"""
    return run_adb_command(["adb", "shell"] + cmd_args, **kwargs)


def get_connected_device(id_only=False):
    try:
        output = run_adb_command(["adb", "devices"])
        if not output:
            return None
            
        devices = [
            line.split()[0]
            for line in output.splitlines()[1:]
            if line.strip() and line.strip().endswith("device")
        ]
        if not devices:
            return None
        device_id = devices[0]
        
        # 获取设备信息
        props = run_adb_command(["adb", "shell", "getprop"])
        if not props:
            return device_id if id_only else f"{device_id} -> Unknown"
            
        manufacturer = None
        model = None
        for line in props.splitlines():
            if "[ro.product.manufacturer]" in line:
                manufacturer = line.split(": ", 1)[1].strip().strip("[]")
            if "[ro.product.model]" in line:
                model = line.split(": ", 1)[1].strip().strip("[]")
        if id_only:
            return device_id
        return (
            f"{device_id} -> {manufacturer} {model}"
            if manufacturer and model
            else device_id
        )
    except Exception as e:
        print(f"Error getting connected device: {e}")
        return None


def is_device_connected():
    """Vérifie si un device Android est connecté."""
    return get_connected_device() is not None


def is_adb_available():
    """Vérifie si ADB est disponible sur le système."""
    try:
        run_adb_command(["adb", "version"])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_device_info():
    """Récupère les informations détaillées du device connecté."""
    try:
        if not is_device_connected():
            return None

        props = run_adb_command(["adb", "shell", "getprop"])
        if not props:
            return None
            
        info = {}

        for line in props.splitlines():
            if "[ro.product.manufacturer]" in line:
                info["manufacturer"] = line.split(": ", 1)[1].strip().strip("[]")
            elif "[ro.product.model]" in line:
                info["model"] = line.split(": ", 1)[1].strip().strip("[]")
            elif "[ro.build.version.release]" in line:
                info["android_version"] = line.split(": ", 1)[1].strip().strip("[]")

        return info if info else None
    except Exception:
        return None


def wait_for_device_connection(verbose):
    print("[PowDroid] Waiting for device connection...")
    while True:
        device = get_connected_device()
        if device:
            print(
                f"[PowDroid] Device {device} connected."
                if verbose
                else "[PowDroid] Device connected."
            )
            break
        else:
            time.sleep(1)


def kill_all():
    try:
        subprocess.run(["adb", "shell", "am", "kill-all"], 
                      check=True, 
                      encoding='utf-8', 
                      errors='ignore')
    except subprocess.CalledProcessError as e:
        print(f"Error killing adb server: {e}")


def clear_batterystats(verbose):
    try:
        subprocess.run(
            ["adb", "shell", "dumpsys", "batterystats", "--reset"],
            check=True,
            encoding='utf-8',
            errors='ignore',
            **(
                {}
                if verbose
                else {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            ),
        )
    except subprocess.CalledProcessError as e:
        print(f"Error clearing battery stats: {e}")


def wait_for_device_disconnection(verbose):
    print("[PowDroid] Waiting for device disconnection...")
    last_device = get_connected_device()
    while True:
        device = get_connected_device()
        if not device:
            print(
                f"[PowDroid] Device {last_device} disconnected."
                if verbose
                else "[PowDroid] Device disconnected."
            )
            break
        else:
            time.sleep(1)


def _spinner(stop_event):
    spinner = itertools.cycle(["/", "-", "\\", "|"])
    while not stop_event.is_set():
        sys.stdout.write("\r[PowDroid] Extract battery data... " + next(spinner))
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write("\r[PowDroid] Extract battery data... done!\n")
    sys.stdout.flush()


def dump_batterystats(verbose):
    device = get_connected_device(id_only=True)
    if not device:
        print("No device connected")
        return
        
    dump_dir = DUMP_DIR.resolve()
    dump_dir.mkdir(parents=True, exist_ok=True)
    batterystats_path = dump_dir / "batterystats.txt"
    bugreport_path = dump_dir / "battery_device.zip"
    
    # 设置通用参数
    run_kwargs = {
        'shell': True,
        'check': True,
        'encoding': 'utf-8',
        'errors': 'ignore'
    }
    
    if not verbose:
        run_kwargs.update({"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})

    spinner_stop = threading.Event()
    spinner_thread = None
    if not verbose:
        spinner_thread = threading.Thread(target=_spinner, args=(spinner_stop,))
        spinner_thread.start()

    try:
        subprocess.run(
            f'adb -s {device} shell dumpsys batterystats --enable full-wake-history > "{batterystats_path}"',
            **run_kwargs
        )
        subprocess.run(
            f'adb -s {device} shell dumpsys batterystats >> "{batterystats_path}"',
            **run_kwargs
        )
        subprocess.run(
            f'adb -s {device} bugreport "{bugreport_path}"',
            **run_kwargs
        )
    except Exception as e:
        print(f"Error during battery stats dump: {e}")
    finally:
        if not verbose:
            spinner_stop.set()
            if spinner_thread:
                spinner_thread.join()


def conversion_batterystats():
    file_name = "battery_device.csv"
    csv_path = str((DUMP_DIR / file_name).resolve())
    zip_path = str((DUMP_DIR / "battery_device.zip").resolve())
    log_path = str((DUMP_DIR / "history_parse_log.txt").resolve())
    command = (
        f"go run cmd/history-parse/local_history_parse.go --summary=totalTime "
        f'--csv="{csv_path}" --input="{zip_path}" > "{log_path}" 2>&1'
    )
    subprocess.run(command, shell=True, check=True, cwd=GO_DIR, encoding='utf-8', errors='ignore')
    return file_name
