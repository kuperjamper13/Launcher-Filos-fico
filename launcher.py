import tkinter as tk
from tkinter import ttk
import os
import json
import requests
import threading
import subprocess
import zipfile
import gdown  # Dependency: pip install gdown
import minecraft_launcher_lib  # Dependency: pip install minecraft-launcher-lib
from pathlib import Path
import uuid
import logging
import time
import shutil # Import shutil for rmtree
import tkinter.font # Import the font module

# --- Setup Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='launcher.log',
    filemode='a' # Append to the log file
)
# Also log to console for immediate feedback
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logging.getLogger('').addHandler(console_handler)


# --- Constants ---
CONFIG_URL = "https://gist.github.com/kuperjamper13/8f7402f86dfbc5b792dd4eda1a81c3ff/raw/launcher_config.json"
# Determine Minecraft directory based on OS
if os.name == 'nt': # Windows
    MINECRAFT_DIR = Path(os.getenv('APPDATA')) / '.minecraft'
elif os.name == 'posix': # macOS/Linux
    MINECRAFT_DIR = Path.home() / 'Library/Application Support/minecraft' # macOS default
    if not MINECRAFT_DIR.exists(): # Check Linux default if macOS doesn't exist
        MINECRAFT_DIR = Path.home() / '.minecraft'
else:
    # Fallback or raise error for unsupported OS
    print("Unsupported operating system!")
    MINECRAFT_DIR = Path.cwd() / '.minecraft' # Default to current dir if unsure

MODS_DIR = MINECRAFT_DIR / 'mods'
LOCAL_CONFIG_FILE = Path("launcher_config.json") # Store local settings in the launcher's directory

# --- Global Variables ---
# It's generally better to pass config around or use a class,
# but for this simple script, globals are acceptable.
launcher_config = {}  # To store fetched config from Gist
local_config = {"nickname": "", "installed_launcher_version": 0}  # Default local settings

# --- GUI Setup ---

# Style Configuration
BG_COLOR = "#2E2E2E"
FG_COLOR = "#F0F0F0"
ENTRY_BG = "#3E3E3E"
ENTRY_FG = "#FFFFFF"
BUTTON_BG = "#4CAF50" # Green accent
BUTTON_FG = "#FFFFFF"
FONT_FAMILY = "Segoe UI" # Or "Calibri", "Arial" - adjust as needed
FONT_SIZE_NORMAL = 11
FONT_SIZE_LARGE = 14

root = tk.Tk()
root.title("Minecraft Launcher")
root.geometry("800x600") # Increased size
root.configure(bg=BG_COLOR)

# --- Main Frame for Centering ---
main_frame = tk.Frame(root, bg=BG_COLOR)
# Pack the main frame to expand and fill, allowing content centering
main_frame.pack(expand=True, fill=tk.BOTH, padx=20, pady=20)

# Configure default font (optional, but can help consistency)
default_font = tk.font.nametofont("TkDefaultFont")
default_font.configure(family=FONT_FAMILY, size=FONT_SIZE_NORMAL)
root.option_add("*Font", default_font)

# --- Input Section ---
input_frame = tk.Frame(main_frame, bg=BG_COLOR)
input_frame.pack(pady=(50, 20)) # Add padding above

# Nickname Label
nickname_label = tk.Label(input_frame, text="Nickname:", bg=BG_COLOR, fg=FG_COLOR, font=(FONT_FAMILY, FONT_SIZE_LARGE))
nickname_label.pack(pady=(10, 5))

# Nickname Entry
nickname_var = tk.StringVar()
# Use tk.Entry for easier background/foreground color control than ttk.Entry
nickname_entry = tk.Entry(input_frame, textvariable=nickname_var, width=40,
                          bg=ENTRY_BG, fg=ENTRY_FG, relief=tk.FLAT, # Flat look
                          insertbackground=ENTRY_FG, # Cursor color
                          font=(FONT_FAMILY, FONT_SIZE_NORMAL))
nickname_entry.pack(pady=(0, 20))

# --- Action Button ---
# Use tk.Button for easier color control
action_button = tk.Button(main_frame, text="Install / Play / Update",
                          command=lambda: start_action_thread(),
                          bg=BUTTON_BG, fg=BUTTON_FG, relief=tk.FLAT,
                          activebackground="#45a049", # Slightly darker green on click
                          activeforeground=BUTTON_FG,
                          font=(FONT_FAMILY, FONT_SIZE_LARGE),
                          padx=20, pady=10) # Add padding inside button
action_button.pack(pady=20)

# --- Status Section (at the bottom) ---
status_frame = tk.Frame(root, bg=BG_COLOR)
# Pack the status frame at the bottom, filling X
status_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=10)

# Status Label
status_var = tk.StringVar()
status_var.set("Ready.")
status_label = tk.Label(status_frame, textvariable=status_var, wraplength=760, # Adjust wrap length
                         bg=BG_COLOR, fg=FG_COLOR, justify=tk.LEFT,
                         font=(FONT_FAMILY, FONT_SIZE_NORMAL))
