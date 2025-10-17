import asyncio
import json
import re
import time
import argparse

from bleak import BleakScanner, BleakClient
import websockets
import aiohttp

# Default parameters
CHARACTERISTIC_UUID = 'f22535de-5375-44bd-8ca9-d0ea9ff9e410'
CONTROL_UNIT_NAME = 'QT Py ESP32-S3'
CONTROL_UNIT_NAME_2 = 'QT Py ESP32-S3 BJ'
DEBUG = False

# Global BLE clients for two controllers
ble_client = None       # For Controller 1 (addresses < 120)
ble_client_2 = None     # For Controller 2 (addresses >= 120)

# --- Message batching for each controller ---
# For each controller we use a dictionary so that if a message with the same "addr" arrives, it is replaced.
# Also we track when the first message in the batch was received (so that we eventually flush even if threshold is not met).
messages_ctrl1 = {}       # key: addr, value: JSON string (unchanged)
messages_ctrl2 = {}       # key: normalized addr, value: JSON string (addr already normalized: original addr - 120)
messages_ctrl1_first_time = None
messages_ctrl2_first_time = None

# One lock per controller to protect the batch dictionary
lock_ctrl1 = asyncio.Lock()
lock_ctrl2 = asyncio.Lock()

ble_write_lock1 = asyncio.Lock()
ble_write_lock2 = asyncio.Lock()

# 添加全局状态字典，保存每个执行器最新的状态
current_states_ctrl1 = {}  # addr: state
current_states_ctrl2 = {}

IMMEDIATE_THRESHOLD = 20  # New constant for immediate flush



def create_command(addr, mode, duty, freq):
    """
    Create a 3-byte command for the motor controller.

    The command is built from:
      - serial_group: addr // 30
      - serial_addr:  addr % 30
      - mode, duty, freq are encoded into the bits as required.
    """
    serial_group = addr // 30
    serial_addr = addr % 30
    byte1 = (serial_group << 2) | (mode & 0x01)
    byte2 = 0x40 | (serial_addr & 0x3F)  # 0x40 represents the leading '01'
    byte3 = 0x80 | ((duty & 0x0F) << 3) | (freq)  # 0x80 represents the leading '1'
    return bytearray([byte1, byte2, byte3])


# async def setMotor(client, message):
#     """
#     Parse the combined message string (which may contain several JSON segments)
#     and send commands in chunks (max 20 bytes per write) over the given BLE client.
#     """
#     try:
#         data_segments = re.findall(r'\{.*?\}', message)
#         if not data_segments:
#             return

#         commands = []
#         for data_segment in data_segments:
#             data_parsed = json.loads(data_segment)
#             command = create_command(
#                 data_parsed['addr'],
#                 data_parsed['mode'],
#                 data_parsed['duty'],
#                 data_parsed['freq']
#             )
#             commands.append(command)
#         max_chunk_size = 20
#         chunk = bytearray()
#         for command in commands:
#             if len(chunk) + len(command) <= max_chunk_size:
#                 chunk += command
#             else:
#                 await client.write_gatt_char(CHARACTERISTIC_UUID, chunk)
#                 chunk = bytearray(command)
#         if chunk:
#             await client.write_gatt_char(CHARACTERISTIC_UUID, chunk)

#     except Exception as e:
#         print(f'Error in setMotor: {e}')

async def setMotor(client, message, write_lock):
    """
    Parse the combined message string (which may contain several JSON segments)
    and send commands in chunks (max 20 bytes per write) over the given BLE client.
    BLE write operations are serialized with a lock to prevent concurrent writes.
    """
    try:
        data_segments = re.findall(r'\{.*?\}', message)
        if not data_segments:
            return

        commands = []
        for data_segment in data_segments:
            data_parsed = json.loads(data_segment)
            command = create_command(
                data_parsed['addr'],
                data_parsed['mode'],
                data_parsed['duty'],
                data_parsed['freq']
            )
            commands.append(command)

        max_chunk_size = 20
        chunks = []
        chunk = bytearray()
        for command in commands:
            if len(chunk) + len(command) <= max_chunk_size:
                chunk += command
            else:
                chunks.append(chunk)
                chunk = bytearray(command)
        if chunk:
            chunks.append(chunk)

        async with write_lock:  # critical section starts here
            for chunk in chunks:
                await client.write_gatt_char(CHARACTERISTIC_UUID, chunk)

    except Exception as e:
        print(f'Error in setMotor: {e}')

