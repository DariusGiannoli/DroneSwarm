# test_haptics.py

from serial_api_espnow import SERIAL_API
import time

# 1. Create an instance of your API
api = SERIAL_API()

# 2. Find and connect to the gateway
ports = api.get_serial_devices()
if not ports:
    print("❌ No gateway found. Please check the connection.")
else:
    print(f"✅ Found gateway: {ports[0]}")
    if api.connect_serial_device(ports[2]):

        # 3. Send a test command to turn a motor ON
        print("\n--> Sending START command to motor #5...")
        # send_command(addr, duty, freq, start_or_stop)
        api.send_command(0, 15, 7, 1) # Start motor 5 at max power
        time.sleep(2) # Keep it on for 2 seconds

        # 4. Send a command to turn the motor OFF
        print("--> Sending STOP command to motor #5...")
        api.send_command(0, 0, 0, 0) # Stop motor 5
        time.sleep(1)

        # 5. Disconnect cleanly
        print("\nTest complete. Disconnecting.")
        api.disconnect_serial_device()