status_label.pack(pady=(5, 5), fill=tk.X) # Fill horizontally

# Progress Bar
# Use ttk.Style for Progressbar customization
style = ttk.Style()
# Configure TProgressbar style (may vary slightly by OS theme)
style.theme_use('clam') # 'clam', 'alt', 'default', 'classic' - experiment if needed
style.configure("green.Horizontal.TProgressbar", troughcolor=ENTRY_BG, bordercolor=ENTRY_BG, background=BUTTON_BG, lightcolor=BUTTON_BG, darkcolor=BUTTON_BG)

progress_var = tk.DoubleVar()
progress_bar = ttk.Progressbar(status_frame, variable=progress_var, maximum=100, length=760, style="green.Horizontal.TProgressbar")
progress_bar.pack(pady=(5, 10), fill=tk.X) # Fill horizontally


# --- Core Functions ---

def update_status(message, progress=None, is_error=False):
    """Updates the status label and optionally the progress bar. Logs messages."""
    status_var.set(message)
    if progress is not None:
        progress_var.set(progress)
    # Log messages appropriately
    if is_error:
        logging.error(f"Status Update: {message}")
    else:
        logging.info(f"Status Update: {message}")
    root.update_idletasks()  # Force GUI update

# --- minecraft-launcher-lib Callback Functions ---
# Simplified progress handling for library callbacks

_lib_max_progress = 0
_lib_current_progress = 0

def _callback_set_status(text):
    """Callback for library status updates."""
    # Don't overwrite major step status, just log library details
    logging.info(f"Lib Status: {text}")
    # Optionally, could show this in a secondary label if needed

def _callback_set_progress(value):
    """Callback for library progress updates."""
    global _lib_current_progress
    _lib_current_progress = value
    # We won't directly update the main progress bar here,
    # as it's driven by major steps. We log it instead.
    if _lib_max_progress > 0:
        logging.info(f"Lib Progress: {value}/{_lib_max_progress} ({(value / _lib_max_progress * 100):.1f}%)")
    else:
        logging.info(f"Lib Progress: {value}/?")


def _callback_set_max(value):
    """Callback for library max progress value."""
    global _lib_max_progress, _lib_current_progress
    _lib_max_progress = value
    _lib_current_progress = 0 # Reset progress for this step
    logging.info(f"Lib Max Set: {value}")

# Dictionary for passing callbacks to the library
LIB_CALLBACKS = {
    "setStatus": _callback_set_status,
    "setProgress": _callback_set_progress,
    "setMax": _callback_set_max
}

# --- Configuration Handling ---

