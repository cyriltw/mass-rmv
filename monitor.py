import sys

# --- Dependency Check ---
try:
    import requests
    from selenium import webdriver
    from dotenv import load_dotenv
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError as e:
    missing_module = str(e).split("'")[1]
    print(f"FATAL: Missing required Python package '{missing_module}'.", file=sys.stderr)
    print("Please install all required packages by running the following command:", file=sys.stderr)
    print("\n    pip install -r requirements.txt\n", file=sys.stderr)
    sys.exit(1)

import json
import os
import time
import logging
import signal
from datetime import datetime
from pathlib import Path
from tqdm import tqdm
import wandb
from rmv_checker import (
    get_rmv_data, 
    get_all_locations,
    prompt_for_rmv_url,
    prompt_for_ntfy_url,
    prompt_for_locations,
    prompt_for_frequency
)
from selenium.webdriver.chrome.service import Service as ChromeService

# --- Logging Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create handlers
c_handler = logging.StreamHandler(sys.stdout) # Console handler
f_handler = logging.FileHandler('monitor.log') # File handler
c_handler.setLevel(logging.INFO)
f_handler.setLevel(logging.INFO)

# Create formatters and add it to handlers
c_format = logging.Formatter('%(message)s')
f_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
c_handler.setFormatter(c_format)
f_handler.setFormatter(f_format)

# Add handlers to the logger
if not logger.handlers:
    logger.addHandler(c_handler)
    logger.addHandler(f_handler)


STATE_FILE = 'state.json'
LOCATIONS_MAP_FILE = 'locations_map.json'
APPOINTMENT_TEXT_FILE = 'booking.md'

def appointment_text_links():
    file_path = Path(APPOINTMENT_TEXT_FILE)
    content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
    return content

def load_locations_map():
    """Loads the locations ID to friendly name mapping."""
    if not os.path.exists(LOCATIONS_MAP_FILE):
        return {}
    with open(LOCATIONS_MAP_FILE, 'r') as f:
        return json.load(f)

def save_locations_map(locations_map):
    """Saves the locations ID to friendly name mapping."""
    with open(LOCATIONS_MAP_FILE, 'w') as f:
        json.dump(locations_map, f, indent=2)

def get_friendly_name(location_id, locations_map):
    """Gets the friendly name for a location ID, with fallback."""
    friendly_name = locations_map.get(str(location_id), f"ID-{location_id}")
    if friendly_name.startswith("ID-"):
        logger.warning(f"Location ID {location_id} not found in locations map, using fallback: {friendly_name}")
    return friendly_name

def refresh_locations_map_if_needed(locations_map, all_locations_data):
    """Refreshes the locations map if new locations are found."""
    if not locations_map:
        return False
    
    current_ids = set(locations_map.keys())
    new_ids = set(loc['id'] for loc in all_locations_data)
    
    if new_ids - current_ids:
        logger.info("New locations found, updating locations map...")
        for loc in all_locations_data:
            if loc['id'] not in locations_map:
                locations_map[loc['id']] = loc['service_center']
                logger.info(f"Added new location: {loc['id']} -> {loc['service_center']}")
        save_locations_map(locations_map)
        return True
    return False

def init_wandb():
    """Initialize wandb for tracking appointment patterns."""
    try:
        wandb.init(
            project="rmv-checker",
            name="appointment-patterns",
            tags=["rmv", "appointments", "monitoring"]
        )
        logger.info("Wandb initialized successfully")
        return True
    except Exception as e:
        logger.warning(f"Could not initialize wandb: {e}")
        return False

def log_appointment_event(wandb_run, event_type, location_data, previous_date=None, new_date=None, time_diff_hours=None, locations_map=None):
    """Log appointment events to wandb for pattern analysis."""
    if not wandb_run:
        return
    
    try:
        # Parse dates for analysis
        new_date_obj = parse_date(new_date) if new_date else None
        previous_date_obj = parse_date(previous_date) if previous_date else None
        
        # Calculate additional metrics
        current_time = datetime.now()
        weekday = current_time.strftime("%A")
        hour_of_day = current_time.hour
        month = current_time.strftime("%B")
        
        # Get friendly name from our locations map
        location_id = str(location_data['id'])
        location_name = get_friendly_name(location_id, locations_map) if locations_map else f"ID-{location_id}"
        
        # Log to wandb
        wandb.log({
            "event_type": event_type,
            "location_id": location_data['id'],
            "location_name": location_name,
            "previous_appointment": previous_date,
            "new_appointment": new_date,
            "time_difference_hours": time_diff_hours,
            "weekday": weekday,
            "hour_of_day": hour_of_day,
            "month": month,
            "timestamp": current_time.isoformat(),
            "check_number": wandb.run.step if hasattr(wandb.run, 'step') else 0
        })
        
        logger.info(f"Logged {event_type} event to wandb for {location_name}")
        
    except Exception as e:
        logger.error(f"Error logging to wandb: {e}")

