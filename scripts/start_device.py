# Copyright (c) 2026 Marco Parenzan. MIT License.
#
# Starts one simulated device (the Python agent) under PySharp, outside Forge. Itself a PySharp script.
#
#   pysharp run scripts\start_device.py device01               runs until you press Ctrl+C
#   pysharp run scripts\start_device.py device01 120           stops cleanly after 120 seconds
#   pysharp run scripts\start_device.py device01 --dry-run     runs the checks and prints the command, starts nothing
#   pysharp run scripts\start_device.py device01 --log-level debug
#   -LogLevel is also accepted. Precedence: flag, config logLevel, info; passed as PYGATE_LOG_LEVEL.
#   At debug/trace the agent logger prints its authoritative log file path on the inherited console.
#
# It reads <config root>\devices\<id>\agent-config.json (config root: FORGE_CONFIG_ROOT or D:\Configurations\AzureIoTForge).
# Nothing is registered with DPS: the agent starts from the assignment already cached in the device folder. It refuses to
# start when the config does not say "allowDpsRegistration": false, or when an agent for this device is already running
# (IoT Hub allows one connection per device id: a second copy would keep kicking the first one off the hub).
# If the agent exits with code 75 it asked for a restart, and this script starts it again.
import json
import os
import re
import subprocess
import sys


def fail(message, code):
    print(message)
    sys.exit(code)


def is_running(device_id, config_path):
    # Matches this exact config path (any devices root) or the legacy \devices\<id>\ layout.
    # Values travel in environment variables, so no quoting of the path is needed.
    query = ("$p=[WildcardPattern]::Escape($env:PYGATE_GUARD_PATH); $l='*\\devices\\'+$env:PYGATE_GUARD_ID+'\\agent-config.json*'; "
             "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pysharp.exe' -and $_.CommandLine -and "
             "($_.CommandLine -like ('*'+$p+'*') -or $_.CommandLine -like $l) }) { exit 1 } else { exit 0 }")
    environment = dict(os.environ, PYGATE_GUARD_PATH=config_path, PYGATE_GUARD_ID=device_id)
    return subprocess.call(["powershell", "-NoProfile", "-Command", query], env=environment) == 1


# 1. Arguments
log_levels = ["off", "info", "debug", "trace"]
log_level = None
dry_run = "--dry-run" in sys.argv[1:]
stop_requested = "--stop" in sys.argv[1:]
rest = []
i = 1
while i < len(sys.argv):
    argument = sys.argv[i]
    if argument in ["--log-level", "-LogLevel"]:
        if i + 1 >= len(sys.argv) or sys.argv[i + 1] not in log_levels:
            fail("Invalid log level flag. Expected off, info, debug or trace.", 2)
        log_level = sys.argv[i + 1]
        i += 2
        continue
    if argument not in ["--dry-run", "--stop"]:
        rest.append(argument)
    i += 1
if len(rest) < 1 or not re.match(r"^[A-Za-z0-9_-]+$", rest[0]):
    fail("Usage: pysharp run scripts\\start_device.py <deviceId> [seconds] [--log-level off|info|debug|trace] [--dry-run]", 2)
device_id = rest[0]
seconds = rest[1] if len(rest) > 1 else None
if seconds is not None and not re.match(r"^[0-9]+$", seconds):
    fail("The duration must be a whole number of seconds.", 2)

# 2. The device's config file
config_root = os.environ.get("PYGATE_DEVICES_ROOT") or os.path.join(os.environ.get("FORGE_CONFIG_ROOT") or "D:\\Configurations\\AzureIoTForge", "devices") # legacy fallback
config_path = os.path.abspath(os.path.join(config_root, device_id, "agent-config.json"))  # full path: the guard matches it in the command line
if not os.path.exists(config_path):
    fail("Config not found: " + config_path, 2)

# 3. Never register with DPS from here
with open(config_path, encoding="utf-8-sig") as f:
    config = json.load(f)
if config.get("allowDpsRegistration") is not False:
    fail("Refusing to start: " + config_path + " must contain \"allowDpsRegistration\": false", 2)

# Resolve once for all restarts; inherited PYGATE_LOG_LEVEL is not a source.
if log_level is None:
    log_level = config.get("logLevel", "info")
    if not isinstance(log_level, str) or log_level not in log_levels:
        fail("Invalid config logLevel. Expected off, info, debug or trace.", 2)

# 4. The agent (the script's own folder comes from argv[0]: PySharp does not set __file__ for the entry script)
agent_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "..", "src", "agent"))
if not os.path.exists(os.path.join(agent_dir, "main.py")):
    fail("Agent not found: " + agent_dir + "\\main.py", 2)

# 5. Not already running (started here, or by Forge)
if stop_requested:
    import time
    if not is_running(device_id, config_path):
        print(device_id + " is not running.")
        sys.exit(0)
    flag = os.path.join(os.path.dirname(config_path), "stop.request")
    with open(flag, "w", encoding="ascii") as f:
        f.write("stop")
    print("Stop requested for " + device_id + " (" + flag + "). Waiting up to 30 s for a clean exit...")
    deadline = time.monotonic() + 30
    while is_running(device_id, config_path) and time.monotonic() < deadline:
        time.sleep(0.5)
    if is_running(device_id, config_path):
        fail(device_id + " is still running after 30 s. Use Ctrl+C in its own console, or ask the owner.", 4)
    print(device_id + " stopped.")
    sys.exit(0)
if is_running(device_id, config_path):
    fail(device_id + " is already running (started here or by Forge). Stop it first: a second copy would fight with it "
         "for the hub connection.", 3)

# 6. Run, and start again when the agent asks for a restart (exit code 75)
command = ["pysharp", "run", "main.py", "--live", config_path]
if seconds is not None:
    command.append(seconds)
if dry_run:
    print("Dry run. Child environment: PYGATE_LOG_LEVEL=" + log_level)
    print("Dry run. Would run, in " + agent_dir + ":  " + " ".join(command))
    sys.exit(0)

# The agent logger owns logDir confinement/rotation and prints the actual file at debug/trace.
# Keep its console inherited rather than guessing a filename here.
previous_log_level = os.environ.get("PYGATE_LOG_LEVEL")
try:
    os.environ["PYGATE_LOG_LEVEL"] = log_level
    while True:
        print("starting " + device_id)
        code = subprocess.call(command, cwd=agent_dir)
        print(device_id + " exited with code " + str(code))
        if code != 75:
            break
finally:
    if previous_log_level is None:
        del os.environ["PYGATE_LOG_LEVEL"]
    else:
        os.environ["PYGATE_LOG_LEVEL"] = previous_log_level
sys.exit(code)
