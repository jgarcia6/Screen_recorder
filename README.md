# Screen Recorder Script

## Overview

This repository contains a Python script for a screen recorder that takes a screenshot every second and condenses them into a video at 4 frames per second (FPS), creating a timelapse of desktop activity.

Output file will be tagged with the timestamp of when the recording started and the public IP (if it can be resolved) of the host.

## Requirements

- Python 3.12

## Example

Here's a brief example of how to use the script:

1. Start the script:

```sh
python screen_recorder.py
```
2. Let it run while you perform your tasks on the desktop.

3. Stop the script with Ctrl+C.

4. Find the generated timelapse.avi video in the same directory.

5. Alternatively, an ___exectuable___ can be found inside the ___dist___  folder.

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue if you have any suggestions or improvements.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.