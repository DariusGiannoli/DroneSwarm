import asyncio
import json
import re
import time
import sys

import websockets
import aiohttp
# --- MODIFIED: Import your new API class instead of the serial library ---
from serial_api_espnow import SERIAL_API

# --- MODIFIED: This will hold an instance of your API class ---
haptic_api = None

# --- Unified Batching System (unchanged) ---
message_batch = {}
batch_first_time = None
batch_lock = asyncio.Lock()
IMMEDIATE_THRESHOLD = 20
DEBUG = False

# --- DELETED: The create_command function is now inside your SERIAL_API class ---

# --- MODIFIED: This function now uses your new API ---
def send_commands_via_serial(api_instance, combined_message):
    """
    Parses JSON messages and sends them as a list to the haptic API.
    """
    if not api_instance or not api_instance.connected:
        print("Haptic API not available. Cannot send commands.")
        return

    try:
        # Find all individual JSON command strings
        data_segments = re.findall(r'\{.*?\}', combined_message)
        if not data_segments:
            return

        # --- CRITICAL CHANGE ---
        # Convert JSON strings into a list of dictionaries.
        # We map the old "mode" key to the new "start_or_stop" key.
        commands_list = []
        for segment in data_segments:
            data = json.loads(segment)
            command_dict = {
                'addr': data.get('addr'),
                'duty': data.get('duty'),
                'freq': data.get('freq'),
                'start_or_stop': data.get('mode') # Mapping "mode" to "start_or_stop"
            }
            commands_list.append(command_dict)

        # The API class now handles command creation, padding, and sending.
        if commands_list:
            api_instance.send_command_list(commands_list)

    except Exception as e:
        print(f"❌ Error in send_commands_via_serial: {e}")

async def handle_connection(websocket):
    """
    Starts the background tasks for processing the unified message batch.
    """
    print('✅ WebSocket connection established!')
    asyncio.create_task(collect_messages(websocket))
    asyncio.create_task(process_batch_timer())
    await websocket.wait_closed()

async def collect_messages(websocket):
    """
    Collects all incoming messages from the WebSocket into a single, unified batch.
    """
    global message_batch, batch_first_time
    try:
        async for message in websocket:
            try:
                msg_obj = json.loads(message)
            except Exception as e:
                print(f"Error parsing JSON: {e}")
                continue

            addr = msg_obj.get('addr')
            if addr is None:
                continue

            async with batch_lock:
                if not message_batch:
                    batch_first_time = time.time()
                message_batch[addr] = message

                if len(message_batch) >= IMMEDIATE_THRESHOLD:
                    combined_message = ''.join(message_batch.values())
                    message_batch.clear()
                    batch_first_time = None
                    asyncio.create_task(process_batch_immediate_flush(combined_message))

    except websockets.exceptions.ConnectionClosed as e:
        print(f'WebSocket closed: {e}')
    except Exception as e:
        print(f'Error in collect_messages: {e}')

async def process_batch_immediate_flush(combined_message):
    """
    Immediately processes and sends the unified message batch.
    """
    print(f"Flushing immediate batch ({IMMEDIATE_THRESHOLD} messages)...")
    send_commands_via_serial(haptic_api, combined_message)
    asyncio.create_task(send_to_server(combined_message))

async def process_batch_timer():
    """
    Periodically checks the unified message batch and flushes it if the
    size or time threshold is met.
    """
    global message_batch, batch_first_time
    THRESHOLD = 10
    TIMEOUT = 0.2

    while True:
        await asyncio.sleep(0.05)
        combined_message = None

        async with batch_lock:
            if message_batch:
                now = time.time()
                if len(message_batch) >= THRESHOLD or (now - batch_first_time >= TIMEOUT):
                    combined_message = ''.join(message_batch.values())
                    message_batch.clear()
                    batch_first_time = None

        if combined_message:
            # Estimate message count for logging
            try:
                # Find the length of the first JSON object to estimate total messages
                first_msg_len = len(next(iter(message_batch.values())))
                msg_count = len(combined_message) // first_msg_len
                print(f"Flushing timed batch ({msg_count} messages)...")
            except StopIteration:
                 print("Flushing timed batch...")

            send_commands_via_serial(haptic_api, combined_message)
            asyncio.create_task(send_to_server(combined_message))

async def send_to_server(message):
    """
    POST the command message to a local server for logging/debugging.
    """
    try:
        async with aiohttp.ClientSession() as session:
            await session.post('http://localhost:5000/commands', json={'command': message, 'timestamp': time.time()})
    except Exception as e:
        print(f"⚠️  Could not connect to logging server: {e}")

# --- MODIFIED: The main function now uses your API for connection ---
async def main():
    """
    Initializes the haptic API, connects to the gateway, and starts the server.
    """
    global haptic_api

    haptic_api = SERIAL_API()
    available_ports = haptic_api.get_serial_devices()

    if not available_ports:
        print("❌ FATAL ERROR: No serial devices found. Please ensure the gateway is connected.")
        sys.exit(1)

    # For now, we automatically connect to the first available device.
    # You could add a user prompt here if needed.
    gateway_port_info = available_ports[2]
    print(f"Found gateway, attempting to connect to: {gateway_port_info}")

    if not haptic_api.connect_serial_device(gateway_port_info):
        print(f"❌ FATAL ERROR: Could not connect to gateway on {gateway_port_info}.")
        sys.exit(1)

    # Start the WebSocket server
    server = await websockets.serve(handle_connection, 'localhost', 9052)
    print("✅ WebSocket server running on ws://localhost:9052")
    print("🚀 System is ready. Waiting for connection from Unity...")

    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())