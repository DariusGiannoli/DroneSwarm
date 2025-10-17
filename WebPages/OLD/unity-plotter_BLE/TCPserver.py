import socket
import asyncio
from bleak import BleakScanner, BleakClient
import argparse
import json
import re

CHARACTERISTIC_UUID = 'f22535de-5375-44bd-8ca9-d0ea9ff9e410'
CONTROL_UNIT_NAME = 'QT Py ESP32-S3'
HOST = "127.0.0.1"  # Standard loopback interface address (localhost)
PORT = 9051  # Port to listen on (non-privileged ports are > 1023)

def create_command(addr, mode, duty, freq):
    serial_group = addr // 30
    serial_addr = addr % 30
    byte1 = (serial_group << 2) | (mode & 0x01)
    byte2 = 0x40 | (serial_addr & 0x3F)  # 0x40 represents the leading '01'
    byte3 = 0x80 | ((duty & 0x0F) << 3) | (freq)  # 0x80 represents the leading '1'
    return bytearray([byte1, byte2, byte3])

async def setMotor(client, socket_conn):
    while True:
        data = socket_conn.recv(1024)
        if not data:
            break
        data_str = data.decode('utf-8').strip()
        data_segments = re.findall(r'\{.*?\}', data_str)
        # create data chunks of 10
        data_chunks = [data_segments[i:i + 10] for i in range(0, len(data_segments), 10)]
        for data_chunk in data_chunks:
            command = bytearray([])
            for data_segment in data_chunk:
                print('data_segment = ', data_segment)
                data_parsed = json.loads(data_segment)
                command = command + create_command(data_parsed['addr'], data_parsed['mode'], data_parsed['duty'], data_parsed['freq'])
            command = command + bytearray([0xFF, 0xFF, 0xFF]) * (20-len(data_chunk))
            await client.write_gatt_char(CHARACTERISTIC_UUID, command)
            print('Motor write = ', command)
            

async def main(socket_conn):
    devices = await BleakScanner.discover()
    for d in devices:
        print('device name = ', d.name)
        if d.name != None:
            if d.name == CONTROL_UNIT_NAME:
                print('feather device found!!!')
                async with BleakClient(d.address) as client:
                    print(f'BLE connected to {d.address}')
                    val = await client.read_gatt_char(CHARACTERISTIC_UUID)
                    print('Motor read = ', val)
                    while True:
                        await setMotor(client, socket_conn)

if __name__ == "__main__":
    # Initialize the argument parser
    parser = argparse.ArgumentParser(description="Read CHARACTERISTIC_UUID and CONTROL_UNIT_NAME from the command line.")

    # Add arguments with flags
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

    parser.add_argument(
        "-host", "--host", required=False, type=str,
        default="127.0.0.1",
        help="The host IP address"
    )

    parser.add_argument(
        "-port", "--port", required=False, type=int,
        default=9051,
        help="The port number"
    )

    # Parse the arguments
    args = parser.parse_args()

    # Access and print the parameters
    print(f"CHARACTERISTIC_UUID: {args.characteristic_uuid}")
    print(f"CONTROL_UNIT_NAME: {args.control_unit_name}")
    print(f"HOST: {args.host}")
    print(f"PORT: {args.port}")

    CHARACTERISTIC_UUID = args.characteristic_uuid
    CONTROL_UNIT_NAME = args.control_unit_name
    HOST = args.host
    PORT = args.port
    
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((HOST, PORT))
            print('python server start listening')
            s.listen()
            conn, addr = s.accept()
            with conn:
                print(f"TCP socket connected by {addr}")
                # while True:
                    # data = conn.recv(1024)
                    # print('TCP data recv = ', data)
                    # if not data:
                    #     break
                asyncio.run(main(conn))
        print('TCP socket disconnected! restart now...')