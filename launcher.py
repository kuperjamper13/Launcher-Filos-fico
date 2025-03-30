import tkinter as tk
from tkinter import ttk
import os
import json
import requests
import threading
import subprocess
import zipfile
import gdown # Placeholder for now, will need installation
import minecraft_launcher_lib # Placeholder for now, will need installation
from pathlib import Path
import uuid # Add the uuid import

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
launcher_config = {} # To store fetched config from Gist
local_config = {"nickname": "", "installed_launcher_version": 0} # Default local settings

# --- GUI Setup ---
root = tk.Tk()
root.title("Minecraft Launcher")
root.geometry("400x250") # Adjusted size

# Nickname Input
nickname_label = ttk.Label(root, text="Nickname:")
nickname_label.pack(pady=(10, 0))
nickname_var = tk.StringVar()
nickname_entry = ttk.Entry(root, textvariable=nickname_var, width=30)
nickname_entry.pack()

# Main Button
action_button = ttk.Button(root, text="Install / Play / Update", command=lambda: start_action_thread()) # Placeholder command
action_button.pack(pady=20)

# Status Label
status_var = tk.StringVar()
status_var.set("Ready.")
status_label = ttk.Label(root, textvariable=status_var, wraplength=380) # Wraplength for longer messages
status_label.pack(pady=(5, 0))

# Progress Bar
progress_var = tk.DoubleVar()
progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100, length=300)
progress_bar.pack(pady=10)

# --- Callback Functions for minecraft-launcher-lib ---
# These need to be defined before they are used in perform_install_update_launch

# Keep track of the total progress for multi-step installations
current_progress = 0
max_progress = 0

def update_progress_label(label_text):
    """Callback to update the status label."""
    # We might want finer control than just replacing the whole status
    # For now, let's append or just show the latest step
    update_status(f"Progress: {label_text}")

def update_progress_bar(progress):
    """Callback to update the progress bar (0-max_progress)."""
    # Scale the progress from minecraft-launcher-lib (0 to max_progress) to our bar (0-100)
    if max_progress > 0:
        scaled_progress = (current_progress + progress) / max_progress * 100
        progress_var.set(scaled_progress)
        root.update_idletasks()

def set_progress_max(new_max):
    """Callback to set the maximum value for the progress bar step."""
    global max_progress, current_progress
    # If this is a new step, add the previous max to current progress
    current_progress += max_progress
    max_progress = new_max
    # Reset the visual progress for the new step, but the overall scale adjusts
    # progress_var.set(current_progress / max_progress * 100 if max_progress > 0 else 0)
    # print(f"Set max progress for step: {new_max}, Total progress base: {current_progress}")


# --- Core Functions ---

def update_status(message, progress=None):
    """Updates the status label and optionally the progress bar."""
    status_var.set(message)
    if progress is not None:
        progress_var.set(progress)
    else:
        # If progress is not specified, maybe reset or indicate indeterminate?
        # For now, just setting text. Could add indeterminate mode later.
        pass
    root.update_idletasks() # Force GUI update

def load_local_config():
    """Loads nickname and installed version from local file."""
    global local_config
    if LOCAL_CONFIG_FILE.exists():
        try:
            with open(LOCAL_CONFIG_FILE, 'r') as f:
                local_config = json.load(f)
                nickname_var.set(local_config.get("nickname", ""))
                print(f"Loaded local config: {local_config}")
        except json.JSONDecodeError:
            print(f"Error reading local config file {LOCAL_CONFIG_FILE}. Using defaults.")
            update_status(f"Error reading {LOCAL_CONFIG_FILE}. Using defaults.")
        except Exception as e:
            print(f"Unexpected error loading local config: {e}")
            update_status(f"Error loading config: {e}")
    else:
        print("Local config file not found. Using defaults.")

def save_local_config():
    """Saves current nickname and installed version to local file."""
    global local_config
    local_config["nickname"] = nickname_var.get()
    # installed_launcher_version will be updated after successful install/update
    try:
        with open(LOCAL_CONFIG_FILE, 'w') as f:
            json.dump(local_config, f, indent=4)
        print(f"Saved local config: {local_config}")
    except Exception as e:
        print(f"Error saving local config: {e}")
        update_status(f"Error saving config: {e}")


def fetch_launcher_config():
    """Fetches the latest config from the Gist URL."""
    global launcher_config
    update_status("Fetching remote configuration...")
    try:
        # Add a timestamp to the URL to try and bypass caches
        import time
        timestamp = int(time.time())
        url_with_timestamp = f"{CONFIG_URL}?t={timestamp}"
        print(f"Fetching config from: {url_with_timestamp}") # Debug print

        # Use cache-control headers as well, just in case
        headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
        response = requests.get(url_with_timestamp, headers=headers, timeout=15) # Increased timeout slightly
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        launcher_config = response.json()
        print(f"Fetched remote config: {launcher_config}")
        update_status("Remote configuration fetched successfully.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error fetching remote config: {e}")
        update_status(f"Error fetching remote config: {e}")
        launcher_config = {} # Reset config on error
        return False
    except json.JSONDecodeError:
        print("Error decoding remote config JSON.")
        update_status("Error decoding remote config JSON.")
        launcher_config = {}
        return False

