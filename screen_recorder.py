import time
import subprocess
import sys
import urllib.request
from datetime import datetime

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

try:
    import pyautogui
except ImportError:
    install("pyautogui")
    install("pillow")
    import pyautogui
try:
    import cv2
except ImportError:
    install("opencv-python")
    import cv2
try:
    import numpy as np
except ImportError:
    install("numpy")
    import numpy as np
try:
    import socket
except ImportError:
    install("socket")
    import socket

def take_screenshot():
    # Capture the screen
    screenshot = pyautogui.screenshot()
    # Convert the screenshot to a numpy array
    frame = np.array(screenshot)
    # Convert the color from BGR to RGB
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return frame

def get_ip_address():
    # Get the hostname of the machine
    hostname = socket.gethostname()
    # Get the IP address using the hostname
    ip_address = socket.gethostbyname(hostname)
    return ip_address
    #external_ip = urllib.request.urlopen('https://v4.ident.me/ ').read().decode('utf8')
    #return external_ip

def convert_ip_to_hex(ip_address):
    # Split the IP address into its components
    parts = ip_address.split('.')
    # Convert each part to hexadecimal and remove the '0x' prefix
    hex_parts = [format(int(part), '02x') for part in parts]
    # Join the hexadecimal parts together with no spaces
    hex_ip = ''.join(hex_parts)
    return hex_ip

def get_timestamp():
    # Get the current date and time
    current_time = datetime.now()
    # Format the timestamp
    timestamp = current_time.strftime('%Y-%m-%d_%H-%M-%S')
    return timestamp

def create_tagged_filename(base_filename):
    # Get the IP address
    ip_address = get_ip_address()
    # Convert the IP address to hexadecimal
    hex_ip = convert_ip_to_hex(ip_address)
    # Get the timestamp
    timestamp = get_timestamp()
    # Create the tagged filename
    tagged_filename = f"{base_filename}_{timestamp}_{hex_ip}"
    return tagged_filename

def main():
    # Define the codec and create VideoWriter object
    codec = 'XVID' #H264,XVID
    videoExtension = '.avi'
    videoName = create_tagged_filename('timelapse')
    fourcc = cv2.VideoWriter_fourcc(*codec)
    if codec == 'H264':
        videoExtension = '.mp4'
    # Set the desired frames per second for the output video
    fps = 4
    frame_size = pyautogui.size()
    out = cv2.VideoWriter(videoName+videoExtension, fourcc, fps, frame_size)
    print("Recording started...")
    try:
        while True:
            frame = take_screenshot()
            out.write(frame)
            # Wait for 1 second before taking the next screenshot
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopped by user")
    finally:
        out.release()
        cv2.destroyAllWindows()

if __name__ == '__main__':
    main()