def load_json(file_path):
    """Loads data from a JSON file."""
    if not os.path.exists(file_path):
        return {}
    with open(file_path, 'r') as f:
        return json.load(f)

def save_json(data, file_path):
    """Saves data to a JSON file."""
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)

def send_ntfy_notification(url, message):
    """Sends a notification to a ntfy URL."""
    try:
        requests.post(url, data=message.encode('utf-8'))
        logger.info(f"Sent notification: {message}")
    except Exception as e:
        logger.error(f"Error sending ntfy notification: {e}")

def parse_date(date_str):
    """Parses the scraped date string into a datetime object."""
    if "No Appointments" in date_str or "No Date Found" in date_str or "Location Not Available" in date_str:
        return None
    try:
        clean_date_str = date_str.strip().rstrip(',')
        return datetime.strptime(clean_date_str, '%a %b %d, %Y, %I:%M %p')
    except ValueError:
        try:
            return datetime.strptime(clean_date_str, '%a %b %d, %Y')
        except ValueError as e:
            logger.error(f"Error parsing date string '{date_str}': {e}")
            return None

def check_for_appointments(rmv_url, ntfy_url, locations_to_monitor, state, wandb_run=None, locations_map=None):
    """The core logic for checking appointments and sending notifications."""
    logger.info(f"--- Running RMV Appointment Check ---")
    
    live_data = get_rmv_data(rmv_url, locations_to_monitor)
    if not live_data:
        logger.warning("Could not fetch live appointment data.")
        return state

    current_time = datetime.now()
    # Note: We could add a buffer time here to avoid updating state immediately 
    # when appointments pass, but for now we update immediately to ensure
    # the state stays current with the latest available appointments
    
    for location_data in live_data:
        location_id = str(location_data['id'])
        # Get friendly name from our locations map, fallback to ID if missing
        location_name = get_friendly_name(location_id, locations_map) if locations_map else f"ID-{location_id}"
        new_date_str = location_data['earliest_date']
        new_date = parse_date(new_date_str)

        if not new_date:
            if "Location Not Available" in new_date_str:
                logger.info(f"Location {location_name} (ID: {location_id}) is currently not available. Preserving previous appointment data.")
                # Don't update state - keep the previous appointment data for comparison
                # This allows us to detect when the location becomes available again
            else:
                logger.info(f"No appointments found for {location_name} (ID: {location_id}).")
            continue

        last_known_date_str = state.get(location_id)
        last_known_date = parse_date(last_known_date_str) if last_known_date_str else None

        # Check if the last known appointment has passed
        # This ensures the state.json stays current with the latest available appointments
        if last_known_date and last_known_date < current_time:
            logger.info(f"Last known appointment at {location_name} has passed (was: {last_known_date_str}). Updating state with new data: {new_date_str}")
            
            # Calculate time difference for analytics
            time_diff_hours = None
            if new_date and last_known_date:
                time_diff = new_date - last_known_date
                time_diff_hours = time_diff.total_seconds() / 3600
            
            # Update state with the new appointment data
            if "AM" in new_date_str or "PM" in new_date_str:
                # We have a specific time
                state[location_id] = new_date_str
                message = f"New appointment at {location_name}: {new_date.strftime('%a, %b %d, %Y at %I:%M %p')}"
            else:
                # We only have a date
                state[location_id] = new_date_str
                message = f"New earliest date at {location_name}: {new_date.strftime('%a, %b %d, %Y')}"
            
            # Log to wandb
            log_appointment_event(wandb_run, "expired_replaced", location_data, last_known_date_str, new_date_str, time_diff_hours, locations_map)
            message = message + "\n" + appointment_text_links()
            # Send notification for the new appointment that replaced the expired one
            send_ntfy_notification(ntfy_url, message)
            continue

        # Check if location was previously unavailable but now has appointments
        # We can detect this by checking if we have a valid date now but the last known date
        # was from a previous check cycle (indicating the location was temporarily unavailable)
        if last_known_date and new_date and new_date > last_known_date:
            # This suggests the location might have been temporarily unavailable
            # and now has a later appointment than before
            logger.info(f"Location {location_name} (ID: {location_id}) appears to have new availability: {new_date_str}")
            
            # Calculate time difference for analytics
            time_diff_hours = None
            if new_date and last_known_date:
                time_diff = new_date - last_known_date
                time_diff_hours = time_diff.total_seconds() / 3600
            
            # Update state with the new appointment data
            if "AM" in new_date_str or "PM" in new_date_str:
                # We have a specific time
                message = f"New appointment at {location_name}: {new_date.strftime('%a, %b %d, %Y at %I:%M %p')}"
                state[location_id] = new_date_str
            else:
                # We only have a date
                message = f"New earliest date at {location_name}: {new_date.strftime('%a, %b %d, %Y')}"
                state[location_id] = new_date_str
            
            # Ensure we're using the friendly name in the notification
            logger.info(f"Preparing notification for {location_name} (ID: {location_id}): {message}")
            
            # Log to wandb
            log_appointment_event(wandb_run, "new_availability", location_data, last_known_date_str, new_date_str, time_diff_hours, locations_map)
            
            # Additional logging to help debug location availability changes
            logger.info(f"Location {location_name} (ID: {location_id}) became available again. Previous: {last_known_date_str}, New: {new_date_str}")
            message = message + "\n" + appointment_text_links()
            
            send_ntfy_notification(ntfy_url, message)
            continue

        if not last_known_date or new_date < last_known_date:
            # Check if the original scraped string contained a time component (AM/PM)
            if "AM" in new_date_str or "PM" in new_date_str:
                # We have a specific time
                message = f"New appointment at {location_name}: {new_date.strftime('%a, %b %d, %Y at %I:%M %p')}"
                state[location_id] = new_date.strftime('%a %b %d, %Y, %I:%M %p')
            else:
                # We only have a date
                message = f"New earliest date at {location_name}: {new_date.strftime('%a, %b %d, %Y')}"
                state[location_id] = new_date.strftime('%a %b %d, %Y')
            
            message = message + "\n" + appointment_text_links()
            
            send_ntfy_notification(ntfy_url, message)
            
            # Log to wandb
            time_diff_hours = None
            if new_date and last_known_date:
                time_diff = last_known_date - new_date  # Note: new_date is earlier, so we subtract
                time_diff_hours = time_diff.total_seconds() / 3600
            
            event_type = "first_appointment" if not last_known_date else "earlier_appointment"
            log_appointment_event(wandb_run, event_type, location_data, last_known_date_str, new_date_str, time_diff_hours, locations_map)
        else:
            logger.info(f"No change for {location_name}. Earliest is still {last_known_date_str}")
            
            # Log to wandb for no-change events (useful for pattern analysis)
            if wandb_run:
                # Get friendly name from our locations map
                location_id = str(location_data['id'])
                location_name_for_logging = get_friendly_name(location_id, locations_map) if locations_map else f"ID-{location_id}"
                
                wandb.log({
                    "event_type": "no_change",
                    "location_id": location_data['id'],
                    "location_name": location_name_for_logging,
                    "current_appointment": last_known_date_str,
                    "timestamp": datetime.now().isoformat(),
                    "check_number": wandb.run.step if hasattr(wandb.run, 'step') else 0
                })
    
    # Ensure state is populated with earliest available appointments if it was empty
    # This handles the case where state.json was empty or all appointments expired
    if not state:
        logger.info("State was empty. Populating with earliest available appointments.")
        for location_data in live_data:
            location_id = str(location_data['id'])
            location_name = location_data['service_center']
            new_date_str = location_data['earliest_date']
            new_date = parse_date(new_date_str)
            
            if new_date:
                state[location_id] = new_date_str
                logger.info(f"Added {location_name} to state: {new_date_str}")
                
                # Log to wandb for initial state population
                log_appointment_event(wandb_run, "initial_population", location_data, None, new_date_str, None, locations_map)
    
    save_json(state, STATE_FILE)
    logger.info("--- Check complete ---")
    return state