def perform_install_update_launch():
    """The main logic executed in a separate thread."""
    action_button.config(state=tk.DISABLED) # Disable button during operation
    update_status("Starting process...")
    progress_var.set(0)

    # 1. Save Nickname
    nickname = nickname_var.get().strip()
    if not nickname:
        update_status("Error: Nickname cannot be empty.")
        action_button.config(state=tk.NORMAL)
        return
    save_local_config()

    # 2. Fetch Remote Config
    if not fetch_launcher_config():
        # Error message already shown by fetch_launcher_config
        action_button.config(state=tk.NORMAL)
        return

    global current_progress, max_progress # Reset progress tracking for each run
    current_progress = 0
    max_progress = 0

    try:
        # 3. Ensure Minecraft Directory Exists
        update_status("Checking Minecraft directory...")
        MINECRAFT_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Ensured Minecraft directory exists: {MINECRAFT_DIR}")
        progress_var.set(5) # Small initial progress

        # --- Get Config Details ---
        mc_version = launcher_config.get("mc_version")
        # Handle potential None type for loader_type before calling .lower()
        raw_loader_type = launcher_config.get("loader_type")
        loader_type = str(raw_loader_type).lower() if raw_loader_type is not None else "" # Use "" if None
        loader_version = launcher_config.get("loader_version")
        mods_url = launcher_config.get("mods_url")
        gist_launcher_version = launcher_config.get("launcher_version", 0)
        version_name = launcher_config.get("version_name", mc_version) # Use mc_version if name missing

        if not mc_version:
            update_status("Error: 'mc_version' missing in remote config.")
            action_button.config(state=tk.NORMAL)
            return

        # --- Installation Steps ---
        update_status(f"Preparing to install/update: {version_name}")
        progress_var.set(10)

        # Callbacks dictionary
        callback_handler = {
            "setStatus": update_progress_label,
            "setProgress": update_progress_bar,
            "setMax": set_progress_max
        }

        # 4. Check/Install Vanilla Minecraft
        update_status(f"Checking/Installing Minecraft {mc_version}...")
        minecraft_launcher_lib.install.install_minecraft_version(mc_version, str(MINECRAFT_DIR), callback=callback_handler)
        update_status(f"Minecraft {mc_version} installed.", progress=current_progress / max_progress * 100 if max_progress else 30) # Estimate progress

        # 5. Check/Install Loader (Forge/Fabric)
        version_id = mc_version # Default to vanilla
        if loader_type == "forge" and loader_version:
            version_id = f"{mc_version}-forge-{loader_version}"
            update_status(f"Checking/Installing Forge {loader_version} for {mc_version} (ID: {version_id})...")
            print(f"Attempting to install Forge with version_id: {version_id}") # Debug print
            try:
                minecraft_launcher_lib.forge.install_forge_version(version_id, str(MINECRAFT_DIR), callback=callback_handler)
                update_status(f"Forge {loader_version} installed.", progress=current_progress / max_progress * 100 if max_progress else 60)
            except Exception as e:
                # Catch potential install error specifically here
                print(f"Error installing Forge version {version_id}: {e}")
                update_status(f"Error installing Forge {loader_version}: {e}. Check version format/availability.")
                # Re-raise or handle as needed - for now, let the main try/except catch it to stop the process
                raise e # Propagate error to the main handler
        elif loader_type == "fabric" and loader_version:
            # Fabric version ID might just be mc_version, but install_fabric needs loader version
            update_status(f"Checking/Installing Fabric {loader_version} for {mc_version}...")
            minecraft_launcher_lib.fabric.install_fabric(mc_version, loader_version, str(MINECRAFT_DIR), callback=callback_handler)
            # Fabric often uses an ID like 'fabric-loader-x.y.z-1.xx.x' - let's try to find it
            installed_fabric_versions = minecraft_launcher_lib.utils.get_installed_versions(str(MINECRAFT_DIR))
            fabric_id_found = False
            for v in installed_fabric_versions:
                if v['type'] == 'release' and mc_version in v['id'] and 'fabric' in v['id'] and loader_version in v['id']:
                     version_id = v['id']
                     fabric_id_found = True
                     break
            if not fabric_id_found:
                 print(f"Warning: Could not auto-detect exact Fabric version ID. Using {mc_version}. Launch might fail.")
                 # Fallback or use a known pattern if possible
                 version_id = f"fabric-loader-{loader_version}-{mc_version}" # Common pattern, but might vary
            update_status(f"Fabric {loader_version} installed.", progress=current_progress / max_progress * 100 if max_progress else 60)
        else:
            update_status("Using Vanilla Minecraft.", progress=60)


        # 6. Check/Update Mods
        update_status("Checking for modpack updates...")
        installed_launcher_version = local_config.get("installed_launcher_version", 0)

        needs_mod_update = gist_launcher_version > installed_launcher_version
        modpack_configured = bool(mods_url)

        if needs_mod_update and modpack_configured:
            update_status(f"New version ({gist_launcher_version}) found. Updating modpack...")
            MODS_DIR.mkdir(parents=True, exist_ok=True) # Ensure mods dir exists

            # Clear existing mods
            update_status("Deleting old mods...")
            for item in MODS_DIR.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        import shutil
                        shutil.rmtree(item)
                except Exception as e:
                    print(f"Warning: Could not delete {item}: {e}")
            update_status("Old mods deleted.", progress=75)

            # Download new mods
            update_status(f"Downloading modpack from Google Drive...")
            download_path = Path("mods_temp.zip")
            try:
                # gdown needs the file ID from the share link, or the full link
                gdown.download(mods_url, str(download_path), quiet=False, fuzzy=True) # fuzzy=True helps with different link formats
                update_status("Modpack downloaded. Extracting...", progress=85)

                # Extract mods
                with zipfile.ZipFile(download_path, 'r') as zip_ref:
                    zip_ref.extractall(MODS_DIR)
                update_status("Mods extracted.", progress=95)

                # Clean up downloaded zip
                download_path.unlink()

                # Update local config version
                local_config["installed_launcher_version"] = gist_launcher_version
                save_local_config() # Save the updated version number
                update_status("Modpack updated successfully.")

            except Exception as e:
                print(f"Error during modpack update: {e}")
                update_status(f"Error updating mods: {e}")
                # Decide if we should proceed without mods or stop
                # For now, let's stop to avoid launching a broken state
                action_button.config(state=tk.NORMAL)
                if download_path.exists(): download_path.unlink() # Clean up partial download
                return
        elif not modpack_configured and MODS_DIR.exists():
             # If config says no mods, but mods folder exists, clear it
             update_status("No modpack configured. Clearing local mods folder...")
             for item in MODS_DIR.iterdir():
                 try:
                     if item.is_file(): item.unlink()
                     elif item.is_dir(): shutil.rmtree(item)
                 except Exception as e: print(f"Warning: Could not delete {item}: {e}")
             update_status("Local mods folder cleared.", progress=95)
        else:
            update_status("Modpack is up-to-date or not configured.", progress=95)


        # 7. Launch Game
        update_status(f"Preparing to launch Minecraft {version_id} as {nickname}...")

        options = {
            "username": nickname,
            # Generate offline UUID based on username (consistent across launches)
            "uuid": str(uuid.uuid3(uuid.NAMESPACE_DNS, nickname)),
            "token": "0" # Offline mode token
            # Add other options if needed (JVM args, etc.)
            # "jvmArguments": ["-Xmx2G", "-Xms2G"],
            # "gameDirectory": str(MINECRAFT_DIR), # Usually inferred
            # "launcherVersion": "MyLauncherName"
        }

        minecraft_command = minecraft_launcher_lib.command.get_minecraft_command(version_id, str(MINECRAFT_DIR), options)

        update_status(f"Launching Minecraft...")
        progress_var.set(100)
        print(f"Launch command: {' '.join(minecraft_command)}")
        subprocess.Popen(minecraft_command)
        update_status("Minecraft launched! You can close this launcher.")
        # Optionally close the launcher after a delay or keep it open
        # root.after(5000, root.destroy) # Example: Close after 5 seconds

    except ValueError as e:
         # Catch potential errors from minecraft-launcher-lib about version not found
         print(f"Installation Error: {e}")
         update_status(f"Error: {e}. The specified version might not be found by the library or format is incorrect.")
    except FileNotFoundError as e:
         # Specific handling for minecraft_launcher_lib errors if Java not found
         print(f"Installation/Launch Error: {e}")
         update_status(f"Error: {e}. Is Java installed correctly and accessible in your system's PATH?")
    except Exception as e:
        print(f"An unexpected error occurred during install/launch: {e}")
        import traceback
        traceback.print_exc() # Print detailed traceback to console
        update_status(f"An error occurred: {e}")
    finally:
        action_button.config(state=tk.NORMAL) # Re-enable button in case of error or completion



def start_action_thread():
    """Starts the main action in a separate thread to keep GUI responsive."""
    action_thread = threading.Thread(target=perform_install_update_launch, daemon=True)
    action_thread.start()

# --- Main Execution ---
if __name__ == "__main__":
    load_local_config() # Load nickname on startup
    root.mainloop()
    # Consider saving config on close? Maybe not necessary if saved on button press.
    # save_local_config() # Example if saving on close is desired