def load_local_config():
    """Loads nickname and installed version from local file."""
    global local_config
    if LOCAL_CONFIG_FILE.exists():
        logging.info(f"Attempting to load local config from {LOCAL_CONFIG_FILE}")
        try:
            with open(LOCAL_CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                # Validate basic structure if necessary
                if isinstance(loaded_data, dict):
                    local_config = loaded_data
                    nickname_var.set(local_config.get("nickname", ""))
                    logging.info(f"Loaded local config: {local_config}")
                else:
                    logging.warning("Local config file has invalid format. Using defaults.")
                    local_config = {"nickname": "", "installed_launcher_version": 0} # Reset
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding local config file {LOCAL_CONFIG_FILE}: {e}. Using defaults.")
            update_status(f"Error reading local config: {e}", is_error=True)
            local_config = {"nickname": "", "installed_launcher_version": 0} # Reset
        except Exception as e:
            logging.exception(f"Unexpected error loading local config: {e}")
            update_status(f"Error loading config: {e}", is_error=True)
            local_config = {"nickname": "", "installed_launcher_version": 0} # Reset
    else:
        logging.info("Local config file not found. Using defaults.")

def save_local_config():
    """Saves current nickname and installed version to local file."""
    global local_config
    current_nickname = nickname_var.get().strip()
    if not current_nickname:
        logging.warning("Attempted to save empty nickname. Skipping save.")
        # Optionally inform the user, though validation should happen before calling this
        return False # Indicate save failed due to validation

    local_config["nickname"] = current_nickname
    # installed_launcher_version is updated elsewhere before saving

    logging.info(f"Attempting to save local config: {local_config}")
    try:
        with open(LOCAL_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(local_config, f, indent=4)
        logging.info("Local config saved successfully.")
        return True
    except Exception as e:
        logging.exception(f"Error saving local config to {LOCAL_CONFIG_FILE}: {e}")
        update_status(f"Error saving config: {e}", is_error=True)
        return False

def fetch_launcher_config():
    """Fetches the latest config from the Gist URL."""
    global launcher_config
    update_status("Fetching remote configuration...", progress=5)
    try:
        # Add a timestamp to the URL to try and bypass caches
        timestamp = int(time.time())
        url_with_timestamp = f"{CONFIG_URL}?t={timestamp}"
        logging.info(f"Fetching config from: {url_with_timestamp}")

        # Use cache-control headers
        headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
        response = requests.get(url_with_timestamp, headers=headers, timeout=20) # Slightly longer timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        launcher_config = response.json()
        logging.info(f"Fetched remote config: {launcher_config}")
        update_status("Remote configuration fetched successfully.", progress=10)
        return True
    except requests.exceptions.Timeout:
        logging.error("Timeout occurred while fetching remote config.")
        update_status("Error: Timeout fetching remote configuration.", is_error=True)
        launcher_config = {}
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching remote config: {e}")
        update_status(f"Error fetching remote config: {e}", is_error=True)
        launcher_config = {} # Reset config on error
        return False
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding remote config JSON: {e}")
        update_status("Error: Invalid format in remote configuration file.", is_error=True)
        launcher_config = {}
        return False
    except Exception as e:
        logging.exception("An unexpected error occurred during config fetch.")
        update_status(f"An unexpected error occurred: {e}", is_error=True)
        launcher_config = {}
        return False


# --- Installation/Update/Launch Steps ---

def _ensure_directories():
    """Ensures Minecraft and Mods directories exist."""
    try:
        update_status("Checking Minecraft directory...", progress=12)
        MINECRAFT_DIR.mkdir(parents=True, exist_ok=True)
        MODS_DIR.mkdir(parents=True, exist_ok=True) # Ensure mods dir also exists early
        logging.info(f"Ensured Minecraft directory exists: {MINECRAFT_DIR}")
        logging.info(f"Ensured Mods directory exists: {MODS_DIR}")
        return True
    except OSError as e:
        logging.exception(f"Error creating directories: {e}")
        update_status(f"Error creating directories: {e}", is_error=True)
        return False

# Note: There were two identical definitions of _install_minecraft_version.
# I've kept only one, assuming it was an accidental duplication.
def _install_minecraft_version(mc_version, max_retries=5, retry_delay=10): # Increased retry_delay to 10 seconds
    """Installs the specified vanilla Minecraft version with retries."""
    update_status(f"Checking/Installing Minecraft {mc_version}...", progress=15)

    last_exception = None
    for attempt in range(1, max_retries + 1):
        logging.info(f"Attempt {attempt}/{max_retries} to install Minecraft {mc_version}...")
        if attempt > 1:
            update_status(f"Retrying Minecraft {mc_version} install (Attempt {attempt}/{max_retries})...", progress=15 + attempt) # Slightly bump progress
            time.sleep(retry_delay) # Wait before retrying

        try:
            logging.info(f"Calling minecraft_launcher_lib.install.install_minecraft_version for {mc_version} (Attempt {attempt})")
            minecraft_launcher_lib.install.install_minecraft_version(
                mc_version,
                str(MINECRAFT_DIR),
                callback=LIB_CALLBACKS  # Use the defined callbacks
            )
            logging.info(f"Finished minecraft_launcher_lib.install.install_minecraft_version for {mc_version} on attempt {attempt}.")
            update_status(f"Minecraft {mc_version} ready.", progress=30)
            return True # Success, exit the loop and function
        except Exception as e:
            last_exception = e
            logging.warning(f"Attempt {attempt} failed for installing {mc_version}: {e}")
            # Don't immediately update status as error, wait until all retries fail

    # If loop finishes without returning True, all attempts failed
    logging.error(f"All {max_retries} attempts to install Minecraft {mc_version} failed.")
    logging.exception(f"Last error during install attempt for {mc_version}: {last_exception}")
    error_msg = f"Failed to install Minecraft {mc_version} after {max_retries} attempts. Last error: {last_exception}"
    update_status(error_msg, is_error=True)

    # Check if it exists anyway, maybe a previous partial install is usable?
    logging.info(f"Checking if Minecraft {mc_version} exists despite installation failure...")
    try:
        installed_versions = minecraft_launcher_lib.utils.get_installed_versions(str(MINECRAFT_DIR))
        if any(v['id'] == mc_version for v in installed_versions):
            logging.warning(f"Installation failed, but found existing Minecraft {mc_version}. Attempting to continue.")
            update_status(f"Using existing Minecraft {mc_version}.", progress=30)
            return True # Allow continuing if it exists
    except Exception as check_e:
        logging.error(f"Could not even check for existing versions after install error: {check_e}")

    return False # Definite failure


def _install_forge(mc_version, loader_version):
    """Installs Forge using the official installer with enhanced checks and logging."""
    version_id = f"{mc_version}-forge-{loader_version}" # Expected ID format
    update_status(f"Checking/Installing Forge {loader_version}...", progress=35)
    installer_filename = f"forge-{mc_version}-{loader_version}-installer.jar"
    installer_path = MINECRAFT_DIR / installer_filename # Store installer inside .minecraft temporarily
    installer_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_version}-{loader_version}/{installer_filename}"

    # --- Pre-flight Checks ---
    # 1. Check Java Installation
    java_path = shutil.which('java')
    if not java_path:
        logging.error("Forge install check failed: 'java' command not found. Is Java installed and in PATH?")
        update_status("Error: Java not found. Please install Java and ensure it's in your PATH.", is_error=True)
        return None
    logging.info(f"Java executable found at: {java_path}")

    # 2. Check Forge Installer URL Availability (HEAD request)
    update_status("Checking Forge installer availability...", progress=37)
    logging.info(f"Checking Forge installer URL (HEAD): {installer_url}")
    try:
        response = requests.head(installer_url, timeout=15)
        response.raise_for_status() # Check for 4xx/5xx errors
        logging.info(f"Forge installer URL check successful (Status: {response.status_code}).")
    except requests.exceptions.RequestException as e:
        logging.error(f"Forge installer URL check failed: {e}")
        if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 404:
            update_status(f"Error: Forge installer for {mc_version}-{loader_version} not found at expected URL.", is_error=True)
        else:
            update_status(f"Error checking Forge installer URL: {e}", is_error=True)
        return None

    # --- Installation Process ---
    download_success = False
    download_attempts = 3
    last_download_exception = None

    for attempt in range(1, download_attempts + 1):
        if attempt > 1:
            logging.warning(f"Retrying Forge installer download (Attempt {attempt}/{download_attempts})...")
            update_status(f"Retrying Forge download (Attempt {attempt})...", progress=40)
            time.sleep(5) # Wait 5 seconds before retrying download

        try:
            # 3. Download Installer
            update_status(f"Downloading Forge installer (Attempt {attempt})...", progress=40)
            logging.info(f"Attempt {attempt}: Downloading Forge installer from {installer_url} to {installer_path}")
            response = requests.get(installer_url, stream=True, timeout=120) # Longer timeout for download
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            bytes_downloaded = 0
            with open(installer_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    # Check for chunk to avoid issues with empty chunks on connection errors
                    if chunk:
                        f.write(chunk)
                        bytes_downloaded += len(chunk)
                        if total_size > 0:
                            dl_progress = (bytes_downloaded / total_size) * 10 # Scale download to 10% of this step
                            update_status(f"Downloading Forge installer... {bytes_downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB", progress=40 + dl_progress)
                    else:
                        # If we receive an empty chunk, it might indicate a problem, break inner loop
                        logging.warning("Received empty chunk during download, might indicate connection issue.")
                        # We might need more robust handling here, but let's see if requests raises an error itself.
                        pass # Continue for now, rely on requests exceptions

            # Check if the full file was downloaded (if size is known)
            if total_size > 0 and bytes_downloaded < total_size:
                raise requests.exceptions.RequestException(f"Incomplete download: Expected {total_size} bytes, got {bytes_downloaded}")

            logging.info(f"Forge installer downloaded successfully on attempt {attempt} ({bytes_downloaded} bytes).")
            update_status("Forge installer downloaded.", progress=50)
            download_success = True
            break # Exit retry loop on success

        except requests.exceptions.RequestException as e:
            last_download_exception = e
            logging.error(f"Attempt {attempt} failed to download Forge installer: {e}")
            # Clean up potentially incomplete download
            if installer_path.exists():
                try:
                    installer_path.unlink()
                except OSError: pass # Ignore cleanup error
            # Continue to next attempt

    if not download_success:
        logging.error(f"Failed to download Forge installer after {download_attempts} attempts.")
        update_status(f"Error downloading Forge installer after multiple attempts: {last_download_exception}", is_error=True)
        return None # Exit if download failed

    # --- Continue only if download was successful ---
    try:
        # 4. Run Installer
        update_status("Running Forge installer...", progress=51)
        command = [java_path, "-jar", str(installer_path), "--installClient"]
        logging.info(f"Running Forge installer command: {' '.join(command)}")
        process = subprocess.run(
            command,
            cwd=str(MINECRAFT_DIR), # Run from Minecraft dir context
            check=False, # Don't raise exception immediately
            capture_output=True, # Capture stdout/stderr
            text=True,
            encoding='utf-8', # Be explicit about encoding
            errors='replace' # Handle potential encoding errors in output
        )

        # Log stdout/stderr regardless of success, but log errors more prominently
        if process.stdout:
            logging.info(f"Forge Installer STDOUT:\n{process.stdout.strip()}")
        if process.stderr:
            # Log stderr as warning even on success, as installers sometimes print info there
            log_level = logging.ERROR if process.returncode != 0 else logging.WARNING
            logging.log(log_level, f"Forge Installer STDERR:\n{process.stderr.strip()}")

        # Check return code
        if process.returncode != 0:
            logging.error(f"Forge installer failed with return code {process.returncode}.")
            error_message = f"Forge installer failed (code {process.returncode}). Check launcher.log for details."
            # Try to parse common errors from stderr
            if "java.net" in process.stderr:
                error_message = "Forge installer failed: Network error during installation."
            elif "FileNotFoundException" in process.stderr:
                error_message = "Forge installer failed: Could not find necessary files."
            elif "Could not find main class" in process.stderr:
                error_message = "Forge installer failed: Corrupted download or Java issue."
            update_status(error_message, is_error=True)
            return None # Installation failed

        logging.info(f"Forge installer process completed successfully (Return Code: {process.returncode}).")
        update_status("Forge installer finished.", progress=58)

        # 5. Verify Installation
        logging.info(f"Verifying Forge installation by checking for version ID: {version_id}")
        try:
            installed_versions = minecraft_launcher_lib.utils.get_installed_versions(str(MINECRAFT_DIR))
            if any(v['id'] == version_id for v in installed_versions):
                logging.info(f"Forge version {version_id} successfully verified in versions list.")
                update_status(f"Forge {loader_version} installed successfully.", progress=60)
                return version_id # Success!
            else:
                logging.error(f"Forge installer ran successfully, but version ID '{version_id}' was not found in versions list afterwards.")
                update_status(f"Warning: Forge install seemed successful, but verification failed.", is_error=True)
                return None # Verification failed
        except Exception as check_e:
            logging.exception(f"Error verifying Forge installation: {check_e}")
            update_status(f"Warning: Error verifying Forge install: {check_e}", is_error=True)
            return None # Verification failed

    except requests.exceptions.RequestException as e:
        logging.exception(f"Error downloading Forge installer: {e}")
        update_status(f"Error downloading Forge installer: {e}", is_error=True)
        return None
    except Exception as e:
        logging.exception(f"An unexpected error occurred during Forge installation: {e}")
        update_status(f"Error installing Forge: {e}", is_error=True)
        return None
    finally:
        # 6. Clean up installer file
        if installer_path.exists():
            try:
                installer_path.unlink()
                logging.info(f"Forge installer file '{installer_path.name}' cleaned up.")
            except OSError as e:
                logging.warning(f"Could not delete Forge installer {installer_path}: {e}")


def _install_fabric(mc_version, loader_version, max_retries=3, retry_delay=10):
    """Installs Fabric using minecraft-launcher-lib with retries."""
    update_status(f"Checking/Installing Fabric {loader_version}...", progress=35)
    last_exception = None

    for attempt in range(1, max_retries + 1):
        logging.info(f"Attempt {attempt}/{max_retries} to install Fabric {loader_version} for {mc_version}...")
        if attempt > 1:
            update_status(f"Retrying Fabric {loader_version} install (Attempt {attempt}/{max_retries})...", progress=35 + attempt)
            time.sleep(retry_delay)

        try:
            logging.info(f"Calling minecraft_launcher_lib.fabric.install_fabric for {mc_version}, {loader_version} (Attempt {attempt})")
            minecraft_launcher_lib.fabric.install_fabric(
                mc_version,
                loader_version,
                str(MINECRAFT_DIR),
                callback=LIB_CALLBACKS
            )
            # Log success immediately after the call returns
            logging.info(f"Call to install_fabric for {mc_version}, {loader_version} completed successfully on attempt {attempt}.")
            update_status(f"Fabric {loader_version} installed.", progress=60)

            # Try to find the exact version ID created by the installer
            installed_versions = minecraft_launcher_lib.utils.get_installed_versions(str(MINECRAFT_DIR))
            for v in installed_versions:
                # Heuristic: Match type, mc_version, 'fabric', and loader_version in the ID
                if v['type'] == 'release' and mc_version in v['id'] and 'fabric' in v['id'] and loader_version in v['id']:
                    logging.info(f"Detected Fabric version ID: {v['id']}")
                    return v['id'] # Return the detected ID

            # Fallback if exact ID not found (might be less reliable)
            fallback_id = f"fabric-loader-{loader_version}-{mc_version}"
            logging.warning(f"Could not auto-detect exact Fabric version ID. Using fallback: {fallback_id}")
            return fallback_id # Return the fallback ID

        except Exception as e:
            last_exception = e
            logging.warning(f"Attempt {attempt} failed for installing Fabric {loader_version}: {e}")
            # Don't update status as error immediately, wait for all retries

    # If loop finishes without returning a version ID, all attempts failed
    logging.error(f"All {max_retries} attempts to install Fabric {loader_version} failed.")
    logging.exception(f"Last error during Fabric install attempt: {last_exception}")
    update_status(f"Failed to install Fabric {loader_version} after {max_retries} attempts: {last_exception}", is_error=True)
    return None


def _update_modpack(mods_url, gist_launcher_version):
    """Handles clearing old mods and downloading/extracting the new modpack."""
    installed_launcher_version = local_config.get("installed_launcher_version", 0)
    logging.info(f"Checking modpack update: Gist Version={gist_launcher_version}, Local Version={installed_launcher_version}")
    needs_mod_update = gist_launcher_version > installed_launcher_version
    modpack_configured = bool(mods_url)

    if not modpack_configured:
        update_status("No modpack configured.", progress=95)
        # Optional: Clear existing mods if none are configured
        if MODS_DIR.exists() and any(MODS_DIR.iterdir()): # Check if not empty
            update_status("No modpack configured. Clearing local mods folder...", progress=70)
            if _clear_mods_folder():
                update_status("Local mods folder cleared.", progress=95)
            else:
                # Error handled in _clear_mods_folder
                return False # Stop if clearing failed
        return True # Success (no update needed/done)

    if not needs_mod_update:
        logging.info("Modpack is up-to-date. No update needed.")
        update_status("Modpack is up-to-date.", progress=95)
        return True # Success (no update needed)

    # --- Mod Update Required ---
    logging.info(f"Newer modpack version ({gist_launcher_version}) found. Starting update process.")
    update_status(f"New version ({gist_launcher_version}) found. Updating modpack...", progress=65)

    # 1. Clear existing mods
    logging.info("Attempting to clear mods folder...")
    clear_success = _clear_mods_folder()
    logging.info(f"Mods folder clear attempt result: {clear_success}")
    if not clear_success:
        return False # Stop if clearing failed

    # 2. Download new mods
    update_status(f"Downloading modpack...", progress=75)
    download_path = Path("mods_temp.zip") # Temporary file name
    try:
        # Determine download method based on URL
        is_direct_zip = mods_url.lower().startswith(('http://', 'https://')) and mods_url.lower().endswith('.zip')

        if is_direct_zip:
            logging.info(f"Downloading modpack from direct URL: {mods_url}")
            response = requests.get(mods_url, stream=True, timeout=180) # Longer timeout for potentially large files
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            bytes_downloaded = 0
            with open(download_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    bytes_downloaded += len(chunk)
                    if total_size > 0:
                        # Scale download progress (75% to 85% range)
                        dl_progress = (bytes_downloaded / total_size) * 10
                        update_status(f"Downloading modpack... {bytes_downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB", progress=75 + dl_progress)
            logging.info(f"Modpack downloaded successfully ({bytes_downloaded} bytes).")
            update_status("Modpack downloaded. Extracting...", progress=85)
        else:
            # Assume Google Drive URL, use gdown
            logging.info(f"Downloading modpack from Google Drive URL: {mods_url}")
            # Note: gdown doesn't easily provide progress for the main progress bar
            # We'll just show a generic message during gdown download
            update_status("Downloading modpack (Google Drive)...", progress=78) # Indicate gdown is working
            gdown.download(mods_url, str(download_path), quiet=False, fuzzy=True)
            logging.info(f"Modpack downloaded via gdown to {download_path}")
            update_status("Modpack downloaded. Extracting...", progress=85)

        # 3. Extract mods (common step)
        logging.info(f"Attempting to extract {download_path} to {MODS_DIR}")
        try:
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                # Log contents before extraction
                zip_contents = zip_ref.namelist()
                logging.info(f"Zip file contents: {zip_contents}")
                zip_ref.extractall(MODS_DIR)
            logging.info(f"Successfully extracted zip to {MODS_DIR}")
            # Log contents of mods dir after extraction
            try:
                mods_dir_contents = os.listdir(MODS_DIR)
                logging.info(f"Mods directory contents after extraction: {mods_dir_contents}")
            except Exception as list_e:
                logging.warning(f"Could not list mods directory after extraction: {list_e}")

            # --- Check for nested directory structure ---
            try:
                mods_dir_items = list(MODS_DIR.iterdir())
                if len(mods_dir_items) == 1 and mods_dir_items[0].is_dir():
                    nested_dir = mods_dir_items[0]
                    logging.warning(f"Detected single nested directory after extraction: {nested_dir.name}. Moving contents up.")
                    update_status("Adjusting nested mod directory structure...", progress=93)
                    moved_count = 0
                    for item in nested_dir.iterdir():
                        try:
                            target_path = MODS_DIR / item.name
                            shutil.move(str(item), str(target_path))
                            moved_count += 1
                        except Exception as move_e:
                            logging.error(f"Failed to move item {item.name} from nested directory: {move_e}")
                            # Decide if this is critical - maybe stop? For now, just log.
                    logging.info(f"Moved {moved_count} items from {nested_dir.name} to {MODS_DIR}.")
                    # Clean up the now potentially empty nested directory
                    try:
                        nested_dir.rmdir() # Fails if not empty, which is good.
                        logging.info(f"Removed empty nested directory: {nested_dir.name}")
                    except OSError as rmdir_e:
                        logging.warning(f"Could not remove nested directory {nested_dir.name} (maybe not empty?): {rmdir_e}")
                else:
                    logging.info("Mods directory structure seems correct (not a single nested directory).")

            except Exception as structure_check_e:
                logging.exception(f"Error checking/adjusting mod directory structure: {structure_check_e}")
                # Don't fail the whole process, but log it.

            update_status("Mods extracted.", progress=95)

        except zipfile.BadZipFile:
            logging.error(f"Error extracting modpack: Downloaded file '{download_path}' is not a valid zip file.")
            update_status("Error: Downloaded modpack file is corrupted or not a zip.", is_error=True)
            return False # Extraction failed
        except Exception as extract_e:
            logging.exception(f"An unexpected error occurred during modpack extraction: {extract_e}")
            update_status(f"Error extracting mods: {extract_e}", is_error=True)
            return False # Extraction failed


        # 4. Update local config version *after* successful extraction
        local_config["installed_launcher_version"] = gist_launcher_version
        if not save_local_config(): # Save the updated version number
            # Error saving config, but mods are updated. Log warning.
            logging.warning("Modpack updated, but failed to save new version number to local config.")
            update_status("Warning: Modpack updated, but failed to save config.", is_error=True)
            # Continue anyway, as the mods are present

        update_status("Modpack updated successfully.")
        return True

    except requests.exceptions.RequestException as e:
        logging.exception(f"Error downloading modpack via requests: {e}")
        update_status(f"Error downloading modpack: {e}", is_error=True)
        return False
    except gdown.exceptions.GDownException as e:
        logging.error(f"gdown error downloading modpack: {e}")
        update_status(f"Error downloading modpack (check GDrive URL/permissions?): {e}", is_error=True)
        return False
    except zipfile.BadZipFile:
        logging.error(f"Error extracting modpack: Downloaded file '{download_path}' is not a valid zip file.")
        update_status("Error: Downloaded modpack file is corrupted or not a zip.", is_error=True)
        return False
    except Exception as e:
        logging.exception(f"An unexpected error occurred during modpack update: {e}")
        update_status(f"Error updating mods: {e}", is_error=True)
        return False
    finally:
        # Clean up downloaded zip file
        if download_path.exists():
            try:
                download_path.unlink()
                logging.info("Temporary modpack zip file deleted.")
            except OSError as e:
                logging.warning(f"Could not delete temporary modpack file {download_path}: {e}")


def _clear_mods_folder():
    """Clears the contents of the mods folder."""
    if not MODS_DIR.exists():
        logging.info("Mods directory does not exist, nothing to clear.")
        return True # Nothing to do

    update_status("Deleting old mods...", progress=70)
    logging.info(f"Clearing mods folder: {MODS_DIR}")
    items_deleted = 0
    items_failed = 0
    for item in MODS_DIR.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
                items_deleted += 1
            elif item.is_dir():
                shutil.rmtree(item)
                items_deleted += 1
            logging.debug(f"Deleted: {item.name}")
        except Exception as e:
            items_failed += 1
            logging.error(f"Failed to delete {item}: {e}")

    if items_failed > 0:
        logging.error(f"Failed to delete {items_failed} items in mods folder.")
        update_status(f"Error: Could not delete all old mods (failed: {items_failed}). Check permissions.", is_error=True)
        return False
    else:
        logging.info(f"Successfully deleted {items_deleted} items from mods folder.")
        update_status("Old mods deleted.", progress=75)
        return True


def _launch_minecraft(version_id, nickname):
    """Launches Minecraft using the specified version ID and nickname."""
    update_status(f"Preparing to launch Minecraft {version_id}...", progress=96)
    logging.info(f"Preparing launch for version='{version_id}', nickname='{nickname}'")

    options = {
        "username": nickname,
        "uuid": str(uuid.uuid3(uuid.NAMESPACE_DNS, nickname)), # Offline mode UUID
        "token": "0", # Offline mode token
        # Use jvm_args from remote config if specified, otherwise default empty list
        "jvmArguments": launcher_config.get("jvm_args", [])
    }
    logging.info(f"Using launch options: {options}")

    try:
        minecraft_command = minecraft_launcher_lib.command.get_minecraft_command(version_id, str(MINECRAFT_DIR), options)
        logging.info(f"Generated Minecraft command: {' '.join(minecraft_command)}")
    except Exception as e:
        # This could happen if the version metadata is corrupted/missing
        logging.exception(f"Error creating launch command for {version_id}: {e}")
        update_status(f"Error preparing launch command: {e}", is_error=True)
        return False

    update_status(f"Launching Minecraft as {nickname}...", progress=98)
    try:
        subprocess.Popen(minecraft_command)
        logging.info("Minecraft process started.")
        update_status("Minecraft launched! You can close this launcher.", progress=100)
        # Consider closing the launcher automatically after a delay?
        # root.after(5000, root.destroy)
        return True
    except FileNotFoundError:
        # This typically means 'java' wasn't found in the PATH
        logging.error("Launch failed: 'java' command not found. Is Java installed and in PATH?")
        update_status("Error: Java not found. Please install Java and ensure it's in your PATH.", is_error=True)
        return False
    except Exception as e:
        logging.exception(f"An unexpected error occurred during Minecraft launch: {e}")
        update_status(f"Error launching Minecraft: {e}", is_error=True)
        return False


# --- Main Action Function ---

def perform_install_update_launch():
    """The main logic sequence, orchestrating the steps."""
    action_button.config(state=tk.DISABLED)
    update_status("Starting process...", progress=0)
    logging.info("="*20 + " Starting Action " + "="*20)

    try:
        # 1. Validate and Save Nickname
        nickname = nickname_var.get().strip()
        if not nickname:
            update_status("Error: Nickname cannot be empty.", is_error=True)
            logging.error("Action aborted: Nickname is empty.")
            return # Exit function early
        if not save_local_config():
            # Error message shown by save_local_config
            logging.error("Action aborted: Failed to save local config.")
            return # Exit function early
        logging.info(f"Using nickname: {nickname}")

        # 2. Fetch Remote Config
        if not fetch_launcher_config():
            # Error message shown by fetch_launcher_config
            logging.error("Action aborted: Failed to fetch remote config.")
            return # Exit function early

        # 3. Ensure Directories Exist
        if not _ensure_directories():
            logging.error("Action aborted: Failed to ensure directories.")
            return # Exit function early

        # 4. Get Config Details
        mc_version = launcher_config.get("mc_version")
        raw_loader_type = launcher_config.get("loader_type")
        loader_type = str(raw_loader_type).lower() if raw_loader_type is not None else ""
        loader_version = launcher_config.get("loader_version")
        mods_url = launcher_config.get("mods_url")
        gist_launcher_version = launcher_config.get("launcher_version", 0) # Default to 0 if missing
        version_name = launcher_config.get("version_name", f"{mc_version} ({loader_type or 'Vanilla'})") # More descriptive default

        logging.info(f"Configuration: MC={mc_version}, Loader={loader_type} {loader_version}, Modpack Version={gist_launcher_version}, Name={version_name}")

        if not mc_version:
            update_status("Error: 'mc_version' missing in remote config.", is_error=True)
            logging.error("Action aborted: 'mc_version' missing in remote config.")
            return # Exit function early

        update_status(f"Preparing to install/update: {version_name}", progress=11)

        # 5. Install Vanilla Minecraft
        if not _install_minecraft_version(mc_version):
            logging.error(f"Action aborted: Failed to install/verify Minecraft {mc_version}.")
            return # Exit function early

        # 6. Install Loader (Forge/Fabric)
        version_id_to_launch = mc_version # Default to vanilla
        if loader_type == "forge" and loader_version:
            version_id_to_launch = _install_forge(mc_version, loader_version)
            if not version_id_to_launch:
                logging.error(f"Action aborted: Failed to install Forge {loader_version}.")
                return # Exit function early
        elif loader_type == "fabric" and loader_version:
            version_id_to_launch = _install_fabric(mc_version, loader_version)
            if not version_id_to_launch:
                logging.error(f"Action aborted: Failed to install Fabric {loader_version}.")
                return # Exit function early
        else:
            update_status("Using Vanilla Minecraft.", progress=60)
            logging.info("No loader specified or required. Using Vanilla.")

        # 7. Check/Update Modpack
        if not _update_modpack(mods_url, gist_launcher_version):
            logging.error("Action aborted: Failed to update modpack.")
            return # Exit function early

        # 8. Launch Game
        if not _launch_minecraft(version_id_to_launch, nickname):
            logging.error("Action aborted: Failed to launch Minecraft.")
            # Button will be re-enabled by finally block
        else:
            logging.info("Launch sequence completed successfully.")
            # Optionally close launcher here?
            # Keep button disabled on successful launch - user can close manually

    except Exception as e:
        # Catch any unexpected errors during the sequence
        logging.exception("An unexpected error occurred during the main action sequence.")
        update_status(f"An unexpected error occurred: {e}", is_error=True)
        # Button will be re-enabled by finally block

    finally:
        # Final step: Re-enable button ONLY if launch was not successful or an error occurred
        logging.info("="*20 + " Action Finished " + "="*20)
        current_status = status_var.get()
        if not current_status.startswith("Minecraft launched!"):
            action_button.config(state=tk.NORMAL) # Re-enable on failure or error


def start_action_thread():
    """Starts the main installation/update/launch process in a separate thread."""
    # Disable button immediately to prevent double clicks
    action_button.config(state=tk.DISABLED)
    update_status("Starting...", progress=0) # Initial status

    action_thread = threading.Thread(target=perform_install_update_launch, daemon=True)
    action_thread.start()

# --- Main Execution ---
if __name__ == "__main__":
    logging.info("Launcher application started.")
    load_local_config()  # Load nickname/version on startup
    root.mainloop()
    logging.info("Launcher application closed.")