def signal_handler(signum, frame):
    """Handle graceful shutdown."""
    logger.info("Received shutdown signal. Cleaning up...")
    if wandb.run:
        wandb.finish()
    sys.exit(0)

def run_monitor():
    """Main monitoring loop."""
    load_dotenv()
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # --- Configuration Healing & Data Fetching ---
    is_interactive = sys.stdout.isatty()
    all_locations_data = None # Initialize
    
    rmv_url = os.getenv("RMV_URL")
    if not rmv_url:
        if not is_interactive:
            logger.error("FATAL: RMV_URL not found. Please run the script interactively once to set it up.")
            sys.exit(1)
        logger.warning("RMV_URL not found in .env file.")
        rmv_url = prompt_for_rmv_url()
        load_dotenv(override=True)

    ntfy_url = os.getenv("NTFY_URL")
    if not ntfy_url:
        if not is_interactive:
            logger.error("FATAL: NTFY_URL not found. Please run the script interactively once to set it up.")
            sys.exit(1)
        logger.warning("NTFY_URL not found in .env file.")
        ntfy_url = prompt_for_ntfy_url()
        load_dotenv(override=True)

    locations_to_monitor_ids_str = os.getenv("LOCATIONS_TO_MONITOR")
    if not locations_to_monitor_ids_str:
        if not is_interactive:
            logger.error("FATAL: LOCATIONS_TO_MONITOR not found. Please run the script interactively once to set it up.")
            sys.exit(1)
        logger.warning("LOCATIONS_TO_MONITOR not found in .env file.")
        # This prompt now returns the fetched location data, so we don't have to fetch it again.
        locations_to_monitor_ids_str, all_locations_data = prompt_for_locations(rmv_url)
        load_dotenv(override=True)

    frequency_minutes_str = os.getenv("CHECK_FREQUENCY_MINUTES")
    if not frequency_minutes_str:
        if not is_interactive:
            logger.error("FATAL: CHECK_FREQUENCY_MINUTES not found. Please run the script interactively once to set it up.")
            sys.exit(1)
        logger.warning("CHECK_FREQUENCY_MINUTES not found in .env file.")
        frequency_minutes_str = str(prompt_for_frequency())
        load_dotenv(override=True)
    
    locations_to_monitor_ids = locations_to_monitor_ids_str.split(',')
    frequency_minutes = int(frequency_minutes_str)

    # If we haven't already fetched the location data during setup, fetch it now.
    if all_locations_data is None:
        logger.info("Fetching all location data for friendly names...")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = None
        try:
            driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
            all_locations_data = get_all_locations(driver, rmv_url)
        finally:
            if driver:
                driver.quit()
    
    if not all_locations_data:
        logger.error("Could not fetch location data. Exiting.")
        sys.exit(1)

    # Create or load locations mapping
    locations_map = load_locations_map()
    if not locations_map:
        logger.info("Creating locations mapping file...")
        locations_map = {loc['id']: loc['service_center'] for loc in all_locations_data}
        save_locations_map(locations_map)
        logger.info(f"Created locations mapping with {len(locations_map)} locations")
    else:
        logger.info(f"Loaded existing locations mapping with {len(locations_map)} locations")
        # Check if we need to refresh the map with any new locations
        refresh_locations_map_if_needed(locations_map, all_locations_data)
    
    # Create locations_to_monitor using the mapping
    locations_to_monitor = [
        {'id': loc_id, 'service_center': get_friendly_name(loc_id, locations_map)} 
        for loc_id in locations_to_monitor_ids
    ]
    
    # Log the mapping for debugging
    logger.info("Locations mapping:")
    for loc_id in locations_to_monitor_ids:
        friendly_name = get_friendly_name(loc_id, locations_map)
        logger.info(f"  {loc_id} -> {friendly_name}")

    # --- State Reset ---
    if is_interactive and os.path.exists(STATE_FILE):
        reset_state_choice = input("Do you want to delete the existing state.json file? [y/N]: ").lower()
        if reset_state_choice == 'y':
            os.remove(STATE_FILE)
            logger.info("Deleted state.json")
    
    state = load_json(STATE_FILE)

    # Initialize wandb for tracking
    wandb_run = init_wandb()
    if wandb_run:
        # Update wandb config with actual values
        wandb.config.update({
            "check_frequency_minutes": frequency_minutes,
            "locations_monitored": locations_to_monitor_ids,
            "rmv_url": rmv_url,
            "monitoring_start_time": datetime.now().isoformat(),
            "total_locations": len(locations_to_monitor_ids)
        }, allow_val_change=True)

    logger.info(f"Starting monitor. Will check every {frequency_minutes} minutes.")

    try:
        while True:
            try:
                state = check_for_appointments(rmv_url, ntfy_url, locations_to_monitor, state, wandb_run, locations_map)
            except Exception as e:
                logger.error("An unexpected error occurred during the check:", exc_info=True)
                logger.warning("The monitor will continue running.")
            
            logger.info(f"Sleeping for {frequency_minutes} minutes...")
            
            # Show progress bar for sleep countdown
            total_seconds = frequency_minutes * 60
            with tqdm(total=total_seconds, desc="Next check in", unit="s", bar_format="{desc}: {bar} {n_fmt}/{total_fmt}s [{elapsed}<{remaining}]") as pbar:
                for _ in range(total_seconds):
                    time.sleep(1)
                    pbar.update(1)
    finally:
        # Ensure wandb is properly closed
        if wandb_run and wandb.run:
            logger.info("Finishing wandb run...")
            wandb.finish()

if __name__ == "__main__":
    run_monitor()
