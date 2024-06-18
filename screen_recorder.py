import time
import subprocess
import sys
import urllib.request
from datetime import datetime
import re
import signal

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
try:
    import requests
except ImportError:
    install("requests")
    import requests

def take_screenshot():
    # Capture the screen
    screenshot = pyautogui.screenshot()
    # Convert the screenshot to a numpy array
    frame = np.array(screenshot)
    # Convert the color from BGR to RGB
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return frame

def get_external_ip():
    services = [
        'https://api.ipify.org?format=json',
        'https://httpbin.org/ip',
        'https://ipinfo.io/ip',
        'https://ifconfig.me/ip',
        'https://ident.me'
    ]

    for service in services:
        try:
            response = requests.get(service)
            response.raise_for_status()
            
            if 'ip' in response.json():  # For JSON responses
                return response.json()['ip']
            else:  # For plain text responses
                return response.text.strip()
        
        except requests.RequestException as e:
            print(f"Error fetching from {service}: {e}")
    
    return None

def get_ip_address():
    ip_address = get_external_ip()
    if not ip_address:
        try:
            # Get the hostname of the machine
            hostname = socket.gethostname()
            # Get the IP address using the hostname
            ip_address = socket.gethostbyname(hostname)
        except Exception as e:
            ip_address = '00000000'

    return ip_address
    #external_ip = urllib.request.urlopen('https://v4.ident.me/ ').read().decode('utf8')
    #return external_ip

def convert_ip_to_hex(ip_address):
    # Split the IP address into its components
    parts = ip_address.split('.')
    # Convert each part to hexadecimal and remove the '0x' prefix
    hex_parts = [format(int(part), '02X') for part in parts]
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
    codec = 'XVID' # Use 'XVID' for portability. Use 'H264' best compression, but host needs to have ffmpeg installed
    videoExtension = '.avi'

    user_input = input("Introduce tu matrícula:")
    pattern = re.compile(r'[^a-zA-Z0-9]')
    # Use the sub() function to replace non-alphanumeric characters with an empty string
    user_input = re.sub(pattern, '', user_input)
    videoName = create_tagged_filename(user_input)

    fourcc = cv2.VideoWriter_fourcc(*codec)
    if codec == 'H264':
        videoExtension = '.mp4'
    # Set the desired frames per second for the output video
    fps = 4
    frame_size = pyautogui.size()
    out = cv2.VideoWriter(videoName+videoExtension, fourcc, fps, frame_size)

    # Trying to prevent corruption of file by handling gracefully a sudden termination
    def signal_handler(sig, frame):
        print('Grabación temrinada por el usuario.')
        out.release()
        cv2.destroyAllWindows()
        sys.exit(0)
    signal.signal(signal.SIGTERM, signal_handler)

    
    print("Iniciando grabación...")
    print("Presiona 'CTRL+C' para terminar.")
    try:
        while True:
            frame = take_screenshot()
            out.write(frame)
            # Wait for 1 second before taking the next screenshot
            time.sleep(1)
    except KeyboardInterrupt:
        print('Grabación temrinada por el usuario.')
    finally:
        out.release()
        cv2.destroyAllWindows()

if __name__ == '__main__':
    main()
