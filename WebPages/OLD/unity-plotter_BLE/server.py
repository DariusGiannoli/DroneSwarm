from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
from datetime import datetime
import ast

app = Flask(__name__)
CORS(app)

class DataManager:
    def __init__(self):
        self.data_rows = []
        self.started = False

    def start(self):
        self.started = True
        self.data_rows = []

    def quit(self):
        self.started = False
        df = pd.DataFrame(self.data_rows)
        df.to_csv('data.csv', index=False)

    def add_data(self, data):
        if self.started:
            row_data = handle_json(data)
            self.data_rows.append(row_data)
            return True
        else:
            return False

    def get_data(self):
        return self.data_rows

data_manager = DataManager()

def handle_json(json_data):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row_data = {"timestamp": timestamp}

    data_items = json_data.get("data", [])
    for item in data_items:
        name = item.get("name")
        value = item.get("value")
        if name is not None:
            if isinstance(value, str):
                try:
                    parsed_value = ast.literal_eval(value)
                    row_data[name] = parsed_value
                except (ValueError, SyntaxError):
                    row_data[name] = value
            else:
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

@app.route('/data', methods=['GET'])
def get_data():
    return jsonify(data_manager.get_data())

if __name__ == '__main__':
    app.run(port=5000)
