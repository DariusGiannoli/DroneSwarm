from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
from datetime import datetime, timedelta
import re
import json
import time


app = Flask(__name__)
CORS(app)

class DataManager:
    def __init__(self):
        self.full_history_data = []
        self.last_values = {}
        self.started = False
        # Define the data items for which you want to store the full history
        self.full_history_items = {'camera', 'temperature', 'humidity'}
        self.commands = []  # Initialize the commands array

    def start(self):
        self.started = True
        self.full_history_data = []
        self.last_values = {}

    def quit(self):
        self.started = False
        df = pd.DataFrame(self.full_history_data)
        df.to_csv('data.csv', index=False)

    def add_data(self, data):
        if self.started:
            row_data = handle_json(data)
            timestamp = row_data.get('timestamp')
            full_history_row = {'timestamp': timestamp}
            # Process each data item
            for key, value in row_data.items():
                if key == 'timestamp':
                    continue
                if key in self.full_history_items:
                    # Store full history
                    full_history_row[key] = value
                if key.startswith('FH-'):
                    # Store full history with custom key
                    full_history_row[key[3:]] = value
                else:
                    # Store only the last value
                    self.last_values[key] = value
            if len(full_history_row) > 1:
                self.full_history_data.append(full_history_row)
            return True
        else:
            return False

    def get_data(self):
        # Restructure full_history_data to group values by keys
        full_history_dict = {}
        for entry in self.full_history_data:
            for key, value in entry.items():
                if key not in full_history_dict:
                    full_history_dict[key] = []
                full_history_dict[key].append(value)
        return {
            'full_history': get_last_values(full_history_dict),
            'last_values': self.last_values  # Ensure last_values is returned as is
        }

    def get_commands(self):
        #return the commands that have been received the last 5 seconds
        current_time = time.time()
        commands = [command for command in self.commands if current_time - command['timestamp'] <= 5]
        return commands

        

def get_last_values(history, number_of_values=100):
    last_values = {}
    for key, values in history.items():
        last_values[key] = values[-number_of_values:]
    return last_values

data_manager = DataManager()

def handle_json(json_data):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row_data = {"timestamp": timestamp}

    data_items = json_data.get("data", [])
    for item in data_items:
        name = item.get("name")
        value = item.get("value")

        if name is not None:
            # Parse the value if it's a string representation of a tuple
            if isinstance(value, str) and value.startswith("(") and value.endswith(")"):
                value = tuple(map(float, value[1:-1].split(',')))
            row_data[name] = value

    return row_data

@app.route('/endpoint', methods=['POST'])
def endpoint():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No JSON data provided'}), 400

    if 'start' in data:
        data_manager.start()
        return jsonify({'started': True}), 200
    elif 'quit' in data:
        data_manager.quit()
        return jsonify({'quit': True}), 200
    elif 'data' in data:
        success = data_manager.add_data(data)
        return jsonify({'success': success}), 200
    else:
        return jsonify({'success': False, 'error': 'Invalid data'}), 400

@app.route('/commands', methods=['POST'])
def receive_command():
    data = request.get_json()
    command = data.get('command')
    time = data.get('timestamp')
    try:        
        data_segments = re.findall(r'\{.*?\}', command)
        if not data_segments:
            return  

        commands = []
        for data_segment in data_segments:
            data_parsed = json.loads(data_segment)
            command = {
                'addr': data_parsed['addr'], 
                'mode': data_parsed['mode'], 
                'duty': data_parsed['duty'], 
                'freq': data_parsed['freq'],
                'timestamp': time
            }
            commands.append(command)
        data_manager.commands.extend(commands)
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    


@app.route('/getCommands', methods=['GET'])
def get_commands():
    return jsonify({'commands': data_manager.get_commands()})

@app.route('/data', methods=['GET'])
def get_data():
    return jsonify(data_manager.get_data())

if __name__ == '__main__':
    app.run(port=5000)
