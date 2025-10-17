#!/bin/bash

# Launch the first Python script in a new terminal tab
wt -w 0 nt cmd /k "python C:\Users\Pablo\Documents\MIT\UnityMIT\WebPages\unity-plotter\httpServer.py"

# Launch the second Python script in another new terminal tab
wt -w 0 nt cmd /k "python C:\Users\Pablo\Documents\MIT\UnityMIT\WebPages\unity-plotter\TCPserver.py"

# Launch the npm project in another new terminal tab
wt -w 0 nt wsl -e bash -c "cd /mnt/c/path/to/your/npm/project && npm start"