async def ble_task():
    """
    Continuously scan for BLE devices and maintain connections for both controllers.
    If a controller disconnects, its global client is set to None so that reconnection is attempted.
    """
    global ble_client, ble_client_2
    while True:
        devices = await BleakScanner.discover()
        device1 = None
        device2 = None

        for d in devices:
            if d.name:
                if d.name == CONTROL_UNIT_NAME:
                    device1 = d
                elif d.name == CONTROL_UNIT_NAME_2:
                    device2 = d

        # Connect to Controller 1 if needed
        if device1 and (ble_client is None or not ble_client.is_connected):
            try:
                print("Attempting to connect to Controller 1...")
                ble_client = BleakClient(device1.address)
                await ble_client.connect()
                # Start a monitor task to detect disconnection because older set_disconnected_callback API
                # is not available in bleak 1.0+. The monitor will call handle_disconnect when needed.
                asyncio.create_task(monitor_connection(ble_client, CONTROL_UNIT_NAME))
                print(f'BLE connected to {device1.address} (Controller 1)')
                val = await ble_client.read_gatt_char(CHARACTERISTIC_UUID)
                print('Motor read (Controller 1) = ', val)
            except Exception as e:
                print(f'Error connecting to Controller 1: {e}')

        # Connect to Controller 2 if needed
        if device2 and (ble_client_2 is None or not ble_client_2.is_connected):
            try:
                print("Attempting to connect to Controller 2...")
                ble_client_2 = BleakClient(device2.address)
                await ble_client_2.connect()
                asyncio.create_task(monitor_connection(ble_client_2, CONTROL_UNIT_NAME_2))
                print(f'BLE connected to {device2.address} (Controller 2)')
                val = await ble_client_2.read_gatt_char(CHARACTERISTIC_UUID)
                print('Motor read (Controller 2) = ', val)
            except Exception as e:
                print(f'Error connecting to Controller 2: {e}')

        await asyncio.sleep(5)  # Retry every 5 seconds


async def handle_disconnect(client, name):
    """
    Callback for BLE disconnect. Reset the corresponding global client so that ble_task() can reconnect.
    """
    global ble_client, ble_client_2
    print(f"{name} disconnected!")
    if name == CONTROL_UNIT_NAME:
        ble_client = None
    elif name == CONTROL_UNIT_NAME_2:
        ble_client_2 = None


async def monitor_connection(client, name, poll_interval: float = 1.0):
    """
    Monitor a BleakClient's connection state and call handle_disconnect when it becomes disconnected.
    This provides a fallback for bleak versions that don't expose set_disconnected_callback.
    """
    try:
        while True:
            # If the client object is gone or not connected, trigger the handler and stop monitoring.
            if client is None:
                return
            try:
                connected = client.is_connected
            except Exception:
                # If accessing is_connected raises, consider it disconnected.
                connected = False
            if not connected:
                await handle_disconnect(client, name)
                return
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        return


async def handle_connection(websocket):
    """
    Start the background tasks for processing messages for each controller
    and for sending status updates over the WebSocket.
    """
    print('WebSocket connection established!')
    # Start background tasks:
    asyncio.create_task(collect_messages(websocket))
    asyncio.create_task(process_ctrl1_timer())
    asyncio.create_task(process_ctrl2_timer())
    asyncio.create_task(send_message_bluetooth_on(websocket))
    await websocket.wait_closed()


async def send_message_bluetooth_on(websocket):
    """
    Every second, send a simple status message over the WebSocket.
    (For example, 'C' if both controllers are connected, or 'D' otherwise.)
    """
    global ble_client, ble_client_2
    while True:
        await asyncio.sleep(1)
        status1 = ble_client is not None and ble_client.is_connected
        status2 = ble_client_2 is not None and ble_client_2.is_connected
        if status1 and status2:
            await websocket.send('C')
        else:
            await websocket.send('D')

async def collect_messages(websocket):
    global messages_ctrl1, messages_ctrl2, messages_ctrl1_first_time, messages_ctrl2_first_time
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

            if addr >= 120:
                # Controller 2: Normalize address and add to batch.
                normalized_addr = addr - 120
                msg_obj['addr'] = normalized_addr
                message_str = json.dumps(msg_obj)
                async with lock_ctrl2:
                    if not messages_ctrl2:
                        messages_ctrl2_first_time = time.time()
                    messages_ctrl2[normalized_addr] = message_str
                    # Check if batch reached the immediate threshold.
                    if len(messages_ctrl2) >= IMMEDIATE_THRESHOLD:
                        combined_message = ''.join(messages_ctrl2.values())
                        messages_ctrl2.clear()
                        messages_ctrl2_first_time = None
                        asyncio.create_task(process_ctrl2_immediate_flush(combined_message))
            else:
                # Controller 1: Keep address as is.
                message_str = message
                async with lock_ctrl1:
                    if not messages_ctrl1:
                        messages_ctrl1_first_time = time.time()
                    messages_ctrl1[addr] = message_str
                    # Check if batch reached the immediate threshold.
                    if len(messages_ctrl1) >= IMMEDIATE_THRESHOLD:
                        combined_message = ''.join(messages_ctrl1.values())
                        messages_ctrl1.clear()
                        messages_ctrl1_first_time = None
                        asyncio.create_task(process_ctrl1_immediate_flush(combined_message))
    except websockets.exceptions.ConnectionClosed as e:
        print(f'WebSocket closed: {e}')
    except Exception as e:
        print(f'Error in collect_messages: {e}')


async def process_ctrl1_immediate_flush(combined_message):
    """
    Immediately process and send the Controller 1 batch.
    """
    print("Immediately processing Controller 1 messages:", combined_message)
    if not DEBUG and ble_client and ble_client.is_connected:
        # await setMotor(ble_client, combined_message)
        await setMotor(ble_client, combined_message, ble_write_lock1)
    else:
        print("Controller 1 BLE not connected or DEBUG mode")
    asyncio.create_task(send_to_server(combined_message))

async def process_ctrl2_immediate_flush(combined_message):
    """
    Immediately process and send the Controller 2 batch.
    """
    print("Immediately processing Controller 2 messages:", combined_message)
    if not DEBUG and ble_client_2 and ble_client_2.is_connected:
        # await setMotor(ble_client_2, combined_message)
        await setMotor(ble_client_2, combined_message, ble_write_lock2)
    else:
        print("Controller 2 BLE not connected or DEBUG mode")

async def process_ctrl1_timer():
    """
    Check the Controller 1 message batch every 20 ms.
    Flush (i.e. process and send) the batch if it has reached the threshold of 20 messages,
    or if the oldest message has been waiting longer than TIMEOUT seconds.
    """
    global messages_ctrl1, messages_ctrl1_first_time
    THRESHOLD = 10
    TIMEOUT = 0.2  # seconds
    while True:
        await asyncio.sleep(0.05)  # check every 20ms
        combined_message = None
        async with lock_ctrl1:
            if messages_ctrl1:
                now = time.time()
                if len(messages_ctrl1) >= THRESHOLD or (now - messages_ctrl1_first_time >= TIMEOUT):
                    combined_message = ''.join(messages_ctrl1.values())
                    messages_ctrl1.clear()
                    messages_ctrl1_first_time = None
        if combined_message:
            print("Processing Controller 1 messages:", combined_message)
            if not DEBUG and ble_client and ble_client.is_connected:
                # await setMotor(ble_client, combined_message)
                await setMotor(ble_client, combined_message, ble_write_lock1)
            else:
                print("Controller 1 BLE not connected or DEBUG mode")
            asyncio.create_task(send_to_server(combined_message))

async def process_ctrl2_timer():
    """
    Check the Controller 2 message batch every 20 ms.
    Flush the batch if it has reached 20 messages or if the oldest message has been waiting too long.
    """
    global messages_ctrl2, messages_ctrl2_first_time
    THRESHOLD = 10
    TIMEOUT = 0.2  # seconds
    while True:
        await asyncio.sleep(0.05)
        combined_message = None
        async with lock_ctrl2:
            if messages_ctrl2:
                now = time.time()
                if len(messages_ctrl2) >= THRESHOLD or (now - messages_ctrl2_first_time >= TIMEOUT):
                    combined_message = ''.join(messages_ctrl2.values())
                    messages_ctrl2.clear()
                    messages_ctrl2_first_time = None
        if combined_message:
            print("Processing Controller 2 messages:", combined_message)
            if not DEBUG and ble_client_2 and ble_client_2.is_connected:
                # await setMotor(ble_client_2, combined_message)
                await setMotor(ble_client_2, combined_message, ble_write_lock2)
            else:
                print("Controller 2 BLE not connected or DEBUG mode")
            #asyncio.create_task(send_to_server(combined_message))


async def send_to_server(message):
    """
    POST the command message along with a timestamp to a local server.
    """
    async with aiohttp.ClientSession() as session:
        await session.post('http://localhost:5000/commands', json={'command': message, 'timestamp': time.time()})


async def main():
    # Initialize the argument parser.
    parser = argparse.ArgumentParser(description="Set CHARACTERISTIC_UUID and CONTROL_UNIT_NAME from the command line.")

    parser.add_argument(
        "-uuid", "--characteristic_uuid", required=False, type=str,
        default="f22535de-5375-44bd-8ca9-d0ea9ff9e410",
        help="The UUID of the characteristic"
    )
    parser.add_argument(
        "-name", "--control_unit_name", required=False, type=str,
        default="QT Py ESP32-S3",
        help="The Bluetooth name of the control unit"
    )
    args = parser.parse_args()

    print(f"CHARACTERISTIC_UUID: {args.characteristic_uuid}")
    print(f"CONTROL_UNIT_NAME: {args.control_unit_name}")

    global CHARACTERISTIC_UUID, CONTROL_UNIT_NAME
    CHARACTERISTIC_UUID = args.characteristic_uuid
    CONTROL_UNIT_NAME = args.control_unit_name

    # Start the BLE scanning/reconnection task.
    asyncio.create_task(ble_task())

    # Start the WebSocket server.
    server = await websockets.serve(handle_connection, 'localhost', 9052)
    print("WebSocket server running on ws://localhost:9052")

    # Run forever.
    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
